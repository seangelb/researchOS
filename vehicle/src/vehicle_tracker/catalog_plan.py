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
from vehicle_tracker.catalog_config import query


def native_makes(capture, *, allow_residual=False):
    """Fail closed on an unaccounted broad category; never silently drop it.

    v3 records a facet-sum residual instead of stopping. The returned names stay
    a plain list so existing callers are unchanged when the residual is zero.
    """
    _validate_discovery(capture)
    buckets = capture['facet_data']['makes']
    if capture['request']['filters'] or not buckets:
        raise ValueError('Require nonempty unfiltered make facets')
    for name, bucket in buckets.items():
        if (name != bucket['key'] or bucket['isApplied'] is not False
                or type(bucket['count']) is not int or bucket['count'] < 0):
            raise ValueError('Invalid broad native make count/application')
    residual = sum(b['count'] for b in buckets.values()) - capture['pagination']['totalMatchedInventory']
    if residual != 0 and not allow_residual:
        raise ValueError('Broad make counts leave an unresolved inventory residual')
    names = sorted(buckets)
    if allow_residual:
        return names, residual
    return names


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


def _model_id_overlap(children, total):
    """Describe shared model-id clusters when their count excess fits the overlap.

    Capacity for each shared id is the minimum count among models that carry it.
    A negative excess means cars sit outside the model list; keep the whole make.
    """
    parents = {child['key']: set(child['modelIds']) for child in children}
    names = sorted(parents)
    root = {name: name for name in names}

    def find(name):
        while root[name] != name:
            root[name] = root[root[name]]
            name = root[name]
        return name

    for left in names:
        for right in names:
            if left < right and parents[left] & parents[right]:
                root[find(left)] = find(right)
    groups = {}
    for name in names:
        groups.setdefault(find(name), []).append(name)
    clusters, shared_ids, capacity = [], [], 0
    for members in sorted(groups.values(), key=lambda items: items[0]):
        if len(members) < 2:
            continue
        ids = sorted({value for name in members for value in parents[name]
                      if sum(value in parents[other] for other in members) > 1})
        shared_ids.extend(ids)
        for value in ids:
            carriers = [next(c['count'] for c in children if c['key'] == name)
                        for name in members if value in parents[name]]
            capacity += min(carriers)
        clusters.append(dict(model_ids=ids, models=members))
    if not clusters:
        return None
    model_sum = sum(child['count'] for child in children)
    excess = model_sum - total
    if excess < 0 or excess > capacity:
        return None
    return dict(shared_model_ids=sorted(set(shared_ids)), clusters=clusters,
                model_count_sum=model_sum, inventory_total=total, count_excess=excess)


def model_partitions(capture, make, zip_code, prefix, *, year_bounds=None):
    """Split into model leaves when counts/IDs partition or an overlap excess fits.

    An all-year whole-make fallback preserves unknown models and year boundaries.
    Its pagination may fail; that failure stays visible, never repaired by deduping.
    The third return value is the overlap record, or None.
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
        return whole, 'empty make', None
    if bucket['isApplied'] is not True:
        raise ValueError('Requested make application differs from response')
    # Facet make counts and inventory totals can disagree by a small residual on the
    # same response clock. Keep the residual visible and collect the whole make
    # instead of treating ordinary Carvana skew as a hard stop.
    if bucket['count'] != total:
        return whole, 'make facet count differs from inventory total; collect whole make', None
    children = bucket['parentModels']
    ids = [value for child in children for value in child['modelIds']]
    shaped = (bool(children) and all(type(c['count']) is int and c['count'] >= 0
               and c['isApplied'] is False and c['modelIds'] for c in children)
              and all(type(value) is int and value >= 0 for value in ids))
    if not shaped:
        return whole, 'model counts/IDs do not partition make; collect whole make', None
    leaves = [query(prefix+f'_model_{i:03d}', zip_code,
        dict(filters, makes=[{'name': make, 'parentModels': [{'name': child['key']}]}]))
        for i, child in enumerate(sorted(children, key=lambda c: c['key']))]
    if len(ids) == len(set(ids)) and sum(c['count'] for c in children) == total:
        return leaves, 'native model partition', None
    overlap = _model_id_overlap(children, total)
    if overlap is not None:
        return leaves, 'overlapping model ids; each model collected', overlap
    return whole, 'model counts/IDs do not partition make; collect whole make', None


def _feasibility(report, config, *, leaf_counts, already_collected, requests, max_requests,
                 seconds_remaining, spacing):
    """Estimate the request floor of the frozen leaf plan before enumerating it.

    Page counts come from the native category counts this discovery observed, so
    the estimate is a lower bound: it excludes later count drift, failed pages
    and any query whose native count is unknown. Declared geographic checks and
    the closing broad read are counted because the plan commits to them.
    """
    pages, unknown = 0, []
    for leaf in report['leaf_queries']:
        if leaf['query_id'] in already_collected:
            continue  # A complete probe is reused, never charged a second time.
        count = leaf_counts.get(leaf['query_id'])
        if count is None:
            unknown.append(leaf['query_id'])
            pages += 1
        else:
            pages += max(1, (count + 23)//24)
    overhead = len(config['validation_zips'])*(1 + config['validation_queries_per_zip']) + 1
    remaining_requests = pages + overhead
    retry_reserve = min(200, max(1, (pages + 19) // 20)) if pages else 0
    page_retries = config.get('page_retries', 2)
    minimum = requests + remaining_requests
    reserved = minimum + retry_reserve
    return dict(declared_leaf_queries=len(report['leaf_queries']),
        leaf_native_count_total=sum(count for count in leaf_counts.values() if count is not None),
        leaf_queries_without_native_count=unknown,
        estimated_leaf_pages=pages, declared_overhead_requests=overhead,
        requests_used=requests, estimated_minimum_requests=minimum, request_ceiling=max_requests,
        page_retries=page_retries, retry_reserve_requests=retry_reserve,
        estimated_minimum_seconds=remaining_requests*spacing, seconds_remaining=seconds_remaining,
        requests_fit=reserved <= max_requests,
        pacing_fits=remaining_requests*spacing <= seconds_remaining,
        fits_remaining_allowance=(reserved <= max_requests
                                  and remaining_requests*spacing <= seconds_remaining),
        basis=('Lower bound from native counts observed during this discovery plus a reserved '
               'retry allowance. It excludes later drift and does not promise a complete population.'))


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
    planned.extend((q, 'primary_inventory_retry') for q in report.get('leaf_retry_attempts', []))
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


def page_count(total):
    if type(total) is not int or total < 0:
        return 1
    return max(1, (total + 23) // 24)


def adaptive_cell(bucket, *, make, query_id, year_bounds, zip_code, threshold):
    """Choose a whole make/year leaf, or a probe plus model leaves.

    Year responses omit model children. A cell above the threshold is probed
    first; once model counts exist, a real partition becomes model leaves and
    anything else stays one make/year query. Small cells are enumerated directly.
    """
    filters = {'makes': [{'name': make}], 'year': dict(year_bounds)}
    whole = query(query_id, zip_code, filters)
    count = bucket['count']
    children = [child for child in (bucket.get('parentModels') or []) if child.get('count', 0) >= 0]
    usable = (count > threshold and children
              and all(type(child.get('count')) is int and child.get('key') and child.get('modelIds')
                      for child in children))
    if not usable:
        kind = 'probe' if count > threshold else 'whole'
        leaf = whole if kind == 'whole' else query(query_id + '_all', zip_code, filters)
        return dict(kind=kind, probe=None if kind == 'whole' else whole, leaves=[leaf] if kind == 'whole' else [],
                    fallback=leaf, requests=(page_count(count) if kind == 'whole' else 1 + page_count(count)),
                    native_count=count, make=make, query_id=query_id)
    ordered = sorted(children, key=lambda child: child['key'])
    leaves = [query(f'{query_id}_model_{i:03d}', zip_code, dict(
        filters, makes=[{'name': make, 'parentModels': [{'name': child['key']}]}]))
        for i, child in enumerate(ordered)]
    ids = [value for child in ordered for value in child['modelIds']]
    if len(ids) == len(set(ids)) and sum(child['count'] for child in ordered) == count:
        reason = 'native model partition'
    else:
        overlap = _model_id_overlap(ordered, count)
        if overlap is None:
            leaf = query(query_id + '_all', zip_code, filters)
            return dict(kind='whole', probe=whole, leaves=[leaf], fallback=leaf,
                        requests=1 + page_count(count), native_count=count, make=make,
                        query_id=query_id, reason='model counts do not partition; collect whole make')
        reason = 'overlapping model ids'
    return dict(kind='split', probe=whole, leaves=leaves, fallback=None,
                requests=1 + sum(page_count(child['count']) for child in ordered),
                native_count=count, make=make, query_id=query_id, reason=reason)


def adaptive_cells_from_capture(capture, partition, *, threshold):
    """Positive make/year cells from one year response, in candidate order."""
    bounds = partition['filters']['year']
    cells = []
    for index, (make, bucket) in enumerate(sorted(capture['facet_data']['makes'].items())):
        if not bucket.get('count'):
            continue
        cells.append(adaptive_cell(bucket, make=make, query_id=partition['query_id'] + f'_make_{index:03d}',
                                   year_bounds=bounds, zip_code=partition['zip_code'], threshold=threshold))
    return cells


def adaptive_estimate(cells):
    """Requests still to make for these cells, excluding discovery already spent."""
    return sum(cell['requests'] for cell in cells)


def adaptive_feasibility(config, *, enumeration_requests, requests_used, seconds_remaining, spacing, max_requests):
    overhead = len(config['validation_zips']) * (1 + config['validation_queries_per_zip']) + 1
    remaining = enumeration_requests + overhead
    retry_reserve = min(200, max(1, (enumeration_requests + 19) // 20)) if enumeration_requests else 0
    minimum = requests_used + remaining
    reserved = minimum + retry_reserve
    return dict(estimated_leaf_pages=enumeration_requests, declared_overhead_requests=overhead,
                requests_used=requests_used, estimated_minimum_requests=minimum, request_ceiling=max_requests,
                page_retries=config.get('page_retries', 2), retry_reserve_requests=retry_reserve,
                estimated_minimum_seconds=remaining * spacing, seconds_remaining=seconds_remaining,
                requests_fit=reserved <= max_requests, pacing_fits=remaining * spacing <= seconds_remaining,
                fits_remaining_allowance=reserved <= max_requests and remaining * spacing <= seconds_remaining,
                basis=('Lower bound from year-level make counts. Cells above the split threshold include one '
                       'probe plus the whole-cell pages; a real model split can cost more and is checked again.'))


