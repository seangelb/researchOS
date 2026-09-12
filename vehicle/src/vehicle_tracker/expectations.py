"""Small calendar, assumption and vintage checks for explicit research scenarios."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from vehicle_tracker.events import CYCLE_COLUMNS, _aware, _cycles


RETAIL_UNIT_DEFINITION = 'carvana_retail_units_including_marketplace_net_returns'


def freeze_forecast(record, *, destination, source_paths):
    """Explicit new-file save of an analyst forecast, never a notebook side effect.

    The local save clock cannot be supplied or backdated. This records a research
    forecast; it does not establish calibration or certify the machine's clock.
    """
    from vehicle_tracker.detail_batch import write_new
    saved_at = datetime.now(timezone.utc).isoformat()
    if {'format', 'saved_at', 'source_sha256', 'forecast_sha256'} & record.keys():
        raise ValueError('Provide a new analyst record, not an edited frozen vintage')
    _validate_forecast(record, saved_at=saved_at)
    paths = [Path(path).resolve() for path in source_paths]
    if not paths:
        raise ValueError('Retain source evidence and the model/assumption file')
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError('Forecast already exists; choose a distinct vintage')
    # Retain copies: later edits to a live model or assumptions must not destroy
    # the ability to review an earlier forecast. A partial save has no final JSON.
    contents = {path: path.read_bytes() for path in paths}
    sources_directory = destination.with_name(destination.name+'.sources')
    sources_directory.mkdir(parents=True, exist_ok=False)
    hashes, originals = {}, {}
    for index, (path, data) in enumerate(contents.items(), start=1):
        retained = sources_directory/f'{index:04d}_{path.name}'
        with retained.open('xb') as handle:
            handle.write(data)
        hashes[str(retained)] = hashlib.sha256(data).hexdigest()
        originals[str(retained)] = str(path)
    # The save clock follows retention, not a slow source-copy operation.
    saved_at = datetime.now(timezone.utc).isoformat()
    value = dict(record, format='carvana-quarter-forecast-v1', saved_at=saved_at,
                 source_sha256=hashes, original_source_paths=originals)
    value['forecast_sha256'] = hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
    write_new(destination, value)
    return value


def _validate_forecast(record, *, saved_at):
    for key in ('forecast_id', 'model_version', 'quarter', 'trained_through_quarter', 'model_description'):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError('Missing forecast identity, model version or training period')
    target = pd.Period(record['quarter'], freq='Q-DEC')
    training = pd.Period(record['trained_through_quarter'], freq='Q-DEC')
    if str(target) != record['quarter'] or str(training) != record['trained_through_quarter'] or training >= target:
        raise ValueError('Calibration must end before the evaluated quarter; use YYYYQn')
    if record.get('definition') != RETAIL_UNIT_DEFINITION:
        raise ValueError('Use the explicit reported retail-unit definition, not website Sold counts')
    units = record.get('forecast_units')
    if type(units) not in (int, float) or not 0 <= units < float('inf'):
        raise ValueError('Forecast units must be finite and nonnegative')
    if _aware(record['as_of']) > _aware(saved_at):
        raise ValueError('Forecast cutoff cannot follow its save clock')
    # Use the same New York reporting calendar as the reported-result checks.
    if (training + 1).start_time.tz_localize('America/New_York') > _aware(record['as_of']):
        raise ValueError('Training quarter had not ended at the forecast cutoff')
    coverage = record.get('source_coverage', {})
    if not isinstance(coverage, dict) or not coverage.get('scope_id') or not coverage.get('description'):
        raise ValueError('State the observed source population and coverage limitations')
    counts = [coverage.get(key) for key in ('expected_dates', 'complete_dates', 'partial_dates', 'missing_dates')]
    if any(type(n) is not int or n < 0 for n in counts) or counts[0] != sum(counts[1:]):
        raise ValueError('Coverage dates must reconcile, with missing and partial dates explicit')


def _reported_results_at_cutoff(reported_results, *, cutoff):
    """Select the latest supplied revision known at the cutoff, with its first clock.

    A revision's available_at never substitutes for first_published_at. The latter
    needs its own source; omitted/null means unknown, including for older inputs.
    Earlier known revisions can supply that shared first-publication evidence.
    Future revisions cannot supply a denominator or resolve an unknown clock.
    """
    grouped = {}
    for supplied in reported_results:
        available = _aware(supplied['available_at'])
        if available > cutoff:
            continue
        quarter, definition = supplied['quarter'], supplied['definition']
        period = pd.Period(quarter, freq='Q-DEC')
        if str(period) != quarter or not isinstance(definition, str) or not definition.strip():
            raise ValueError('Reported results require YYYYQn and an explicit definition')
        quarter_end = period.end_time.tz_localize('America/New_York')
        if available <= quarter_end:
            raise ValueError('Reported result precedes the end of its quarter')
        units, source = supplied['reported_units'], supplied.get('source')
        if (type(units) not in (int, float) or not 0 < units < float('inf')
                or not isinstance(source, str) or not source.strip()):
            raise ValueError('Reported units must be positive, finite and sourced')
        first = supplied.get('first_published_at')
        first_source = supplied.get('first_publication_source')
        if first is not None:
            first = _aware(first)
            if first <= quarter_end or first > available:
                raise ValueError('First publication must follow the end of its quarter and not follow revision availability')
            if not isinstance(first_source, str) or not first_source.strip():
                raise ValueError('Known first publication requires first_publication_source')
        else:
            first_source = None
        grouped.setdefault((quarter, definition), []).append(dict(
            reported_units=float(units), available_at=available, source=source,
            first_published_at=first, first_publication_source=first_source))

    selected = {}
    for key, revisions in grouped.items():
        revisions.sort(key=lambda row: row['available_at'])
        if len({row['available_at'] for row in revisions}) != len(revisions):
            raise ValueError('Choose one explicit reported-result vintage per quarter/definition/available_at')
        first_records = [row for row in revisions if row['first_published_at'] is not None]
        if len({row['first_published_at'] for row in first_records}) > 1:
            raise ValueError('Conflicting first-publication clocks across known reported revisions')
        chosen = dict(revisions[-1], known_revision_count=len(revisions))
        if first_records:
            first_record = first_records[0]
            # A later record cannot move first publication beyond an already
            # available original result, even when that original clock is missing.
            if first_record['first_published_at'] > revisions[0]['available_at']:
                raise ValueError('First publication follows an earlier known reported result')
            chosen.update(first_published_at=first_record['first_published_at'],
                          first_publication_source=first_record['first_publication_source'])
        selected[key] = chosen
    return selected


def forecast_review_rows(paths, reported_results, *, as_of):
    """Read every explicit forecast against the latest supplied result known as_of.

    Each result has quarter, definition, reported_units, available_at and source.
    first_published_at and first_publication_source independently establish whether
    a forecast was saved strictly before the first public quarterly result. Missing
    first publication remains unresolved. All result inputs are explicit and local;
    no revisions or company results are fetched, and no frozen files are rewritten.
    """
    cutoff, output, identities = _aware(as_of), [], set()
    results = _reported_results_at_cutoff(reported_results, cutoff=cutoff)
    for path in paths:
        forecast = json.loads(Path(path).read_text(encoding='utf-8'))
        if forecast.get('format') != 'carvana-quarter-forecast-v1':
            raise ValueError('Unknown forecast format')
        claimed_hash = forecast.pop('forecast_sha256', None)
        if hashlib.sha256(json.dumps(forecast, sort_keys=True, allow_nan=False).encode()).hexdigest() != claimed_hash:
            raise ValueError('Saved forecast changed')
        if _aware(forecast['saved_at']) > cutoff:
            continue
        _validate_forecast(forecast, saved_at=forecast['saved_at'])
        if forecast['forecast_id'] in identities:
            raise ValueError('Duplicate forecast ID; retain revisions under distinct IDs')
        identities.add(forecast['forecast_id'])
        if not forecast.get('source_sha256') or any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != sha
                for p, sha in forecast['source_sha256'].items()):
            raise ValueError('Frozen forecast source evidence changed or is missing')
        row = dict(forecast_id=forecast['forecast_id'], quarter=forecast['quarter'],
            model_version=forecast['model_version'], as_of=forecast['as_of'], saved_at=forecast['saved_at'],
            forecast_units=forecast['forecast_units'], source_coverage=forecast['source_coverage'],
            forecast_path=str(path), status='awaiting reported result', reported_units=None,
            reported_available_at=None, reported_source=None, reported_first_published_at=None,
            reported_first_publication_source=None, known_result_revisions=0, prospective_eligible=None)
        result = results.get((forecast['quarter'], forecast['definition']))
        if result is not None:
            first = result['first_published_at']
            eligible = _aware(forecast['saved_at']) < first if first is not None else None
            status = ('first publication unknown; prospective eligibility unresolved' if eligible is None
                else 'eligible' if eligible else 'saved after result; exclude from accuracy')
            row.update(reported_units=result['reported_units'], reported_available_at=result['available_at'].isoformat(),
                reported_source=result['source'], reported_first_published_at=first.isoformat() if first is not None else None,
                reported_first_publication_source=result['first_publication_source'],
                known_result_revisions=result['known_revision_count'], prospective_eligible=eligible, status=status)
        output.append(row)
    return pd.DataFrame(output)


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
