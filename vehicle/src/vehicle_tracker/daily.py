"""One explicit daily inventory workflow using the existing cycle and history storage."""
from contextlib import nullcontext
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from vehicle_tracker.cycles import (collect_cycle, cycle_config, cycle_diagnostic, cycle_evidence, cycle_lock,
                                    import_cycle, read_cycle_history, utcnow)
from vehicle_tracker.events import _aware, vin_events
from vehicle_tracker.sales import CANDIDATE_COLUMNS, REVIEW_COLUMNS, sale_candidates
from vehicle_tracker.checks import (CHECK_COLUMNS, append_record, followup_queue, read_records,
    select_checks, select_reviews, validate_checks, validate_reviews, validate_check_identities)
from vehicle_tracker.storage import write_json_atomic
from vehicle_tracker.search_plan import require_safe_resume


def _prepared_check_id(record):
    content = {key: value for key, value in record.items() if key != 'check_id'}
    return 'prepared-' + hashlib.sha256(json.dumps(content, sort_keys=True).encode('utf-8')).hexdigest()


def _validated_check_record(record):
    """The notebook and JSON entry points use the same structural/entry checks."""
    validated = validate_checks(pd.DataFrame([record])).iloc[0].to_dict()
    for field, value in validated.items():
        text = value.strip().casefold()
        if text in {'...', 'todo', 'tbd', 'replace me', 'your name'} or (text.startswith('<') and text.endswith('>')):
            raise ValueError('Replace the placeholder in ' + field)
    if validated['source'].strip().casefold().startswith('synthetic://'):
        raise ValueError('Synthetic evidence cannot be recorded in the daily history')
    native = validated['native_text']
    without_boilerplate = re.sub(r'\bas\s+originally\s+sold\b', '', native, flags=re.IGNORECASE)
    if (validated['observed_status'] == 'sold_label' and native != without_boilerplate
            and not re.search(r'\bsold\b', without_boilerplate, flags=re.IGNORECASE)):
        raise ValueError('Generic equipment wording "as originally sold" is not a listing Sold label')
    if validated['check_id'].startswith('prepared-') and validated['check_id'] != _prepared_check_id(validated):
        raise ValueError('Prepared check changed; prepare and preview the edited record again')
    return validated


def prepare_check(observations, *, retailer, vin, listing_id, draft, available_at):
    """Pure preview: explicit retained identity, actual check time, and fixed availability.

    The draft contains checked_at, observed_status, native_text, source, reviewer,
    and note. The caller supplies availability once; this function reads no clock
    and writes nothing. Repeating identical normalized inputs yields the same ID.
    The eventual save rechecks the identity against registered inventory evidence.
    """
    fields = {'checked_at', 'observed_status', 'native_text', 'source', 'reviewer', 'note'}
    if not isinstance(draft, dict) or set(draft) != fields:
        raise ValueError('Draft requires exactly: ' + ', '.join(sorted(fields)))
    if observations.empty:
        raise ValueError('Select an identity from retained inventory first; history is empty')
    record = _validated_check_record(dict(check_id='draft', retailer=retailer, vin=vin,
        listing_id=listing_id, available_at=available_at, **draft))
    validate_check_identities(pd.DataFrame([record]), observations)
    record['check_id'] = _prepared_check_id(record)
    return record


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tracking_settings(path):
    path = Path(path).resolve()
    settings = json.loads(path.read_text(encoding='utf-8'))
    root = path.parent.parent
    for name in ['plan', 'capture_root', 'database', 'register', 'exports']:
        settings[name] = (root / settings[name]).resolve()
    for name, filename in [('checks', 'listing_checks.csv'), ('reviews', 'candidate_reviews.csv')]:
        settings[name] = (root / settings[name]).resolve() if name in settings else settings['database'].parent / filename
    if len({settings[key] for key in ['database', 'register', 'checks', 'reviews']}) != 4:
        raise ValueError('Database, register, checks and reviews require distinct paths')
    settings['queries'] = json.loads(settings['plan'].read_text(encoding='utf-8'))['queries']
    settings['config_path'] = path
    ZoneInfo(settings['timezone'])
    if type(settings['followup_limit']) is not int or not 0 <= settings['followup_limit'] <= 100:
        raise ValueError('Require 0-100 detail follow-ups')
    return settings


def preview(settings, *, now=None):
    now = now or utcnow()
    day = now.astimezone(ZoneInfo(settings['timezone'])).date().isoformat()
    config = cycle_config(settings['queries'], cycle_date=day, timezone_name=settings['timezone'],
        window_start=now.isoformat(), window_end=(now + timedelta(seconds=settings['max_seconds'])).isoformat(),
        max_requests=settings['max_requests'], max_seconds=settings['max_seconds'])
    config['destination'] = str(settings['capture_root'] / day)
    config['isolate_pagination'] = True
    return config


def registered_cycles(settings):
    if not settings['register'].is_file():
        return []
    register = json.loads(settings['register'].read_text(encoding='utf-8'))
    if register['database'] != str(settings['database']):
        raise ValueError('Registered database differs from tracking settings')
    entries = register['cycles']
    if len({row['cycle_date'] for row in entries}) != len(entries):
        raise ValueError('Duplicate registered dates require explicit review')
    for row in entries:
        path = Path(row['path'])
        if not path.is_file() or digest(path) != row['sha256']:
            raise ValueError('Registered cycle changed or is missing: ' + str(path))
        state = json.loads(path.read_text(encoding='utf-8'))
        if (state['cycle_id'] != row['cycle_id'] or state['cycle_date'] != row['cycle_date']
                or state['queries'] != settings['queries'] or state['timezone'] != settings['timezone']):
            raise ValueError('Registered identity or daily population changed')
        report_paths = {str(path.parent / attempt / query['query_id'] / 'run_report.json')
                        for attempt in state['attempts'] for query in state['queries']
                        if (path.parent / attempt / query['query_id'] / 'run_report.json').is_file()}
        if report_paths != set(row['reports']) or any(digest(report) != sha for report, sha in row['reports'].items()):
            raise ValueError('Registered query evidence changed or is missing')
    return entries


def recovery_candidates(settings, *, now=None):
    """Read-only discovery of unregistered cycles; this never resumes a request."""
    now = now or utcnow()
    entries = registered_cycles(settings)
    registered_paths = {entry['path'] for entry in entries}
    registered_dates = {entry['cycle_date'] for entry in entries}
    candidates = []
    for folder in sorted(settings['capture_root'].glob('*')):
        if not folder.is_dir():
            continue
        path = folder / 'cycle.json'
        if not path.is_file() and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', folder.name):
            continue
        if str(path.resolve()) in registered_paths:
            continue
        row = dict(path=str(path.resolve()), import_allowed=False, live_resume_allowed=False)
        try:
            diagnostic, query_health = cycle_diagnostic(path, now=now)
            row.update(diagnostic, query_health=query_health.to_dict('records'))
            if diagnostic['validation_errors'] or not diagnostic['requests_reconciled']:
                raise ValueError('; '.join(diagnostic['validation_errors']) or
                                 'Durable request budget differs from child/page evidence; outcome uncertain')
            cycle_state, coverage, reports = cycle_evidence(path)
            retained_stop_reason = None
            for report in reports:
                try:
                    require_safe_resume(json.loads(report.read_text(encoding='utf-8')))
                except ValueError as error:
                    retained_stop_reason = str(error)
                    break
            matches_population = (cycle_state['queries'] == settings['queries']
                                  and cycle_state['timezone'] == settings['timezone'])
            date_already_registered = cycle_state['cycle_date'] in registered_dates
            row.update(cycle_date=cycle_state['cycle_date'], coverage_complete=cycle_state['coverage_complete'],
                       requests=cycle_state['budget']['requests'],
                       import_allowed=matches_population and not date_already_registered)
            elapsed = (now - _aware(cycle_state['created_at'])).total_seconds()
            # The first applicable reason wins; offline import and another request
            # have separate eligibility. Completed evidence needs no new request.
            if not matches_population:
                reason = 'Settings population or timezone differs'
            elif date_already_registered:
                reason = 'Date already registered with different evidence; review only'
            elif cycle_state['coverage_complete']:
                reason = 'Complete retained cycle; no requests needed'
            elif cycle_state.get('isolate_pagination') or cycle_state.get('isolated_query_failures'):
                reason = 'Pagination-isolated cycle cannot resume; retain its incomplete coverage'
            elif diagnostic['request_outcome_uncertain']:
                reason = 'Retained request outcome uncertain; live resume blocked'
            elif cycle_state['budget']['pending_request']:
                reason = 'Previous request outcome uncertain; live resume blocked'
            elif cycle_state['budget']['stopped']:
                reason = 'Access or transport stop; live resume blocked'
            elif retained_stop_reason:
                reason = retained_stop_reason
            elif any(cycle_state[key] != settings[key] for key in ['max_requests', 'max_seconds']):
                reason = 'Settings limits differ; live resume blocked'
            elif (coverage.query_complete.eq(1) & ~coverage.within_window).any():
                reason = 'Completed query is outside the frozen window; live resume blocked'
            elif not (0 <= elapsed < cycle_state['max_seconds']
                      and _aware(cycle_state['window_start']) <= now < _aware(cycle_state['window_end'])):
                reason = 'Observation window expired or clock changed; live resume blocked'
            elif (cycle_state['budget']['last_request_utc']
                  and now < _aware(cycle_state['budget']['last_request_utc'])):
                reason = 'Current clock precedes the last request; live resume blocked'
            elif cycle_state['budget']['requests'] >= cycle_state['max_requests']:
                reason = 'Request budget exhausted; live resume blocked'
            else:
                reason = None
            row['live_resume_allowed'] = reason is None
            row['reason'] = reason or 'Incomplete retained cycle; explicit identical-window resume may be possible'
            row['offline_action'] = f'--import-cycle "{path.resolve()}"' if row['import_allowed'] else 'Review the retained cycle with its original settings'
        except (ValueError, OSError, KeyError, TypeError) as error:
            row.update(reason='Retained cycle validation failed: ' + str(error), offline_action='Review retained evidence; do not recollect into this folder')
        candidates.append(row)
    return candidates


def tracking_history(settings, *, as_of):
    entries = registered_cycles(settings)
    return read_cycle_history([row['path'] for row in entries], settings['database'], as_of=as_of)


def daily_tables(days, rows, *, as_of, timezone_name, followup_limit=20, checks=None, reviews=None):
    """Visible daily calendar, actual observation rows and a bounded URL follow-up queue."""
    cutoff = pd.Timestamp(as_of)
    if cutoff.tzinfo is None or pd.isna(cutoff):
        raise ValueError('as_of must be a timezone-aware timestamp')
    if type(followup_limit) is not int or not 0 <= followup_limit <= 100:
        raise ValueError('Require 0-100 detail follow-ups')
    checks, reviews = select_checks(checks, as_of=as_of), select_reviews(reviews, as_of=as_of)
    evidence = dict(listing_checks=checks, selected_reviews=reviews)
    if not days.empty:
        days = days.loc[pd.to_datetime(days.available_at, utc=True, format='ISO8601').le(cutoff)].copy()
        rows = rows.loc[rows.cycle_id.isin(days.cycle_id)].copy()
    if days.empty:
        if not checks.empty or not reviews.empty:
            raise ValueError('Check/review inputs require matching inventory evidence at this cutoff')
        return {**{name: pd.DataFrame() for name in ['daily_inventory', 'vehicle_observations',
                                                'sale_candidates', 'detail_followups']}, **evidence}
    ordered = days.sort_values('cycle_date')
    if not ordered.timezone.eq(timezone_name).all():
        raise ValueError('Daily table timezone differs from cycle evidence')
    analysis_error = None
    try:
        events = vin_events(ordered, rows)
    except ValueError as error:
        # Preserve actual rows and the diagnostic while withholding derived counts.
        analysis_error = str(error)
        events, candidates = pd.DataFrame(), pd.DataFrame(columns=CANDIDATE_COLUMNS)
    if not analysis_error:
        validate_check_identities(checks, rows)
        candidates, candidate_daily = sale_candidates(ordered, rows, as_of=as_of, reviews=reviews)
    first = pd.Timestamp(ordered.cycle_date.min())
    last = cutoff.tz_convert(timezone_name).date()
    calendar = pd.DataFrame({'cycle_date': pd.date_range(first, last).strftime('%Y-%m-%d')})
    summary = calendar.merge(ordered, on='cycle_date', how='left', validate='one_to_one')
    inventory_rows = rows.merge(ordered[['cycle_id', 'cycle_date', 'coverage_complete']],
                               on='cycle_id', validate='many_to_one')
    inventory_rows['duplicate_vin_in_cycle'] = rows.duplicated(['cycle_id', 'retailer', 'vin'], keep=False).to_numpy()
    inventory_rows['duplicate_listing_in_cycle'] = rows.duplicated(['cycle_id', 'retailer', 'listing_id'], keep=False).to_numpy()
    metrics = []
    previous_identities, previous_date, previous_inventory_complete = set(), None, False
    for day in summary.to_dict('records'):
        day_observations = rows[rows.cycle_id.eq(day['cycle_id'])]
        observed_identities = set(zip(day_observations.retailer, day_observations.vin))
        inventory_complete = (pd.notna(day['coverage_complete'])
                              and bool(day['coverage_complete']) and not analysis_error)
        has_recorded_cycle = pd.notna(day['cycle_id'])
        date = pd.Timestamp(day['cycle_date'])
        daily_change_available = (inventory_complete and previous_inventory_complete
                                  and date - previous_date == pd.Timedelta(days=1))
        if analysis_error and has_recorded_cycle:
            coverage_status = 'invalid'
        elif inventory_complete:
            coverage_status = 'complete'
        elif has_recorded_cycle:
            coverage_status = 'partial'
        else:
            coverage_status = 'missing'
        # Partial cycles describe only observed rows. Inventory changes require
        # complete coverage on both consecutive dates; absence is not a sale.
        metrics.append(dict(coverage_status=coverage_status,
            analysis_error=analysis_error,
            observed_vins=len(observed_identities) if has_recorded_cycle else pd.NA,
            inventory_count=len(observed_identities) if inventory_complete else pd.NA,
            pending_true=int(day_observations.purchase_pending.eq(True).sum()) if has_recorded_cycle else pd.NA,
            pending_unknown=int(day_observations.purchase_pending.isna().sum()) if has_recorded_cycle else pd.NA,
            average_asking_price_usd=day_observations.asking_price_usd.mean(),
            missing_prices=int(day_observations.asking_price_usd.isna().sum()) if has_recorded_cycle else pd.NA,
            new_since_previous_day=len(observed_identities - previous_identities) if daily_change_available else pd.NA,
            absent_since_previous_day=len(previous_identities - observed_identities) if daily_change_available else pd.NA,
            site_marked_sold=pd.NA, estimated_sales=pd.NA))
        previous_identities, previous_date, previous_inventory_complete = observed_identities, date, inventory_complete
    summary = pd.concat([summary, pd.DataFrame(metrics)], axis=1)
    summary['as_of'] = cutoff.isoformat()
    if analysis_error:
        summary['new_candidates'], summary['candidate_reappearances'] = pd.NA, pd.NA
        summary['reviewed_sales_with_known_date'] = pd.NA
        return dict(daily_inventory=summary, vehicle_observations=inventory_rows,
                    sale_candidates=candidates, detail_followups=pd.DataFrame(), **evidence)
    summary = summary.merge(candidate_daily[['cycle_date', 'new_candidates', 'candidate_reappearances',
                                            'reviewed_sales_with_known_date']],
                            on='cycle_date', how='left', validate='one_to_one')
    followups = followup_queue(events, checks, as_of=as_of, limit=followup_limit)
    return dict(daily_inventory=summary, vehicle_observations=inventory_rows,
                sale_candidates=candidates, detail_followups=followups, **evidence)


def review_inputs(settings):
    return read_records(settings['checks'], CHECK_COLUMNS), read_records(settings['reviews'], REVIEW_COLUMNS)


def tracking_report(settings, *, as_of, checks=None, reviews=None):
    """Shared offline report for the command and notebook; explicit frames override saved inputs."""
    days, rows = tracking_history(settings, as_of=as_of)
    checks = read_records(settings['checks'], CHECK_COLUMNS) if checks is None else checks
    reviews = read_records(settings['reviews'], REVIEW_COLUMNS) if reviews is None else reviews
    return daily_tables(days, rows, as_of=as_of, timezone_name=settings['timezone'],
        followup_limit=settings['followup_limit'], checks=checks, reviews=reviews)


def record_evidence(settings, record_path, *, kind):
    """Record explicit JSON or a prepared dictionary using the same locked write path."""
    if kind not in {'check', 'review'}:
        raise ValueError('Record kind must be check or review')
    record = dict(record_path) if isinstance(record_path, dict) else json.loads(Path(record_path).read_text(encoding='utf-8-sig'))
    if kind == 'check':
        record = _validated_check_record(record)
    supplied = (validate_checks if kind == 'check' else validate_reviews)(pd.DataFrame([record]))
    available = supplied.available_at.iloc[0]
    if _aware(available) > utcnow():
        raise ValueError('Cannot record evidence with future availability')
    settings['register'].parent.mkdir(parents=True, exist_ok=True)
    with cycle_lock(settings['register'].parent):
        days, rows = tracking_history(settings, as_of=available)
        if kind == 'check':
            validate_check_identities(supplied, rows)
        else:
            sale_candidates(days, rows, as_of=available, reviews=select_reviews(supplied, as_of=available))
        return append_record(settings['checks' if kind == 'check' else 'reviews'], record, kind=kind)


def register_cycle(settings, cycle_path):
    """Import retained evidence and append its explicit date selection; caller owns lock."""
    path = Path(cycle_path).resolve()
    # A collector may be invoked separately from the daily command. Bind evidence
    # only while its cycle is idle; a crash releases both operating-system locks.
    lock = nullcontext() if path.parent == settings['register'].parent.resolve() else cycle_lock(path.parent)
    with lock:
        return _register_cycle(settings, path)


def _register_cycle(settings, path):
    state, _, reports = cycle_evidence(path)
    if state['queries'] != settings['queries'] or state['timezone'] != settings['timezone']:
        raise ValueError('Cycle population differs from daily tracking settings')
    entries = registered_cycles(settings)
    same_day = [entry for entry in entries if entry['cycle_date'] == state['cycle_date']]
    entry = dict(cycle_date=state['cycle_date'], cycle_id=state['cycle_id'], path=str(path), sha256=digest(path),
                 reports={str(report): digest(report) for report in reports})
    if same_day and same_day != [entry]:
        raise ValueError('Date already registered with different evidence')
    import_cycle(path, settings['database'])
    if not same_day:
        entries.append(entry)
        write_json_atomic(settings['register'], dict(database=str(settings['database']),
                          cycles=sorted(entries, key=lambda row: row['cycle_date'])))
    return state


def export_tracking(settings, *, as_of):
    tables = tracking_report(settings, as_of=as_of)
    folder = settings['exports'] / (utcnow().strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8])
    staging = folder.with_name('.' + folder.name + '.partial')
    staging.mkdir(parents=True, exist_ok=False)
    for name, table in tables.items():
        with (staging / (name + '.csv')).open('w', encoding='utf-8', newline='') as stream:
            table.to_csv(stream, index=False)
            stream.flush()
            os.fsync(stream.fileno())
    files = [settings['config_path'], settings['plan'], settings['register'], settings['checks'], settings['reviews'],
             *sorted(Path(__file__).parent.glob('*.py'))]
    if settings['database'].is_file():
        files.append(settings['database'])
    write_json_atomic(staging / 'manifest.json', dict(as_of=as_of,
        sources={str(path): digest(path) for path in files if path.is_file()},
        review_inputs={str(path): digest(path) if path.is_file() else None for path in [settings['checks'], settings['reviews']]},
        outputs={path.name: digest(path) for path in sorted(staging.glob('*.csv'))},
        interpretation='Fixed pilot scope. Sold labels and sales estimates remain unavailable.'))
    staging.rename(folder)  # Publish the complete CSV set and manifest together.
    return folder, tables


def run_tracking(settings, *, live=False, import_path=None, refresh=False, post=None):
    """Explicitly collect/import/export; default call only previews the next run."""
    if sum(bool(action) for action in [live, import_path, refresh]) > 1:
        raise ValueError('Select only one action: live, import or refresh')
    if not any([live, import_path, refresh]):
        config = preview(settings)
        config['recovery_candidates'] = recovery_candidates(settings)
        config['incomplete_exports'] = [str(path) for path in sorted(settings['exports'].glob('.*.partial'))]
        return config, None, None
    settings['register'].parent.mkdir(parents=True, exist_ok=True)
    with cycle_lock(settings['register'].parent):
        entries = registered_cycles(settings)
        state = None
        if live:
            config = preview(settings)
            if any(entry['cycle_date'] == config['cycle_date'] for entry in entries):
                raise ValueError('Today is already registered; no second collection was started')
            if Path(config['destination']).exists():
                raise ValueError('Retained daily destination already exists; preview recovery candidates or use --import-cycle. No collection started')
            # Check old selected evidence before collecting anything new.
            tracking_history(settings, as_of=utcnow().isoformat())
            state = collect_cycle(settings['queries'], destination=config['destination'],
                cycle_date=config['cycle_date'], timezone_name=settings['timezone'],
                window_start=config['window_start'], window_end=config['window_end'],
                max_requests=settings['max_requests'], max_seconds=settings['max_seconds'], post=post,
                isolate_pagination=config['isolate_pagination'])
            import_path = Path(config['destination']) / 'cycle.json'
        if import_path:
            state = register_cycle(settings, import_path)
        folder, tables = export_tracking(settings, as_of=utcnow().isoformat())
        return state, folder, tables
