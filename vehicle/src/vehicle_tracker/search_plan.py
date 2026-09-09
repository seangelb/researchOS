"""Sequential search partitions, one checkpoint, and explicit restart of incomplete queries."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import pandas as pd

from vehicle_tracker.collect import NavigationBudget
from vehicle_tracker.search import collect_search
from vehicle_tracker.storage import read_snapshots, write_json_atomic
from vehicle_tracker.carvana import parse_capture


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def query_outcome(name, folder, *, recover=False):
    """Recover a complete child only when its retained rows match its database."""
    folder = Path(folder)
    report_file, database = folder/'run_report.json', folder/'vehicle.sqlite'
    report = json.loads(report_file.read_text(encoding='utf-8'))
    artifacts = [report_file, database] + [Path(p['retained_source']) for p in report['pages']]
    if recover:
        from vehicle_tracker.history import read_query_evidence
        read_query_evidence(report_file)  # Reconcile claimed completeness to native pages.
        frames = []
        for page in report['pages']:
            source = Path(page['retained_source'])
            if digest(source) != page['source_sha256']:
                raise ValueError('Recovered source hash changed')
            if page['status'] != 'parsed':
                raise ValueError('Recovered complete query contains a failed page')
            frames.append(parse_capture(json.loads(source.read_text(encoding='utf-8'))))
        _, stored = read_snapshots(database)
        if frames and not all(f.empty for f in frames):
            expected = pd.concat(frames, ignore_index=True).sort_values('listing_id').reset_index(drop=True)
            try:
                pd.testing.assert_frame_equal(expected, stored[expected.columns].sort_values('listing_id').reset_index(drop=True), check_dtype=False)
            except (AssertionError, KeyError) as exc:
                raise ValueError('Recovered database differs from retained source') from exc
        if len(stored) != report['complete_query_count']:
            raise ValueError('Recovered complete count differs from stored observations')
    return dict(query_id=name, query_complete=report['query_complete'], status=report['status'],
        reason=report['reason'], report=str(report_file), database=str(database),
        artifact_hashes={str(p): digest(p) for p in artifacts}, resumed=recover)


def validate_plan(queries):
    """The plan is an explicit list of observed make/model/year queries and ZIPs."""
    names = [q['query_id'] for q in queries]
    if not names or len(names) != len(set(names)):
        raise ValueError('Require nonempty, unique query IDs')
    for q in queries:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', q['query_id']) or not re.fullmatch(r'\d{5}', q['zip_code']):
            raise ValueError('Require plain query IDs and five-digit ZIPs')
        if not isinstance(q['filters'], dict) or set(q['filters']) - {'makes', 'year'}:
            raise ValueError('Use only verified make/model/year filters')
        if type(q.get('location_filter', False)) is not bool:
            raise ValueError('location_filter must be an explicit boolean')


def collect_plan(queries, *, destination, target_listings=1000, budget=None, resume_from=None, post=None, full_plan=False):
    """Count a union of identities, never a sum of ZIP/query totals.

    Resume references hash-checked completed queries. Incomplete queries restart at
    page one in a new directory; old pages are not spliced into a later complete run.
    The capture window can span both invocations. This never certifies a daily census.
    """
    validate_plan(queries)
    if type(target_listings) is not int or target_listings < 1:
        raise ValueError('Require a positive integer target')
    if type(full_plan) is not bool:
        raise ValueError('full_plan must be explicit boolean')
    if full_plan:
        target_listings = None
    previous = {}
    if resume_from:
        resume_path = Path(resume_from)
        resume_path = resume_path/'run_report.json' if resume_path.is_dir() else resume_path
        manifest = resume_path.parent/'query_plan.json'
        prior = json.loads((resume_path if resume_path.exists() else manifest).read_text(encoding='utf-8'))
        if prior['queries'] != queries:
            raise ValueError('Resume requires the identical query plan')
        previous = {r['query_id']: r for r in prior.get('outcomes', []) if r.get('query_complete')}
        for query in queries:
            child = resume_path.parent/query['query_id']/'run_report.json'
            if query['query_id'] not in previous and child.is_file():
                report = json.loads(child.read_text(encoding='utf-8'))
                if report['filters'] != query['filters'] or report['zip_code'] != query['zip_code'] or report.get('location_filter', False) != query.get('location_filter', False):
                    raise ValueError('Recovered child query differs from the plan')
                if report['query_complete']:
                    previous[query['query_id']] = query_outcome(query['query_id'], child.parent, recover=True)
        # Validate every reused result before creating files or making requests.
        for entry in previous.values():
            if not {entry['database'], entry['report']} <= set(entry['artifact_hashes']):
                raise ValueError('Resume requires hashes for its report and database')
            for path, expected in entry['artifact_hashes'].items():
                if digest(path) != expected:
                    raise ValueError('Resume artifact changed; retained results require review')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    budget = budget or NavigationBudget()
    start_requests = budget.requests
    identities, vins, outcomes = set(), set(), []
    summary = dict(queries=queries, started_utc=datetime.now(timezone.utc).isoformat(),
                   target_listings=target_listings, resumed_from=str(resume_from) if resume_from else None,
                   national_coverage_verified=False, daily_sales_estimate=None)
    write_json_atomic(destination/'query_plan.json', dict(queries=queries))
    summary.update(outcomes=[], unique_listings=0, unique_vins=0, target_reached=False,
                   requests=0, all_queries_complete=False, observation_started_utc=None, observation_ended_utc=None)
    write_json_atomic(destination/'run_report.json', summary)
    observation_times = []
    for q in queries:
        name = q['query_id']
        if name in previous:
            entry = dict(previous[name], resumed=True)
        elif ((target_listings is not None and len(identities) >= target_listings) or budget.stopped or budget.requests >= budget.max_requests
              or time.monotonic() - budget.started >= budget.max_seconds):
            entry = dict(query_id=name, query_complete=False, status='unattempted',
                         reason='Target reached or shared request/access budget stopped')
        else:
            folder = destination/name
            report = collect_search(filters=q['filters'], zip_code=q['zip_code'], destination=folder,
                target_listings=None if full_plan else target_listings-len(identities), budget=budget, post=post,
                location_filter=q.get('location_filter', False), known_listing_ids=identities)
            entry = query_outcome(name, folder)
        if 'database' in entry:
            captures, rows = read_snapshots(entry['database'])
            observation_times.extend(captures.loc[captures.status.eq('parsed'), 'observed_at_utc'].dropna())
            if not rows.empty:
                identities.update(zip(rows.retailer, rows.listing_id))
                known_vins = rows.dropna(subset=['vin'])
                vins.update(zip(known_vins.retailer, known_vins.vin))
        outcomes.append(entry)
        summary.update(outcomes=outcomes, unique_listings=len(identities), unique_vins=len(vins),
            target_reached=target_listings is not None and len(identities)>=target_listings, requests=budget.requests-start_requests,
            ended_utc=datetime.now(timezone.utc).isoformat(),
            observation_started_utc=min(observation_times) if observation_times else None,
            observation_ended_utc=max(observation_times) if observation_times else None,
            all_queries_complete=len(outcomes)==len(queries) and all(r['query_complete'] for r in outcomes))
        write_json_atomic(destination/'run_report.json', summary)
    return summary
