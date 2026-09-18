"""Coverage errors that a count-only full-site claim would otherwise conceal."""
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from vehicle_tracker.partitions import load_partition_review


def query(name, make, year, model=None):
    selected = dict(name=make)
    if model is not None:
        selected['parentModels'] = [dict(name=model)]
    return dict(query_id=name, zip_code='08542', location_filter=False,
                filters=dict(makes=[selected], year=dict(min=year, max=year)))


@pytest.fixture
def files(tmp_path):
    broad = dict(capture_method='public_search_facets', captured_at_utc='2026-09-08T12:00:00Z',
                 request=dict(filters={}, pagination=dict(page=1, pageSize=24), sortBy='MostPopular', zip5='08542'),
                 pagination=dict(currentPage=1, pageSize=24, totalMatchedInventory=15, totalMatchedPages=1),
                 facet_data=dict(year=dict(min=2022, max=2023),
                                 makes={'A': dict(count=12), 'B': dict(count=3)},
                                 categoryIds=dict(buckets=[dict(key='overlap1', count=15), dict(key='overlap2', count=15)])))
    parent = copy.deepcopy(broad)
    parent['request']['filters'] = query('parent', 'A', 2022)['filters']
    parent['pagination']['totalMatchedInventory'] = 7
    parent['facet_data']['year'].update(appliedMin=2022, appliedMax=2022)
    parent['facet_data']['makes'] = {
        'A': dict(count=7, isApplied=True, parentModels=[dict(key='X', count=4, modelIds=[1]),
                                                        dict(key='Y', count=3, modelIds=[2, 3])]),
        'B': dict(count=999, isApplied=False)}  # Deliberately disjunctive, not a child.
    candidate = dict(facet_source=str(tmp_path/'broad.json'), model_facet_sources=[str(tmp_path/'parent.json')],
                     queries=[query('ax', 'A', 2022, 'X'), query('ay', 'A', 2022, 'Y'),
                              query('a23', 'A', 2023), query('b22', 'B', 2022), query('b23', 'B', 2023)])
    panel = dict(queries=[query('px', 'A', 2022, 'X'), query('pz', 'A', 2023, 'Z')])
    return tmp_path, dict(broad=broad, parent=parent, candidate=candidate, panel=panel)


def freeze(files, available_at='2026-09-17T20:00:00Z'):
    root, data = files
    for name, content in data.items():
        (root/(name+'.json')).write_text(json.dumps(content), encoding='utf-8')
    hashes = {name+'.json': hashlib.sha256((root/(name+'.json')).read_bytes()).hexdigest() for name in data}
    review = dict(available_at=available_at, candidate_plan='candidate.json', panel_plan='panel.json', input_hashes=hashes)
    path = root/'review.json'
    path.write_text(json.dumps(review), encoding='utf-8')
    return path


def load(files):
    return load_partition_review(freeze(files), as_of='2026-09-18T00:00:00Z')


def test_own_make_counts_and_native_model_families_only(files):
    sources, parents, queries, inputs = load(files)
    assert sources.child_count_sum.tolist() == [15, 7]  # Neither 999 nor overlapping category labels.
    assert sources.count_residual.tolist() == [0, 0]
    assert len(parents) == 4
    selected = parents.set_index(['make', 'year']).loc['A', 2022]
    assert selected.candidate_reported_count == 7 and selected.panel_reported_count == 4
    assert selected.panel_residual == 3
    assert selected.panel_omitted_models == 'Y' and selected.candidate_omitted_models == ''
    assert selected.candidate_page_floor == 2 and selected.panel_page_floor == 1
    assert not parents.membership_reconciled.any()
    assert parents.unknown_category_count.isna().all()
    assert parents.missing_year_count.isna().all()
    assert sources.source_available_at.isna().all()
    assert len(inputs) == 5
    panel = queries.loc[queries.plan.eq('panel')].set_index('query_id')
    assert panel.loc['px', 'candidate_relationship'] == 'exact'
    assert panel.loc['pz', 'candidate_relationship'] == 'model subset of make/year parent'
    assert pd.isna(panel.loc['pz', 'reported_count'])
    assert parents.set_index(['make','year']).loc['A',2023].panel_unmeasured_models == 'Z'


def test_unmeasured_year_counts_are_not_zero_or_make_total(files):
    sources, parents, _, _ = load(files)
    assert parents.parent_reported_total.notna().sum() == 1
    assert parents.candidate_reported_count.notna().sum() == 1
    assert parents.candidate_page_floor.notna().sum() == 1
    assert parents.loc[parents.year.eq(2023), 'facet_status'].eq('parent facet unmeasured').all()


def test_missing_declared_child_keeps_numerical_residual(files):
    files[1]['candidate']['queries'].pop(1)
    _, parents, _, _ = load(files)
    assert parents.set_index(['make','year']).loc['A',2022].candidate_residual == 3


@pytest.mark.parametrize('overlap', ['parent_child', 'duplicate_child', 'native_model_id'])
def test_overlapping_queries_or_native_model_ids_withhold_count_sum(files, overlap):
    if overlap == 'native_model_id':
        files[1]['parent']['facet_data']['makes']['A']['parentModels'][1]['modelIds'].append(1)
    else:
        files[1]['candidate']['queries'].append(query('extra', 'A', 2022, 'X' if overlap=='duplicate_child' else None))
    sources, parents, _, _ = load(files)
    selected = parents.set_index(['make','year']).loc['A',2022]
    assert pd.isna(selected.candidate_reported_count) and pd.isna(selected.candidate_page_floor)
    if overlap == 'native_model_id':
        assert sources.iloc[1].overlapping_model_ids == '1'
        assert sources.iloc[1].count_residual == 0  # Zero arithmetic does not prove disjointness.


def test_negative_residual_is_visible_and_never_clamped(files):
    files[1]['parent']['facet_data']['makes']['A']['parentModels'][0]['count'] = 9
    sources, parents, _, _ = load(files)
    assert sources.iloc[1].count_residual == -5
    assert parents.set_index(['make','year']).loc['A',2022].candidate_residual == -5


def test_unmatched_model_keeps_unknown_instead_of_zero(files):
    files[1]['panel']['queries'][0] = query('unknown', 'A', 2022, 'never observed')
    _, parents, queries, _ = load(files)
    assert pd.isna(parents.set_index(['make','year']).loc['A',2022].panel_reported_count)
    assert queries.loc[queries.query_id.eq('unknown'), 'candidate_relationship'].item() == 'unmapped'
    assert parents.set_index(['make','year']).loc['A',2022].panel_unmeasured_models == 'never observed'


@pytest.mark.parametrize('problem', ['hash', 'zip', 'feature', 'filter', 'applied_make', 'applied_year',
                                      'duplicate_source', 'future_source', 'naive_clock', 'missing_count'])
def test_changed_or_incompatible_source_fails_closed(files, problem):
    data = files[1]
    if problem=='zip': data['parent']['request']['zip5']='98101'
    elif problem=='feature': data['parent']['request']['requestedFeatures']=['LocationBasedPrefiltering']
    elif problem=='filter': data['panel']['queries'][0]['filters']['price']={'min':0}
    elif problem=='applied_make': data['parent']['facet_data']['makes']['A']['isApplied']=False
    elif problem=='applied_year': data['parent']['facet_data']['year']['appliedMin']=2021
    elif problem=='duplicate_source': data['candidate']['model_facet_sources'] *= 2
    elif problem=='future_source': data['parent']['captured_at_utc']='2026-09-20T00:00:00Z'
    elif problem=='naive_clock': data['parent']['captured_at_utc']='2026-09-08T12:00:00'
    elif problem=='missing_count': data['parent']['facet_data']['makes']['A']['parentModels'][0]['count']=None
    path = freeze(files)
    if problem=='hash': (files[0]/'parent.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError):
        load_partition_review(path, as_of='2026-09-18T00:00:00Z')


def test_publication_cutoff_does_not_backdate_retrospective_review(files):
    path = freeze(files)
    # Source files are not even needed before this retrospective review exists.
    (files[0]/'parent.json').unlink()
    sources, parents, queries, inputs = load_partition_review(path, as_of='2026-09-16T00:00:00Z')
    assert sources.empty and parents.empty and queries.empty and inputs == {path}


def test_review_is_offline_and_read_only(files):
    path = freeze(files)
    hashes = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files[0].glob('*.json')}
    with patch('socket.socket', side_effect=AssertionError('Network forbidden')), \
            patch.object(Path, 'write_text', side_effect=AssertionError('Write forbidden')), \
            patch.object(Path, 'write_bytes', side_effect=AssertionError('Write forbidden')):
        load_partition_review(path, as_of='2026-09-18T00:00:00Z')
    assert hashes == {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files[0].glob('*.json')}
