"""Sequential search partitions, one checkpoint, and explicit restart of incomplete queries."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import pandas as pd

from vehicle_tracker.collect import NavigationBudget
from vehicle_tracker.search import ENDPOINT, collect_search, search_transport
from vehicle_tracker.storage import read_snapshots, write_json_atomic
from vehicle_tracker.carvana import NATIVE_FIELDS


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def query_outcome(name, folder, *, recover=False):
    """Recover a complete child only when its retained rows match its database."""
    folder = Path(folder)
    report_file, database = folder/'run_report.json', folder/'vehicle.sqlite'
    report = json.loads(report_file.read_text(encoding='utf-8'))
    artifacts = [report_file] + ([database] if database.is_file() else [])
    for page in report['pages']:
        if page.get('retained_source'):
            artifacts.append(Path(page['retained_source']))
        if page.get('response_evidence', {}).get('source_path'):
            artifacts.append(Path(page['response_evidence']['source_path']))
    artifacts.extend(sorted((folder/'attempts').glob('*.json')))
    if recover:
        from vehicle_tracker.history import read_query_evidence
        run, sources, observations = read_query_evidence(report_file)
        if not sources.status.eq('parsed').all():
            raise ValueError('Recovered complete query contains a failed page')
        captured, stored = read_snapshots(database)
        if (len(captured) != len(report['pages']) or set(captured.run_id) != {report['run_id']}
                or captured.page_number.tolist() != list(range(1, len(captured)+1))):
            raise ValueError('Recovered capture records differ from the query report')
        for page, saved, source in zip(report['pages'], captured.to_dict('records'), sources.to_dict('records')):
            if (saved['source_sha256'] != page['source_sha256'] or saved['status'] != 'parsed'
                    or saved['row_count'] != page['stored_rows'] or saved['observed_at_utc'] != source['observed_at_utc']
                    or json.loads(run['context_json'])['sort'] != 'MostPopular'):
                raise ValueError('Recovered capture context/identities require review')
        if not observations.empty:
            expected = observations.drop(columns=[*NATIVE_FIELDS, 'capture_id', 'run_id']).sort_values('listing_id').reset_index(drop=True)
            try:
                pd.testing.assert_frame_equal(expected, stored[expected.columns].sort_values('listing_id').reset_index(drop=True), check_dtype=False, check_exact=True)
            except (AssertionError, KeyError) as exc:
                raise ValueError('Recovered database differs from retained source') from exc
        if len(stored) != report['complete_query_count']:
            raise ValueError('Recovered complete count differs from stored observations')
    entry = dict(query_id=name, query_complete=report['query_complete'], status=report['status'],
        reason=report['reason'], report=str(report_file), outcome_kind=report.get('outcome_kind'),
        artifact_hashes={str(p): digest(p) for p in artifacts}, resumed=recover)
    if database.is_file():
        entry['database'] = str(database)
    return entry


def require_safe_resume(report):
    """A new budget must not turn a previous access block into an automatic retry."""
    for page in report.get('pages', []):
        status = page.get('http_status')
        uncertain = page.get('request_started_at_utc') and not page.get('response_received_at_utc')
        old_failure = any(word in page.get('error', '') for word in
                          ('TimeoutError', 'ConnectionError', 'unexpected_content', 'cloudflare', 'http_access', 'rate_limited'))
        if (page.get('outcome_kind') in {'access_failure', 'transport_failure', 'request_reserved'}
                or uncertain or (status is not None and status != 200) or old_failure):
            raise ValueError('Prior access/transport outcome blocks resume; no automatic retry')


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


def verify_isolated_pagination(report_file, *, known_listing_vins, requests):
    """Prove a completed, stored pagination failure has no hidden identity conflict.

    Failed page rows remain excluded. Replaying them here only checks that it is
    safe to collect another independent query; this never repairs completeness.
    """
    from vehicle_tracker.history import read_query_evidence
    from vehicle_tracker.search import project_response, build_search_request
    from vehicle_tracker.search_evidence import verify_response_evidence
    from vehicle_tracker.carvana import parse_capture
    report = json.loads(Path(report_file).read_text(encoding='utf-8'))
    pages = report['pages']
    if (report.get('outcome_kind') != 'pagination_unstable' or report['query_complete']
            or report['requests'] != requests or not pages or len(pages) != requests
            or pages[-1].get('outcome_kind') != 'pagination_unstable'
            or pages[-1]['status'] != 'failed'
            or any(p['status'] != 'parsed' for p in pages[:-1])):
        raise ValueError('Only a completed pagination-only query may be isolated')
    _, source_captures, source_rows = read_query_evidence(report_file)
    captured, stored = read_snapshots(Path(report_file).parent/'vehicle.sqlite')
    actual = captured.rename(columns={'page_number':'page', 'source_sha256':'capture_id'})
    columns = ['run_id','page','capture_id','status','row_count','observed_at_utc','error']
    pd.testing.assert_frame_equal(actual[columns].sort_values('page').reset_index(drop=True),
        source_captures[columns].sort_values('page').reset_index(drop=True), check_dtype=False, check_exact=True)
    if not source_rows.empty:
        expected = source_rows.drop(columns=[*NATIVE_FIELDS,'capture_id','run_id'])
        pd.testing.assert_frame_equal(expected.sort_values('listing_id').reset_index(drop=True),
            stored[expected.columns].sort_values('listing_id').reset_index(drop=True), check_dtype=False, check_exact=True)
    elif not stored.empty:
        raise ValueError('Unexpected stored observations in failed query')
    listing_vins = dict(known_listing_vins)
    vin_listings = {vin: listing for listing,vin in listing_vins.items()}
    for page in pages:
        if (page.get('http_status') != 200 or page.get('database_outcome') == 'unconfirmed'
                or not page.get('request_started_at_utc') or not page.get('response_received_at_utc')):
            raise ValueError('Uncertain response/storage outcome cannot be isolated')
        journal = json.loads((Path(report_file).parent/'attempts'/f"{page['page']:04d}.json").read_text(encoding='utf-8'))
        if any(journal.get(key) != value for key,value in page.items()):
            raise ValueError('Page journal differs from final report')
        request = build_search_request(filters=report['filters'],zip_code=report['zip_code'],
            page=page['page'],location_filter=report.get('location_filter',False))
        if journal['request'] != request:
            raise ValueError('Failed request context differs from planned query')
        source = verify_response_evidence(page['response_evidence'])
        projection = project_response(source,request,observed_at=page['response_received_at_utc'])
        frame = parse_capture(projection)  # Reject missing/invalid VINs and malformed rows.
        for row in frame.itertuples():
            if (row.listing_id in listing_vins and listing_vins[row.listing_id] != row.vin
                    or row.vin in vin_listings and vin_listings[row.vin] != row.listing_id):
                raise ValueError('Conflicting VIN/listing identity cannot be isolated')
            listing_vins[row.listing_id] = row.vin
            vin_listings[row.vin] = row.listing_id


def collect_plan(queries, *, destination, target_listings=1000, budget=None, resume_from=None, post=None,
                 full_plan=False, target_vins=None, isolate_pagination=False):
    """Count a union of identities, never a sum of ZIP/query totals.

    Resume references hash-checked completed queries. Incomplete queries restart at
    page one in a new directory; old pages are not spliced into a later complete run.
    The capture window can span both invocations. This never certifies a daily census.
    """
    validate_plan(queries)
    if type(isolate_pagination) is not bool or (isolate_pagination and (resume_from or not full_plan)):
        raise ValueError('Pagination isolation requires an explicit boolean and a fresh full plan')
    if type(target_listings) is not int or target_listings < 1:
        raise ValueError('Require a positive integer target')
    if type(full_plan) is not bool:
        raise ValueError('full_plan must be explicit boolean')
    if target_vins is not None:
        if type(target_vins) is not int or target_vins < 1 or full_plan or resume_from:
            raise ValueError('Distinct VIN trials require a positive target, one fresh destination and no resume/full-plan')
    if full_plan or target_vins is not None:
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
        for outcome in prior.get('outcomes', []):
            if outcome.get('report'):
                require_safe_resume(json.loads(Path(outcome['report']).read_text(encoding='utf-8')))
        for query in queries:
            child = resume_path.parent/query['query_id']/'run_report.json'
            if query['query_id'] not in previous and child.is_file():
                report = json.loads(child.read_text(encoding='utf-8'))
                require_safe_resume(report)
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
        for query in queries:
            if query['query_id'] in previous:
                entry = previous[query['query_id']]
                report = json.loads(Path(entry['report']).read_text(encoding='utf-8'))
                if (not report['query_complete'] or report['endpoint'] != ENDPOINT
                        or any(report.get(k, False) != query.get(k, False)
                               for k in ('filters', 'zip_code', 'location_filter'))):
                    raise ValueError('Reused complete query differs from requested settings')
                previous[query['query_id']] = query_outcome(query['query_id'], Path(entry['report']).parent, recover=True)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    budget = budget or NavigationBudget()
    start_requests = budget.requests
    def request_count():
        # A probe already charged to this VIN trial remains in its overall total.
        return budget.requests if target_vins is not None else budget.requests-start_requests
    identities, vins, outcomes = set(), set(), []
    listing_vins = {}
    checkpoint_targets = sorted({n for n in (1000, 5000, target_vins) if n is not None and n <= target_vins}) if target_vins else []
    summary = dict(queries=queries, started_utc=datetime.now(timezone.utc).isoformat(),
                   target_listings=target_listings, target_vins=target_vins,
                   target_definition='distinct VIN union' if target_vins else 'distinct listing union',
                   max_requests=budget.max_requests, max_seconds=budget.max_seconds,
                   requests_before_collection=start_requests,
                   pause_seconds=budget.pause_seconds, resumed_from=str(resume_from) if resume_from else None,
                   national_coverage_verified=False, daily_sales_estimate=None,
                   freshness_note='Generic plan/resume is not a fresh daily cycle; use the explicit windowed cycle runner.')
    summary['isolate_pagination'] = isolate_pagination
    write_json_atomic(destination/'query_plan.json', dict(queries=queries))
    summary.update(outcomes=[], unique_listings=0, unique_vins=0, target_reached=False,
                   requests=request_count(), all_queries_complete=False, observation_started_utc=None, observation_ended_utc=None,
                   elapsed_seconds=time.monotonic()-budget.started, checkpoints=[], duplicate_memberships=0,
                   identity_conflicts=[])
    write_json_atomic(destination/'run_report.json', summary)
    observation_times = []

    def record_page(query_id, report, rows):
        """Publish progress from durable parsed pages, within the original budget."""
        if not rows.empty:
            summary['duplicate_memberships'] += sum(str(listing) in listing_vins for listing in rows.listing_id)
            listing_vins.update(zip(rows.listing_id, rows.vin))
            identities.update(zip(rows.retailer, rows.listing_id))
            vins.update(zip(rows.retailer, rows.vin))
        summary.update(unique_listings=len(identities), unique_vins=len(vins),
                       target_reached=len(vins) >= target_vins, requests=request_count(),
                       elapsed_seconds=time.monotonic()-budget.started,
                       ended_utc=datetime.now(timezone.utc).isoformat(),
                       active_query=dict(query_id=query_id, report=str(destination/query_id/'run_report.json'),
                                         page=report['pages'][-1]['page'], query_complete=report['query_complete']))
        crossed = [n for n in checkpoint_targets if len(vins) >= n and
                   n not in {item['target_vins'] for item in summary['checkpoints']}]
        for threshold in crossed:
            summary['checkpoints'].append(dict(target_vins=threshold, unique_vins=len(vins),
                unique_listings=len(identities), requests=summary['requests'],
                elapsed_seconds=summary['elapsed_seconds'], observed_at=summary['ended_utc'],
                query_id=query_id, page=report['pages'][-1]['page']))
        write_json_atomic(destination/'run_report.json', summary)
        if crossed:
            print(f"Inventory progress: {len(vins):,} distinct VINs; {summary['requests']} attempted requests; "
                  f"{summary['elapsed_seconds']/60:.2f} minutes; {query_id} page {report['pages'][-1]['page']}", flush=True)

    with search_transport(post) as send:
        for q in queries:
            name = q['query_id']
            if name in previous:
                entry = dict(previous[name], resumed=True)
            elif ((target_listings is not None and len(identities) >= target_listings)
                  or (target_vins is not None and len(vins) >= target_vins)
                  or budget.stopped or budget.requests >= budget.max_requests
                  or time.monotonic() - budget.started >= budget.max_seconds):
                entry = dict(query_id=name, query_complete=False, status='unattempted',
                             outcome_kind='unattempted', reason='Target reached or shared request/access budget stopped')
            else:
                folder = destination/name
                before_requests = budget.requests
                report = collect_search(filters=q['filters'], zip_code=q['zip_code'], destination=folder,
                    target_listings=None if target_listings is None else target_listings-len(identities), budget=budget, post=send,
                    location_filter=q.get('location_filter', False), known_listing_ids=identities,
                    target_vins=None if target_vins is None else target_vins-len(vins),
                    known_listing_vins=listing_vins,
                    page_progress=(lambda report, rows: record_page(name, report, rows)) if target_vins else None)
                summary['identity_conflicts'].extend(conflict for page in report['pages']
                    for conflict in page.get('identity_conflicts', []))
                entry = query_outcome(name, folder)
                if isolate_pagination and report.get('outcome_kind') == 'pagination_unstable':
                    verify_isolated_pagination(folder/'run_report.json', known_listing_vins=listing_vins,
                        requests=budget.requests-before_requests)
                    budget.isolate_query_failure(requests=budget.requests, report=folder/'run_report.json',
                        report_sha256=entry['artifact_hashes'][str(folder/'run_report.json')])
                    entry['failure_scope'] = 'query; pagination evidence reconciled'
            if 'database' in entry:
                captures, rows = read_snapshots(entry['database'])
                admitted = json.loads(Path(entry['report']).read_text(encoding='utf-8'))
                admitted_pages = {p['page'] for p in admitted['pages'] if p['status'] == 'parsed'}
                captures = captures[captures.page_number.isin(admitted_pages)]
                if not rows.empty:
                    rows = rows[rows.page_number.isin(admitted_pages)]
                observation_times.extend(captures.loc[captures.status.eq('parsed'), 'observed_at_utc'].dropna())
                if not rows.empty:
                    identities.update(zip(rows.retailer, rows.listing_id))
                    known_vins = rows.dropna(subset=['vin'])
                    vins.update(zip(known_vins.retailer, known_vins.vin))
                    listing_vins.update(zip(known_vins.listing_id, known_vins.vin))
            outcomes.append(entry)
            summary.update(outcomes=outcomes, unique_listings=len(identities), unique_vins=len(vins),
                target_reached=(len(vins)>=target_vins if target_vins is not None else
                                target_listings is not None and len(identities)>=target_listings), requests=request_count(),
                elapsed_seconds=time.monotonic()-budget.started, stopped=budget.stopped,
                ended_utc=datetime.now(timezone.utc).isoformat(),
                observation_started_utc=min(observation_times) if observation_times else None,
                observation_ended_utc=max(observation_times) if observation_times else None,
                all_queries_complete=len(outcomes)==len(queries) and all(r['query_complete'] for r in outcomes))
            write_json_atomic(destination/'run_report.json', summary)
    summary.pop('active_query', None)
    summary['stop_reason'] = (next((entry.get('reason') for entry in outcomes
                                  if entry.get('status') == 'blocked' and not entry.get('failure_scope')), None)
        or ('target_reached' if summary['target_reached'] else
            'all_queries_complete' if summary['all_queries_complete'] else
            'queries_finished_with_incomplete_coverage' if all(e.get('status') != 'unattempted' for e in outcomes)
            else 'request_or_time_budget_exhausted'))
    write_json_atomic(destination/'run_report.json', summary)
    return summary
