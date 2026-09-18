"""Retrospective facet/plan ledger. Counts are diagnostics, never enumeration."""
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

import pandas as pd


def _clock(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError('Partition review clocks must be known and timezone-aware')
    return stamp


def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError('Native facet counts must be nonnegative integers')
    return value


def _shape(query):
    """Only the demonstrated single make/model/exact-year partition vocabulary."""
    filters = query['filters']
    makes, year = filters.get('makes', []), filters.get('year', {})
    if (set(filters) != {'makes', 'year'} or len(makes) != 1
            or set(makes[0]) - {'name', 'parentModels'} or set(year) != {'min', 'max'}
            or type(year['min']) is not int or year['min'] != year['max']):
        raise ValueError('Unsupported partition filters; review without guessing a relationship')
    make, models = makes[0]['name'], makes[0].get('parentModels', [])
    if (not isinstance(make, str) or not make or len(models) > 1
            or any(set(m) != {'name'} or not isinstance(m['name'], str) or not m['name'] for m in models)
            or not isinstance(query['zip_code'], str) or len(query['zip_code']) != 5
            or not query['zip_code'].isdigit() or type(query.get('location_filter', False)) is not bool):
        raise ValueError('Unsupported partition make/model/context')
    return make, year['min'], models[0]['name'] if models else None


def _selection(queries, counts, total, overlaps):
    """Do not add a parent to its children, or sum overlapping model families."""
    models = [model for _, model in queries]
    omitted = (None if counts is None else ';'.join(sorted(set(counts)-set(models)))
               if None not in models else '')
    unmeasured = ';'.join(sorted(m for m in set(models) if m is not None
                                and (counts is None or m not in counts)))
    if not models:
        return dict(mode='not selected', reported_count=None, residual=None, page_floor=None,
                    omitted_models=omitted, unmeasured_models=unmeasured)
    if len(models) != len(set(models)) or (None in models and len(models) > 1):
        mode, value = 'overlapping declared queries', None
    elif models == [None]:
        mode, value = 'whole make/year', total
    else:
        mode = 'model children'
        value = (sum(counts[m] for m in models) if counts is not None
                 and not overlaps and all(m in counts for m in models) else None)
    pages = None
    if value is not None:
        pages = (max(1, math.ceil(value / 24)) if models == [None] else
                 sum(max(1, math.ceil(counts[m] / 24)) for m in models))
    return dict(mode=mode, reported_count=value,
                residual=total-value if total is not None and value is not None else None,
                page_floor=pages, omitted_models=omitted, unmeasured_models=unmeasured)


def load_partition_review(review_path, *, as_of):
    """Replay a frozen offline review; return source, parent, query tables and inputs.

    Old facet projections lack source availability clocks. The review's own
    publication clock gates this retrospective diagnostic; it does not backdate
    source availability or admit these counts into inventory/forecast history.
    """
    review_path = Path(review_path).resolve()
    review = json.loads(review_path.read_text(encoding='utf-8'))
    available, cutoff = _clock(review['available_at']), _clock(as_of)
    inputs = {review_path}
    if available > cutoff:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), inputs

    expected = {(review_path.parent / p).resolve(): h for p, h in review['input_hashes'].items()}

    def read(path):
        path = Path(path).resolve()
        content = path.read_bytes()
        if expected.get(path) != hashlib.sha256(content).hexdigest():
            raise ValueError(f'Partition review input hash mismatch: {path}')
        inputs.add(path)
        return json.loads(content)

    candidate = read(review_path.parent / review['candidate_plan'])
    panel = read(review_path.parent / review['panel_plan'])
    grouped, query_rows, contexts = defaultdict(lambda: defaultdict(list)), [], set()
    for role, plan in [('candidate', candidate), ('panel', panel)]:
        ids = set()
        for query in plan['queries']:
            if query['query_id'] in ids:
                raise ValueError('Duplicate query ID in partition plan')
            ids.add(query['query_id'])
            make, year, model = _shape(query)
            contexts.add((query['zip_code'], query.get('location_filter', False)))
            grouped[make, year][role].append((query['query_id'], model))
            query_rows.append(dict(plan=role, query_id=query['query_id'], make=make,
                                   year=year, model=model))
    if len(contexts) != 1:
        raise ValueError('Partition plans must use the same single ZIP/location context')
    zip_code, location_filter = contexts.pop()
    if location_filter:
        raise ValueError('This facet review requires location prefiltering omitted')

    sources, parents, root_makes, root_year = [], {}, {}, {}
    paths = [candidate['facet_source'], *candidate['model_facet_sources']]
    if len(paths) != len(set(paths)):
        raise ValueError('Duplicate facet source in review')
    for index, path in enumerate(paths):
        # Candidate paths are source references, not live query instructions.
        path = (review_path.parent / path).resolve()
        data = read(path)
        observed = _clock(data['captured_at_utc'])
        if observed > available:
            raise ValueError('Facet observation is later than this review')
        request, facets = data['request'], data['facet_data']
        if (data['capture_method'] != 'public_search_facets' or request.get('zip5') != zip_code
                or request.get('sortBy') != 'MostPopular'
                or set(request) != {'filters', 'pagination', 'sortBy', 'zip5'}
                or request['pagination'] != {'page': 1, 'pageSize': 24}
                or data['pagination']['currentPage'] != 1
                or data['pagination']['pageSize'] != 24):
            raise ValueError('Facet source context differs from reviewed plan')
        total = _count(data['pagination']['totalMatchedInventory'])
        row = dict(source_path=str(path), source_sha256=expected[path],
                   observed_at=observed.isoformat(), source_available_at=None,
                   review_available_at=available.isoformat(), zip_code=zip_code,
                   location_filter=False, reported_total=total, make=None, year=None,
                   native_year_min=facets['year']['min'], native_year_max=facets['year']['max'],
                   category_meaning='Overlapping native labels; retail ownership unverified',
                   membership_reconciled=False)
        if index == 0:
            if request['filters']:
                raise ValueError('Broad facet source must be unfiltered')
            root_makes = {name: _count(bucket['count']) for name, bucket in facets['makes'].items()}
            root_year = facets['year']
            row.update(comparison='broad total minus make buckets',
                       child_count_sum=sum(root_makes.values()), overlapping_model_ids='')
        else:
            make, year, model = _shape(dict(filters=request['filters'], zip_code=zip_code))
            if model is not None or (make, year) in parents:
                raise ValueError('Expected one unique make/year parent facet per source')
            selected = facets['makes'].get(make)
            if (not selected or selected.get('isApplied') is not True
                    or facets['year'].get('appliedMin') != year
                    or facets['year'].get('appliedMax') != year):
                raise ValueError('Facet applied make/year differs from request')
            buckets = selected['parentModels']
            counts = {b['key']: _count(b['count']) for b in buckets}
            if len(counts) != len(buckets) or any(not name for name in counts):
                raise ValueError('Duplicate or missing native parent-model name')
            model_ids = [v for b in buckets for v in b['modelIds']]
            if any(type(v) is not int for v in model_ids) or any(not b['modelIds'] for b in buckets):
                raise ValueError('Native model family membership is missing')
            overlaps = sorted(v for v, n in Counter(model_ids).items() if n > 1)
            # Other make counts in this response are disjunctive facets, not children.
            row.update(make=make, year=year, comparison='make/year total minus parent-model buckets',
                       selected_make_count=_count(selected['count']),
                       selected_make_residual=total-_count(selected['count']),
                       child_count_sum=sum(counts.values()) if counts else None,
                       overlapping_model_ids=','.join(map(str, overlaps)))
            parents[make, year] = dict(counts=counts, overlaps=overlaps, total=total,
                                      source=str(path), observed_at=observed.isoformat())
        row['count_residual'] = total-row['child_count_sum'] if row['child_count_sum'] is not None else None
        row['interpretation'] = 'Same-response arithmetic only; unknown categories and membership remain unverified'
        sources.append(row)

    # Native year min/max are filter metadata, not measured year-bucket boundaries.
    low, high = root_year['min'], root_year['max']
    if type(low) is not int or type(high) is not int or not 1900 <= low <= high <= 2200:
        raise ValueError('Invalid native year filter metadata')
    universe = {(make, year) for make in root_makes for year in range(low, high+1)}
    records = []
    for make, year in sorted(universe | set(grouped) | set(parents)):
        source = parents.get((make, year), {})
        row = dict(make=make, year=year, within_native_filter_grid=(make, year) in universe,
                   broad_make_count=root_makes.get(make), parent_reported_total=source.get('total'),
                   source_path=source.get('source'), observed_at=source.get('observed_at'),
                   review_available_at=available.isoformat(), source_available_at=None,
                   unknown_category_count=None, missing_year_count=None,
                   membership_reconciled=False,
                   facet_status='retained parent facet' if source else 'parent facet unmeasured')
        for role in ['candidate', 'panel']:
            queries = grouped[make, year].get(role, [])
            selected = _selection(queries, source.get('counts'), source.get('total'), source.get('overlaps'))
            row.update({role+'_'+key: value for key, value in selected.items()})
            row[role+'_query_count'] = len(queries)
        records.append(row)
    for row in query_rows:
        make, year, model = row['make'], row['year'], row['model']
        source = parents.get((make, year), {})
        counts = source.get('counts', {})
        peers = grouped[make, year].get('candidate', [])
        exact = [qid for qid, m in peers if m == model]
        broad = [qid for qid, m in peers if m is None and model is not None]
        row.update(reported_count=source.get('total') if model is None else counts.get(model),
                   source_path=source.get('source'), observed_at=source.get('observed_at'),
                   review_available_at=available.isoformat(), source_available_at=None,
                   candidate_matches=';'.join(exact or broad),
                   candidate_relationship='exact' if exact else 'model subset of make/year parent' if broad else 'unmapped')
    if inputs - {review_path.resolve()} != set(expected):
        raise ValueError('Partition review hash manifest has missing or unused inputs')
    return pd.DataFrame(sources), pd.DataFrame(records), pd.DataFrame(query_rows), inputs
