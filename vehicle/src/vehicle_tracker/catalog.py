"""Bounded full-catalog discovery; ordinary search evidence, no sales inference.

Discover all current makes, then non-overlapping model families where the native
counts support them. Never constrain the population to a fixed year range. Small
make probes are already complete queries and are reused without another request.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from vehicle_tracker.cycles import CycleBudget, aware, cycle_config, cycle_lock
from vehicle_tracker.carvana import NATIVE_FIELDS
from vehicle_tracker.history import OBSERVATION_COLUMNS, import_reports, read_query_evidence
from vehicle_tracker.search import ENDPOINT, collect_search, search_transport
from vehicle_tracker.search_plan import verify_isolated_pagination
from vehicle_tracker.storage import read_snapshots, write_json_atomic


def utcnow():
    return datetime.now(timezone.utc)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def query(name, zip_code, filters):
    return dict(query_id=name, zip_code=zip_code, filters=filters, location_filter=False)


def settings(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding='utf-8'))
    if (config['format'] != 'carvana-full-inventory-v1'
            or config['endpoint'] != ENDPOINT or config['location_filter'] is not False
            or config['page_size'] != 24 or config['sort'] != 'MostPopular'
            or config['minimum_spacing_seconds'] != 3
            or type(config['max_requests']) is not int or not 1 <= config['max_requests'] <= 6000
            or type(config['max_seconds']) is not int or not 1 <= config['max_seconds'] <= 21600):
        raise ValueError('Require the public protocol, <=6000 requests, <=21600 seconds and 3-second spacing')
    zips = [config['primary_zip'], *config['validation_zips']]
    if (len(zips) != len(set(zips)) or not 1 <= len(zips) <= 4
            or any(not isinstance(z, str) or not re.fullmatch(r'\d{5}', z) for z in zips)):
        raise ValueError('Require distinct five-digit primary and validation ZIPs')
    for field, upper in [('validation_queries_per_zip', 8), ('validation_max_native_count', 240)]:
        if type(config[field]) is not int or not 1 <= config[field] <= upper:
            raise ValueError('Invalid bounded geographic validation setting')
    ZoneInfo(config['timezone'])
    config['capture_root'] = str((path.parent.parent/config['capture_root']).resolve())
    return config


def preview(path, *, now=None):
    config = settings(path)
    now = now or utcnow()
    zone = ZoneInfo(config['timezone'])
    local = now.astimezone(zone)
    midnight = datetime.combine(local.date()+timedelta(days=1), datetime.min.time(), zone)
    end = min(now+timedelta(seconds=config['max_seconds']), midnight-timedelta(microseconds=1))
    day = local.date().isoformat()
    root = Path(config['capture_root'])
    return dict(config=config, config_sha256=digest(path), cycle_date=day,
        window_start=now.isoformat(), window_end=end.isoformat(), destination=str(root/day),
        destination_fresh=not (root/day).exists(), access_stopped=(root/'access_stop.json').exists(),
        request_ceiling=config['max_requests'], effective_seconds=(end-now).total_seconds(),
        discovery='Fresh broad make counts, then current make/model partitions; all years',
        national_coverage_verified=False, writes=False, requests=0)


def native_makes(capture):
    """Fail closed on an unaccounted broad category; never silently drop it."""
    buckets = capture['facet_data']['makes']
    if capture['request']['filters'] or not buckets:
        raise ValueError('Require nonempty unfiltered make facets')
    for name, bucket in buckets.items():
        if (name != bucket['key'] or bucket['isApplied'] is not False
                or type(bucket['count']) is not int or bucket['count'] < 0):
            raise ValueError('Invalid broad native make count/application')
    if sum(b['count'] for b in buckets.values()) != capture['pagination']['totalMatchedInventory']:
        raise ValueError('Broad make counts leave an unresolved inventory residual')
    return sorted(buckets)


def model_partitions(capture, make, zip_code, prefix):
    """Split only when native model counts and IDs form a partition; else whole make.

    An all-year whole-make fallback preserves unknown models and year boundaries.
    Its pagination may fail; that failure stays visible, never repaired by deduping.
    """
    whole = [query(prefix+'_all', zip_code, {'makes': [{'name': make}]})]
    buckets = capture['facet_data']['makes']
    total = capture['pagination']['totalMatchedInventory']
    bucket = buckets.get(make)
    if any(b['isApplied'] for name, b in buckets.items() if name != make):
        raise ValueError('Unexpected applied make in discovery')
    if bucket is None:
        if total:
            raise ValueError('Requested make missing from a positive response')
        return whole, 'empty make'
    if bucket['isApplied'] is not True or bucket['count'] != total:
        raise ValueError('Requested make count/application differs from response')
    children = bucket['parentModels']
    ids = [value for child in children for value in child['modelIds']]
    valid = (bool(children) and all(type(c['count']) is int and c['count'] >= 0
             and c['isApplied'] is False and c['modelIds'] for c in children)
             and all(type(value) is int and value >= 0 for value in ids)
             and len(ids) == len(set(ids))
             and sum(c['count'] for c in children) == total)
    if not valid:
        return whole, 'model counts/IDs do not partition make; collect whole make'
    return [query(prefix+f'_model_{i:03d}', zip_code,
        {'makes': [{'name': make, 'parentModels': [{'name': child['key']}]}]})
        for i, child in enumerate(sorted(children, key=lambda c: c['key']))], 'native model partition'


def _facets(report):
    page = report['pages'][0]
    if digest(page['facet_source']) != page['facet_sha256']:
        raise ValueError('Retained discovery facet changed')
    capture = json.loads(Path(page['facet_source']).read_text(encoding='utf-8'))
    from vehicle_tracker.search import build_search_request
    expected = build_search_request(filters=report['filters'], zip_code=report['zip_code'])
    if (capture['request'] != expected or capture['zip_code'] != report['zip_code']
            or capture['captured_at_utc'] != page['response_received_at_utc']
            or capture['pagination']['totalMatchedInventory'] != report['reported_total']):
        raise ValueError('Facet request, clock or count differs from inventory response')
    return capture


def _rows(folder):
    path = Path(folder)/'vehicle.sqlite'
    return read_snapshots(path)[1] if path.is_file() else pd.DataFrame()


def collect_catalog(config_path, *, expected_sha256, post=None):
    """One fresh local-date invocation, one shared durable budget, no retry/resume."""
    start = preview(config_path)
    if expected_sha256 != start['config_sha256']:
        raise ValueError('Full-inventory config changed after preview')
    if not start['destination_fresh'] or start['access_stopped']:
        raise ValueError('Existing date or unresolved access stop; no replacement attempt')
    config, folder = start['config'], Path(start['destination'])
    root = folder.parent
    root.mkdir(parents=True, exist_ok=True)
    with cycle_lock(root):
        # Recheck inside the shared root lock, including races with another date.
        if folder.exists() or (root/'access_stop.json').exists():
            raise ValueError('Existing date or unresolved access stop')
        for prior in root.glob('*/catalog_budget.json'):
            prior_report = prior.parent/'catalog_report.json'
            if (not prior_report.is_file() or json.loads(prior_report.read_text()).get('status') == 'running'
                    or json.loads(prior.read_text())['budget']['pending_request']):
                raise ValueError('Unresolved prior catalog invocation; inspect original evidence')
        folder.mkdir()
        selected = Path(config_path).read_bytes()
        if hashlib.sha256(selected).hexdigest() != expected_sha256:
            raise ValueError('Config changed before collection')
        (folder/'selected_config.json').write_bytes(selected)
        broad = query('broad_open', config['primary_zip'], {})
        budget_path = folder/'catalog_budget.json'
        state = cycle_config([broad], cycle_date=start['cycle_date'], timezone_name=config['timezone'],
            window_start=start['window_start'], window_end=start['window_end'],
            max_requests=config['max_requests'], max_seconds=config['max_seconds'])
        write_json_atomic(budget_path, dict(state, cycle_id=uuid4().hex, created_at=utcnow().isoformat(),
            budget=dict(requests=0, stopped=False, pending_request=False, last_request_utc=None)))
        report = dict(format='carvana-full-inventory-run-v1', started_at=utcnow().isoformat(),
            capture_directory=str(folder),
            config_sha256=expected_sha256, cycle_date=start['cycle_date'], status='running',
            entries=[], leaf_queries=[], makes=[], discovery_complete=False,
            primary_queries_complete=False, national_coverage_verified=False,
            estimated_sales=None, requests=0,
            window_start=start['window_start'], window_end=start['window_end'],
            code_hashes={str(p): digest(p) for p in sorted(Path(__file__).parent.glob('*.py'))})
        report_path = folder/'catalog_report.json'
        budget = CycleBudget(budget_path)
        known, primary = {}, {}

        def save():
            report['requests'] = budget.requests
            report['updated_at'] = utcnow().isoformat()
            write_json_atomic(report_path, report)

        def run(q, role, *, probe=False):
            entry = dict(query=q, role=role, status='unattempted')
            report['entries'].append(entry)
            save()  # Publish intent before any request.
            if budget.stopped or budget.requests >= budget.max_requests:
                return None, pd.DataFrame()
            budget.timeout_ms()
            before = budget.requests
            child = folder/q['query_id']
            result = collect_search(filters=q['filters'], zip_code=q['zip_code'], destination=child,
                target_listings=1 if probe else None, budget=budget, post=send,
                known_listing_vins=known, retain_facets=probe)
            entry.update(status=result['status'], report=str(child/'run_report.json'),
                report_sha256=digest(child/'run_report.json'), query_complete=result['query_complete'],
                reported_total=result['reported_total'], requests=result['requests'],
                outcome_kind=result.get('outcome_kind'))
            if result.get('outcome_kind') == 'pagination_unstable':
                verify_isolated_pagination(child/'run_report.json', known_listing_vins=known,
                    requests=budget.requests-before)
                budget.isolate_query_failure(requests=budget.requests, report=child/'run_report.json',
                    report_sha256=entry['report_sha256'])
                entry['failure_scope'] = 'query; reconciled pagination failure'
            rows = _rows(child)
            if not rows.empty:
                known.update(zip(rows.listing_id, rows.vin))
            save()
            if not budget.stopped:
                budget.timeout_ms()  # Even the final response must arrive inside the window.
            return result, rows

        save()
        with search_transport(post) as send:
            try:
                opening, _ = run(broad, 'discovery', probe=True)
                if not opening or budget.stopped:
                    raise ValueError('Broad discovery failed')
                report['makes'] = native_makes(_facets(opening))
                report['opening_total'] = opening['reported_total']
                report['inventory_payload_page_floor'] = (opening['reported_total']+23)//24
                # Publish all make probes, so a failed discovery keeps its denominator.
                report['planned_make_probes'] = [query(f'make_{i:03d}', config['primary_zip'],
                    {'makes': [{'name': make}]}) for i, make in enumerate(report['makes'])]
                save()
                for make, q in zip(report['makes'], report['planned_make_probes']):
                    probe, rows = run(q, 'make_discovery', probe=True)
                    if not probe or budget.stopped:
                        break
                    # Validate facet application even when the first page is complete.
                    children, reason = model_partitions(_facets(probe), make, q['zip_code'], q['query_id'])
                    if probe['query_complete']:
                        # Reuse <=24 rows (including empty makes): zero extra requests.
                        children, reason = [q], 'complete make probe reused'
                        primary[q['query_id']] = rows
                    report['leaf_queries'].extend(children)
                    report['entries'][-1]['partition_reason'] = reason
                    save()
                    for child in children:
                        if child == q:
                            continue
                        result, rows = run(child, 'primary_inventory')
                        if result:
                            primary[child['query_id']] = rows
                    if budget.stopped:
                        break
                attempted_makes = [e for e in report['entries'] if e['role'] == 'make_discovery'
                                   and e.get('query_complete') is not None and e['status'] != 'blocked']
                report['discovery_complete'] = len(attempted_makes) == len(report['makes'])
                save()
                # Geographic checks are diagnostic; never pooled into the primary denominator.
                by_id = {e['query']['query_id']: e for e in report['entries']}
                eligible = [q for q in report['leaf_queries'] if by_id.get(q['query_id'], {}).get('query_complete')
                    and 0 < by_id[q['query_id']]['reported_total'] <= config['validation_max_native_count']]
                # Rotate deterministically by date; not a representative statistical sample.
                eligible.sort(key=lambda q: hashlib.sha256((start['cycle_date']+q['query_id']).encode()).hexdigest())
                selected_checks = eligible[:config['validation_queries_per_zip']]
                report['planned_geographic_checks'] = [dict(query=query(f'zip_{z}_{q["query_id"]}', z, q['filters']),
                    primary_query_id=q['query_id']) for z in config['validation_zips'] for q in selected_checks]
                report['geographic_checks'] = []
                save()
                for z in config['validation_zips']:
                    result, _ = run(query('broad_zip_'+z, z, {}), 'geographic_discovery', probe=True)
                    if result and not budget.stopped:
                        entry = report['entries'][-1]
                        entry['additional_makes'] = sorted(set(native_makes(_facets(result)))-set(report['makes']))
                        entry['count_difference_from_opening'] = result['reported_total']-report['opening_total']
                        save()
                for check in report['planned_geographic_checks']:
                    result, rows = run(check['query'], 'geographic_inventory')
                    previous = primary[check['primary_query_id']]
                    complete = bool(result and result['query_complete'])
                    new = sorted(set(rows.vin)-set(previous.vin)) if complete else None
                    missing = sorted(set(previous.vin)-set(rows.vin)) if complete else None
                    report['geographic_checks'].append(dict(**check, complete=complete,
                        additional_vins=new, primary_vins_not_seen=missing))
                    save()
                closing, _ = run(query('broad_close', config['primary_zip'], {}), 'closing_discovery', probe=True)
                if closing and not budget.stopped:
                    report['closing_total'] = closing['reported_total']
                    report['closing_additional_makes'] = sorted(set(native_makes(_facets(closing)))-set(report['makes']))
                by_id = {e['query']['query_id']: e for e in report['entries']}
                leaves = report['leaf_queries']
                report['primary_queries_complete'] = bool(report['discovery_complete'] and leaves
                    and all(by_id.get(q['query_id'], {}).get('query_complete') for q in leaves))
                frames = [rows for rows in primary.values() if not rows.empty]
                union = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                report['primary_observed_vins'] = union.vin.nunique() if not union.empty else 0
                report['duplicate_primary_memberships'] = int(union.duplicated(['retailer','vin']).sum()) if not union.empty else 0
                for name in ['opening', 'closing']:
                    report[name+'_count_residual'] = (report[name+'_total']-report['primary_observed_vins']
                        if name+'_total' in report else None)
                report['primary_scope_reconciled'] = bool(report['primary_queries_complete']
                    and report['duplicate_primary_memberships'] == 0
                    and report['opening_count_residual'] == report['closing_count_residual'] == 0
                    and report.get('closing_additional_makes') == [])
                report['geographic_checks_complete'] = bool(report['planned_geographic_checks']
                    and len(report['geographic_checks']) == len(report['planned_geographic_checks'])
                    and all(c['complete'] for c in report['geographic_checks'])
                    and all(e.get('additional_makes') == [] for e in report['entries']
                            if e['role'] == 'geographic_discovery'))
                report['geographic_membership_stable'] = bool(report['geographic_checks_complete']
                    and all(c['additional_vins'] == c['primary_vins_not_seen'] == []
                            for c in report['geographic_checks']))
                report['coverage_note'] = ('A sequential observed union, not a point-in-time census. '
                    'Opening/closing counts and sampled ZIP membership cannot prove national completeness.')
                report['status'] = 'collection_finished' if not budget.stopped else 'stopped'
            except BaseException as error:
                report.update(status='stopped', failure_type=type(error).__name__,
                    failure_reason=str(error) if isinstance(error, ValueError) else type(error).__name__)
                budget.stop()
                save()
                if not isinstance(error, Exception):
                    raise
            finally:
                report['ended_at'] = utcnow().isoformat()
                save()
                fatal = any(e.get('outcome_kind') in {'access_failure','transport_failure','schema_failure',
                    'identity_failure','storage_failure'} for e in report['entries'])
                # Pure discovery/schema/storage exceptions also block the next date.
                from vehicle_tracker.collect import CollectionStopped
                failed_outside_budget = (report.get('failure_type') is not None
                                         and report['failure_type'] != CollectionStopped.__name__)
                if fatal or failed_outside_budget or json.loads(budget_path.read_text())['budget']['pending_request']:
                    write_json_atomic(root/'access_stop.json', dict(run=str(report_path),
                        recorded_at=utcnow().isoformat(), reason='Unresolved fatal/uncertain collection outcome; manual review required'))
        return report


def export_catalog(folder, *, output):
    """Replay query sources, import only primary leaves, and publish a fresh audit.

    This deliberately uses a per-run history database: changing discovery scopes
    must not silently join the old fixed-panel daily register or infer absences.
    """
    folder, output = Path(folder), Path(output)
    report_path = folder/'catalog_report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if report['status'] == 'running' or not report.get('ended_at'):
        raise ValueError('Collection is not terminal')
    if digest(folder/'selected_config.json') != report['config_sha256']:
        raise ValueError('Selected catalog configuration changed')
    budget = json.loads((folder/'catalog_budget.json').read_text())['budget']
    if budget['requests'] != report['requests'] or budget['pending_request']:
        raise ValueError('Unreconciled or uncertain durable request ledger')
    sources = {str(report_path): digest(report_path),
               str(folder/'selected_config.json'): digest(folder/'selected_config.json'),
               str(folder/'catalog_budget.json'): digest(folder/'catalog_budget.json')}
    reports, frames, ledger = [], [], []
    if len({e['query']['query_id'] for e in report['entries']}) != len(report['entries']):
        raise ValueError('Duplicate catalog query IDs')
    leaves = {q['query_id'] for q in report['leaf_queries']}
    derived_leaves = []
    charged = 0
    starts = []
    observed = []
    for entry in report['entries']:
        row = dict(query_id=entry['query']['query_id'], role=entry['role'], status=entry['status'],
            query_complete=entry.get('query_complete', False), reported_total=entry.get('reported_total'))
        if entry.get('report'):
            path = Path(entry['report'])
            if digest(path) != entry['report_sha256']:
                raise ValueError('Catalog child report changed')
            child = json.loads(path.read_text())
            q = entry['query']
            if (child['filters'] != q['filters'] or child['zip_code'] != q['zip_code']
                    or child['location_filter'] is not False):
                raise ValueError('Catalog child query context changed')
            run, captures, observations = read_query_evidence(path)
            if child['query_complete'] != entry.get('query_complete') or child['reported_total'] != entry.get('reported_total'):
                raise ValueError('Catalog child coverage summary changed')
            if entry['role'] == 'discovery' and child.get('reported_total') is not None:
                if native_makes(_facets(child)) != report['makes']:
                    raise ValueError('Catalog make scope differs from native source')
            if entry['role'] == 'make_discovery' and child.get('reported_total') is not None:
                make = q['filters']['makes'][0]['name']
                children, _ = model_partitions(_facets(child), make, q['zip_code'], q['query_id'])
                derived_leaves.extend([q] if child['query_complete'] else children)
            # Reconcile the actual collector database, not only a fresh source import.
            database = path.parent/'vehicle.sqlite'
            if database.is_file():
                saved_captures, saved_rows = read_snapshots(database)
                sources[str(database)] = digest(database)
                columns = ['run_id','page','capture_id','status','row_count','observed_at_utc','error']
                saved_captures = saved_captures.rename(columns={'page_number':'page','source_sha256':'capture_id'})
                cols = [c for c in observations.columns if c not in [*NATIVE_FIELDS,'capture_id','run_id']]
                try:
                    pd.testing.assert_frame_equal(saved_captures[columns].sort_values('page').reset_index(drop=True),
                        captures[columns].sort_values('page').reset_index(drop=True), check_dtype=False, check_exact=True)
                    if not (saved_rows.empty and observations.empty):
                        pd.testing.assert_frame_equal(saved_rows[cols].sort_values('listing_id').reset_index(drop=True),
                            observations[cols].sort_values('listing_id').reset_index(drop=True), check_dtype=False, check_exact=True)
                except (AssertionError, KeyError) as error:
                    raise ValueError('Collector SQLite differs from retained sources') from error
            elif not observations.empty:
                raise ValueError('Collected query database is missing')
            charged += child['requests']
            sources[str(path)] = digest(path)
            for page in child['pages']:
                journal_path = path.parent/'attempts'/f"{page['page']:04d}.json"
                from vehicle_tracker.search import build_search_request
                request = build_search_request(filters=q['filters'], zip_code=q['zip_code'], page=page['page'])
                if json.loads(journal_path.read_text()) != dict(run_id=child['run_id'], request=request, **page):
                    raise ValueError('Catalog page journal differs from report')
                sources[str(journal_path)] = digest(journal_path)
                if page.get('request_started_at_utc'):
                    starts.append(aware(page['request_started_at_utc']))
                if page.get('response_received_at_utc'):
                    observed.append(aware(page['response_received_at_utc']))
                for key, sha in [('facet_source','facet_sha256'),('retained_source','source_sha256')]:
                    if page.get(key):
                        if digest(page[key]) != page[sha]:
                            raise ValueError('Catalog source changed')
                        sources[page[key]] = page[sha]
                evidence = page.get('response_evidence', {})
                if evidence.get('source_path'):
                    sources[evidence['source_path']] = digest(evidence['source_path'])
            if row['query_id'] in leaves:
                reports.append(path)
                frames.append(observations.assign(query_id=row['query_id']))
            row['verified_rows'] = len(observations)
        ledger.append(row)
    if charged != budget['requests']:
        raise ValueError('Catalog query requests differ from durable budget')
    if derived_leaves != report['leaf_queries']:
        raise ValueError('Catalog leaves differ from fresh native discovery')
    if any((b-a).total_seconds() < 3 for a,b in zip(starts,starts[1:])):
        raise ValueError('Catalog request spacing is below the declared minimum')
    if any(not aware(report['window_start']) <= stamp <= aware(report['window_end']) for stamp in starts+observed):
        raise ValueError('Catalog observation/request outside declared window')
    for q in report.get('planned_make_probes', []):
        if q['query_id'] not in {row['query_id'] for row in ledger}:
            ledger.append(dict(query_id=q['query_id'], role='make_discovery', status='unattempted', query_complete=False))
    inventory = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=[*OBSERVATION_COLUMNS,'query_id'])
    output.mkdir(parents=True, exist_ok=False)
    if reports:
        import_reports(reports, output/'history.sqlite')
    pd.DataFrame(ledger).to_csv(output/'coverage.csv', index=False)
    inventory.to_csv(output/'observations.csv', index=False)
    pd.DataFrame(report.get('geographic_checks', [])).to_json(output/'geographic_checks.json', orient='records', indent=2)
    write_json_atomic(output/'summary.json', report)
    write_json_atomic(output/'manifest.json', dict(created_at=utcnow().isoformat(),
        sources=sources, code_hashes={str(p):digest(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
        outputs={p.name:digest(p) for p in output.iterdir() if p.is_file()},
        interpretation='Observed primary make/model inventory; retain query gaps and ZIP diagnostics. Missing is not sold.'))
    return output
