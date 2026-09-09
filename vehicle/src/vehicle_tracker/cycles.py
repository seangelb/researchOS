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
from vehicle_tracker.search_plan import collect_plan, query_outcome, validate_plan
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
        state = json.loads(self.path.read_text(encoding='utf-8'))
        saved = state['budget']
        if saved['pending_request']:
            raise ValueError('Previous request outcome uncertain; cycle cannot retry automatically')
        if saved['stopped']:
            raise ValueError('Cycle stopped after an access/transport failure')
        self.max_requests = state['max_requests']
        elapsed = (utcnow()-aware(state['created_at'])).total_seconds()
        self.max_seconds = min(state['max_seconds'],(aware(state['window_end'])-aware(state['created_at'])).total_seconds())
        self.started = time.monotonic()-elapsed
        self.requests, self.stopped, self.pause_seconds = saved['requests'], False, 3
        self.last_request = (time.monotonic()-(utcnow()-aware(saved['last_request_utc'])).total_seconds()
                             if saved['last_request_utc'] else None)
        self.last_request_utc = saved['last_request_utc']

    def save(self, pending=False):
        state = json.loads(self.path.read_text(encoding='utf-8'))
        state['budget'] = dict(requests=self.requests, stopped=self.stopped,
                               pending_request=pending, last_request_utc=self.last_request_utc)
        write_json_atomic(self.path,state)

    def before_navigation(self):
        super().before_navigation()
        self.last_request_utc = utcnow().isoformat()
        self.save(pending=True)  # An interrupted/uncertain request remains reserved.

    def response_received(self):
        self.save()

    def stop(self):
        self.stopped = True
        self.save()


def cycle_evidence(path, *, as_of=None):
    """Return cycle metadata, every requested query's coverage, and all attempt reports."""
    path = Path(path).resolve()
    state = json.loads(path.read_text(encoding='utf-8'))
    cutoff = aware(as_of) if as_of is not None else None
    config = cycle_config(state['queries'],cycle_date=state['cycle_date'],timezone_name=state['timezone'],
        window_start=state['window_start'],window_end=state['window_end'],
        max_requests=state['max_requests'],max_seconds=state['max_seconds'])
    if any(state[key] != value for key,value in config.items()):
        raise ValueError('Cycle configuration or scope fingerprint changed')
    selected, reports = {}, []
    for index, attempt in enumerate(state['attempts'], 1):
        if attempt != f'attempt_{index:04d}':
            raise ValueError('Invalid cycle attempt path')
        for query in state['queries']:
            report = path.parent/attempt/query['query_id']/'run_report.json'
            if not report.is_file():
                continue
            native = json.loads(report.read_text(encoding='utf-8'))
            # A later retry must not revise a result available at an earlier cutoff.
            if cutoff and (not native.get('ended_utc') or aware(native['ended_utc']) > cutoff):
                continue
            run, _, _ = read_query_evidence(report)
            if cutoff and run['observation_end'] and aware(run['observation_end']) > cutoff:
                continue
            if (native['filters'] != query['filters'] or native['zip_code'] != query['zip_code']
                    or native.get('location_filter',False) != query.get('location_filter',False)):
                raise ValueError('Query attempt differs from the cycle plan')
            reports.append(report)
            if query['query_id'] not in selected or not selected[query['query_id']]['query_complete']:
                selected[query['query_id']] = dict(run,report_path=str(report))
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
        for key in ('observation_end','invocation_end') if run.get(key)]
    state['available_at'] = max(map(aware,clocks)).isoformat()
    return state, coverage, reports


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
            state, coverage, _ = cycle_evidence(path)
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
