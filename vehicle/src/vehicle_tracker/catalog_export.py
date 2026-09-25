"""Full-inventory catalog helpers. See catalog.py for the public entry points."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from vehicle_tracker.cycles import CycleBudget, aware, cycle_config, cycle_lock
from vehicle_tracker.carvana import NATIVE_FIELDS
from vehicle_tracker.history import OBSERVATION_COLUMNS, import_reports, read_query_evidence
from vehicle_tracker.search import ENDPOINT, collect_search, search_transport
from vehicle_tracker.search_context import empty_context_status, validate_first_page
from vehicle_tracker.search_plan import verify_isolated_leaf_failure, verify_isolated_pagination
from vehicle_tracker.storage import read_snapshots, write_json_atomic
from vehicle_tracker.catalog_partitions import plan_year_partitions, year_make_candidates
from vehicle_tracker.catalog_config import _strategy, digest, query
from vehicle_tracker.catalog_plan import (
    _candidate_queries, _discovery_probes, _planned_coverage, _validate_year_children, native_makes)
from vehicle_tracker.catalog_plan import model_partitions
from vehicle_tracker.catalog_reconcile import (
    MAKE_RECONCILIATION_COLUMNS, YEAR_RECONCILIATION_COLUMNS, _facets, _leaf_complete,
    _make_reconciliation, _year_diagnostics)

def _replay_adaptive_leaves(report, config):
    """Rebuild adaptive leaves from retained year facets and large-cell probes."""
    from vehicle_tracker.catalog_plan import adaptive_cell, adaptive_cells_from_capture
    threshold = config['split_threshold_vehicles']
    entries = {entry['query']['query_id']: entry for entry in report['entries'] if entry['role'] == 'year_discovery'}
    probes = {entry['query']['query_id']: entry for entry in report['entries'] if entry['role'] == 'make_discovery'}
    leaves = []
    for part in report['year_plan']['partitions']:
        entry = entries.get(part['query_id'])
        if not entry or not entry.get('year_context_validated'):
            continue
        child = json.loads(Path(entry['report']).read_text(encoding='utf-8'))
        page = child['pages'][0]
        capture = json.loads(Path(page['facet_source']).read_text(encoding='utf-8'))
        for cell in adaptive_cells_from_capture(capture, part, threshold=threshold):
            if cell['kind'] == 'whole':
                leaves.extend(cell['leaves'])
                continue
            probe = probes.get(cell['query_id'])
            if not probe or not probe.get('report') or probe.get('outcome_kind') in {
                    'pagination_unstable', 'schema_failure', 'identity_failure', 'transport_failure', 'server_failure'}:
                continue
            probe_child = json.loads(Path(probe['report']).read_text(encoding='utf-8'))
            facet_page = next(item for item in probe_child['pages'] if item.get('facet_source'))
            facets = json.loads(Path(facet_page['facet_source']).read_text(encoding='utf-8'))
            live = facets['facet_data']['makes'].get(cell['make'], {})
            decided = adaptive_cell(dict(live, count=live.get('count', cell['native_count'])),
                make=cell['make'], query_id=cell['query_id'], year_bounds=part['filters']['year'],
                zip_code=part['zip_code'], threshold=threshold)
            leaves.extend(decided['leaves'] if decided['kind'] == 'split' else [decided['fallback']])
    return leaves


def export_catalog(folder, *, output):
    """Replay query sources, import only primary leaves, and publish a fresh audit.

    This deliberately uses a per-run history database: changing discovery scopes
    must not silently join the old fixed-panel daily register or infer absences.
    """
    from vehicle_tracker import catalog as _api
    utcnow = _api.utcnow
    model_partitions = _api.model_partitions
    _make_reconciliation = _api._make_reconciliation
    folder, output = Path(folder), Path(output)
    report_path = folder/'catalog_report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if report['status'] == 'running' or not report.get('ended_at'):
        raise ValueError('Collection is not terminal')
    if digest(folder/'selected_config.json') != report['config_sha256']:
        raise ValueError('Selected catalog configuration changed')
    config = json.loads((folder/'selected_config.json').read_text())
    strategy = _strategy(config)
    expected_format = {'year_then_make_model': 'carvana-full-inventory-run-v2',
                       'year_make_adaptive': 'carvana-full-inventory-run-v3'}.get(
                           strategy, 'carvana-full-inventory-run-v1')
    adaptive = strategy == 'year_make_adaptive'
    if (report['format'] != expected_format
            or report.get('partition_strategy', 'all_year_models') != strategy):
        raise ValueError('Catalog partition strategy differs from selected configuration')
    if report['planned_discovery_probes'] != _discovery_probes(config):
        raise ValueError('Catalog broad probe plan differs from selected configuration')
    budget = json.loads((folder/'catalog_budget.json').read_text())['budget']
    if budget['requests'] != report['requests'] or budget['pending_request']:
        raise ValueError('Unreconciled or uncertain durable request ledger')
    sources = {str(report_path): digest(report_path),
               str(folder/'selected_config.json'): digest(folder/'selected_config.json'),
               str(folder/'catalog_budget.json'): digest(folder/'catalog_budget.json')}
    reports, dest_reports, frames, ledger, primary = [], {}, [], [], {}
    if len({e['query']['query_id'] for e in report['entries']}) != len(report['entries']):
        raise ValueError('Duplicate catalog query IDs')
    leaves = {q['query_id'] for q in report['leaf_queries']}
    derived_leaves = []
    derived_year_plan, derived_years, derived_make_probes, derived_zeros = None, [], [], []
    charged = 0
    starts = []
    observed = []
    for entry in report['entries']:
        row = dict(query_id=entry['query']['query_id'], role=entry['role'], status=entry['status'],
            query_complete=entry.get('query_complete', False), reported_total=entry.get('reported_total'),
            context_validated=entry.get('context_validated', False),
            context_status=entry.get('context_status', 'unverified'),
            leaf_complete=(_leaf_complete(entry)
                           if entry['query']['query_id'] in leaves or entry.get('retry_of') in leaves
                           else None))
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
            if (child.get('first_page_context_validated', False) is not entry.get('context_validated', False)
                    or child.get('first_page_context_status', 'unverified')
                    != entry.get('context_status', 'unverified')):
                raise ValueError('Catalog child context validation differs from retained evidence')
            if entry['role'] == 'discovery' and child.get('reported_total') is not None:
                names = (native_makes(_facets(child), allow_residual=True)[0] if adaptive
                         else native_makes(_facets(child)))
                if names != report['makes']:
                    raise ValueError('Catalog make scope differs from native source')
                if strategy in ('year_then_make_model', 'year_make_adaptive'):
                    page = child['pages'][0]
                    derived_year_plan = plan_year_partitions(page['facet_source'],
                        expected_sha256=page['facet_sha256'], zip_code=config['primary_zip'])
                else:
                    derived_make_probes = [query(f'make_{i:03d}', config['primary_zip'],
                        {'makes': [{'name': make}]}) for i, make in enumerate(report['makes'])]
            if entry['role'] == 'year_discovery':
                if strategy not in ('year_then_make_model', 'year_make_adaptive') or derived_year_plan is None:
                    raise ValueError('Year discovery lacks its frozen native basis')
                parts = [p for p in derived_year_plan['partitions'] if p['query_id'] == q['query_id']]
                if len(parts) != 1 or q != query(parts[0]['query_id'], parts[0]['zip_code'], parts[0]['filters']):
                    raise ValueError('Year discovery differs from the exact declared partition')
                page = child['pages'][0]
                if page.get('facet_source'):
                    # A retained facet can precede a fatal schema/identity/storage stop.
                    # Replay its bytes, but only collection-admitted discovery
                    # may create downstream candidates or native-zero records.
                    if entry.get('year_context_validated') is True:
                        _facets(child)
                        discovery = year_make_candidates(page['facet_source'],
                            expected_sha256=page['facet_sha256'], partition=parts[0],
                            allow_count_residual=adaptive)
                        derived_years.append(discovery)
                        candidates = _candidate_queries(discovery)
                        _validate_year_children(candidates, q)
                        derived_make_probes.extend(candidates)
                        derived_zeros.extend(dict(row, year_query_id=q['query_id'])
                                             for row in discovery['native_zero_categories'])
                elif entry.get('year_context_validated'):
                    raise ValueError('Validated year context lacks retained facets')
            if entry['role'] == 'make_discovery' and 'partition_reason' in entry:
                if q not in derived_make_probes:
                    raise ValueError('Make discovery is outside the native candidate plan')
                make = q['filters']['makes'][0]['name']
                facets = _facets(child)
                children, reason, overlap = model_partitions(facets, make, q['zip_code'], q['query_id'],
                    year_bounds=q['filters'].get('year') if strategy in ('year_then_make_model', 'year_make_adaptive') else None)
                models = facets['facet_data']['makes'].get(make, {}).get('parentModels', [])
                model_total = sum(model['count'] for model in models) if models else None
                if (entry.get('native_model_count_sum') != model_total
                        or entry.get('discovery_observed_at_utc') != facets['captured_at_utc']):
                    raise ValueError('Catalog make discovery diagnostic changed')
                expected_reason = 'complete make probe reused' if child['query_complete'] else reason
                expected_overlap = None if child['query_complete'] else overlap
                if entry['partition_reason'] != expected_reason:
                    raise ValueError('Catalog model partition reason differs from retained discovery')
                if entry.get('model_id_overlap') != expected_overlap:
                    raise ValueError('Catalog model-id overlap differs from retained discovery')
                if strategy in ('year_then_make_model', 'year_make_adaptive'):
                    _validate_year_children([q] if child['query_complete'] else children, q)
                if not adaptive:
                    derived_leaves.extend([q] if child['query_complete'] else children)
            # Reconcile the actual collector database, not only a fresh source import.
            from vehicle_tracker.search import attempt_journal_name, build_search_request, overlap_siblings
            stored_hashes = {page.get('source_sha256') for page in child['pages']
                             if page.get('source_sha256') and page.get('database_outcome') != 'deferred'}
            if not captures.empty and stored_hashes:
                captures = captures[captures.capture_id.isin(stored_hashes)]
            database = path.parent/'vehicle.sqlite'
            if database.is_file():
                saved_captures, saved_rows = read_snapshots(database)
                sources[str(database)] = digest(database)
                columns = ['run_id','page','capture_id','status','row_count','observed_at_utc','error']
                saved_captures = saved_captures.rename(columns={'page_number':'page','source_sha256':'capture_id'})
                cols = [c for c in observations.columns if c not in [*NATIVE_FIELDS,'capture_id','run_id']]
                try:
                    pd.testing.assert_frame_equal(saved_captures[columns].sort_values('capture_id').reset_index(drop=True),
                        captures[columns].sort_values('capture_id').reset_index(drop=True), check_dtype=False, check_exact=True)
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
                journal_path = path.parent/'attempts'/attempt_journal_name(page)
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
            dest = entry.get('retry_of') or row['query_id']
            if dest in leaves:
                if not entry.get('retry_of'):
                    dest_reports[dest] = path
                if adaptive:
                    previous = primary.get(dest)
                    frames = [frame for frame in (previous, observations) if frame is not None and not frame.empty]
                    if frames:
                        combined = pd.concat(frames, ignore_index=True)
                        primary[dest] = combined.drop_duplicates(['retailer', 'vin'])
                    elif dest not in primary:
                        primary[dest] = observations
                elif entry.get('query_complete') or dest not in primary:
                    primary[dest] = observations
                page0 = next((item for item in child['pages'] if item.get('facet_source')), None)
                requested = q['filters'].get('makes', [{}])[0].get('parentModels') or []
                if page0 and requested:
                    facet = json.loads(Path(page0['facet_source']).read_text(encoding='utf-8'))
                    siblings = set(overlap_siblings(facet.get('facet_data'), q['filters']))
                    allowed = set()
                    for disc in report['entries']:
                        overlap = disc.get('model_id_overlap')
                        if (disc.get('role') != 'make_discovery' or not overlap
                                or disc['query']['filters']['makes'][0]['name'] != q['filters']['makes'][0]['name']
                                or disc['query']['filters'].get('year') != q['filters'].get('year')):
                            continue
                        for cluster in overlap['clusters']:
                            if requested[0]['name'] in cluster['models']:
                                allowed.update(name for name in cluster['models']
                                               if name != requested[0]['name'])
                    if siblings and not siblings <= allowed:
                        raise ValueError('Overlap siblings differ from declared model-id cluster')
            row['verified_rows'] = len(observations)
        ledger.append(row)
    if charged != budget['requests']:
        raise ValueError('Catalog query requests differ from durable budget')
    if adaptive:
        derived_leaves = _replay_adaptive_leaves(report, config)
    if derived_leaves != report['leaf_queries']:
        raise ValueError('Catalog leaves differ from fresh native discovery')
    if derived_make_probes != report.get('planned_make_probes', []):
        raise ValueError('Catalog make candidate plan differs from native discovery')
    if strategy in ('year_then_make_model', 'year_make_adaptive'):
        expected_year_queries = [query(p['query_id'], p['zip_code'], p['filters'])
                                for p in (derived_year_plan or {}).get('partitions', [])]
        if (derived_year_plan != report['year_plan'] or expected_year_queries != report['planned_year_probes']
                or derived_years != report['year_discoveries'] or derived_zeros != report['native_zero_categories']):
            raise ValueError('Catalog year plan, candidates or zero categories differ from retained discovery')
        diagnostics = _year_diagnostics(report, primary)
        if any(report.get(key) != value for key, value in diagnostics.items()):
            raise ValueError('Catalog year reconciliation differs from retained sources')
    declared = {(q['query_id'], role): q for q, role in _planned_coverage(report)}
    for entry in report['entries']:
        q = entry['query']
        if declared.get((q['query_id'], entry['role'])) != q:
            raise ValueError('Catalog entry differs from its declared query and role')
    if any((b-a).total_seconds() < 3 for a,b in zip(starts,starts[1:])):
        raise ValueError('Catalog request spacing is below the declared minimum')
    if any(not aware(report['window_start']) <= stamp <= aware(report['window_end']) for stamp in starts+observed):
        raise ValueError('Catalog observation/request outside declared window')
    for q, role in _planned_coverage(report):
        if q['query_id'] not in {row['query_id'] for row in ledger}:
            ledger.append(dict(query_id=q['query_id'], role=role, status='unattempted', query_complete=False,
                context_validated=False, context_status='unattempted',
                leaf_complete=False if q['query_id'] in leaves else None))
    make_reconciliation = _make_reconciliation(report, primary)
    if make_reconciliation != report.get('make_reconciliation'):
        raise ValueError('Catalog make reconciliation differs from retained sources')
    reports = [path for path in dest_reports.values() if not isinstance(path, list)]
    if adaptive:
        reports.extend(Path(entry['report']) for entry in report['entries']
                       if entry.get('retry_of') in leaves and entry.get('report'))
    frames = [rows.assign(query_id=query_id) for query_id, rows in primary.items()
              if query_id in leaves and not rows.empty]
    inventory = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=[*OBSERVATION_COLUMNS,'query_id'])
    output.mkdir(parents=True, exist_ok=False)
    if reports:
        import_reports(reports, output/'history.sqlite')
    pd.DataFrame(ledger).to_csv(output/'coverage.csv', index=False)
    make_columns = MAKE_RECONCILIATION_COLUMNS
    if strategy in ('year_then_make_model', 'year_make_adaptive'):
        make_columns = [*MAKE_RECONCILIATION_COLUMNS, 'year_min', 'year_max']
        pd.DataFrame(report['year_reconciliation'], columns=YEAR_RECONCILIATION_COLUMNS).to_csv(
            output/'year_reconciliation.csv', index=False)
        write_json_atomic(output/'native_zero_categories.json', report['native_zero_categories'])
    pd.DataFrame(make_reconciliation, columns=make_columns).to_csv(output/'make_reconciliation.csv', index=False)
    inventory.to_csv(output/'observations.csv', index=False)
    pd.DataFrame(report.get('geographic_checks', [])).to_json(output/'geographic_checks.json', orient='records', indent=2)
    write_json_atomic(output/'summary.json', report)
    write_json_atomic(output/'manifest.json', dict(created_at=utcnow().isoformat(),
        sources=sources, code_hashes={str(p):digest(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
        outputs={p.name:digest(p) for p in output.iterdir() if p.is_file()},
        interpretation='Observed primary make/model inventory; retain query gaps and ZIP diagnostics. Missing is not sold.'))
    return output


