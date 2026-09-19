"""Known native empty layout remains positive-only, with make scope unknown."""
import copy
import json

import pandas as pd
import pytest

from test_gap_recovery import experiment, run, recovery
from vehicle_tracker.facets import select_facets
from vehicle_tracker.search import collect_search


NATIVE_EMPTY = dict(facetData=dict(year=dict(appliedMax=2009, min=2010, max=2025)),
    inventory=dict(pagination=dict(currentPage=1, pageSize=24,
        totalMatchedInventory=0, totalMatchedPages=1), vehicles=[]),
    userDeliveryInfo=dict(zip5='08542'))


def change_response(e, mutate):
    original = e.send
    def send(*args, **kwargs):
        response = original(*args, **kwargs)
        data = response.json()
        mutate(data)
        response.content = json.dumps(data).encode()
        return response
    e.send = send


def test_exact_native_empty_optional_selection_preserves_omission():
    data = copy.deepcopy(NATIVE_EMPTY)
    with pytest.raises(KeyError, match='makes'):
        select_facets(data)
    selected = select_facets(data, allow_empty_missing_makes=True)
    assert selected == dict(year=data['facetData']['year'], makes={}, makes_present=False)
    assert data == NATIVE_EMPTY and 'makes' not in data['facetData']


@pytest.mark.parametrize('value', [None, 0, 1, 'true'])
def test_policy_requires_boolean(value):
    with pytest.raises(ValueError):
        select_facets(NATIVE_EMPTY, allow_empty_missing_makes=value)


def test_empty_missing_make_then_positive_child_exports_unknown_scope(experiment, tmp_path):
    e = experiment
    def mutate(data):
        if len(e.requests) == 1:
            data.clear(); data.update(copy.deepcopy(NATIVE_EMPTY))
            e.mode = 'positive'
    change_response(e, mutate)
    report = run(e, limit=2)
    first, second = report['entries'][:2]
    assert first['query_complete'] and not first['context_validated']
    assert first['context_status'] == 'empty_make_context_unavailable'
    assert second['query_complete'] and second['context_validated']
    output = recovery.export_recovery(e.folder, output=tmp_path/'export')
    children = pd.read_csv(output/'child_coverage.csv')
    assert len(children) == 500 and children.child_complete.sum() == 1
    assert children.iloc[0].context_status == 'empty_make_context_unavailable'
    assert not children.iloc[0].child_complete
    assert len(pd.read_csv(output/'observations.csv')) == 1
    assert not pd.read_csv(output/'parent_coverage.csv').recovery_contexts_complete.any()


@pytest.mark.parametrize('bad', ['year', 'zip', 'make', 'model', 'nonzero', 'page', 'null_makes'])
def test_incompatible_empty_context_stops_before_storage(experiment, tmp_path, bad):
    e = experiment
    def mutate(data):
        if bad == 'make':
            next(iter(data['facetData']['makes'].values()))['isApplied'] = False
        elif bad == 'model':
            next(iter(data['facetData']['makes'].values()))['parentModels'][0]['isApplied'] = False
        else:
            if bad == 'nonzero':
                del data['facetData']['makes']
                return
            data.clear(); data.update(copy.deepcopy(NATIVE_EMPTY))
            if bad == 'year': data['facetData']['year']['appliedMax'] = 2008
            elif bad == 'zip': data['userDeliveryInfo']['zip5'] = '90210'
            elif bad == 'page': data['inventory']['pagination']['currentPage'] = 2
            elif bad == 'null_makes': data['facetData']['makes'] = None
    change_response(e, mutate)
    report = run(e, mode='positive' if bad == 'nonzero' else 'zero')
    assert len(e.requests) == 1 and report['status'] == 'stopped'
    child = recovery.load(report['entries'][0]['report'])
    assert child['outcome_kind'] == 'schema_failure'
    assert child['pages'][0]['stored_rows'] == 0
    assert child['pages'][0].get('database_outcome') is None
    output = recovery.export_recovery(e.folder, output=tmp_path/'export')
    assert not recovery.load(output/'summary.json')['recovery_contexts_complete']


@pytest.mark.parametrize('mode', ['zero', 'positive'])
def test_false_validator_is_not_generic_admission_bypass(experiment, tmp_path, mode):
    e = experiment; e.mode = mode; q = e.plan['children'][0]
    report = collect_search(filters=q['filters'], zip_code=q['zip_code'],
        destination=tmp_path/'direct', target_listings=None, post=e.send,
        retain_facets=True, first_page_validator=lambda *_: False,
        allow_empty_missing_makes=True)
    assert report['outcome_kind'] == 'schema_failure'
    assert report['pages'][0]['stored_rows'] == 0


@pytest.mark.parametrize('retain, validator', [(False, lambda *_: False), (True, None)])
def test_allowance_requires_retention_and_validator(experiment, tmp_path, retain, validator):
    e = experiment
    with pytest.raises(ValueError):
        collect_search(filters={}, zip_code='08542', destination=tmp_path/'direct', post=e.send,
            retain_facets=retain, first_page_validator=validator, allow_empty_missing_makes=True)
    assert not e.requests
