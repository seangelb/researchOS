"""One bounded facet experiment, separate from inventory enumeration and imports."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from uuid import uuid4

import pandas as pd

from vehicle_tracker.collect import CollectionStopped
from vehicle_tracker.cycles import CycleBudget, aware, cycle_config, cycle_lock
from vehicle_tracker.search import ENDPOINT, build_search_request, parse_search_capture, project_response, search_transport
from vehicle_tracker.search_evidence import retain_response_evidence, unique_object, verify_response_evidence
from vehicle_tracker.storage import retain_capture, write_json_atomic


def utcnow():
    return datetime.now(timezone.utc)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def facet_queries(makes, years, zip_code):
    """Frozen order: broad opening, exact-year/make grid, broad closing."""
    queries = [dict(query_id='broad_open', filters={}, zip_code=zip_code, location_filter=False)]
    for year in years:
        for index, make in enumerate(makes):
            queries.append(dict(query_id=f'year_{year}_make_{index:03d}', zip_code=zip_code,
                location_filter=False, filters=dict(makes=[dict(name=make)], year=dict(min=year, max=year))))
    queries.append(dict(queries[0], query_id='broad_close'))
    return queries


def preview_facets(plan_path):
    """Read-only preflight. A matching hash is not a substitute for user approval."""
    plan_path = Path(plan_path).resolve()
    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    if plan['format'] != 'carvana-facet-experiment-v1':
        raise ValueError('Unknown facet plan format')
    source = (plan_path.parent/plan['basis_source']).resolve()
    basis_bytes = source.read_bytes()
    if hashlib.sha256(basis_bytes).hexdigest() != plan['basis_sha256']:
        raise ValueError('Facet basis source changed; no silent refresh')
    basis = json.loads(basis_bytes)
    if basis['request'] != build_search_request(filters={}, zip_code=plan['zip_code']):
        raise ValueError('Basis request differs from the proposed broad context')
    makes = sorted(basis['facet_data']['makes'])
    year = basis['facet_data']['year']
    years = list(range(year['min'], year['max']+1))
    if plan['makes'] != makes or plan['years'] != years:
        raise ValueError('Frozen makes/years differ from retained basis')
    queries = facet_queries(makes, years, plan['zip_code'])
    if (plan['queries'] != queries or plan['max_requests'] != len(queries)
            or not 1 <= plan['max_requests'] <= 800 or plan['max_seconds'] != 3600
            or plan['minimum_spacing_seconds'] != 3 or plan['location_filter'] is not False):
        raise ValueError('Require the exact facet grid, <=800 requests, 3600 seconds and 3-second spacing')
    config = cycle_config(queries, cycle_date=plan['cycle_date'], timezone_name=plan['timezone'],
        window_start=plan['window_start'], window_end=plan['window_end'],
        max_requests=plan['max_requests'], max_seconds=plan['max_seconds'])
    if (aware(plan['window_end'])-aware(plan['window_start'])).total_seconds() > 3600:
        raise ValueError('Facet window cannot exceed one hour')
    if not aware(plan['window_start']) <= aware(plan['latest_start']) < aware(plan['window_end']):
        raise ValueError('Latest start must be inside the frozen window')
    destination = (plan_path.parent/plan['destination']).resolve()
    now = utcnow()
    return dict(plan_path=str(plan_path), plan_sha256=hashlib.sha256(plan_bytes).hexdigest(),
        destination=str(destination), destination_fresh=not destination.exists(),
        start_eligible=aware(plan['window_start']) <= now <= aware(plan['latest_start']),
        query_count=len(queries), parent_queries=len(queries)-2,
        request_count_above_daily_600=max(0,len(queries)-600),
        spacing_floor_seconds=(len(queries)-1)*3, config=config,
        source=str(source), source_sha256=plan['basis_sha256'],
        purpose='Facet counts only; first-page samples are not enumerated inventory',
        live_authorization='Requires explicit authorization for this exact plan hash and destination'), plan


def _label(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9 .()/'&+\-]{1,100}", value):
        raise ValueError('Unsupported public facet label')
    return value


def _number(value):
    if type(value) is not int or value < 0:
        raise ValueError('Invalid native facet count or ID')
    return value


def _public_number(value):
    # Retain native missing/zero/negative counts before semantic validation.
    if value is None or type(value) in (int, bool) or (type(value) is float and math.isfinite(value)):
        return value
    raise ValueError('Unsupported public facet numeric representation')


def select_facets(data):
    """Retain only observed public make/model/year fields; other fields stay omitted."""
    facets = data['facetData']
    result = dict(makes={}, year={})
    for key in ['min', 'max', 'appliedMin', 'appliedMax']:
        if key in facets['year']:
            value = facets['year'][key]
            result['year'][key] = _public_number(value)
    for name, bucket in facets['makes'].items():
        _label(name)
        if type(bucket['isApplied']) is not bool or bucket['key'] != name:
            raise ValueError('Invalid native make label/application flag')
        children = []
        for child in bucket['parentModels']:
            ids = [_public_number(value) for value in child['modelIds']]
            if not ids or type(child['isApplied']) is not bool:
                raise ValueError('Invalid parent-model application flag')
            children.append(dict(key=_label(child['key']), count=_public_number(child['count']),
                                 modelIds=ids, isApplied=child['isApplied']))
        if len({child['key'] for child in children}) != len(children):
            raise ValueError('Duplicate native parent-model label')
        result['makes'][name] = dict(key=name, count=_public_number(bucket['count']),
                                    isApplied=bucket['isApplied'], parentModels=children)
    return result


def facet_diagnostic(capture, plan):
    """Single-response arithmetic. A residual or zero never certifies membership."""
    request, facets = capture['request'], capture['facet_data']
    total = capture['pagination']['totalMatchedInventory']
    for bucket in facets['makes'].values():
        _number(bucket['count'])
        for child in bucket['parentModels']:
            _number(child['count'])
            for value in child['modelIds']:
                _number(value)
    low, high = _number(facets['year']['min']), _number(facets['year']['max'])
    if low > high:
        raise ValueError('Reversed native year metadata')
    if capture['zip_code'] != request['zip5'] or request['zip5'] != plan['zip_code']:
        raise ValueError('Returned facet ZIP differs from requested context')
    row = dict(reported_total=total, make=None, year=None, model_count_sum=None,
               count_residual=None, overlapping_model_ids='', membership_reconciled=False)
    if not request['filters']:
        new = sorted(set(facets['makes'])-set(plan['makes']))
        missing = sorted(set(plan['makes'])-set(facets['makes']))
        row.update(new_native_makes=';'.join(new), basis_makes_not_in_response=';'.join(missing),
                   new_native_make_count=sum(facets['makes'][name]['count'] for name in new),
                   native_year_min=low, native_year_max=high,
                   year_boundary_changed=[low,high] != [plan['years'][0],plan['years'][-1]],
                   outside_year_grid_count=None)
        if any(value['isApplied'] for value in facets['makes'].values()):
            raise ValueError('Unexpected applied make in unfiltered response')
        row['model_count_sum'] = sum(value['count'] for value in facets['makes'].values())
    else:
        make = request['filters']['makes'][0]['name']
        year = request['filters']['year']['min']
        if (facets['year'].get('appliedMin') != year or facets['year'].get('appliedMax') != year):
            raise ValueError('Returned facet year application differs from request')
        bucket = facets['makes'].get(make)
        if any(name != make and b['isApplied'] for name,b in facets['makes'].items()):
            raise ValueError('Unexpected additional applied make')
        row.update(make=make, year=year, selected_make_present=bucket is not None)
        if bucket is None:
            if total != 0:
                raise ValueError('Positive query lacks its requested make facet')
        else:
            if bucket['isApplied'] is not True:
                raise ValueError('Requested make facet is not applied')
            row['selected_make_residual'] = total-bucket['count']
            children = bucket['parentModels']
            ids = [value for child in children for value in child['modelIds']]
            row['overlapping_model_ids'] = ','.join(map(str, sorted({value for value in ids if ids.count(value)>1})))
            if children:
                row['model_count_sum'] = sum(child['count'] for child in children)
    if row['model_count_sum'] is not None:
        row['count_residual'] = total-row['model_count_sum']
    return row


def collect_facets(plan_path, *, expected_sha256, post=None):
    """One fresh explicit invocation. No resume, retries, imports or new query discovery."""
    preview, plan = preview_facets(plan_path)
    if preview['plan_sha256'] != expected_sha256:
        raise ValueError('Plan hash differs from the explicitly selected scope')
    if not preview['start_eligible'] or not preview['destination_fresh']:
        raise ValueError('Require a fresh destination inside the exact approved window')
    selected = Path(plan_path).read_bytes()
    if hashlib.sha256(selected).hexdigest() != expected_sha256:
        raise ValueError('Plan changed after preview; no collection')
    folder = Path(preview['destination'])
    folder.mkdir(parents=True, exist_ok=False)
    with (folder/'selected_plan.json').open('xb') as stream:
        stream.write(selected)
        stream.flush()
        os.fsync(stream.fileno())
    state = dict(preview['config'], cycle_id=uuid4().hex, created_at=utcnow().isoformat(),
                 budget=dict(requests=0, stopped=False, pending_request=False, last_request_utc=None),
                 experiment_kind='facet_counts_only; not an inventory cycle')
    # Use the existing durable budget with a separate name so daily discovery cannot import it.
    write_json_atomic(folder/'facet_budget.json', state)
    write_json_atomic(folder/'facet_plan.json', plan)
    report = dict(plan_sha256=expected_sha256, source_plan=str(Path(plan_path).resolve()),
                  started_at=utcnow().isoformat(), status='running', requests=0, entries=[],
                  inventory_complete=False, national_coverage_verified=False,
                  retention='Selected public make/model/year facets plus selected inventory source; original response body may be unavailable',
                  code_hashes={p.name:digest(p) for p in sorted(Path(__file__).parent.glob('*.py'))})
    write_json_atomic(folder/'facet_report.json', report)
    identities, vins = {}, {}
    with cycle_lock(folder), search_transport(post) as send:
        budget = CycleBudget(folder/'facet_budget.json')
        for query in plan['queries']:
            entry = dict(query_id=query['query_id'], status='unattempted', request_started_at=None,
                         response_received_at=None, evidence_available_at=None)
            report['entries'].append(entry)
            request = build_search_request(filters=query['filters'], zip_code=query['zip_code'])
            stage = 'budget_or_storage_failure'
            try:
                budget.before_navigation()
                entry.update(status='reserved', request_reserved_at=utcnow().isoformat(), request=request)
                report['requests'] = budget.requests
                write_json_atomic(folder/'facet_report.json', report)
                timeout = budget.timeout_ms(30000)/1000
                stage = 'transport_failure'
                budget.request_started()
                entry['request_started_at'] = utcnow().isoformat()
                response = send(ENDPOINT, json=request, timeout=timeout, allow_redirects=False,
                    headers={'User-Agent':'researchOS personal inventory research','Accept':'application/json'})
                stamp = utcnow().isoformat()
                entry.update(response_received_at=stamp, http_status=response.status_code,
                    response_content_sha256=hashlib.sha256(response.content).hexdigest())
                stage = 'storage_failure'
                write_json_atomic(folder/'facet_report.json', report)
                evidence = retain_response_evidence(response, folder/'response_sources')
                entry['response_evidence'] = evidence
                write_json_atomic(folder/'facet_report.json', report)
                budget.response_received()  # The response outcome is now durable, even if parsing fails.
                stage = 'access_failure'
                if (response.status_code != 200 or response.headers.get('cf-mitigated') == 'challenge'
                        or 'application/json' not in response.headers.get('content-type','')):
                    raise CollectionStopped('HTTP/content/access stop; no retries')
                stage = 'schema_or_context_failure'
                if evidence['kind']=='not_retained' or evidence.get('redacted_values') or evidence.get('ambiguous_json'):
                    raise ValueError('Unusable public search source')
                source = verify_response_evidence(evidence)
                projection = project_response(source, request, observed_at=stamp)
                rows = parse_search_capture(projection)
                data = json.loads(response.content, object_pairs_hook=unique_object)
                capture = dict(capture_method='public_search_facets', request=request,
                    captured_at_utc=stamp, zip_code=projection['zip_code'],
                    facet_data=select_facets(data), pagination=projection['pagination'])
                stage = 'storage_failure'
                path = retain_capture(capture, folder/'raw')
                entry.update(source_path=str(path), source_sha256=digest(path))
                entry['evidence_available_at'] = utcnow().isoformat()
                write_json_atomic(folder/'facet_report.json', report)
                stage = 'schema_or_context_failure'
                entry['diagnostic'] = facet_diagnostic(capture, plan)
                stage = 'identity_failure'
                for row in rows.itertuples():
                    if ((row.listing_id in identities and identities[row.listing_id] != row.vin)
                            or (row.vin in vins and vins[row.vin] != row.listing_id)):
                        raise ValueError('Conflicting retained VIN/listing identities')
                    identities[row.listing_id], vins[row.vin] = row.vin, row.listing_id
                stage = 'time_budget_exhausted'
                budget.timeout_ms()
                entry['status'] = 'retained'
                write_json_atomic(folder/'facet_report.json', report)
            except BaseException as error:
                entry.update(status='failed', failure_stage=stage,
                    failed_at=utcnow().isoformat(),
                    reason=str(error) if isinstance(error, CollectionStopped) else type(error).__name__)
                report.update(status='stopped', requests=budget.requests, stopped_at=utcnow().isoformat())
                try:
                    budget.stop()
                finally:
                    write_json_atomic(folder/'facet_report.json', report)
                if not isinstance(error, Exception):
                    raise
                break
        report.update(status='retained_all_facets' if all(e['status']=='retained' for e in report['entries'])
                      and len(report['entries'])==len(plan['queries']) else 'stopped',
                      ended_at=utcnow().isoformat(), requests=budget.requests)
        write_json_atomic(folder/'facet_report.json', report)
    return report


def replay_facets(folder, *, as_of):
    """One row per planned request, including unattempted/failed/unavailable entries."""
    folder, cutoff = Path(folder), aware(as_of)
    plan_path, report_path = folder/'facet_plan.json', folder/'facet_report.json'
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    report = json.loads(report_path.read_text(encoding='utf-8'))
    # Exact selected bytes remain separate from the readable frozen plan copy.
    frozen = folder/'selected_plan.json'
    if digest(frozen) != report['plan_sha256'] or json.loads(frozen.read_text(encoding='utf-8')) != plan:
        raise ValueError('Retained facet plan differs from selected bytes')
    entries = {entry['query_id']: entry for entry in report['entries']}
    if len(entries) != len(report['entries']) or set(entries)-{q['query_id'] for q in plan['queries']}:
        raise ValueError('Facet report has duplicate or undeclared requests')
    budget = json.loads((folder/'facet_budget.json').read_text(encoding='utf-8'))
    if (budget['queries'] != plan['queries'] or budget['budget']['requests'] != report['requests']
            or sum(bool(e.get('request_reserved_at')) for e in entries.values()) > report['requests']):
        raise ValueError('Facet journal differs from its durable budget')
    if any(entry['status'] not in {'unattempted','reserved','retained','failed'} for entry in entries.values()):
        raise ValueError('Unknown facet request status')
    identities, vins = {}, {}
    result = []
    for query in plan['queries']:
        filters = query['filters']
        row = dict(query_id=query['query_id'], zip_code=query['zip_code'],
                   planned_make=filters.get('makes',[{}])[0].get('name'),
                   planned_year=filters.get('year',{}).get('min'),
                   status='unattempted', reported_total=None,
                   source_path=None, observed_at=None, available_at=None)
        entry = entries.get(query['query_id'])
        if entry:
            row['status'] = entry['status']
            available = entry.get('evidence_available_at') if entry['status']=='retained' else entry.get('failed_at')
            if not available or aware(available) > cutoff:
                row['status'] = 'evidence unavailable at cutoff'
            elif entry['status']=='retained':
                path = Path(entry['source_path'])
                if digest(path) != entry['source_sha256']:
                    raise ValueError('Facet source hash changed')
                capture = json.loads(path.read_text(encoding='utf-8'))
                if (capture['request'] != build_search_request(filters=query['filters'], zip_code=query['zip_code'])
                        or aware(capture['captured_at_utc']) > aware(available)
                        or aware(capture['captured_at_utc']) != aware(entry['response_received_at'])):
                    raise ValueError('Facet source request or clocks differ from its journal')
                clocks = [aware(plan['window_start']), aware(entry['request_reserved_at']),
                          aware(entry['request_started_at']), aware(entry['response_received_at']),
                          aware(available), aware(plan['window_end'])]
                if clocks != sorted(clocks):
                    raise ValueError('Facet clocks are out of order or outside the frozen window')
                diagnostic = facet_diagnostic(capture, plan)
                if diagnostic != entry['diagnostic']:
                    raise ValueError('Facet diagnostic differs from replayed source')
                source = verify_response_evidence(entry['response_evidence'])
                projection = project_response(source, capture['request'], observed_at=capture['captured_at_utc'])
                if projection['pagination'] != capture['pagination'] or projection['zip_code'] != capture['zip_code']:
                    raise ValueError('Facet pagination/context differs from retained inventory source')
                for observed in parse_search_capture(projection).itertuples():
                    if ((observed.listing_id in identities and identities[observed.listing_id] != observed.vin)
                            or (observed.vin in vins and vins[observed.vin] != observed.listing_id)):
                        raise ValueError('Conflicting replayed sample identities')
                    identities[observed.listing_id], vins[observed.vin] = observed.vin, observed.listing_id
                row.update(diagnostic, source_path=str(path), source_sha256=entry['source_sha256'],
                           request_started_at=entry['request_started_at'],
                           observed_at=capture['captured_at_utc'], available_at=available)
            if available and aware(available) <= cutoff:
                row.update(failure_stage=entry.get('failure_stage'), reason=entry.get('reason'))
        result.append(row)
    return pd.DataFrame(result)
