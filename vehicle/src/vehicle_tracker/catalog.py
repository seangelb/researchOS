"""Bounded full-catalog discovery; ordinary search evidence, no sales inference.

Discover all current makes, then non-overlapping model families where the native
counts support them. Never constrain the population to a fixed year range. Small
make probes are already complete queries and are reused without another request.
"""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
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
from vehicle_tracker.catalog_partitions import plan_year_partitions, year_make_candidates

MAKE_RECONCILIATION_COLUMNS = ('''make discovery_query_id discovery_observed_at_utc
    discovery_native_count native_model_count_sum native_model_count_residual
    declared_leaf_queries attempted_leaf_queries completed_leaf_queries all_leaf_queries_complete
    leaf_reported_total_sum observed_rows observed_unique_vins duplicate_memberships
    discovery_minus_leaf_native_count discovery_minus_observed_vins leaf_native_minus_observed_vins
    inventory_observation_start inventory_observation_end observed_count_scope''').split()
YEAR_RECONCILIATION_COLUMNS = ('''query_id kind year_min year_max status context_validated
    observed_at_utc reported_total positive_make_candidates native_zero_categories
    declared_make_queries validated_make_queries declared_leaf_queries completed_leaf_queries
    observed_rows observed_unique_vins native_minus_observed_vins''').split()


def _strategy(config):
    strategy = config.get('partition_strategy', 'all_year_models')
    if ((config['format'], strategy) not in {
            ('carvana-full-inventory-v1', 'all_year_models'),
            ('carvana-full-inventory-v2', 'year_then_make_model')}):
        raise ValueError('Require v1/all_year_models or v2/year_then_make_model configuration')
    return strategy


def utcnow():
    return datetime.now(timezone.utc)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def query(name, zip_code, filters):
    return dict(query_id=name, zip_code=zip_code, filters=filters, location_filter=False)


def _unique_roots(paths):
    """Use the same normalized path identity for deduplication and lock ordering."""
    roots = {}
    for path in paths:
        resolved = Path(path).resolve()
        roots.setdefault(os.path.normcase(str(resolved)), resolved)
    return [roots[key] for key in sorted(roots)]


def _capture_roots(config):
    return _unique_roots([config['capture_root'], *config.get('related_capture_roots', [])])


def _capture_root_state(root):
    """Read prior evidence without opening a lock or creating any directories."""
    unresolved = []
    if root.exists() and not root.is_dir():
        unresolved.append(dict(capture_directory=str(root), reason='Capture root is not a directory'))
    else:
        # These are dedicated capture roots: an empty/config-only date can be
        # a crash before its durable budget existed, so it remains unresolved.
        prior = [path for path in root.glob('*') if path.is_dir()]
        for folder in sorted(prior):
            try:
                budget = json.loads((folder/'catalog_budget.json').read_text(encoding='utf-8'))['budget']
                report = json.loads((folder/'catalog_report.json').read_text(encoding='utf-8'))
                if any(type(budget[key]) is not bool for key in ['pending_request', 'stopped']):
                    raise ValueError('Invalid pending/stopped request state')
                if budget['pending_request']:
                    reason = 'Pending request outcome remains uncertain'
                elif budget['stopped']:
                    reason = 'Durable catalog budget remains stopped'
                elif report.get('status') not in {'collection_finished', 'stopped'}:
                    reason = 'Catalog report is running or lacks terminal status'
                else:
                    aware(report['ended_at'])
                    continue
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                reason = 'Missing or invalid catalog budget/report; inspect original evidence'
            unresolved.append(dict(capture_directory=str(folder), reason=reason))
    stopped = (root/'access_stop.json').exists()
    return dict(capture_root=str(root), access_stopped=stopped,
                unresolved_invocations=unresolved, blocked=stopped or bool(unresolved))


def _require_clear_roots(states):
    if any(state['access_stopped'] for state in states):
        raise ValueError('Unresolved access stop in a related capture root; inspect original evidence')
    if any(state['unresolved_invocations'] for state in states):
        raise ValueError('Unresolved prior catalog invocation in a related capture root; inspect original evidence')


def settings(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding='utf-8'))
    _strategy(config)
    if (config['endpoint'] != ENDPOINT or config['location_filter'] is not False
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
    peers = config.get('related_capture_roots')
    if 'related_capture_roots' in config or _strategy(config) == 'year_then_make_model':
        if (not isinstance(peers, list) or not peers
                or any(not isinstance(peer, str) or not peer.strip() for peer in peers)):
            raise ValueError('Require an explicit nonempty related_capture_roots path list')
        config['related_capture_roots'] = [str(root) for root in
            _unique_roots(path.parent.parent/peer for peer in peers)]
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
    states = [_capture_root_state(peer) for peer in _capture_roots(config)]
    return dict(config=config, config_sha256=digest(path), cycle_date=day,
        window_start=now.isoformat(), window_end=end.isoformat(), destination=str(root/day),
        destination_fresh=not (root/day).exists(), access_stopped=any(s['access_stopped'] for s in states),
        capture_root_states=states, capture_preflight_blocked=any(s['blocked'] for s in states),
        active_locks_checked=False,
        request_ceiling=config['max_requests'], effective_seconds=(end-now).total_seconds(),
        discovery=('Fresh broad counts, integer-year cells and both tails, then make/model partitions'
                   if _strategy(config) == 'year_then_make_model' else
                   'Fresh broad make counts, then current make/model partitions; all years'),
        national_coverage_verified=False, writes=False, requests=0)


def native_makes(capture):
    """Fail closed on an unaccounted broad category; never silently drop it."""
    _validate_discovery(capture)
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


def _validate_discovery(capture, *, year_bounds=None):
    """Require exact requested year application and valid native categories."""
    year = capture['facet_data']['year']
    expected = year_bounds or {}
    if capture['request']['filters'].get('year', {}) != expected:
        raise ValueError('Discovery year request differs from the selected bounds')
    for bound, applied in [('min', 'appliedMin'), ('max', 'appliedMax')]:
        value = year.get(applied)
        if ((bound in expected and (type(value) is not int or value != expected[bound]))
                or (bound not in expected and value is not None)):
            raise ValueError('Unexpected applied year boundary in discovery')
    for bucket in capture['facet_data']['makes'].values():
        for value in [bucket['count'], *(child['count'] for child in bucket['parentModels'])]:
            if type(value) is not int or value < 0:
                raise ValueError('Invalid native discovery count')
        for child in bucket['parentModels']:
            if child['isApplied'] is not False:
                raise ValueError('Unexpected applied model filter in discovery')
            if any(type(value) is not int or value < 0 for value in child['modelIds']):
                raise ValueError('Invalid native discovery model ID')


def model_partitions(capture, make, zip_code, prefix, *, year_bounds=None):
    """Split only when native model counts and IDs form a partition; else whole make.

    An all-year whole-make fallback preserves unknown models and year boundaries.
    Its pagination may fail; that failure stays visible, never repaired by deduping.
    """
    _validate_discovery(capture, year_bounds=year_bounds)
    filters = {'makes': [{'name': make}]}
    if year_bounds is not None:
        filters['year'] = dict(year_bounds)
    if capture['request']['filters'] != filters:
        raise ValueError('Requested make/year discovery differs from selected scope')
    whole = [query(prefix+'_all', zip_code, filters)]
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
        dict(filters, makes=[{'name': make, 'parentModels': [{'name': child['key']}]}]))
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


def _make_reconciliation(report, primary):
    """Expose category drift and unknown coverage without cancelling residuals."""
    entries = {entry['query']['query_id']: entry for entry in report['entries']}
    records = []
    for probe in report.get('planned_make_probes', []):
        make = probe['filters']['makes'][0]['name']
        discovery = entries.get(probe['query_id'], {})
        leaves = [q for q in report['leaf_queries'] if q['filters']['makes'][0]['name'] == make
                  and q['filters'].get('year') == probe['filters'].get('year')]
        leaf_entries = [entries.get(q['query_id'], {}) for q in leaves]
        frames = [primary[q['query_id']] for q in leaves
                  if q['query_id'] in primary and not primary[q['query_id']].empty]
        rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        native = discovery.get('reported_total')
        model_total = discovery.get('native_model_count_sum')
        leaf_total = (sum(e['reported_total'] for e in leaf_entries)
                      if leaves and all(e.get('reported_total') is not None for e in leaf_entries) else None)
        distinct = int(rows.vin.nunique()) if not rows.empty else 0
        clocks = pd.to_datetime(rows.observed_at_utc, utc=True, format='ISO8601') if not rows.empty else None
        record = dict(make=make, discovery_query_id=probe['query_id'],
            discovery_observed_at_utc=discovery.get('discovery_observed_at_utc'),
            discovery_native_count=native, native_model_count_sum=model_total,
            native_model_count_residual=native-model_total if native is not None and model_total is not None else None,
            declared_leaf_queries=len(leaves),
            attempted_leaf_queries=sum(bool(e.get('report')) for e in leaf_entries),
            completed_leaf_queries=sum(bool(e.get('query_complete')) for e in leaf_entries),
            all_leaf_queries_complete=bool(leaves and all(e.get('query_complete') for e in leaf_entries)),
            leaf_reported_total_sum=leaf_total, observed_rows=len(rows), observed_unique_vins=distinct,
            duplicate_memberships=int(rows.duplicated(['retailer','vin']).sum()) if not rows.empty else 0,
            discovery_minus_leaf_native_count=native-leaf_total if native is not None and leaf_total is not None else None,
            discovery_minus_observed_vins=native-distinct if native is not None else None,
            leaf_native_minus_observed_vins=leaf_total-distinct if leaf_total is not None else None,
            inventory_observation_start=clocks.min().isoformat() if clocks is not None else None,
            inventory_observation_end=clocks.max().isoformat() if clocks is not None else None,
            observed_count_scope=('Retained positive observations only; incomplete/unattempted leaves '
                                  'give lower bounds, not zero inventory or evidence of absence.'))
        if report.get('partition_strategy') == 'year_then_make_model':
            record.update(year_min=probe['filters']['year'].get('min'),
                          year_max=probe['filters']['year'].get('max'))
        records.append(record)
    return records


def _discovery_probes(config):
    return [dict(query=query('broad_open', config['primary_zip'], {}), role='discovery'),
            *(dict(query=query('broad_zip_'+z, z, {}), role='geographic_discovery')
              for z in config['validation_zips']),
            dict(query=query('broad_close', config['primary_zip'], {}), role='closing_discovery')]


def _planned_coverage(report):
    """Every declared query remains in the denominator after an early stop."""
    planned = [(p['query'], p['role']) for p in report['planned_discovery_probes']]
    planned.extend((q, 'year_discovery') for q in report.get('planned_year_probes', []))
    planned.extend((q, 'make_discovery') for q in report.get('planned_make_probes', []))
    planned.extend((q, 'primary_inventory') for q in report['leaf_queries'])
    planned.extend((check['query'], 'geographic_inventory')
                   for check in report.get('planned_geographic_checks', []))
    return planned


def _candidate_queries(discovery):
    return [query(row['query_id'], row['zip_code'], row['filters'])
            for row in discovery['candidates']]


def _validate_year_children(children, parent):
    """Make/model subdivision cannot clip an exact year or either open tail."""
    bounds = parent['filters']['year']
    if any(child['filters'].get('year') != bounds for child in children):
        raise ValueError('Year partition child clips or changes its mandatory year context')


def _year_reconciliation(report, primary):
    """Count evidence per frozen year context, including both unbounded tails."""
    entries = {entry['query']['query_id']: entry for entry in report['entries']}
    discoveries = {item['partition']['query_id']: item for item in report['year_discoveries']}
    records = []
    for part in (report.get('year_plan') or {}).get('partitions', []):
        bounds, identity = part['filters']['year'], part['query_id']
        entry, discovery = entries.get(identity, {}), discoveries.get(identity)
        probes = [q for q in report.get('planned_make_probes', []) if q['filters']['year'] == bounds]
        leaves = [q for q in report['leaf_queries'] if q['filters'].get('year') == bounds]
        frames = [primary[q['query_id']] for q in leaves
                  if q['query_id'] in primary and not primary[q['query_id']].empty]
        rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        total = discovery['reported_total'] if discovery is not None else None
        unique = int(rows.vin.nunique()) if not rows.empty else 0
        records.append(dict(query_id=identity, kind=part['kind'], year_min=bounds.get('min'),
            year_max=bounds.get('max'), status=entry.get('status', 'unattempted'),
            context_validated=discovery is not None,
            observed_at_utc=discovery['source']['observed_at_utc'] if discovery else None,
            reported_total=total, positive_make_candidates=len(discovery['candidates']) if discovery else None,
            native_zero_categories=len(discovery['native_zero_categories']) if discovery else None,
            declared_make_queries=len(probes),
            validated_make_queries=sum('partition_reason' in entries.get(q['query_id'], {}) for q in probes),
            declared_leaf_queries=len(leaves),
            completed_leaf_queries=sum(bool(entries.get(q['query_id'], {}).get('query_complete')) for q in leaves),
            observed_rows=len(rows), observed_unique_vins=unique,
            native_minus_observed_vins=total-unique if total is not None else None))
    return records


def _year_diagnostics(report, primary):
    records = _year_reconciliation(report, primary)
    complete = bool(records and all(row['context_validated'] for row in records))
    known = sum(row['reported_total'] for row in records if row['reported_total'] is not None)
    total = known if complete else None
    return dict(year_reconciliation=records, year_contexts_validated=complete,
        year_native_count_partial_sum=known, year_native_count_sum=total,
        opening_minus_year_native_count=report['opening_total']-total if total is not None else None,
        year_tail_counts={kind: next((row['reported_total'] for row in records if row['kind'] == kind), None)
                          for kind in ['lower_tail', 'upper_tail']},
        missing_or_noninteger_year_count=None,
        year_count_note='Counts have different observation clocks. Residuals may reflect drift or unallocated years; zero does not establish national or missing-year coverage.')


def collect_catalog(config_path, *, expected_sha256, post=None):
    """One fresh local-date invocation, one shared durable budget, no retry/resume."""
    start = preview(config_path)
    if expected_sha256 != start['config_sha256']:
        raise ValueError('Full-inventory config changed after preview')
    if not start['destination_fresh']:
        raise ValueError('Existing date or unresolved access stop; no replacement attempt')
    _require_clear_roots(start['capture_root_states'])
    config, folder = start['config'], Path(start['destination'])
    root = folder.parent
    roots = _capture_roots(config)
    with ExitStack() as locks:
        for peer in roots:
            peer.mkdir(parents=True, exist_ok=True)
            locks.enter_context(cycle_lock(peer))
        # Recheck under every root's existing OS lock, including old v1 collectors.
        if folder.exists():
            raise ValueError('Existing date; no replacement attempt')
        _require_clear_roots([_capture_root_state(peer) for peer in roots])
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
        strategy = _strategy(config)
        report = dict(format='carvana-full-inventory-run-v2' if strategy == 'year_then_make_model' else
                           'carvana-full-inventory-run-v1', started_at=utcnow().isoformat(),
            partition_strategy=strategy,
            capture_directory=str(folder),
            config_sha256=expected_sha256, cycle_date=start['cycle_date'], status='running',
            planned_discovery_probes=_discovery_probes(config),
            entries=[], leaf_queries=[], makes=[], discovery_complete=False,
            primary_queries_complete=False, declared_collection_complete=False, national_coverage_verified=False,
            estimated_sales=None, requests=0,
            window_start=start['window_start'], window_end=start['window_end'],
            code_hashes={str(p): digest(p) for p in sorted(Path(__file__).parent.glob('*.py'))})
        report_path = folder/'catalog_report.json'
        if strategy == 'year_then_make_model':
            report.update(year_plan=None, planned_year_probes=[], planned_make_probes=[],
                          year_discoveries=[], native_zero_categories=[])
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
            if role == 'primary_inventory':
                primary[q['query_id']] = rows
            save()
            if not budget.stopped:
                budget.timeout_ms()  # Even the final response must arrive inside the window.
            return result, rows

        def collect_make(q):
            make = q['filters']['makes'][0]['name']
            probe, rows = run(q, 'make_discovery', probe=True)
            if not probe or budget.stopped:
                return False
            facets = _facets(probe)
            children, reason = model_partitions(facets, make, q['zip_code'], q['query_id'],
                                                year_bounds=q['filters'].get('year'))
            models = facets['facet_data']['makes'].get(make, {}).get('parentModels', [])
            report['entries'][-1].update(discovery_observed_at_utc=facets['captured_at_utc'],
                native_model_count_sum=sum(child['count'] for child in models) if models else None)
            if probe['query_complete']:
                children, reason = [q], 'complete make probe reused'
                primary[q['query_id']] = rows
            if strategy == 'year_then_make_model':
                _validate_year_children(children, q)
            report['leaf_queries'].extend(children)
            report['entries'][-1]['partition_reason'] = reason
            save()
            for child in children:
                if child != q:
                    run(child, 'primary_inventory')
                if budget.stopped:
                    break
            return True

        save()
        with search_transport(post) as send:
            try:
                opening, _ = run(broad, 'discovery', probe=True)
                if not opening or budget.stopped:
                    raise ValueError('Broad discovery failed')
                report['makes'] = native_makes(_facets(opening))
                report['opening_total'] = opening['reported_total']
                report['inventory_payload_page_floor'] = (opening['reported_total']+23)//24
                if strategy == 'year_then_make_model':
                    page = opening['pages'][0]
                    report['year_plan'] = plan_year_partitions(page['facet_source'],
                        expected_sha256=page['facet_sha256'], zip_code=config['primary_zip'])
                    report['planned_year_probes'] = [query(p['query_id'], p['zip_code'], p['filters'])
                                                     for p in report['year_plan']['partitions']]
                    save()  # Freeze all integer-year contexts and both tails before any year request.
                    for part, q in zip(report['year_plan']['partitions'], report['planned_year_probes']):
                        result, _ = run(q, 'year_discovery', probe=True)
                        if not result or budget.stopped:
                            break
                        _facets(result)  # Bind its source, clock and query to the charged response.
                        page = result['pages'][0]
                        discovery = year_make_candidates(page['facet_source'],
                            expected_sha256=page['facet_sha256'], partition=part)
                        report['year_discoveries'].append(discovery)
                        report['entries'][-1]['year_context_validated'] = True
                        candidates = _candidate_queries(discovery)
                        _validate_year_children(candidates, q)
                        report['planned_make_probes'].extend(candidates)
                        report['native_zero_categories'].extend(dict(row, year_query_id=q['query_id'])
                                                               for row in discovery['native_zero_categories'])
                        save()  # Every positive candidate is declared before enumeration starts.
                        for candidate in candidates:
                            if not collect_make(candidate) or budget.stopped:
                                break
                        if budget.stopped or budget.requests >= budget.max_requests:
                            break
                    all_years = len(report['year_discoveries']) == len(report['planned_year_probes'])
                else:
                    report['planned_make_probes'] = [query(f'make_{i:03d}', config['primary_zip'],
                        {'makes': [{'name': make}]}) for i, make in enumerate(report['makes'])]
                    save()
                    for q in report['planned_make_probes']:
                        if not collect_make(q) or budget.stopped:
                            break
                    all_years = True
                validated_makes = [e for e in report['entries'] if e['role'] == 'make_discovery'
                                   and 'partition_reason' in e]
                report['discovery_complete'] = bool(all_years
                    and len(validated_makes) == len(report['planned_make_probes']))
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
                    and all(e.get('additional_makes') is not None for e in report['entries']
                            if e['role'] == 'geographic_discovery'))
                report['geographic_membership_stable'] = bool(report['geographic_checks_complete']
                    and all(c['additional_vins'] == c['primary_vins_not_seen'] == []
                            for c in report['geographic_checks'])
                    and all(e.get('additional_makes') == [] for e in report['entries']
                            if e['role'] == 'geographic_discovery'))
                report['declared_collection_complete'] = bool(not budget.stopped
                    and report['primary_queries_complete'] and report['geographic_checks_complete']
                    and 'closing_total' in report)
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
                try:
                    report['make_reconciliation'] = _make_reconciliation(report, primary)
                    if strategy == 'year_then_make_model':
                        report.update(_year_diagnostics(report, primary))
                except Exception as error:
                    # A diagnostic failure must not leave a terminal process labelled running.
                    report.update(status='stopped', declared_collection_complete=False,
                        make_reconciliation=None, reconciliation_failure_type=type(error).__name__)
                    report.setdefault('failure_type', type(error).__name__)
                    report.setdefault('failure_reason', 'Make reconciliation failed; retained evidence requires review')
                    try:
                        budget.stop()
                    except Exception as storage_error:
                        report['budget_finalization_failure_type'] = type(storage_error).__name__
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
    config = json.loads((folder/'selected_config.json').read_text())
    strategy = _strategy(config)
    expected_format = ('carvana-full-inventory-run-v2' if strategy == 'year_then_make_model' else
                       'carvana-full-inventory-run-v1')
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
    reports, frames, ledger, primary = [], [], [], {}
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
                if strategy == 'year_then_make_model':
                    page = child['pages'][0]
                    derived_year_plan = plan_year_partitions(page['facet_source'],
                        expected_sha256=page['facet_sha256'], zip_code=config['primary_zip'])
                else:
                    derived_make_probes = [query(f'make_{i:03d}', config['primary_zip'],
                        {'makes': [{'name': make}]}) for i, make in enumerate(report['makes'])]
            if entry['role'] == 'year_discovery':
                if strategy != 'year_then_make_model' or derived_year_plan is None:
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
                            expected_sha256=page['facet_sha256'], partition=parts[0])
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
                children, reason = model_partitions(facets, make, q['zip_code'], q['query_id'],
                    year_bounds=q['filters'].get('year') if strategy == 'year_then_make_model' else None)
                models = facets['facet_data']['makes'].get(make, {}).get('parentModels', [])
                model_total = sum(model['count'] for model in models) if models else None
                if (entry.get('native_model_count_sum') != model_total
                        or entry.get('discovery_observed_at_utc') != facets['captured_at_utc']):
                    raise ValueError('Catalog make discovery diagnostic changed')
                expected_reason = 'complete make probe reused' if child['query_complete'] else reason
                if entry['partition_reason'] != expected_reason:
                    raise ValueError('Catalog model partition reason differs from retained discovery')
                if strategy == 'year_then_make_model':
                    _validate_year_children([q] if child['query_complete'] else children, q)
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
                primary[row['query_id']] = observations
            row['verified_rows'] = len(observations)
        ledger.append(row)
    if charged != budget['requests']:
        raise ValueError('Catalog query requests differ from durable budget')
    if derived_leaves != report['leaf_queries']:
        raise ValueError('Catalog leaves differ from fresh native discovery')
    if derived_make_probes != report.get('planned_make_probes', []):
        raise ValueError('Catalog make candidate plan differs from native discovery')
    if strategy == 'year_then_make_model':
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
            ledger.append(dict(query_id=q['query_id'], role=role, status='unattempted', query_complete=False))
    make_reconciliation = _make_reconciliation(report, primary)
    if make_reconciliation != report.get('make_reconciliation'):
        raise ValueError('Catalog make reconciliation differs from retained sources')
    inventory = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=[*OBSERVATION_COLUMNS,'query_id'])
    output.mkdir(parents=True, exist_ok=False)
    if reports:
        import_reports(reports, output/'history.sqlite')
    pd.DataFrame(ledger).to_csv(output/'coverage.csv', index=False)
    make_columns = MAKE_RECONCILIATION_COLUMNS
    if strategy == 'year_then_make_model':
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
