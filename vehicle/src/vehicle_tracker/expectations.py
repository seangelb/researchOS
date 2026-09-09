"""Small calendar, assumption and vintage checks for explicit research scenarios."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from vehicle_tracker.events import CYCLE_COLUMNS, _aware, _cycles


def quarter_coverage(cycles, *, as_of, quarter, timezone_name, max_age_hours=36):
    """Calendar through the cutoff's local date; missing/partial days cannot become zero.

    Returns the calendar and a one-row research-readiness table. No sales conversion.
    A completed cycle on the current local date covers its interval, not the full day.
    """
    cutoff = _aware(as_of)
    period = pd.Period(quarter, freq='Q-DEC')
    if str(period) != quarter or type(max_age_hours) not in (int, float) or not 0 < max_age_hours < float('inf'):
        raise ValueError('Use YYYYQn and a positive freshness threshold')
    last_day = min(cutoff.tz_convert(timezone_name).date(), period.end_time.date())
    if last_day < period.start_time.date():
        raise ValueError('The quarter has not begun at this cutoff')
    if cycles.empty:
        cycles = cycles.reindex(columns=CYCLE_COLUMNS)
    available = pd.to_datetime(cycles.available_at.map(_aware), utc=True)
    known = cycles.loc[available.le(cutoff)].copy()
    known = _cycles(known)
    if not known.empty and not known.timezone.eq(timezone_name).all():
        raise ValueError('Calendar timezone differs from cycle timezone')
    known = known.loc[known._window_end.le(cutoff)]
    calendar = pd.DataFrame({'cycle_date': pd.date_range(period.start_time, last_day).strftime('%Y-%m-%d')})
    calendar = calendar.merge(known[['cycle_date', 'cycle_id', 'coverage_complete', 'coverage_reason', 'window_end']],
                              on='cycle_date', how='left', validate='one_to_one')
    calendar['day_status'] = 'missing'
    calendar.loc[calendar.cycle_id.notna(), 'day_status'] = 'partial'
    calendar.loc[calendar.coverage_complete.eq(True), 'day_status'] = 'complete'
    last_observation = pd.to_datetime(calendar.window_end, utc=True, format='ISO8601').max()
    age = (cutoff-last_observation).total_seconds()/3600 if pd.notna(last_observation) else None
    reasons = []
    if not calendar.day_status.eq('complete').all(): reasons.append('Missing or incomplete quarter dates')
    if age is None or age > max_age_hours: reasons.append('No fresh observations')
    reasons.append('No calibrated sales conversion or verified national population')
    result = dict(as_of=cutoff.isoformat(), quarter=quarter, scope_id=known.scope_id.iloc[0] if len(known) else None,
        expected_dates=len(calendar), complete_dates=int(calendar.day_status.eq('complete').sum()),
        missing_dates=int(calendar.day_status.eq('missing').sum()), partial_dates=int(calendar.day_status.eq('partial').sum()),
        latest_observation=last_observation, age_hours=age,
        remaining_days=(period.end_time.date()-last_day).days,
        estimated_retail_units=pd.NA, status='UNAVAILABLE', reason='; '.join(reasons))
    return calendar, pd.DataFrame([result])


def dated_input(record, *, as_of, quarter, scope_id, metric, units, max_age_days=None):
    """Validate one explicit analyst/benchmark input; never choose a newest version."""
    required = ['input_id', 'source', 'available_at', 'quarter', 'scope_id', 'metric', 'units', 'value']
    if any(key not in record or pd.isna(record[key]) or str(record[key]).strip() == '' for key in required):
        raise ValueError('Missing input identity, provenance or value')
    cutoff, available = _aware(as_of), _aware(record['available_at'])
    if available > cutoff:
        raise ValueError('Input was not available at the cutoff')
    if max_age_days is not None and (max_age_days < 0 or cutoff-available > pd.Timedelta(days=max_age_days)):
        raise ValueError('Input is stale under the explicit freshness policy')
    if any(record[key] != expected for key, expected in
           [('quarter', quarter), ('scope_id', scope_id), ('metric', metric), ('units', units)]):
        raise ValueError('Input period, population, metric or units differ')
    value = record['value']
    if type(value) not in (int, float) or not float('-inf') < value < float('inf') or value < 0:
        raise ValueError('Input must be finite and nonnegative')
    return float(value)


def revision_bridge(previous, current, *, prior_activity_revised):
    """Ordered arithmetic bridge for two comparable assumption scenarios, in retail units.

    Revisions to common dates precede new dates, time roll, then assumption changes.
    Coverage/population changes block numeric attribution. This is not a sales model.
    """
    for key in ('quarter', 'scope_id', 'metric', 'units', 'coverage_basis'):
        if previous[key] != current[key]:
            raise ValueError('Incomparable scenario coverage or definition: '+key)
    if _aware(current['as_of']) < _aware(previous['as_of']):
        raise ValueError('Scenario cutoffs are out of order')
    numbers = [prior_activity_revised] + [row[key] for row in (previous, current)
        for key in ('activity', 'conversion', 'remaining_days', 'daily_rate')]
    if any(type(value) not in (int, float) or not 0 <= value < float('inf') for value in numbers):
        raise ValueError('Scenario inputs must be finite and nonnegative')
    if prior_activity_revised > current['activity'] or current['remaining_days'] > previous['remaining_days']:
        raise ValueError('Invalid common-date activity or remaining days')
    p, c = previous, current
    return pd.DataFrame([
        ('Revised common-date observations', (prior_activity_revised-p['activity'])*p['conversion']),
        ('New observation dates', (c['activity']-prior_activity_revised)*p['conversion']),
        ('Calendar roll', (c['remaining_days']-p['remaining_days'])*p['daily_rate']),
        ('Changed conversion assumption', c['activity']*(c['conversion']-p['conversion'])),
        ('Changed remaining-day assumption', c['remaining_days']*(c['daily_rate']-p['daily_rate']))],
        columns=['component', 'scenario_unit_change'])


def export_research(tables, *, destination, as_of, source_paths, assumptions):
    """Explicit export to a NEW directory with exact file/code/configuration hashes."""
    cutoff = _aware(as_of).isoformat()
    paths = list(map(Path, source_paths)) + list(Path(__file__).parent.glob('*.py'))
    hashes = {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    manifest = dict(as_of=cutoff, file_sha256=hashes, assumptions=assumptions,
                    interpretation='Research scenarios are distinct from observed or validated sales')
    json.dumps(manifest, allow_nan=False)  # Validate assumptions before creating the destination.
    if any(not name.replace('_', '').isalnum() for name in tables):
        raise ValueError('Use plain table names')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for name, table in tables.items(): table.to_csv(destination/(name+'.csv'), index=False)
    manifest['table_sha256'] = {name: hashlib.sha256((destination/(name+'.csv')).read_bytes()).hexdigest() for name in tables}
    (destination/'manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
