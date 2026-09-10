"""A read-only VIN story: physical observations, derived absences and calendar gaps."""
import pandas as pd

from vehicle_tracker.checks import (CHECK_COLUMNS, IDENTITY, _text_rows,
    validate_checks, validate_check_identities)
from vehicle_tracker.events import _aware, vin_events

TIMELINE_COLUMNS = ['retailer', 'vin', 'evidence_type', 'observed_at', 'available_at',
    'assessed_at', 'cycle_date', 'window_start', 'window_end', 'cycle_id', 'listing_id',
    'last_known_listing_id', 'purchase_pending', 'vehicle_lock_type', 'asking_price_usd',
    'observed_status', 'native_text', 'derived_event', 'derived_available_at',
    'timing_uncertain', 'coverage_complete', 'coverage_reason', 'absence_streak',
    'reappeared_after_absence', 'source', 'capture_id', 'check_id', 'correction_versions',
    'reviewer', 'note']


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
