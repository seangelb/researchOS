"""Read-only daily VIN diagnostics. Missing listings and native pending are not sales."""
import pandas as pd

RULE_VERSION = 'vin-daily-v2'
CYCLE_COLUMNS = ['cycle_id', 'cycle_date', 'timezone', 'window_start', 'window_end',
                 'scope_id', 'coverage_complete', 'coverage_reason', 'available_at']
SOURCE_FIELDS = ['listing_id', 'capture_id', 'run_id', 'source_url', 'listing_url', 'observed_at_utc']
NATIVE_FIELDS = ['asking_price_usd', 'purchase_pending', 'vehicle_lock_type']
EVENT_TYPES = ['first_observed', 'observed', 'relisted', 'reappeared', 'first_absence',
               'continued_absence', 'persistent_absence', 'absence_unassessable']
EVENT_FIELDS = ['event_type', 'observed_in_cycle', 'first_observed_cycle_id', 'last_observed_cycle_id',
    'left_censored', 'timing_uncertain', 'reappeared_after_absence', 'absence_started', 'absence_streak', 'absence_days',
    'native_status_changed', 'pending_changed', 'asking_price_change_usd', 'estimated_sales', 'rule_version']


def _aware(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError('Evidence timestamps must be present and timezone aware')
    return stamp.tz_convert('UTC')


def _cycles(cycles):
    if set(CYCLE_COLUMNS) - set(cycles.columns):
        raise ValueError('Missing daily cycle metadata')
    result = cycles.copy()
    keys = ['cycle_id', 'cycle_date', 'scope_id', 'timezone']
    if (result[keys].isna().any().any() or result[keys].astype(str).apply(lambda x: x.str.strip().eq('')).any().any()
            or result.cycle_id.duplicated().any() or result.cycle_date.duplicated().any()
            or result.scope_id.nunique() > 1 or result.timezone.nunique() > 1):
        raise ValueError('Daily cycles require unique IDs/dates and one consistent scope/timezone')
    if not result.coverage_complete.map(lambda x: type(x) is bool).all():
        raise ValueError('coverage_complete must contain explicit booleans')
    result['_date'] = pd.to_datetime(result.cycle_date, format='%Y-%m-%d', errors='raise')
    if not result._date.dt.strftime('%Y-%m-%d').eq(result.cycle_date).all():
        raise ValueError('cycle_date must be an ISO date')
    for column in ['window_start', 'window_end', 'available_at']:
        result['_'+column] = pd.to_datetime(result[column].map(_aware), utc=True)
    if (result._window_start.gt(result._window_end).any()
            or result._available_at.lt(result._window_end).any()):
        raise ValueError('Cycle windows or availability are inconsistent')
    return result.sort_values('_date').reset_index(drop=True)


def _change(before, after):
    return pd.NA if pd.isna(before) or pd.isna(after) else bool(before != after)


def vin_events(cycles, observations, *, absence_days=3):
    """One row per known retailer/VIN per cycle, ordered by day then identity.

    Observation fields on absent rows retain the last presence evidence; check
    observed_in_cycle and last_observed_cycle_id before treating them as current.
    First observations are left-censored. A persistent-absence event occurs once
    per absence episode when a consecutive complete-day threshold is reached.
    Gaps/partial days reset the streak, and timing uncertainty survives until return.
    Availability is the latest supporting cycle availability, never a backdated sale.
    """
    if type(absence_days) is not int or absence_days < 1:
        raise ValueError('absence_days must be a positive integer')
    schedule = _cycles(cycles)
    required = ['cycle_id', 'retailer', 'vin', 'listing_id', 'capture_id', 'observed_at_utc']
    if set(required) - set(observations.columns):
        raise ValueError('Missing observation identity or evidence columns')
    if (observations[required].isna().any().any()
            or observations[required].astype(str).apply(lambda x: x.str.strip().eq('')).any().any()
            or not observations.cycle_id.isin(schedule.cycle_id).all()):
        raise ValueError('Missing identities/evidence or unknown observation-cycle keys')
    if (observations.duplicated(['cycle_id', 'retailer', 'vin']).any()
            or observations.duplicated(['cycle_id', 'retailer', 'listing_id']).any()
            or observations.groupby(['retailer', 'listing_id']).vin.nunique().gt(1).any()):
        raise ValueError('Conflicting VIN/listing identities require explicit review')
    bounds = observations[['cycle_id', 'observed_at_utc']].merge(
        schedule[['cycle_id', '_window_start', '_window_end']], on='cycle_id', validate='many_to_one')
    clocks = pd.to_datetime(bounds.observed_at_utc.map(_aware), utc=True)
    if not clocks.between(bounds._window_start, bounds._window_end).all():
        raise ValueError('Observation timestamp falls outside its daily cycle window')
    columns = list(dict.fromkeys([*observations.columns, *SOURCE_FIELDS, *NATIVE_FIELDS, *CYCLE_COLUMNS,
        *('previous_'+field for field in [*SOURCE_FIELDS, *NATIVE_FIELDS]), *EVENT_FIELDS]))
    rows, known, previous, availability = [], {}, None, None
    missing = float('nan')
    for cycle in schedule.to_dict('records'):
        availability = max(availability, cycle['_available_at']) if availability is not None else cycle['_available_at']
        present = {(row['retailer'], row['vin']): row for row in observations.loc[
            observations.cycle_id.eq(cycle['cycle_id'])].to_dict('records')}
        gap = previous is not None and (cycle['_date'] - previous['_date']).days != 1
        for key in sorted(set(known) | set(present)):
            state, current = known.get(key), present.get(key)
            before = state['last'] if state else {}
            uncertain = bool(state and (state['uncertain'] or gap or (previous and
                not previous['coverage_complete'] and before['cycle_id'] != previous['cycle_id'])))
            row = dict(current if current is not None else before)
            row.update({name: cycle[name] for name in CYCLE_COLUMNS})
            row.update({'previous_'+field: before.get(field) for field in [*SOURCE_FIELDS, *NATIVE_FIELDS]})
            row.update(available_at=availability.isoformat(), observed_in_cycle=current is not None,
                left_censored=state is None, timing_uncertain=uncertain or state is None,
                reappeared_after_absence=bool(current is not None and state and state['absent']),
                absence_started=False,
                native_status_changed=pd.NA, pending_changed=pd.NA, asking_price_change_usd=pd.NA,
                estimated_sales=pd.NA, rule_version=RULE_VERSION, absence_days=absence_days)
            if current is not None:
                event = ('first_observed' if state is None else 'relisted' if current['listing_id'] != before['listing_id']
                         else 'reappeared' if state['absent'] else 'observed')
                if state:
                    changes = [_change(before.get(field), current.get(field))
                               for field in ['purchase_pending', 'vehicle_lock_type']]
                    row['pending_changed'] = changes[0]
                    # A known change stays true even if another native field is unknown.
                    known_changes = [change for change in changes if pd.notna(change)]
                    row['native_status_changed'] = (True if any(known_changes) else
                        False if len(known_changes) == len(changes) else pd.NA)
                    prices = [before.get('asking_price_usd'), current.get('asking_price_usd')]
                    if all(pd.notna(x) for x in prices):
                        row['asking_price_change_usd'] = prices[1] - prices[0]
                state = dict(last=current, first=state['first'] if state else cycle['cycle_id'],
                             streak=0, absent=False, persistent=False, uncertain=False)
            else:
                if gap or (previous and not previous['coverage_complete']):
                    state['streak'] = 0
                state['uncertain'] = uncertain or not cycle['coverage_complete']
                if not cycle['coverage_complete']:
                    event, state['streak'] = 'absence_unassessable', 0
                else:
                    state['streak'] += 1
                    row['absence_started'] = not state['absent']
                    event = 'continued_absence' if state['absent'] else 'first_absence'
                    state['absent'] = True
                    if state['streak'] >= absence_days and not state['persistent']:
                        event, state['persistent'] = 'persistent_absence', True
                row['timing_uncertain'] = state['uncertain']
            known[key] = state
            row.update(event_type=event, absence_streak=state['streak'],
                first_observed_cycle_id=state['first'], last_observed_cycle_id=state['last']['cycle_id'])
            # Tuples avoid retaining a second large dictionary for every VIN/day.
            rows.append(tuple(row.get(name, missing) for name in columns))
        previous = cycle
    result = pd.DataFrame(rows, columns=columns)
    for column in ['native_status_changed', 'pending_changed']:
        result[column] = result[column].astype('boolean')
    return result


def daily_counts(cycles, events):
    """Daily diagnostics over the supplied scope; incomplete-day absence counts are unknown.

    Zero observed VINs on a partial cycle means zero observed, not zero inventory.
    Pending transitions are native-state diagnostics, never confirmed cancellations/sales.
    """
    schedule = _cycles(cycles)
    if set(['cycle_id', 'retailer', 'vin', 'event_type', 'observed_in_cycle']) - set(events.columns):
        raise ValueError('Expected the VIN event table')
    if (not events.cycle_id.isin(schedule.cycle_id).all()
            or events.duplicated(['cycle_id', 'retailer', 'vin']).any()
            or not events.event_type.isin(EVENT_TYPES).all()):
        raise ValueError('Invalid or ambiguous event-cycle identities')
    rows, previous, availability = [], None, None
    for cycle in schedule.to_dict('records'):
        availability = max(availability, cycle['_available_at']) if availability is not None else cycle['_available_at']
        frame = events.loc[events.cycle_id.eq(cycle['cycle_id'])]
        seen = frame.loc[frame.observed_in_cycle.eq(True)]
        row = {name: cycle[name] for name in CYCLE_COLUMNS}
        row.update({event: int(frame.event_type.eq(event).sum()) for event in EVENT_TYPES})
        row.update(first_absence=int(frame.absence_started.eq(True).sum()), available_at=availability.isoformat())
        if not cycle['coverage_complete']:
            row.update({event: pd.NA for event in ['first_absence', 'continued_absence', 'persistent_absence']})
        row.update(observed_vins=len(seen), pending_true=int(seen.purchase_pending.eq(True).sum()),
            pending_unknown=int(seen.purchase_pending.isna().sum()),
            native_status_changed=int(seen.native_status_changed.eq(True).sum()),
            native_status_unknown=int(seen.native_status_changed.isna().sum()),
            pending_started=int((seen.previous_purchase_pending.eq(False) & seen.purchase_pending.eq(True)).sum()),
            pending_cleared=int((seen.previous_purchase_pending.eq(True) & seen.purchase_pending.eq(False)).sum()),
            skipped_days_before=0 if previous is None else (cycle['_date'] - previous).days - 1,
            timing_uncertain=int(frame.timing_uncertain.eq(True).sum()), estimated_sales=pd.NA, rule_version=RULE_VERSION)
        rows.append(row)
        previous = cycle['_date']
    return pd.DataFrame(rows)
