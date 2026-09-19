"""A read-only VIN story: physical observations, derived absences and calendar gaps."""
import pandas as pd

from vehicle_tracker.checks import (CHECK_COLUMNS, IDENTITY, _text_rows,
    validate_checks, validate_check_identities)
from vehicle_tracker.events import CYCLE_COLUMNS, _aware, vin_events

TIMELINE_COLUMNS = ['retailer', 'vin', 'evidence_type', 'observed_at', 'available_at',
    'assessed_at', 'cycle_date', 'window_start', 'window_end', 'cycle_id', 'listing_id',
    'last_known_listing_id', 'purchase_pending', 'vehicle_lock_type', 'asking_price_usd',
    'observed_status', 'native_text', 'derived_event', 'derived_available_at',
    'timing_uncertain', 'coverage_complete', 'coverage_reason', 'absence_streak',
    'reappeared_after_absence', 'source', 'capture_id', 'check_id', 'correction_versions',
    'reviewer', 'note']

HISTORY_COLUMNS = ['retailer', 'vin', 'first_observed_at', 'last_observed_at',
    'first_observed_date', 'first_available_at', 'first_known_at', 'available_at', 'observed_span_days',
    'retained_captures', 'observed_cycles', 'observed_scopes', 'listing_ids',
    'first_cycle_ids', 'first_scope_ids', 'first_capture_ids',
    'first_evidence_includes_partial', 'known_only_from_partial_cycles',
    'present_in_earliest_selected_cycle', 'earlier_listing_history_unknown', 'as_of']
COHORT_COLUMNS = ['retailer', 'first_observed_date', 'observed_vins',
    'first_evidence_includes_partial', 'known_only_from_partial_cycles',
    'known_only_from_partial_cycles_known_vins', 'present_in_earliest_selected_cycle',
    'present_in_earliest_selected_cycle_known_vins', 'as_of']


def observed_history(days, rows, *, as_of):
    """First sightings within selected evidence, including valid partial-cycle rows.

    Return one history row per retailer/VIN, first-local-date cohort counts, and
    every original observation membership joined to its cycle context. Different
    scopes and relistings do not reset first sighting when earlier evidence is
    selected. No price/status is chosen across contexts, and no absence or new
    listing is inferred. Elapsed first-to-last sighting is not continuous time on
    market or the age of the current listing episode. Caller replays source bytes
    before passing rows; this function performs no I/O and changes no inputs.
    """
    cutoff = _aware(as_of)
    empty = (pd.DataFrame(columns=HISTORY_COLUMNS), pd.DataFrame(columns=COHORT_COLUMNS),
             pd.DataFrame(columns=list(dict.fromkeys([*rows.columns, *CYCLE_COLUMNS]))))
    if days.empty:
        if not rows.empty:
            raise ValueError('Observation history requires matching cycle evidence')
        return empty
    if set(CYCLE_COLUMNS) - set(days.columns):
        raise ValueError('Observation history requires cycle context and clocks')
    schedule = days[CYCLE_COLUMNS].copy()
    required = ['cycle_id', 'retailer', 'vin', 'listing_id', 'capture_id', 'observed_at_utc']
    if set(required) - set(rows.columns):
        raise ValueError('Observation history requires identity and source columns')
    if (schedule.cycle_id.isna().any() or schedule.cycle_id.duplicated().any()
            or not rows.cycle_id.isin(schedule.cycle_id).all()):
        raise ValueError('Missing, duplicate or unknown observation-cycle identities')
    schedule['available_at'] = schedule.available_at.map(_aware)
    schedule = schedule.loc[schedule.available_at.le(cutoff)].copy()
    selected = rows.loc[rows.cycle_id.isin(schedule.cycle_id)].copy()
    if schedule.empty:
        return empty
    keys = ['cycle_id', 'cycle_date', 'scope_id', 'timezone']
    if (schedule[keys].isna().any().any()
            or schedule[keys].astype(str).apply(lambda s: s.str.strip().eq('')).any().any()
            or schedule.timezone.nunique() != 1
            or not schedule.coverage_complete.map(lambda value: type(value) is bool).all()):
        raise ValueError('Require explicit cycle identities, coverage and one timezone')
    dates = pd.to_datetime(schedule.cycle_date, format='%Y-%m-%d', errors='raise')
    if not dates.dt.strftime('%Y-%m-%d').eq(schedule.cycle_date).all():
        raise ValueError('Cycle dates must be ISO dates')
    for column in ['window_start', 'window_end']:
        schedule[column] = schedule[column].map(_aware)
    if (schedule.window_start.gt(schedule.window_end).any()
            or schedule.window_end.gt(schedule.available_at).any()):
        raise ValueError('Cycle observation and availability clocks are inconsistent')
    if (selected[required].isna().any().any()
            or selected[required].astype(str).apply(lambda s: s.str.strip().eq('')).any().any()
            or selected.duplicated(['cycle_id', 'capture_id', 'retailer', 'listing_id']).any()
            or selected.groupby(['retailer', 'listing_id']).vin.nunique().gt(1).any()):
        raise ValueError('Missing, repeated or conflicting retained observation identities')
    selected['observed_at_utc'] = pd.to_datetime(selected.observed_at_utc.map(_aware), utc=True)
    memberships = selected.merge(schedule, on='cycle_id', how='left', validate='many_to_one')
    if not memberships.observed_at_utc.between(memberships.window_start, memberships.window_end).all():
        raise ValueError('Observation falls outside its retained cycle window')
    if memberships.empty:
        return empty[0], empty[1], memberships
    earliest = set(schedule.loc[schedule.window_start.eq(schedule.window_start.min()), 'cycle_id'])
    memberships['source_group_kind'] = 'cycle'
    memberships['source_group_id'] = memberships.cycle_id
    memberships['analysis_available_at'] = memberships.available_at
    return summarize_observed_memberships(memberships, as_of=as_of, earliest_cycle_ids=earliest)


def summarize_observed_memberships(memberships, *, as_of, earliest_cycle_ids=()):
    """Pure positive history for validated cycle, query-report and DOM memberships.

    Non-cycle sources keep null cycle identities. ``first_available_at`` is the
    availability of the earliest observed capture; ``first_known_at`` is when any
    selected observation of the VIN first became eligible. Late historical
    publications can change the first metric without backdating the second.
    Unknown analysis availability is withheld, never replaced by an observation
    clock. This function chooses no representative price and infers no absence.
    """
    cutoff = _aware(as_of)
    memberships = memberships.copy(deep=True)
    empty = (pd.DataFrame(columns=HISTORY_COLUMNS), pd.DataFrame(columns=COHORT_COLUMNS), memberships.iloc[:0])
    if memberships.empty:
        return empty
    required = ['retailer', 'vin', 'listing_id', 'capture_id', 'observed_at_utc',
                'source_group_kind', 'source_group_id', 'cycle_id', 'scope_id',
                'timezone', 'coverage_complete', 'available_at', 'analysis_available_at']
    if set(required) - set(memberships.columns):
        raise ValueError('Positive history requires explicit source memberships and analysis clocks')
    for name in ['available_at', 'analysis_available_at']:
        memberships[name] = pd.to_datetime(memberships[name].map(
            lambda value: _aware(value) if pd.notna(value) else pd.NaT), utc=True)
    if not (memberships.available_at.eq(memberships.analysis_available_at)
            | (memberships.available_at.isna() & memberships.analysis_available_at.isna())).all():
        raise ValueError('Membership availability differs from its analysis publication clock')
    memberships = memberships.loc[memberships.available_at.notna() & memberships.available_at.le(cutoff)].copy()
    if memberships.empty:
        return empty[0], empty[1], memberships
    identities = [name for name in required if name not in {'cycle_id', 'coverage_complete',
                                                          'available_at', 'analysis_available_at'}]
    if (memberships[identities].isna().any().any()
            or memberships[identities].astype(str).apply(lambda s: s.str.strip().eq('')).any().any()
            or memberships.timezone.nunique() != 1
            or not memberships.coverage_complete.map(lambda value: type(value) is bool).all()
            or memberships.duplicated(['source_group_kind', 'source_group_id', 'capture_id', 'retailer', 'listing_id']).any()
            or memberships.groupby(['retailer', 'listing_id']).vin.nunique().gt(1).any()):
        raise ValueError('Missing, repeated or conflicting positive observation memberships')
    real_cycle = memberships.source_group_kind.eq('cycle')
    if (memberships.loc[real_cycle, 'cycle_id'].isna().any()
            or memberships.loc[~real_cycle, 'cycle_id'].notna().any()
            or memberships.loc[~real_cycle, 'coverage_complete'].any()):
        raise ValueError('Non-cycle evidence cannot claim cycle identity or complete cycle coverage')
    memberships['observed_at_utc'] = pd.to_datetime(memberships.observed_at_utc.map(_aware), utc=True)
    if memberships.observed_at_utc.gt(memberships.available_at).any():
        raise ValueError('Observation cannot follow its analysis availability')
    memberships = memberships.sort_values(
        ['observed_at_utc', 'retailer', 'vin', 'source_group_kind', 'source_group_id', 'capture_id'],
        kind='stable').reset_index(drop=True)
    earliest = set(earliest_cycle_ids)
    zone = memberships.timezone.iloc[0]
    summaries = []
    for (retailer, vin), group in memberships.groupby(['retailer', 'vin'], sort=True):
        first, last = group.observed_at_utc.min(), group.observed_at_utc.max()
        initial = group.loc[group.observed_at_utc.eq(first)]
        cycles = group.loc[group.cycle_id.notna()]
        joined = lambda name: ';'.join(sorted(set(initial[name].dropna().astype(str)))) or None
        summaries.append(dict(retailer=retailer, vin=vin, first_observed_at=first,
            last_observed_at=last, first_observed_date=first.tz_convert(zone).date().isoformat(),
            first_available_at=initial.available_at.min(), first_known_at=group.available_at.min(),
            available_at=group.available_at.max(),
            observed_span_days=(last-first).total_seconds()/86400,
            retained_captures=group.capture_id.nunique(), observed_cycles=group.cycle_id.nunique(),
            observed_scopes=group.scope_id.nunique(), listing_ids=';'.join(sorted(set(group.listing_id.astype(str)))),
            first_cycle_ids=joined('cycle_id'), first_scope_ids=joined('scope_id'),
            first_capture_ids=joined('capture_id'),
            first_evidence_includes_partial=bool((~initial.coverage_complete).any()),
            known_only_from_partial_cycles=bool((~cycles.coverage_complete).all()) if len(cycles) else None,
            present_in_earliest_selected_cycle=bool(cycles.cycle_id.isin(earliest).any()) if len(cycles) and earliest else None,
            earlier_listing_history_unknown=True, as_of=cutoff))
    history = pd.DataFrame(summaries, columns=HISTORY_COLUMNS)
    cohorts = history.groupby(['retailer', 'first_observed_date'], as_index=False).agg(
        observed_vins=('vin', 'size'),
        first_evidence_includes_partial=('first_evidence_includes_partial', 'sum'),
        known_only_from_partial_cycles=('known_only_from_partial_cycles', lambda values: values.sum(min_count=1)),
        known_only_from_partial_cycles_known_vins=('known_only_from_partial_cycles', 'count'),
        present_in_earliest_selected_cycle=('present_in_earliest_selected_cycle', lambda values: values.sum(min_count=1)),
        present_in_earliest_selected_cycle_known_vins=('present_in_earliest_selected_cycle', 'count'))
    cohorts['as_of'] = cutoff
    return history, cohorts[COHORT_COLUMNS], memberships


def vin_timeline(days, rows, checks=None, *, retailer, vin, as_of):
    """Combine retained evidence for one retailer/VIN at an explicit aware cutoff.

    Physical checks are keyed by retailer/VIN/listing/checked_at; each shows its
    latest available correction, not just the latest visit. correction_versions
    counts earlier retained versions of that same physical check at this cutoff.
    Inventory availability is its own cycle's availability; derived_available_at
    can be later because an event also depends on earlier cycles.

    Absence rows have no physical observation time, listing ID, price, or native
    status. Their cycle window anchors the comparison, not an exact disappearance
    time. Gap rows describe an unregistered date as assessed at this cutoff, not
    evidence of an absent vehicle. Internal ordering anchors are never presented
    as physical timestamps. This function performs no network access or writes.
    """
    cutoff = _aware(as_of)
    if any(not isinstance(value, str) or not value.strip() for value in [retailer, vin]):
        raise ValueError('Select an explicit retailer and VIN')
    known_checks = _text_rows(checks, CHECK_COLUMNS, optional=['native_text'])
    known_checks = known_checks.loc[pd.to_datetime(known_checks.available_at, utc=True,
        format='ISO8601').le(cutoff)]
    known_checks = validate_checks(known_checks)
    if days.empty:
        if not known_checks.empty:
            raise ValueError('Page checks require matching inventory evidence at this cutoff')
        return pd.DataFrame(columns=TIMELINE_COLUMNS)
    days = days.loc[days.available_at.map(_aware).le(cutoff)].copy()
    rows = rows.loc[rows.cycle_id.isin(days.cycle_id)].copy()
    # Validate the whole known population before narrowing the VIN. Otherwise a
    # listing ID assigned to two VINs could disappear behind the selection filter.
    events = vin_events(days, rows)
    validate_check_identities(known_checks, rows)
    selected = events.loc[events.retailer.eq(retailer) & events.vin.eq(vin)]
    if selected.empty:
        return pd.DataFrame(columns=TIMELINE_COLUMNS)
    zone = days.timezone.iloc[0]
    cycle_availability = days.set_index('cycle_id').available_at
    story = []
    for event in selected.to_dict('records'):
        present = event['observed_in_cycle']
        item = {name: event[name] for name in ['retailer', 'vin', 'cycle_id', 'cycle_date',
            'window_start', 'window_end', 'timing_uncertain', 'coverage_complete',
            'coverage_reason', 'absence_streak', 'reappeared_after_absence']}
        item.update(evidence_type='inventory_observation' if present else 'derived_absence',
            available_at=cycle_availability[event['cycle_id']], assessed_at=cutoff,
            derived_event=event['event_type'], derived_available_at=event['available_at'],
            _order=_aware(event['observed_at_utc'] if present else event['window_end']))
        if present:
            item.update({name: event.get(name) for name in ['listing_id', 'purchase_pending',
                'vehicle_lock_type', 'asking_price_usd', 'capture_id']})
            item.update(observed_at=event['observed_at_utc'], source=event.get('source_url'),
                note='Actual inventory observation; derived_event is a separate interpretation.')
        else:
            item.update(last_known_listing_id=event['listing_id'],
                source='registered cycle ' + event['cycle_id'],
                note='No VIN observation in this cycle; window is not an exact disappearance time.')
        story.append(item)
    selected_checks = known_checks.loc[known_checks.retailer.eq(retailer) & known_checks.vin.eq(vin)].copy()
    physical_key = [*IDENTITY, 'checked_at']
    selected_checks['correction_versions'] = selected_checks.groupby(physical_key).check_id.transform('size') - 1
    selected_checks = selected_checks.sort_values('available_at').drop_duplicates(physical_key, keep='last')
    for check in selected_checks.to_dict('records'):
        check.update(evidence_type='page_check', observed_at=check.pop('checked_at'), assessed_at=cutoff)
        check['_order'] = _aware(check['observed_at'])
        check['cycle_date'] = check['_order'].tz_convert(zone).date().isoformat()
        story.append(check)
    dates = pd.date_range(selected.cycle_date.min(), cutoff.tz_convert(zone).date())
    for date in dates:
        day = date.date().isoformat()
        if day not in set(days.cycle_date):
            story.append(dict(retailer=retailer, vin=vin, evidence_type='collection_gap',
                cycle_date=day, assessed_at=cutoff, derived_event='no_registered_cycle',
                timing_uncertain=True, source='registered cycle calendar at cutoff',
                note='No registered cycle at cutoff; inventory and absence are unknown.',
                _order=min(cutoff, (date + pd.Timedelta(days=1)).tz_localize(zone).tz_convert('UTC'))))
    result = pd.DataFrame(story).reindex(columns=[*TIMELINE_COLUMNS, '_order'])
    for column in ['observed_at', 'available_at', 'assessed_at', 'derived_available_at', 'window_start', 'window_end']:
        result[column] = pd.to_datetime(result[column], utc=True, format='ISO8601')
    return result.sort_values(['_order', 'evidence_type'], kind='stable').drop(columns='_order').reset_index(drop=True)
