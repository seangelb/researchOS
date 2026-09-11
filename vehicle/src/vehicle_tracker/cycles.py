"""Explicit daily search windows, durable budgets and read-only history selection."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from vehicle_tracker.collect import NavigationBudget, CollectionStopped
from vehicle_tracker.history import OBSERVATION_COLUMNS, import_reports, read_history, read_query_evidence
from vehicle_tracker.search import ENDPOINT
from vehicle_tracker.search_plan import collect_plan, query_outcome, require_safe_resume, validate_plan
from vehicle_tracker.storage import write_json_atomic


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.utcoffset() is None:
        raise ValueError('Use timezone-aware observation windows')
    return stamp.astimezone(timezone.utc)


def cycle_config(queries, *, cycle_date, timezone_name, window_start, window_end,
                 max_requests=120, max_seconds=1200):
    """Validate without writes/network. A cycle belongs to one explicit local date."""
    validate_plan(queries)
    zone = ZoneInfo(timezone_name)
    start, end = aware(window_start), aware(window_end)
    day = date.fromisoformat(cycle_date)
    if (start >= end or start.astimezone(zone).date() != day or end.astimezone(zone).date() != day
            or type(max_requests) is not int or not 1 <= max_requests <= 10000
            or type(max_seconds) not in (int, float) or not math.isfinite(max_seconds)
            or not 0 < max_seconds <= 21600):
        raise ValueError('Use one local date, <=10000 requests, and <=21600 seconds')
    scope = dict(queries=queries, endpoint=ENDPOINT, sort='MostPopular')
    return dict(cycle_date=cycle_date, timezone=timezone_name, window_start=start.isoformat(),
                window_end=end.isoformat(), max_requests=max_requests, max_seconds=max_seconds,
                scope_id=hashlib.sha256(json.dumps(scope,sort_keys=True).encode()).hexdigest(), queries=queries)


def _cycle_state(path):
    """Validate the frozen configuration and durable reservation before using either."""
    state = json.loads(Path(path).read_text(encoding='utf-8'))
    config = cycle_config(state['queries'], cycle_date=state['cycle_date'], timezone_name=state['timezone'],
        window_start=state['window_start'], window_end=state['window_end'],
        max_requests=state['max_requests'], max_seconds=state['max_seconds'])
    if any(state[key] != value for key, value in config.items()):
        raise ValueError('Cycle configuration or scope fingerprint changed')
    saved = state['budget']
    if (type(saved['requests']) is not int or not 0 <= saved['requests'] <= state['max_requests']
            or type(saved['stopped']) is not bool or type(saved['pending_request']) is not bool
            or bool(saved['requests']) != bool(saved['last_request_utc'])
            or (saved['pending_request'] and not saved['requests'])):
        raise ValueError('Invalid durable cycle budget; retained evidence requires review')
    created = aware(state['created_at'])
    if not aware(state['window_start']) <= created < aware(state['window_end']):
        raise ValueError('Cycle creation is outside its frozen observation window')
    if saved['last_request_utc'] and aware(saved['last_request_utc']) < created:
        raise ValueError('Invalid durable request clock; retained evidence requires review')
    return state


@contextmanager
def cycle_lock(directory):
    """OS lock is released on process exit, including a crash; no stale-lock deletion."""
    with (directory/'cycle.lock').open('a+b') as handle:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError('Cycle already has an active owner') from exc
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


class CycleBudget(NavigationBudget):
    """The existing sequential budget, persisted before transport across attempts."""
    def __init__(self, path):
        self.path = Path(path)
        state = _cycle_state(self.path)
        saved = state['budget']
        if saved['pending_request']:
            raise ValueError('Previous request outcome uncertain; cycle cannot retry automatically')
        if saved['stopped']:
            raise ValueError('Cycle stopped after an access/transport failure')
        self.max_requests = state['max_requests']
        now = utcnow()
        elapsed = (now-aware(state['created_at'])).total_seconds()
        if elapsed < 0 or (saved['last_request_utc'] and aware(saved['last_request_utc']) > now):
            raise ValueError('Current clock precedes the durable cycle budget; cannot resume')
        self.max_seconds = min(state['max_seconds'],(aware(state['window_end'])-aware(state['created_at'])).total_seconds())
        self.started = time.monotonic()-elapsed
        self.requests, self.stopped, self.pause_seconds = saved['requests'], False, 3
        self.last_request = (time.monotonic()-(now-aware(saved['last_request_utc'])).total_seconds()
                             if saved['last_request_utc'] else None)
        self.last_request_utc = saved['last_request_utc']

    def save(self, pending=None):
        state = json.loads(self.path.read_text(encoding='utf-8'))
        if pending is None:
            pending = state['budget']['pending_request']
        state['budget'] = dict(requests=self.requests, stopped=self.stopped,
                               pending_request=pending, last_request_utc=self.last_request_utc)
        write_json_atomic(self.path,state)

    def before_navigation(self):
        super().before_navigation()
        self.last_request_utc = utcnow().isoformat()
        self.save(pending=True)  # An interrupted/uncertain request remains reserved.

    def request_started(self):
        super().request_started()
        self.last_request_utc = utcnow().isoformat()
        # Reservation is already durable. Persist this actual start only when
        # acknowledging the response; no filesystem delay belongs before transport.

    def response_received(self):
        self.save(pending=False)

    def stop(self):
        self.stopped = True
        self.save()


def cycle_evidence(path, *, as_of=None):
    """Return cycle metadata, every requested query's coverage, and all attempt reports."""
    path = Path(path).resolve()
    state = _cycle_state(path)
    cutoff = aware(as_of) if as_of is not None else None
    selected, reports, reported_requests = {}, [], 0
    for index, attempt in enumerate(state['attempts'], 1):
        if attempt != f'attempt_{index:04d}':
            raise ValueError('Invalid cycle attempt path')
        for query in state['queries']:
            report = path.parent/attempt/query['query_id']/'run_report.json'
            if not report.is_file():
                continue
            native = json.loads(report.read_text(encoding='utf-8'))
            if 'requests' in native:
                if type(native['requests']) is not int or native['requests'] < 0:
                    raise ValueError('Invalid retained query request count')
                reported_requests += native['requests']
            # A later retry must not revise a result available at an earlier cutoff.
            if cutoff and (not native.get('ended_utc') or aware(native['ended_utc']) > cutoff):
                continue
            run, captures, _ = read_query_evidence(report)
            availability = captures['evidence_available_at_utc'].dropna().tolist() if 'evidence_available_at_utc' in captures else []
            run['evidence_available_at_utc'] = max(map(aware, availability)).isoformat() if availability else None
            if cutoff and run['observation_end'] and aware(run['observation_end']) > cutoff:
                continue
            if cutoff and run['evidence_available_at_utc'] and aware(run['evidence_available_at_utc']) > cutoff:
                continue
            if (native['filters'] != query['filters'] or native['zip_code'] != query['zip_code']
                    or native.get('location_filter',False) != query.get('location_filter',False)):
                raise ValueError('Query attempt differs from the cycle plan')
            reports.append(report)
            if query['query_id'] not in selected or not selected[query['query_id']]['query_complete']:
                selected[query['query_id']] = dict(run,report_path=str(report))
    if state['budget']['requests'] < reported_requests:
        raise ValueError('Durable cycle budget understates retained query requests')
    coverage = []
    for query in state['queries']:
        run = selected.get(query['query_id'],{})
        has_clocks = bool(run.get('observation_start') and run.get('observation_end'))
        fresh = bool(has_clocks and
            aware(state['window_start']) <= aware(run['observation_start']) <= aware(run['observation_end']) < aware(state['window_end']))
        reason = ('No attempted query' if not run else
                  'Outside daily observation window' if has_clocks and not fresh else run['coverage_reason'])
        coverage.append({'query_id': query['query_id'], 'query_complete': 0, **run,
                         'within_window': fresh,
                         'coverage_complete': run.get('query_complete') == 1 and fresh, 'reason': reason})
    coverage = pd.DataFrame(coverage)
    state['coverage_complete'] = bool(coverage.coverage_complete.all())
    state['coverage_reason'] = ('Complete requested daily scope' if state['coverage_complete'] else
                               '; '.join(coverage.loc[~coverage.coverage_complete,'reason'].unique()))
    clocks = [state['created_at']] + [run[key] for run in selected.values()
        for key in ('observation_end','invocation_end','evidence_available_at_utc') if run.get(key)]
    state['available_at'] = max(map(aware,clocks)).isoformat()
    return state, coverage, reports


def cycle_diagnostic(path, *, now=None):
    """Read current checkpoints without importing, repairing or authorizing requests.

    Parent summaries can lag child checkpoints. Source-less pages are inspected
    only through the diagnostic reader; they never become captures or inventory.
    This is operational health now, not a historical evidence-cutoff reconstruction.
    """
    path = Path(path).resolve()
    state = _cycle_state(path)
    now = now or utcnow()
    selected, parents, errors = {}, [], []
    child_requests = page_requests = uncertain_pages = 0
    for index, attempt in enumerate(state['attempts'], 1):
        if attempt != f'attempt_{index:04d}':
            raise ValueError('Invalid cycle attempt path')
        attempt_requests, frames = 0, []
        for query in state['queries']:
            report = path.parent / attempt / query['query_id'] / 'run_report.json'
            if not report.is_file():
                if report.parent.exists():
                    errors.append(f"{attempt}/{query['query_id']}: query folder lacks report")
                    selected[query['query_id']] = dict(status='invalid evidence', reason=errors[-1])
                continue
            row = dict(status='invalid evidence', report_path=str(report))
            try:
                native = json.loads(report.read_text(encoding='utf-8'))
                if (native['filters'] != query['filters'] or native['zip_code'] != query['zip_code']
                        or native.get('location_filter', False) != query.get('location_filter', False)
                        or native.get('endpoint') != ENDPOINT):
                    raise ValueError('Query attempt differs from the cycle plan')
                count = native['requests']
                if type(count) is not int or count < 0:
                    raise ValueError('Invalid retained query request count')
                child_requests += count
                attempt_requests += count
                row['child_requests'] = count
                pages = native['pages']
                if [page['page'] for page in pages] != list(range(1, len(pages) + 1)):
                    raise ValueError('Query page sequence differs from checkpoint order')
                reserved = sum(bool(page.get('request_reserved_at_utc') or page.get('request_started_at_utc')
                    or page.get('response_received_at_utc') or page.get('status') == 'parsed') for page in pages)
                page_requests += reserved
                if count != reserved:
                    raise ValueError('Child request count differs from page reservation evidence')
                run, captures, rows = read_query_evidence(report, diagnostic=True)
                frames.append(rows)
                uncertain = run['uncertain_pages'] + sum(bool(page.get('request_started_at_utc')
                    and not page.get('response_received_at_utc') and page.get('retained_source')) for page in pages)
                uncertain_pages += uncertain
                fresh = bool(run['observation_start'] and run['observation_end'] and
                    aware(state['window_start']) <= aware(run['observation_start']) <=
                    aware(run['observation_end']) < aware(state['window_end']))
                row.update(verified_retained_rows=len(rows), parsed_pages=int(captures.status.eq('parsed').sum()),
                    unattempted_pages=run['unattempted_pages'], uncertain_pages=uncertain,
                    child_requests=count, reported_total=run['reported_total'],
                    query_complete=bool(run['query_complete']), within_window=fresh,
                    status=('complete' if run['query_complete'] and fresh else
                            'request outcome uncertain' if uncertain else
                            'partial; unattempted checkpoint' if run['unattempted_pages'] else 'partial'),
                    reason='Outside frozen window' if run['query_complete'] and not fresh else run['coverage_reason'])
            except (ValueError, OSError, KeyError, TypeError) as error:
                row['reason'] = str(error)
                errors.append(f"{attempt}/{query['query_id']}: {error}")
            if query['query_id'] not in selected or selected[query['query_id']].get('status') != 'complete':
                selected[query['query_id']] = row
        parent = path.parent / attempt / 'run_report.json'
        parent_row = dict(attempt=attempt, child_requests=attempt_requests)
        if frames:
            source_rows = pd.concat(frames, ignore_index=True)
            parent_row['verified_retained_unique_listings'] = len(source_rows[['retailer', 'listing_id']].drop_duplicates())
        if parent.is_file():
            try:
                native_parent = json.loads(parent.read_text(encoding='utf-8'))
                if native_parent['queries'] != state['queries']:
                    raise ValueError('Parent plan differs from frozen cycle plan')
                for key in ['requests', 'unique_listings']:
                    value = native_parent.get(key)
                    if type(value) is not int or value < 0:
                        raise ValueError('Invalid parent summary count')
                    parent_row['parent_' + key] = value
                parent_row['summary_differs_from_children'] = (
                    parent_row['parent_requests'] != attempt_requests or
                    parent_row['parent_unique_listings'] != parent_row.get('verified_retained_unique_listings', 0))
            except (ValueError, OSError, KeyError, TypeError) as error:
                errors.append(f'{attempt} parent: {error}')
        else:
            parent_row['summary_differs_from_children'] = True
        parents.append(parent_row)
    budget = state['budget']
    reconciled = budget['requests'] == child_requests == page_requests
    uncertainty = bool(budget['pending_request'] or uncertain_pages or not reconciled)
    coverage = []
    for query in state['queries']:
        row = dict(query_id=query['query_id'], status='not started', verified_retained_rows=0,
            parsed_pages=0, unattempted_pages=0, uncertain_pages=0, child_requests=0,
            reported_total=None, query_complete=False, within_window=False, reason='No child report')
        row.update(selected.get(query['query_id'], {}))
        if row['status'] == 'invalid evidence':
            row.update(verified_retained_rows=None, parsed_pages=None, unattempted_pages=None, uncertain_pages=None)
        if uncertainty and row['unattempted_pages']:
            row['status'] = 'partial; request reservation uncertain'
            row['uncertain_pages'] += row['unattempted_pages']
            row['unattempted_pages'] = 0
            row['reason'] = 'Durable reservation cannot be attributed safely to an unattempted checkpoint'
        if not reconciled and row['status'] == 'not started':
            row['status'] = 'no report; request attribution uncertain'
        coverage.append(row)
    coverage = pd.DataFrame(coverage)
    elapsed = (now - aware(state['created_at'])).total_seconds()
    window_open = 0 <= elapsed < state['max_seconds'] and aware(state['window_start']) <= now < aware(state['window_end'])
    summary = dict(cycle_date=state['cycle_date'], window_start=state['window_start'], window_end=state['window_end'],
        checked_at=now.isoformat(), window_open=window_open, coverage_complete=bool(coverage.status.eq('complete').all()) and not errors,
        durable_requests=budget['requests'], child_requests=child_requests, page_reservations=page_requests,
        unattributed_reservations=max(0, budget['requests'] - page_requests),
        requests_reconciled=reconciled, request_outcome_uncertain=uncertainty,
        pending_request=budget['pending_request'], stopped=budget['stopped'],
        parent_summaries=parents, validation_errors=errors,
        recovery_limit='Diagnostic only; if present, source-less checkpoints require a separate reviewed recovery change. '
                       'No window extension or automatic resume.')
    return summary, coverage


def collect_cycle(queries, *, destination, cycle_date, timezone_name, window_start, window_end,
                  max_requests=120, max_seconds=1200, resume=False, post=None):
    """Collect the whole declared plan in a bounded daily window; no implicit retries."""
    config = cycle_config(queries,cycle_date=cycle_date,timezone_name=timezone_name,
        window_start=window_start,window_end=window_end,max_requests=max_requests,max_seconds=max_seconds)
    if not resume and not aware(config['window_start']) <= utcnow() < aware(config['window_end']):
        raise ValueError('Current time is outside the requested daily observation window')
    directory = Path(destination).resolve()
    if not resume:
        directory.mkdir(parents=True,exist_ok=False)
    path = directory/'cycle.json'
    with cycle_lock(directory):
        if resume:
            state, coverage, reports = cycle_evidence(path)
            if any(state[key] != value for key,value in config.items()):
                raise ValueError('Resume requires identical date, window, plan and limits')
            if state['coverage_complete']:
                return state  # Completed evidence needs no new requests, even after expiry.
        else:
            state = dict(config,cycle_id=uuid4().hex,created_at=utcnow().isoformat(),attempts=[],
                budget=dict(requests=0,stopped=False,pending_request=False,last_request_utc=None))
            write_json_atomic(path,state)
        if not aware(config['window_start']) <= utcnow() < aware(config['window_end']):
            raise ValueError('Current time is outside the requested daily observation window')
        budget = CycleBudget(path)
        if resume:
            # A damaged budget flag cannot erase retained access/transport evidence.
            for report in reports:
                require_safe_resume(json.loads(report.read_text(encoding='utf-8')))
        if budget.requests >= budget.max_requests:
            raise ValueError('Cycle request budget exhausted')
        budget.timeout_ms()
        reused = []
        if resume:
            for row in coverage.to_dict('records'):
                if row['coverage_complete']:
                    reused.append(query_outcome(row['query_id'],Path(row['report_path']).parent,recover=True))
                elif row.get('query_complete') == 1:
                    raise ValueError('Completed query is outside the cycle window')
        checkpoint = directory/'resume.json'
        write_json_atomic(checkpoint,dict(queries=queries,outcomes=reused))
        attempt = f"attempt_{len(state['attempts'])+1:04d}"
        state['attempts'].append(attempt)
        write_json_atomic(path,state)
        collect_plan(queries,destination=directory/attempt,full_plan=True,budget=budget,
                     resume_from=checkpoint,post=post)
        state, _, _ = cycle_evidence(path)
        return state


def import_cycle(path, database):
    """Explicit idempotent import of complete and partial attempts into analysis storage."""
    _, _, reports = cycle_evidence(path)
    return import_reports(reports,database)


def read_cycle_history(cycle_paths, database, *, as_of=None):
    """Read selected runs; optional UTC cutoff excludes later attempts and cycles.

    This reanalysis uses the current parser. Reproducing an old published vintage
    also requires its saved code/configuration hashes, not only an observation cutoff.
    """
    days, frames = [], []
    for path in cycle_paths:
        if as_of is not None and aware(json.loads(Path(path).read_text())['created_at']) > aware(as_of):
            continue
        state, coverage, _ = cycle_evidence(path, as_of=as_of)
        ids = coverage.run_id.dropna().tolist() if 'run_id' in coverage else []
        rows = pd.DataFrame()
        imported = not ids
        if Path(database).is_file() and ids:
            runs, captures, rows = read_history(database,run_ids=ids)
            imported = set(runs.run_id)==set(ids)
            expected = coverage.dropna(subset=['run_id']).set_index('run_id')
            invalid = []
            for run in runs.itertuples():
                imported = imported and run.report_sha256 == expected.loc[run.run_id,'report_sha256']
                _, source_captures, source_rows = read_query_evidence(expected.loc[run.run_id,'report_path'])
                try:
                    for actual,source,keys in [(rows[rows.run_id.eq(run.run_id)],source_rows,['capture_id','retailer','listing_id']),
                            (captures[captures.run_id.eq(run.run_id)],source_captures,['capture_id'])]:
                        actual=actual[source.columns].sort_values(keys).reset_index(drop=True)
                        source=source.sort_values(keys).reset_index(drop=True)
                        pd.testing.assert_frame_equal(actual.astype(object).where(actual.notna(),None),
                            source.astype(object).where(source.notna(),None),check_dtype=False,check_exact=True)
                except (AssertionError,KeyError):
                    imported=False
                    invalid.append(run.run_id)
            fresh_ids=coverage.loc[coverage.within_window,'run_id'].dropna()
            rows=rows[~rows.run_id.isin(invalid) & rows.run_id.isin(fresh_ids)]
            if not rows.empty:
                rows = rows.merge(captures[['capture_id','source_path']],on='capture_id',validate='many_to_one')
        if not imported:
            state['coverage_complete'] = False
            state['coverage_reason'] = 'Selected retained reports are missing or differ from imported evidence'
        day = {key:state[key] for key in ('cycle_id','cycle_date','timezone','window_start','window_end',
                                        'scope_id','coverage_complete','coverage_reason','available_at')}
        day.update(target_window_start=state['window_start'],target_window_end=state['window_end'])
        for column,field,operation in [('window_start','observation_start',min),('window_end','observation_end',max)]:
            clocks=coverage.loc[coverage.within_window,field].dropna().tolist() if field in coverage else []
            day[column]=operation(map(aware,clocks)).isoformat() if clocks else state['created_at']
        days.append(day)
        if not rows.empty:
            frames.append(rows.assign(cycle_id=state['cycle_id']))
    day_columns = ['cycle_id','cycle_date','timezone','window_start','window_end','scope_id',
                   'coverage_complete','coverage_reason','available_at','target_window_start','target_window_end']
    return pd.DataFrame(days, columns=day_columns), pd.concat(frames,ignore_index=True) if frames else pd.DataFrame(
        columns=[*OBSERVATION_COLUMNS,'cycle_id','source_path'])
