"""Offline year discovery plans; no transport, inventory import, or live authority.

The tails make the proposed ranges exhaustive over integer years. They do not
account for missing/noninteger years or establish endpoint support. In particular,
passing synthetic offline tests cannot validate one-sided live year filters.
"""
import copy
import hashlib
import json
from pathlib import Path
import re

from vehicle_tracker.cycles import aware
from vehicle_tracker.search import build_search_request
from vehicle_tracker.search_evidence import unique_object


def _capture(path, expected_sha256):
    path = Path(path).resolve()
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Retained year-discovery source hash changed')
    capture = json.loads(raw, object_pairs_hook=unique_object)
    clock = capture['captured_at_utc']
    if not isinstance(clock, str):
        raise ValueError('Require a retained aware observation clock')
    aware(clock)
    source = dict(source_path=str(path), source_sha256=expected_sha256,
                  observed_at_utc=clock)
    return capture, source


def _context(capture, filters, zip_code):
    if not isinstance(zip_code, str) or not re.fullmatch(r'\d{5}', zip_code):
        raise ValueError('Require an explicit five-digit ZIP')
    expected = build_search_request(filters=filters, zip_code=zip_code)
    # JSON comparison also rejects bool/float values that Python equates to ints.
    if (json.dumps(capture['request'], sort_keys=True) != json.dumps(expected, sort_keys=True)
            or capture['zip_code'] != zip_code):
        raise ValueError('Year-discovery request or returned ZIP differs from the selected context')
    page = capture['pagination']
    if (any(type(page[key]) is not int or page[key] < 0 for key in
            ['currentPage', 'pageSize', 'totalMatchedInventory', 'totalMatchedPages'])
            or page['currentPage'] != 1 or page['pageSize'] != 24
            or page['totalMatchedPages'] != (page['totalMatchedInventory']+23)//24):
        raise ValueError('Year discovery requires consistent native first-page pagination')
    bounds = filters.get('year', {})
    year = capture['facet_data']['year']
    if any(value is not None and type(value) is not int for value in
           [year.get('min'), year.get('max')]):
        raise ValueError('Invalid native year metadata')
    if year.get('min') is not None and year.get('max') is not None and year['min'] > year['max']:
        raise ValueError('Reversed native year metadata')
    for key, applied in [('min', 'appliedMin'), ('max', 'appliedMax')]:
        value = year.get(applied)
        if ((key in bounds and (type(value) is not int or value != bounds[key]))
                or (key not in bounds and value is not None)):
            raise ValueError('Native applied year bounds differ from the request')
    facets = capture['facet_data']
    makes = facets.get('makes')
    # Live empty year/tail pages may omit make facets entirely; retain that shape
    # without inventing zero categories. Positive pages still require make counts.
    if facets.get('makes_present') is False:
        if makes != {} or page['totalMatchedInventory'] != 0:
            raise ValueError('Omitted make facets are valid only for empty year-only inventory')
        return 0
    if not isinstance(makes, dict):
        raise ValueError('Native make facets must be a mapping')
    for name, bucket in makes.items():
        if (not isinstance(name, str) or not name.strip() or name != bucket['key']
                or bucket['isApplied'] is not False
                or type(bucket['count']) is not int or bucket['count'] < 0):
            raise ValueError('Invalid native year-only make category')
        children = bucket['parentModels']
        if not isinstance(children, list) or any(c['isApplied'] is not False for c in children):
            raise ValueError('Unexpected native model application in year-only discovery')
        for child in children:
            if (not isinstance(child['key'], str) or not child['key'].strip()
                    or type(child['count']) is not int or child['count'] < 0
                    or not isinstance(child['modelIds'], list) or not child['modelIds']
                    or any(type(value) is not int or value < 0 for value in child['modelIds'])):
                raise ValueError('Invalid native year-only model category')
    if sum(bucket['count'] for bucket in makes.values()) != page['totalMatchedInventory']:
        raise ValueError('Native year-only make counts leave an unresolved residual')
    return page['totalMatchedInventory']


def plan_year_partitions(basis_path, *, expected_sha256, zip_code, max_partitions=6000):
    """Plan disjoint integer-year ranges from a reviewed unfiltered first page.

    Native extrema choose convenient exact-year cells, not universe boundaries.
    Both open tails are always planned, including when no outlier is sampled.
    Their request contract remains unverified until real retained responses are
    separately reviewed; this function makes no requests and writes no files.
    """
    capture, source = _capture(basis_path, expected_sha256)
    total = _context(capture, {}, zip_code)
    year = capture['facet_data']['year']
    low, high = year['min'], year['max']
    if type(low) is not int or type(high) is not int or low > high:
        raise ValueError('Require ordered integer native year extrema')
    if (type(max_partitions) is not int or not 3 <= max_partitions <= 6000
            or high-low+3 > max_partitions):
        raise ValueError('Year discovery alone exceeds the bounded partition allowance')

    def partition(identity, kind, bounds):
        return dict(query_id=identity, kind=kind, zip_code=zip_code, location_filter=False,
                    filters={'year': bounds}, native_year_min=low, native_year_max=high,
                    basis_source=copy.deepcopy(source), mandatory=True,
                    endpoint_contract_verified=False)

    queries = [partition('year_lower_tail', 'lower_tail', {'max': low-1})]
    queries.extend(partition(f'year_{value}', 'exact_year', {'min': value, 'max': value})
                   for value in range(low, high+1))
    queries.append(partition('year_upper_tail', 'upper_tail', {'min': high+1}))
    return dict(format='carvana-offline-year-partition-plan-v1', basis_source=source,
                native_year_range={'min': low, 'max': high}, broad_native_count=total,
                partitions=queries, minimum_discovery_requests=len(queries),
                integer_year_ranges_exhaustive=True, inventory_coverage_complete=False,
                missing_or_noninteger_year_count=None, endpoint_contract_verified=False,
                endpoint_run_page_provenance_bound=False,
                limitation='Integer-year partition only. Missing years, temporal drift and endpoint support remain unverified. Source bytes/context do not bind endpoint/run/page provenance; authoritative journal reconciliation is required.')


def _partition_bounds(partition):
    low, high = partition['native_year_min'], partition['native_year_max']
    if type(low) is not int or type(high) is not int or low > high:
        raise ValueError('Invalid retained native year range')
    if (partition['location_filter'] is not False or partition['mandatory'] is not True
            or set(partition['filters']) != {'year'}):
        raise ValueError('Require a mandatory year-only partition without location filtering')
    bounds = partition['filters']['year']
    if (not isinstance(bounds, dict) or not bounds or set(bounds)-{'min', 'max'}
            or any(type(value) is not int for value in bounds.values())):
        raise ValueError('Invalid proposed year bounds')
    kind = partition['kind']
    if kind == 'lower_tail':
        valid = bounds == {'max': low-1}
    elif kind == 'upper_tail':
        valid = bounds == {'min': high+1}
    else:
        valid = (kind == 'exact_year' and set(bounds) == {'min', 'max'}
                 and low <= bounds['min'] == bounds['max'] <= high)
    if not valid:
        raise ValueError('Partition bounds do not match the declared exact year or open tail')
    basis = partition['basis_source']
    derived = plan_year_partitions(basis['source_path'], expected_sha256=basis['source_sha256'],
                                   zip_code=partition['zip_code'])
    if not any(json.dumps(partition, sort_keys=True) == json.dumps(item, sort_keys=True)
               for item in derived['partitions']):
        raise ValueError('Partition identity, bounds or metadata differ from the reviewed basis')
    return copy.deepcopy(bounds)


def year_make_candidates(facet_path, *, expected_sha256, partition):
    """Validate one retained year-only response and describe its native categories.

    Candidates are queries to consider, never inventory reports. Zero categories
    remain explicit source observations. Parent-model children are not inferred.
    Positive tails keep their one-sided year filter mandatory: all declared
    positive make/model children must cover it without narrower year clipping.
    This does not force enumeration of the unfiltered tail as a single query.
    Matching
    a supplied capture validates its context, not that a live request occurred;
    synthetic fixtures cannot certify endpoint support or complete inventory.
    Facet wrappers do not bind endpoint/run/page provenance by themselves. Future
    live integration must reconcile the authoritative parent and page journals.
    """
    bounds = _partition_bounds(partition)
    capture, source = _capture(facet_path, expected_sha256)
    total = _context(capture, {'year': bounds}, partition['zip_code'])
    candidates, zeros = [], []
    for index, (make, bucket) in enumerate(sorted(capture['facet_data']['makes'].items())):
        record = dict(make=make, native_count=bucket['count'], source=copy.deepcopy(source),
                      filters={'makes': [{'name': make}], 'year': copy.deepcopy(bounds)},
                      zip_code=partition['zip_code'], location_filter=False,
                      query_id=partition['query_id']+f'_make_{index:03d}',
                      inventory_report=False)
        (candidates if bucket['count'] else zeros).append(record)
    tail = partition['kind'] in {'lower_tail', 'upper_tail'}
    return dict(format='carvana-offline-year-make-candidates-v1',
                partition=copy.deepcopy(partition), source=source,
                observed_year_metadata=copy.deepcopy(capture['facet_data']['year']),
                reported_total=total, native_make_count_sum=sum(c['native_count'] for c in candidates),
                candidates=candidates, native_zero_categories=zeros,
                mandatory_tail_year_context=copy.deepcopy(partition) if tail and total > 0 else None,
                retained_context_validated=True, endpoint_contract_verified=False,
                endpoint_run_page_provenance_bound=False,
                inventory_coverage_complete=False, missing_or_noninteger_year_count=None,
                limitation='Matched source bytes and context only; endpoint/run/page provenance requires authoritative journal reconciliation. Counts do not prove membership or account for missing years.')
