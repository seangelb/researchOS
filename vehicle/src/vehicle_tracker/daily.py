"""One explicit daily inventory workflow using the existing cycle and history storage."""
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from vehicle_tracker.cycles import (collect_cycle, cycle_config, cycle_evidence, cycle_lock,
                                    import_cycle, read_cycle_history, utcnow)
from vehicle_tracker.events import _aware, vin_events
from vehicle_tracker.sales import CANDIDATE_COLUMNS, REVIEW_COLUMNS, sale_candidates
from vehicle_tracker.checks import (CHECK_COLUMNS, append_record, followup_queue, read_records,
    select_checks, select_reviews, validate_checks, validate_reviews, validate_check_identities)
from vehicle_tracker.storage import write_json_atomic


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
        days = days.loc[pd.to_datetime(days.available_at, utc=True).le(cutoff)].copy()
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
    metrics, previous, previous_date, previous_complete = [], set(), None, False
    for day in summary.to_dict('records'):
        frame = rows[rows.cycle_id.eq(day['cycle_id'])]
        identities = set(zip(frame.retailer, frame.vin))
        complete = pd.notna(day['coverage_complete']) and bool(day['coverage_complete']) and not analysis_error
        known = pd.notna(day['cycle_id'])
        date = pd.Timestamp(day['cycle_date'])
        comparable = complete and previous_complete and date - previous_date == pd.Timedelta(days=1)
        metrics.append(dict(coverage_status='invalid' if analysis_error and known else 'complete' if complete else 'partial' if known else 'missing',
            analysis_error=analysis_error,
            observed_vins=len(identities) if known else pd.NA,
            inventory_count=len(identities) if complete else pd.NA,
            pending_true=int(frame.purchase_pending.eq(True).sum()) if known else pd.NA,
            pending_unknown=int(frame.purchase_pending.isna().sum()) if known else pd.NA,
            average_asking_price_usd=frame.asking_price_usd.mean(),
            missing_prices=int(frame.asking_price_usd.isna().sum()) if known else pd.NA,
            new_since_previous_day=len(identities-previous) if comparable else pd.NA,
            absent_since_previous_day=len(previous-identities) if comparable else pd.NA,
            site_marked_sold=pd.NA, estimated_sales=pd.NA))
        previous, previous_date, previous_complete = identities, date, complete
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
    """Record one explicit JSON input; validate against retained history before writing CSV."""
    if kind not in {'check', 'review'}:
        raise ValueError('Record kind must be check or review')
    record = json.loads(Path(record_path).read_text(encoding='utf-8-sig'))
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
    folder.mkdir(parents=True, exist_ok=False)
    for name, table in tables.items():
        table.to_csv(folder / (name + '.csv'), index=False)
    files = [settings['config_path'], settings['plan'], settings['register'], settings['checks'], settings['reviews'],
             *sorted(Path(__file__).parent.glob('*.py'))]
    if settings['database'].is_file():
        files.append(settings['database'])
    write_json_atomic(folder / 'manifest.json', dict(as_of=as_of,
        sources={str(path): digest(path) for path in files if path.is_file()},
        review_inputs={str(path): digest(path) if path.is_file() else None for path in [settings['checks'], settings['reviews']]},
        outputs={path.name: digest(path) for path in sorted(folder.glob('*.csv'))},
        interpretation='Fixed pilot scope. Sold labels and sales estimates remain unavailable.'))
    return folder, tables


def run_tracking(settings, *, live=False, import_path=None, refresh=False, post=None):
    """Explicitly collect/import/export; default call only previews the next run."""
    if sum(bool(action) for action in [live, import_path, refresh]) > 1:
        raise ValueError('Select only one action: live, import or refresh')
    if not any([live, import_path, refresh]):
        registered_cycles(settings)
        return preview(settings), None, None
    settings['register'].parent.mkdir(parents=True, exist_ok=True)
    with cycle_lock(settings['register'].parent):
        entries = registered_cycles(settings)
        state = None
        if live:
            config = preview(settings)
            if any(entry['cycle_date'] == config['cycle_date'] for entry in entries):
                raise ValueError('Today is already registered; no second collection was started')
            # Check old selected evidence before collecting anything new.
            tracking_history(settings, as_of=utcnow().isoformat())
            state = collect_cycle(settings['queries'], destination=config['destination'],
                cycle_date=config['cycle_date'], timezone_name=settings['timezone'],
                window_start=config['window_start'], window_end=config['window_end'],
                max_requests=settings['max_requests'], max_seconds=settings['max_seconds'], post=post)
            import_path = Path(config['destination']) / 'cycle.json'
        if import_path:
            state = register_cycle(settings, import_path)
        folder, tables = export_tracking(settings, as_of=utcnow().isoformat())
        return state, folder, tables
