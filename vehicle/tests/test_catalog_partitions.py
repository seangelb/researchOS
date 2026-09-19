"""Synthetic planner tests do not certify live year-filter endpoint support."""
import copy
import hashlib
import json

import pytest

from vehicle_tracker.catalog_partitions import plan_year_partitions, year_make_candidates
from vehicle_tracker.search import build_search_request


def retain(tmp_path, capture, name='source.json'):
    path = tmp_path/name
    path.write_text(json.dumps(capture), encoding='utf-8')
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def capture(filters=None, counts=None):
    filters = {} if filters is None else copy.deepcopy(filters)
    counts = {'Audi': 7, 'Tesla': 0} if counts is None else counts
    year = {'min': 2010, 'max': 2012}
    year.update({'applied'+key.title(): value for key, value in filters.get('year', {}).items()})
    total = sum(counts.values())
    return dict(request=build_search_request(filters=filters, zip_code='08542'),
                captured_at_utc='2026-09-19T13:00:00.123456+00:00', zip_code='08542',
                pagination=dict(currentPage=1, pageSize=24, totalMatchedInventory=total,
                                totalMatchedPages=(total+23)//24),
                facet_data=dict(year=year, makes={name: dict(key=name, count=count,
                    isApplied=False, parentModels=[]) for name, count in counts.items()}))


@pytest.fixture
def plan(tmp_path):
    path, sha = retain(tmp_path, capture(), 'basis.json')
    return plan_year_partitions(path, expected_sha256=sha, zip_code='08542')


def test_tails_and_exact_years_cover_every_integer_once_without_native_boundary_assumption(plan):
    queries = plan['partitions']
    assert [q['filters']['year'] for q in queries] == [
        {'max': 2009}, {'min': 2010, 'max': 2010}, {'min': 2011, 'max': 2011},
        {'min': 2012, 'max': 2012}, {'min': 2013}]
    for year in [-10**9, 0, 2005, 2009, 2010, 2011, 2012, 2013, 9999, 10**9]:
        matches = [q for q in queries if
            ('min' not in q['filters']['year'] or year >= q['filters']['year']['min']) and
            ('max' not in q['filters']['year'] or year <= q['filters']['year']['max'])]
        assert len(matches) == 1
    assert plan['minimum_discovery_requests'] == 5
    assert all(q['mandatory'] and not q['endpoint_contract_verified'] for q in queries)
    assert plan['integer_year_ranges_exhaustive'] and not plan['inventory_coverage_complete']
    assert not plan['endpoint_run_page_provenance_bound']
    assert plan['missing_or_noninteger_year_count'] is None


@pytest.mark.parametrize('index', [0, 1, 4])
def test_valid_context_preserves_candidates_zeros_clocks_and_positive_unsplit_tails(tmp_path, plan, index):
    part = plan['partitions'][index]
    data = capture(part['filters'])
    path, sha = retain(tmp_path, data)
    before = path.read_bytes(), copy.deepcopy(part)
    result = year_make_candidates(path, expected_sha256=sha, partition=part)
    assert path.read_bytes() == before[0] and part == before[1]
    assert result['source'] == dict(source_path=str(path), source_sha256=sha,
        observed_at_utc=data['captured_at_utc'])
    assert result['observed_year_metadata'] == data['facet_data']['year']
    assert result['reported_total'] == result['native_make_count_sum'] == 7
    assert [(r['make'], r['native_count']) for r in result['candidates']] == [('Audi', 7)]
    assert [(r['make'], r['native_count']) for r in result['native_zero_categories']] == [('Tesla', 0)]
    assert not result['native_zero_categories'][0]['inventory_report']
    assert result['candidates'][0]['filters'] == {'makes': [{'name': 'Audi'}], 'year': part['filters']['year']}
    assert result['mandatory_unsplit_context'] == (part if index in [0, 4] else None)
    assert result['retained_context_validated'] and not result['endpoint_contract_verified']
    assert not result['endpoint_run_page_provenance_bound']
    assert result['missing_or_noninteger_year_count'] is None


def test_empty_year_keeps_native_zeros_without_manufacturing_inventory(tmp_path, plan):
    part = plan['partitions'][0]
    path, sha = retain(tmp_path, capture(part['filters'], {'Audi': 0, 'Tesla': 0}))
    result = year_make_candidates(path, expected_sha256=sha, partition=part)
    assert result['reported_total'] == 0 and result['candidates'] == []
    assert len(result['native_zero_categories']) == 2
    assert result['mandatory_unsplit_context'] is None
    assert result['partition']['mandatory'] and not result['inventory_coverage_complete']


@pytest.mark.parametrize('problem', ['zip', 'returned_zip', 'page', 'returned_page', 'size', 'sort',
    'location', 'extra_filter', 'missing_bound', 'extra_bound', 'wrong_bound', 'bool_bound',
    'negative_count', 'bool_count', 'float_count', 'make_applied', 'make_key', 'model_applied',
    'residual', 'page_count', 'missing_clock', 'naive_clock', 'bool_page', 'float_size', 'float_requested_bound'])
def test_invalid_retained_context_fails_closed(tmp_path, plan, problem):
    part = plan['partitions'][0]
    data = capture(part['filters'])
    if problem == 'zip': data['request']['zip5'] = '98101'
    elif problem == 'returned_zip': data['zip_code'] = '98101'
    elif problem == 'page': data['request']['pagination']['page'] = 2
    elif problem == 'returned_page': data['pagination']['currentPage'] = 2
    elif problem == 'size': data['request']['pagination']['pageSize'] = 48
    elif problem == 'sort': data['request']['sortBy'] = 'LowestPrice'
    elif problem == 'location': data['request']['requestedFeatures'] = ['LocationBasedPrefiltering']
    elif problem == 'extra_filter': data['request']['filters']['makes'] = [{'name': 'Audi'}]
    elif problem == 'missing_bound': del data['facet_data']['year']['appliedMax']
    elif problem == 'extra_bound': data['facet_data']['year']['appliedMin'] = 2010
    elif problem == 'wrong_bound': data['facet_data']['year']['appliedMax'] = 2010
    elif problem == 'bool_bound': data['facet_data']['year']['appliedMax'] = True
    elif problem in ['negative_count', 'bool_count', 'float_count']:
        data['facet_data']['makes']['Audi']['count'] = {'negative_count': -1, 'bool_count': True, 'float_count': 7.0}[problem]
    elif problem == 'make_applied': data['facet_data']['makes']['Audi']['isApplied'] = True
    elif problem == 'make_key': data['facet_data']['makes']['Audi']['key'] = 'BMW'
    elif problem == 'model_applied': data['facet_data']['makes']['Audi']['parentModels'] = [{'isApplied': True}]
    elif problem == 'residual': data['facet_data']['makes']['Audi']['count'] = 6
    elif problem == 'page_count': data['pagination']['totalMatchedPages'] = 2
    elif problem == 'missing_clock': data['captured_at_utc'] = None
    elif problem == 'naive_clock': data['captured_at_utc'] = '2026-09-19T13:00:00'
    elif problem == 'bool_page': data['request']['pagination']['page'] = True
    elif problem == 'float_size': data['request']['pagination']['pageSize'] = 24.0
    elif problem == 'float_requested_bound': data['request']['filters']['year']['max'] = 2009.0
    path, sha = retain(tmp_path, data)
    with pytest.raises((ValueError, TypeError)):
        year_make_candidates(path, expected_sha256=sha, partition=part)


def test_changed_source_rejected_and_plan_is_bounded(tmp_path):
    path, sha = retain(tmp_path, capture())
    with pytest.raises(ValueError, match='hash changed'):
        plan_year_partitions(path, expected_sha256='0'*64, zip_code='08542')
    with pytest.raises(ValueError, match='bounded partition allowance'):
        plan_year_partitions(path, expected_sha256=sha, zip_code='08542', max_partitions=4)
    data = capture(); data['facet_data']['year']['min'] = True
    path, sha = retain(tmp_path, data)
    with pytest.raises(ValueError, match='native year'):
        plan_year_partitions(path, expected_sha256=sha, zip_code='08542')


def test_broad_basis_must_be_unfiltered_and_partition_cannot_drop_tail(tmp_path, plan):
    path, sha = retain(tmp_path, capture({'year': {'min': 2010, 'max': 2010}}))
    with pytest.raises(ValueError, match='selected context'):
        plan_year_partitions(path, expected_sha256=sha, zip_code='08542')
    part = copy.deepcopy(plan['partitions'][0])
    part['filters']['year']['min'] = 2010
    path, sha = retain(tmp_path, capture(part['filters']))
    with pytest.raises(ValueError, match='open tail'):
        year_make_candidates(path, expected_sha256=sha, partition=part)


def test_partition_cannot_substitute_native_extrema_or_basis_clock(tmp_path, plan):
    part = copy.deepcopy(plan['partitions'][0])
    part['native_year_min'] = 2011
    part['filters']['year'] = {'max': 2010}
    path, sha = retain(tmp_path, capture(part['filters']))
    with pytest.raises(ValueError, match='reviewed basis'):
        year_make_candidates(path, expected_sha256=sha, partition=part)
    part = copy.deepcopy(plan['partitions'][0])
    part['basis_source']['observed_at_utc'] = '2026-09-18T13:00:00+00:00'
    path, sha = retain(tmp_path, capture(part['filters']))
    with pytest.raises(ValueError, match='reviewed basis'):
        year_make_candidates(path, expected_sha256=sha, partition=part)


@pytest.mark.parametrize('field,value', [('query_id', 'different_id'),
    ('endpoint_contract_verified', True), ('invented_metadata', 'not in reviewed basis')])
def test_selected_partition_must_equal_exact_regenerated_member(tmp_path, plan, field, value):
    part = copy.deepcopy(plan['partitions'][0])
    part[field] = value
    path, sha = retain(tmp_path, capture(part['filters']))
    with pytest.raises(ValueError, match='reviewed basis'):
        year_make_candidates(path, expected_sha256=sha, partition=part)


@pytest.mark.parametrize('field,value', [('count', -1), ('count', True), ('count', 7.0),
    ('modelIds', [-1]), ('modelIds', [True]), ('modelIds', [1.0]), ('modelIds', []), ('key', ' ')])
def test_unused_model_metadata_still_rejects_schema_drift(tmp_path, plan, field, value):
    part = plan['partitions'][1]
    data = capture(part['filters'])
    child = dict(key='A5', count=7, modelIds=[253], isApplied=False)
    child[field] = value
    data['facet_data']['makes']['Audi']['parentModels'] = [child]
    path, sha = retain(tmp_path, data)
    with pytest.raises(ValueError, match='native year-only model'):
        year_make_candidates(path, expected_sha256=sha, partition=part)


def test_valid_model_metadata_is_not_inferred_to_partition_the_make(tmp_path, plan):
    part = plan['partitions'][1]
    data = capture(part['filters'])
    # Overlapping IDs/counts are not a schema error or usable model partition.
    data['facet_data']['makes']['Audi']['parentModels'] = [
        dict(key='A5', count=7, modelIds=[253], isApplied=False),
        dict(key='Other', count=7, modelIds=[253], isApplied=False)]
    path, sha = retain(tmp_path, data)
    result = year_make_candidates(path, expected_sha256=sha, partition=part)
    assert result['native_make_count_sum'] == 7
    assert result['candidates'][0]['filters']['makes'] == [{'name': 'Audi'}]
