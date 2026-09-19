"""One fixed, separately retained year-grid recovery; positives never repair old runs."""
from contextlib import ExitStack
from datetime import datetime, timedelta
import json
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from vehicle_tracker.catalog import (_capture_roots, _capture_root_state, _require_clear_roots,
    digest, utcnow)
from vehicle_tracker.catalog_partitions import plan_year_partitions
from vehicle_tracker.cycles import CycleBudget, _cycle_state, aware, cycle_config, cycle_lock
from vehicle_tracker.history import OBSERVATION_COLUMNS, import_reports, read_query_evidence
from vehicle_tracker.retained_history import _snapshot_witness
from vehicle_tracker.search import ENDPOINT, build_search_request, collect_search, search_transport
from vehicle_tracker.search_plan import validate_plan, verify_isolated_pagination
from vehicle_tracker.storage import write_json_atomic


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def same(left, right):
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def _bound(path, sha, bindings):
    path = str(Path(path).resolve())
    if bindings.get(path) != sha or digest(path) != sha:
        raise ValueError('Recovery source binding changed: ' + path)
    return load(path)


def settings(path):
    config = load(path)
    if (config['format'] != 'carvana-gap-recovery-v1' or config['endpoint'] != ENDPOINT
            or config['primary_zip'] != '08542' or config['location_filter'] is not False
            or config['sort'] != 'MostPopular' or type(config['page_size']) is not int
            or config['page_size'] != 24 or config['minimum_spacing_seconds'] != 3
            or type(config['max_requests']) is not int or not 1 <= config['max_requests'] <= 6000
            or type(config['max_seconds']) is not int or not 1 <= config['max_seconds'] <= 21600):
        raise ValueError('Recovery requires the frozen public protocol and bounded allowance')
    ZoneInfo(config['timezone'])
    peers = config['related_capture_roots']
    if not isinstance(peers, list) or not peers:
        raise ValueError('Recovery requires explicit shared capture roots')
    for name in [config['capture_root'], config['plan_path'], *peers]:
        if not isinstance(name, str) or not Path(name).is_absolute():
            raise ValueError('Recovery configuration paths must be absolute')
    if digest(config['plan_path']) != config['plan_sha256']:
        raise ValueError('Recovery plan changed')
    plan = load(config['plan_path'])
    if plan['format'] != 'carvana-gap-recovery-plan-v1':
        raise ValueError('Unknown recovery plan')
    bindings = {str(Path(p).resolve()): sha for p, sha in plan['source_hashes'].items()}
    for p, sha in bindings.items():
        if digest(p) != sha:
            raise ValueError('Recovery original source changed')
    catalog = _bound(plan['original_catalog_path'], plan['original_catalog_sha256'], bindings)
    budget = _bound(plan['original_budget_path'], plan['original_budget_sha256'], bindings)
    audit = _bound(plan['original_terminal_audit_path'], plan['original_terminal_audit_sha256'], bindings)
    if (catalog['status'] != 'collection_finished' or catalog['declared_collection_complete'] is not False
            or audit['audit_complete'] is not True or audit['capture'] != catalog['capture_directory']
            or budget['budget']['pending_request'] or budget['budget']['requests'] != catalog['requests']):
        raise ValueError('Original invocation is not an audited terminal partial capture')
    _bound(plan['broad_facet_path'], plan['broad_facet_sha256'], bindings)
    years = plan_year_partitions(plan['broad_facet_path'], expected_sha256=plan['broad_facet_sha256'],
                                  zip_code=config['primary_zip'])
    if years['native_year_range'] != {'min': 2010, 'max': 2027}:
        raise ValueError('Original native year endpoints differ from the authorized recovery')
    failed = [e for e in catalog['entries'] if e.get('outcome_kind') == 'pagination_unstable']
    leaf_ids = {q['query_id'] for q in catalog['leaf_queries']}
    parents, children = plan['parents'], plan['children']
    if len(failed) != 25 or len(parents) != 25 or len(children) != 500:
        raise ValueError('Recovery must preserve the fixed 25-parent / 500-child denominator')
    isolated = {str(Path(x['report']).resolve()): x['report_sha256']
                for x in budget.get('isolated_query_failures', [])}
    expected_children = []
    for parent, entry in zip(parents, failed):
        q = entry['query']
        if (parent['parent_id'] != q['query_id'] or q['query_id'] not in leaf_ids
                or not same(parent['filters'], q['filters']) or 'year' in parent['filters']
                or str(Path(parent['report_path']).resolve()) != str(Path(entry['report']).resolve())
                or parent['report_sha256'] != entry['report_sha256']
                or isolated.get(str(Path(entry['report']).resolve())) != entry['report_sha256']):
            raise ValueError('Recovery parent differs from an original isolated primary gap')
        original = _bound(parent['report_path'], parent['report_sha256'], bindings)
        if (original['query_complete'] is not False or original.get('outcome_kind') != 'pagination_unstable'
                or not same(original['filters'], parent['filters']) or original['zip_code'] != '08542'
                or original.get('location_filter') is not False):
            raise ValueError('Original query context differs from recovery parent')
        for part in years['partitions']:
            expected_children.append(dict(parent_id=parent['parent_id'],
                filters=dict(parent['filters'], year=part['filters']['year']),
                zip_code='08542', location_filter=False))
    validate_plan(children)
    for child, expected in zip(children, expected_children):
        if not same({k: child.get(k) for k in expected}, expected):
            raise ValueError('Recovery children differ from the exact ordered year grid')
    return config, plan


def validate_context(capture, facets, query):
    """Require applied native year/make/model context even for empty tail responses."""
    request = build_search_request(filters=query['filters'], zip_code=query['zip_code'])
    if (not same(capture['request'], request) or not same(facets['request'], request)
            or capture['zip_code'] != query['zip_code'] or facets['zip_code'] != query['zip_code']
            or facets['captured_at_utc'] != capture['captured_at_utc']
            or not same(facets['pagination'], capture['pagination'])):
        raise ValueError('Recovery first-page source context differs')
    bounds, year = query['filters']['year'], facets['facet_data']['year']
    for key, native in [('min', 'appliedMin'), ('max', 'appliedMax')]:
        value = year.get(native)
        if ((key in bounds and (type(value) is not int or value != bounds[key]))
                or (key not in bounds and value is not None)):
            raise ValueError('Recovery native applied year differs, including the open tail')
    requested = query['filters']['makes']
    if len(requested) != 1 or len(requested[0].get('parentModels', [])) > 1:
        raise ValueError('Recovery requires one original make or make/model parent')
    make = requested[0]['name']
    requested_models = [m['name'] for m in requested[0].get('parentModels', [])]
    makes = facets['facet_data']['makes']
    applied = [name for name, bucket in makes.items() if bucket['isApplied'] is True]
    if applied != [make]:
        raise ValueError('Recovery native applied make differs')
    models = [child['key'] for bucket in makes.values() for child in bucket['parentModels']
              if child['isApplied'] is True]
    if models != requested_models or any(not any(c['key'] == model and c['isApplied'] is True
                                                for c in makes[make]['parentModels'])
                                        for model in requested_models):
        raise ValueError('Recovery native applied model differs')


def preview(path, *, now=None):
    config, plan = settings(path)
    now = now or utcnow()
    zone = ZoneInfo(config['timezone'])
    day = now.astimezone(zone).date()
    if day.isoformat() != config['authorized_cycle_date']:
        raise ValueError('Recovery is authorized for one explicit local date only')
    midnight = datetime.combine(day+timedelta(days=1), datetime.min.time(), zone)
    end = min(now+timedelta(seconds=config['max_seconds']), midnight-timedelta(microseconds=1))
    folder = Path(config['capture_root'])/day.isoformat()
    own_prior = [p for p in folder.parent.glob('*') if p.is_dir()]
    states = [_capture_root_state(root) for root in _capture_roots(config)]
    return dict(config=config, config_sha256=digest(path), cycle_date=day.isoformat(),
        window_start=now.isoformat(), window_end=end.isoformat(), destination=str(folder),
        destination_fresh=not folder.exists() and not own_prior, capture_root_states=states,
        capture_preflight_blocked=any(s['blocked'] for s in states), active_locks_checked=False,
        parents=len(plan['parents']), children=len(plan['children']), requests=0, writes=False)


def collect_recovery(config_path, *, expected_sha256, post=None):
    start = preview(config_path)
    if expected_sha256 != start['config_sha256'] or not start['destination_fresh']:
        raise ValueError('Changed config or existing recovery destination; no retry/resume')
    _require_clear_roots(start['capture_root_states'])
    config, plan = settings(config_path)
    folder = Path(start['destination'])
    with ExitStack() as locks:
        for root in _capture_roots(config):
            root.mkdir(parents=True, exist_ok=True)
            locks.enter_context(cycle_lock(root))
        _require_clear_roots([_capture_root_state(root) for root in _capture_roots(config)])
        if any(p.is_dir() for p in folder.parent.glob('*')):
            raise ValueError('Recovery is one-shot; a prior attempt is retained')
        config, plan = settings(config_path)
        if digest(config_path) != expected_sha256:
            raise ValueError('Recovery config changed under lock')
        folder.mkdir(exist_ok=False)
        (folder/'selected_config.json').write_bytes(Path(config_path).read_bytes())
        (folder/'selected_plan.json').write_bytes(Path(config['plan_path']).read_bytes())
        budget_path = folder/'catalog_budget.json'
        state = cycle_config(plan['children'], cycle_date=start['cycle_date'], timezone_name=config['timezone'],
            window_start=start['window_start'], window_end=start['window_end'],
            max_requests=config['max_requests'], max_seconds=config['max_seconds'])
        write_json_atomic(budget_path, dict(state, cycle_id=uuid4().hex, created_at=utcnow().isoformat(),
            budget=dict(requests=0, stopped=False, pending_request=False, last_request_utc=None)))
        budget = CycleBudget(budget_path)
        report = dict(format='carvana-gap-recovery-run-v1', capture_directory=str(folder),
            config_sha256=expected_sha256, plan_sha256=config['plan_sha256'], status='running',
            cycle_date=start['cycle_date'],
            started_at=utcnow().isoformat(), window_start=start['window_start'], window_end=start['window_end'],
            entries=[dict(query=q, status='unattempted', query_complete=False, context_validated=False)
                     for q in plan['children']], requests=0, national_coverage_verified=False, estimated_sales=None,
            missing_or_noninteger_year_count=None,
            code_hashes={str(p): digest(p) for p in Path(__file__).parent.glob('*.py')})
        def save():
            report['requests'] = budget.requests
            write_json_atomic(folder/'catalog_report.json', report)
        known = {}
        try:
            save()
            with search_transport(post) as send:
                for entry in report['entries']:
                    budget.timeout_ms()
                    if budget.stopped or budget.requests >= budget.max_requests:
                        budget.stop()
                        break
                    q = entry['query']
                    entry['status'] = 'attempting'
                    save()
                    before = budget.requests
                    child = folder/q['query_id']
                    result = collect_search(filters=q['filters'], zip_code=q['zip_code'], destination=child,
                        target_listings=None, budget=budget, post=send, known_listing_vins=known,
                        retain_facets=True, first_page_validator=lambda capture, facets: validate_context(capture, facets, q))
                    path = child/'run_report.json'
                    entry.update(status=result['status'], report=str(path), report_sha256=digest(path),
                        query_complete=result['query_complete'], requests=result['requests'],
                        context_validated=result.get('first_page_context_validated', False),
                        outcome_kind=result.get('outcome_kind'))
                    if result.get('outcome_kind') == 'pagination_unstable':
                        verify_isolated_pagination(path, known_listing_vins=known, requests=budget.requests-before)
                        budget.isolate_query_failure(requests=budget.requests, report=path,
                                                     report_sha256=entry['report_sha256'])
                    _, _, rows = read_query_evidence(path, diagnostic=True)
                    known.update(zip(rows.listing_id, rows.vin))
                    save()
                    if budget.stopped:
                        break
                if not budget.stopped:
                    budget.timeout_ms()
            report['status'] = 'stopped' if budget.stopped else 'collection_finished'
        except BaseException as error:
            report.update(status='stopped', failure_type=type(error).__name__)
            budget.stop()
            if not isinstance(error, Exception):
                raise
        finally:
            report['ended_at'] = utcnow().isoformat()
            save()
            if (report['status'] == 'stopped' or load(budget_path)['budget']['pending_request']):
                write_json_atomic(folder.parent/'access_stop.json', dict(run=str(folder/'catalog_report.json'),
                    recorded_at=utcnow().isoformat(), reason='Recovery stopped; preserve evidence and require review'))
        return report


def _query_replay(entry, sources, *, window):
    """Verify retained source, journal, context and original collector SQLite values."""
    path = Path(entry['report'])
    if digest(path) != entry['report_sha256']:
        raise ValueError('Recovery child report changed')
    report, q = load(path), entry['query']
    if (not same(report['filters'], q['filters']) or report['zip_code'] != q['zip_code']
            or report['location_filter'] is not False or report['query_complete'] != entry['query_complete']
            or report['status'] != entry['status'] or report.get('outcome_kind') != entry.get('outcome_kind')
            or report['requests'] != entry['requests']):
        raise ValueError('Recovery child differs from fixed plan or parent state')
    run, captures, rows = read_query_evidence(path, diagnostic=True)
    sources[str(path)] = digest(path)
    starts, responses, reservations = [], [], 0
    if {p.name for p in (path.parent/'attempts').glob('*.json')} != {
            f"{page['page']:04d}.json" for page in report['pages']}:
        raise ValueError('Recovery has orphan or missing page journals')
    for page in report['pages']:
        journal = path.parent/'attempts'/f"{page['page']:04d}.json"
        expected = dict(run_id=report['run_id'], request=build_search_request(filters=q['filters'],
            zip_code=q['zip_code'], page=page['page']), **page)
        if not same(load(journal), expected):
            raise ValueError('Recovery journal differs from its child')
        sources[str(journal)] = digest(journal)
        clocks = [aware(page[key]) for key in ('request_reserved_at_utc','request_started_at_utc',
            'response_received_at_utc','evidence_available_at_utc') if page.get(key) is not None]
        if (clocks != sorted(clocks) or any(not window[0] <= clock <= window[1] for clock in clocks)
                or (page.get('request_started_at_utc') and not page.get('request_reserved_at_utc'))
                or ((page.get('http_status') is not None or page.get('response_evidence'))
                    and not page.get('response_received_at_utc'))
                or (page.get('response_received_at_utc') and not page.get('request_started_at_utc'))
                or (responses and page.get('request_started_at_utc')
                    and aware(page['request_started_at_utc']) < responses[-1])):
            raise ValueError('Recovery page reservation/response clocks are missing or out of order')
        reservations += page.get('request_reserved_at_utc') is not None
        if page.get('request_started_at_utc'):
            starts.append(aware(page['request_started_at_utc']))
        if page.get('response_received_at_utc'):
            responses.append(aware(page['response_received_at_utc']))
        if page.get('database_outcome') == 'unconfirmed':
            raise ValueError('Recovery storage outcome remains unconfirmed')
        for field, sha in [('retained_source', 'source_sha256'), ('facet_source', 'facet_sha256')]:
            if page.get(field):
                if digest(page[field]) != page[sha]:
                    raise ValueError('Recovery source hash changed')
                sources[page[field]] = page[sha]
        evidence = page.get('response_evidence') or {}
        if evidence.get('source_path'):
            sources[evidence['source_path']] = evidence['source_sha256']
    validated = False
    if report['pages'] and report['pages'][0].get('facet_source'):
        first = report['pages'][0]
        try:
            validate_context(load(first['retained_source']), load(first['facet_source']), q)
            validated = True
        except ValueError:
            if first['status'] != 'failed' or first.get('outcome_kind') != 'schema_failure' or not rows.empty:
                raise
    if (entry['context_validated'] is not validated
            or report.get('first_page_context_validated', False) is not validated
            or (not rows.empty and not validated)):
        raise ValueError('Recovery native context validation differs on replay')
    database = path.parent/'vehicle.sqlite'
    if database.exists():
        if any(Path(str(database)+suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
            raise ValueError('Recovery database has unsettled sidecars')
        witness_sources = [dict(capture_id=p['source_sha256'], source_path=str(Path(p['retained_source']).resolve()),
            capture=load(p['retained_source']), status=p['status'], page=p['page'],
            row_count=p['stored_rows'], error=p.get('error')) for p in report['pages'] if p.get('retained_source')]
        _snapshot_witness(database, witness_sources, run_id=report['run_id'])
        sources[str(database)] = digest(database)
    elif not captures.empty:
        raise ValueError('Recovery collector SQLite is missing')
    if reservations != report['requests']:
        raise ValueError('Recovery durable reservations differ from child request count')
    return rows, starts, responses, reservations


def export_recovery(folder, *, output):
    folder, output = Path(folder).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError('Recovery export destination must be fresh')
    config, plan = settings(folder/'selected_config.json')
    report = load(folder/'catalog_report.json')
    code = {str(p):digest(p) for p in Path(__file__).parent.glob('*.py')}
    if (report['status'] not in {'stopped','collection_finished'} or not report.get('ended_at')
            or digest(folder/'selected_config.json') != report['config_sha256']
            or digest(folder/'selected_plan.json') != config['plan_sha256']
            or not same(load(folder/'selected_plan.json'), plan)
            or report['plan_sha256'] != config['plan_sha256']
            or report['code_hashes'] != code
            or not same([e['query'] for e in report['entries']], plan['children'])):
        raise ValueError('Recovery terminal configuration or full child denominator differs')
    state = _cycle_state(folder/'catalog_budget.json')
    budget = state['budget']
    if (not same(state['queries'], plan['children']) or budget['pending_request']
            or budget['requests'] != report['requests']
            or state['max_requests'] != config['max_requests'] or state['max_seconds'] != config['max_seconds']
            or state['timezone'] != config['timezone'] or state['cycle_date'] != report['cycle_date']
            or state['cycle_date'] != config['authorized_cycle_date']
            or (aware(state['window_end'])-aware(state['window_start'])).total_seconds() > config['max_seconds']
            or (report['status'] == 'collection_finished' and budget['stopped'])
            or state['window_start'] != report['window_start'] or state['window_end'] != report['window_end']):
        raise ValueError('Recovery durable ledger is uncertain or differs')
    expected_dirs = {e['query']['query_id'] for e in report['entries'] if e['status'] != 'unattempted'}
    if {p.name for p in folder.iterdir() if p.is_dir()} - expected_dirs - {'analysis'}:
        raise ValueError('Recovery has undeclared child directories')
    sources = dict(plan['source_hashes'])
    for name in ['catalog_report.json','catalog_budget.json','selected_config.json','selected_plan.json']:
        sources[str(folder/name)] = digest(folder/name)
    frames, ledger, reports, starts, responses = [], [], [], [], []
    known, charged, unattempted_seen, fatal_seen = {}, 0, False, False
    isolated = []
    for entry in report['entries']:
        q = entry['query']
        row = dict(query_id=q['query_id'], parent_id=q['parent_id'], status=entry['status'],
            context_validated=False, child_complete=False, observed_rows=0,
            year_min=q['filters']['year'].get('min'), year_max=q['filters']['year'].get('max'))
        if entry['status'] == 'unattempted':
            if entry.get('report') or (folder/q['query_id']).exists():
                raise ValueError('Unattempted recovery child has undeclared evidence')
            unattempted_seen = True
        else:
            if fatal_seen or unattempted_seen or Path(entry['report']).resolve() != folder/q['query_id']/'run_report.json':
                raise ValueError('Recovery child order/path differs from fixed plan')
            rows, query_starts, query_responses, requests = _query_replay(entry, sources,
                window=(aware(report['window_start']), aware(report['window_end'])))
            if responses and query_starts and query_starts[0] < responses[-1]:
                raise ValueError('Recovery next query starts before prior response')
            starts.extend(query_starts); responses.extend(query_responses); charged += requests
            if entry.get('outcome_kind') == 'pagination_unstable':
                verify_isolated_pagination(entry['report'], known_listing_vins=known, requests=requests)
                isolated.append((entry['report'], entry['report_sha256'], charged))
            elif entry.get('outcome_kind') not in (None, 'success'):
                fatal_seen = True
            known.update(zip(rows.listing_id, rows.vin))
            row.update(context_validated=entry['context_validated'],
                child_complete=entry['query_complete'] and entry['context_validated'], observed_rows=len(rows),
                report_path=entry['report'], report_sha256=entry['report_sha256'])
            frames.append(rows.assign(query_id=q['query_id'], parent_id=q['parent_id']))
            if all(p.get('retained_source') for p in load(entry['report'])['pages']):
                reports.append(entry['report'])
        ledger.append(row)
    retained_isolation = [(x['report'], x['report_sha256'], x['requests'])
                          for x in state.get('isolated_query_failures', [])]
    if charged != budget['requests'] or isolated != retained_isolation:
        raise ValueError('Recovery reservations or isolated failure ledger differ')
    if (len(starts) != charged or (starts and aware(budget['last_request_utc']) != starts[-1])
            or (fatal_seen and (report['status'] != 'stopped' or not budget['stopped']))
            or aware(report['ended_at']) < max([aware(report['started_at']), *starts, *responses])):
        raise ValueError('Recovery terminal state or durable actual-start clock differs')
    if (any((b-a).total_seconds() < 3 for a,b in zip(starts, starts[1:]))
            or any(not aware(report['window_start']) <= t <= aware(report['window_end']) for t in starts+responses)):
        raise ValueError('Recovery request spacing or observation window differs')
    inventory = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=[*OBSERVATION_COLUMNS,'query_id','parent_id'])
    conflict = (inventory.groupby('listing_id').vin.nunique().gt(1).any()
                or inventory.groupby('vin').listing_id.nunique().gt(1).any())
    if conflict:
        raise ValueError('Recovery cross-query identity conflict')
    coverage = pd.DataFrame(ledger)
    parents = []
    for parent in plan['parents']:
        children = coverage[coverage.parent_id.eq(parent['parent_id'])]
        rows = inventory[inventory.parent_id.eq(parent['parent_id'])]
        duplicates = int(rows.duplicated(['retailer','vin']).sum())
        parents.append(dict(parent_id=parent['parent_id'], original_report=parent['report_path'],
            original_report_sha256=parent['report_sha256'], child_queries=len(children),
            complete_children=int(children.child_complete.sum()),
            recovery_contexts_complete=bool(len(children)==20 and children.child_complete.all() and not duplicates),
            observed_rows=len(rows), distinct_vins=rows.vin.nunique(), duplicate_memberships=duplicates,
            missing_or_noninteger_year_count=None, original_parent_reclassified=False))
    # Verify immutable inputs again before creating any derived output.
    if any(digest(p) != sha for p,sha in sources.items()):
        raise ValueError('Recovery source changed during export')
    output.mkdir(parents=True, exist_ok=False)
    if reports:
        import_reports(reports, output/'history.sqlite')
    coverage.to_csv(output/'child_coverage.csv', index=False)
    pd.DataFrame(parents).to_csv(output/'parent_coverage.csv', index=False)
    inventory.to_csv(output/'observations.csv', index=False)
    summary = dict(requests=charged, parents=len(parents), children=len(coverage),
        observed_rows=len(inventory), distinct_vins=inventory.vin.nunique(),
        duplicate_memberships=int(inventory.duplicated(['retailer','vin']).sum()), identity_conflicts=0,
        recovery_contexts_complete=(report['status'] == 'collection_finished' and not budget['stopped']
            and all(p['recovery_contexts_complete'] for p in parents)
            and not inventory.duplicated(['retailer','vin']).any()),
        missing_or_noninteger_year_count=None, national_coverage_verified=False, estimated_sales=None)
    write_json_atomic(output/'summary.json', summary)
    write_json_atomic(output/'manifest.json', dict(created_at=utcnow().isoformat(), sources=sources,
        collection_code_hashes=report['code_hashes'],
        code_hashes=code,
        outputs={p.name:digest(p) for p in output.iterdir() if p.is_file()},
        interpretation='Separate recovery positives; original gaps unchanged. Integer-year contexts do not prove unknown-year or national coverage. No sales inference.'))
    return output
