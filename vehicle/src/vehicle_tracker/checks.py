"""Saved page checks and analyst reviews. Observed website text is not a sale."""
from pathlib import Path
from uuid import uuid4

import pandas as pd

from vehicle_tracker.events import _aware
from vehicle_tracker.sales import REVIEW_COLUMNS

IDENTITY = ['retailer', 'vin', 'listing_id']
CHECK_COLUMNS = ['check_id', *IDENTITY, 'checked_at', 'available_at', 'observed_status',
                 'native_text', 'source', 'reviewer', 'note']
CHECK_STATUSES = {'available', 'pending', 'sold_label', 'unavailable', 'access_blocked', 'unknown'}
RECHECK_HOURS = 48  # Operating interval, not an inferred sale delay.


def read_records(path, columns):
    """Read strings literally (including 'NA' and leading zeros); missing file is empty."""
    return (pd.read_csv(path, dtype=str, keep_default_na=False) if Path(path).is_file()
            else pd.DataFrame(columns=columns))


def _text_rows(rows, columns, optional=()):
    if rows is None:
        return pd.DataFrame(columns=columns)
    if set(rows.columns) != set(columns):
        raise ValueError('Expected exactly these columns: ' + ', '.join(columns))
    result = rows[columns].copy().fillna('')
    for column in columns:
        if not result[column].map(lambda value: isinstance(value, str)).all():
            raise ValueError('Use text values for ' + column)
        if column not in optional and result[column].str.strip().eq('').any():
            raise ValueError('Required value is empty: ' + column)
    result['available_at'] = result.available_at.map(lambda value: _aware(value).isoformat())
    return result


def _unique(rows, keys):
    result = rows.drop_duplicates().reset_index(drop=True)
    if result.duplicated(keys).any():
        raise ValueError('Conflicting records for ' + ', '.join(keys))
    return result


def validate_checks(rows):
    result = _text_rows(rows, CHECK_COLUMNS, optional=['native_text'])
    result['checked_at'] = result.checked_at.map(lambda value: _aware(value).isoformat())
    if (not result.observed_status.isin(CHECK_STATUSES).all()
            or pd.to_datetime(result.checked_at, utc=True, format='ISO8601').gt(pd.to_datetime(result.available_at, utc=True, format='ISO8601')).any()):
        raise ValueError('Invalid check status or checked_at is later than available_at')
    textual = result.observed_status.isin(['available', 'pending', 'sold_label', 'unavailable'])
    if result.loc[textual, 'native_text'].str.strip().eq('').any():
        raise ValueError('A website status requires the exact relevant native wording')
    result = _unique(result, ['check_id'])
    return _unique(result, [*IDENTITY, 'checked_at', 'available_at'])


def validate_reviews(rows):
    result = _text_rows(rows, REVIEW_COLUMNS, optional=['sale_date'])
    if not result.outcome.isin(['confirmed_sale', 'not_sale', 'unresolved']).all():
        raise ValueError('Invalid review outcome')
    for row in result.itertuples():
        if row.sale_date and (row.outcome != 'confirmed_sale'
                or pd.Timestamp(row.sale_date).strftime('%Y-%m-%d') != row.sale_date):
            raise ValueError('Sale date requires a confirmed outcome and an ISO date')
    return _unique(result, ['candidate_id', 'available_at'])


def select_checks(rows, *, as_of):
    # Filter first: a later check/version cannot change an earlier selection.
    known = _text_rows(rows, CHECK_COLUMNS, optional=['native_text'])
    known = known.loc[pd.to_datetime(known.available_at, utc=True, format='ISO8601').le(_aware(as_of))]
    known = validate_checks(known)
    return known.sort_values(['checked_at', 'available_at']).drop_duplicates(IDENTITY, keep='last').reset_index(drop=True)


def select_reviews(rows, *, as_of):
    known = _text_rows(rows, REVIEW_COLUMNS, optional=['sale_date'])
    known = known.loc[pd.to_datetime(known.available_at, utc=True, format='ISO8601').le(_aware(as_of))]
    known = validate_reviews(known).sort_values('available_at').drop_duplicates('candidate_id', keep='last')
    return known.assign(sale_date=known.sale_date.replace('', None)).reset_index(drop=True)


def append_record(path, record, *, kind):
    """Explicit atomic local write; caller holds the daily register's existing lock.

    Checks are immutable by check_id. A correction uses a new ID and later
    availability, retaining the actual checked_at time. Review versions are keyed
    by candidate_id/available_at. Exact replay leaves the file unchanged.
    """
    if kind not in {'check', 'review'}:
        raise ValueError('Record kind must be check or review')
    columns, validate = (CHECK_COLUMNS, validate_checks) if kind == 'check' else (REVIEW_COLUMNS, validate_reviews)
    path = Path(path)
    previous = validate(read_records(path, columns))
    supplied = validate(pd.DataFrame([record]))
    combined = validate(pd.concat([previous, supplied], ignore_index=True))
    if len(combined) == len(previous):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        combined.to_csv(temporary, index=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def validate_check_identities(checks, observations):
    vin_counts = observations.groupby(['retailer', 'listing_id'], as_index=False).vin.nunique(dropna=False)
    referenced = checks[['retailer', 'listing_id']].merge(vin_counts, on=['retailer', 'listing_id'], how='left', validate='many_to_one')
    if referenced.vin.ne(1).any():
        raise ValueError('Check must reference an observed retailer/VIN/listing with an unambiguous VIN')
    first = observations.groupby(IDENTITY, as_index=False).observed_at_utc.min()
    joined = checks.merge(first, on=IDENTITY, how='left', validate='many_to_one')
    if (joined.observed_at_utc.isna().any() or pd.to_datetime(joined.checked_at, utc=True, format='ISO8601').lt(
            pd.to_datetime(joined.observed_at_utc, utc=True, format='ISO8601')).any()):
        raise ValueError('Check must reference an observed retailer/VIN/listing and cannot predate its first observation')


def followup_queue(events, checks, *, as_of, limit=20):
    """Keep actionable history until checked; prioritize first checks, changes, then due rechecks."""
    if events.empty:
        return pd.DataFrame()
    checks = select_checks(checks, as_of=as_of)
    history = events.copy()
    history['followup_reason'] = pd.NA
    history.loc[history.native_status_changed.eq(True), 'followup_reason'] = 'native_status_changed'
    history.loc[history.event_type.eq('relisted'), 'followup_reason'] = 'relisted'
    history.loc[history.reappeared_after_absence.eq(True), 'followup_reason'] = 'reappeared'
    history.loc[history.absence_started.eq(True), 'followup_reason'] = 'first_absence'
    actions = history.loc[history.followup_reason.notna()]
    last_action = actions.drop_duplicates(IDENTITY, keep='last')[[*IDENTITY, 'followup_reason', 'available_at']]
    last_action = last_action.rename(columns={'available_at': 'changed_at'})
    first_action = actions.groupby(IDENTITY, as_index=False).available_at.min().rename(columns={'available_at': 'waiting_since'})
    latest = history.loc[history.cycle_date.eq(history.cycle_date.max())].drop(columns='followup_reason')
    queue = latest.merge(last_action, on=IDENTITY, how='left', validate='one_to_one')
    queue = queue.merge(first_action, on=IDENTITY, how='left', validate='one_to_one')
    check_fields = ['check_id', 'checked_at', 'check_available_at', 'native_text', 'source', 'reviewer', 'note']
    queue = queue.merge(checks.rename(columns={'available_at': 'check_available_at', 'observed_status': 'detail_status'}),
                        on=IDENTITY, how='left', validate='one_to_one')
    unresolved = queue.detail_status.isin(CHECK_STATUSES - {'available'})
    queue = queue.loc[queue.followup_reason.notna() | unresolved].copy()
    queue['followup_reason'] = queue.followup_reason.fillna('unresolved_check')
    queue.loc[queue.absence_streak.ge(3), 'followup_reason'] = 'persistent_absence'
    checked = pd.to_datetime(queue.checked_at, utc=True, format='ISO8601')
    changed = pd.to_datetime(queue.changed_at, utc=True, format='ISO8601')
    due = unresolved.reindex(queue.index) & checked.add(pd.Timedelta(hours=RECHECK_HOURS)).le(_aware(as_of))
    queue['queue_state'] = 'up_to_date'
    queue.loc[due, 'queue_state'] = 'recheck_due'
    queue.loc[changed.gt(checked), 'queue_state'] = 'changed_since_check'
    queue.loc[checked.isna(), 'queue_state'] = 'first_check'
    queue['priority'] = queue.queue_state.map({'first_check': 1, 'changed_since_check': 2, 'recheck_due': 3, 'up_to_date': 4})
    queue['waiting_since'] = queue.waiting_since.where(checked.isna(), queue.checked_at)
    queue.loc[queue.queue_state.eq('changed_since_check'), 'waiting_since'] = queue.changed_at
    queue = queue.sort_values(['priority', 'waiting_since', *IDENTITY]).reset_index(drop=True)
    queue['selected_for_check'] = (queue.index < limit) & queue.priority.lt(4)
    queue['detail_status'] = queue.detail_status.fillna('not_checked')
    return queue[['cycle_date', *IDENTITY, 'listing_url', 'capture_id', 'last_observed_cycle_id',
        'observed_at_utc', 'followup_reason', 'waiting_since', 'queue_state', 'selected_for_check',
        'detail_status', *check_fields]]
