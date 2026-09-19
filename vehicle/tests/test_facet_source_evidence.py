"""Public facet evidence survives schema failures without retaining private fields."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from test_search_evidence import response
from vehicle_tracker.facets import select_facets
from vehicle_tracker.history import read_query_evidence
from vehicle_tracker.search import collect_search
from vehicle_tracker.search_evidence import public_source, retain_response_evidence, verify_response_evidence


def source():
    return dict(userDeliveryInfo={'zip5':'08542'}, inventory=dict(vehicles=[],
        pagination=dict(currentPage=1,pageSize=24,totalMatchedInventory=0,totalMatchedPages=1)),
        facetData=dict(year=dict(min=2010,max=2027,appliedMin=None,appliedMax=2009),
            makes={'Audi':dict(key='Audi',count=0,isApplied=True,
                parentModels=[dict(key='A5',count=0,isApplied=True,modelIds=[15])])}))


def test_public_facet_fields_roundtrip_and_old_source_without_facets(tmp_path):
    data=source();evidence=retain_response_evidence(response(data),tmp_path/'new')
    assert evidence['kind']=='original_response_content'
    assert verify_response_evidence(evidence)==data
    assert select_facets(verify_response_evidence(evidence))==select_facets(data)
    old=copy.deepcopy(data);old.pop('facetData')
    old_evidence=retain_response_evidence(response(old),tmp_path/'old')
    assert verify_response_evidence(old_evidence)==old
    assert 'facetData' not in verify_response_evidence(old_evidence)


def test_unrelated_fields_omitted_at_every_facet_level(tmp_path):
    expected=source();data=copy.deepcopy(expected)
    data['account']={'token':'SECRET','email':'private@example.com'}
    data['facetData']['userId']='SECRET'
    data['facetData']['year']['email']='private@example.com'
    bucket=data['facetData']['makes']['Audi']
    bucket['accountToken']='SECRET';bucket['parentModels'][0]['privateMetadata']={'email':'private@example.com'}
    evidence=retain_response_evidence(response(data),tmp_path/'sources')
    assert evidence['kind']=='selected_source' and evidence['redacted_values']==0
    assert verify_response_evidence(evidence)==expected
    retained=Path(evidence['source_path']).read_text()
    assert 'SECRET' not in retained and 'private@example.com' not in retained


@pytest.mark.parametrize('mutation',['make_label','model_label','numeric_value','oversized_label'])
def test_unsafe_facet_strings_are_not_retained_or_admitted(tmp_path,mutation):
    data=source();bucket=data['facetData']['makes']['Audi']
    if mutation=='make_label':
        data['facetData']['makes']['private@example.com']=bucket
    elif mutation=='model_label':
        bucket['parentModels'][0]['key']='private@example.com'
    elif mutation=='oversized_label':
        data['facetData']['makes']['X'*101]=bucket
    else:
        data['facetData']['year']['appliedMax']='Bearer: SECRET'
    report=collect_search(filters={},zip_code='08542',destination=tmp_path/'capture',
        target_listings=None,post=Mock(return_value=response(data)),retain_facets=True)
    evidence=report['pages'][0]['response_evidence']
    assert evidence['redacted_values']==1 and report['outcome_kind']=='schema_failure'
    assert not report['query_complete'] and report['unique_listings']==0
    for path in (tmp_path/'capture').rglob('*.json'):
        content=path.read_text()
        assert 'private@example.com' not in content and 'Bearer: SECRET' not in content and 'X'*101 not in content


@pytest.mark.parametrize('facet_value',[
    None, [], 0, False, {'year':None,'makes':{}}, {'year':[],'makes':None},
    {'year':{'min':None,'max':0,'appliedMax':False},'makes':{'Audi':None}},
    {'year':{},'makes':{'Audi':{'key':'Audi','count':0,'isApplied':True,'parentModels':None}}},
    {'year':{},'makes':{'Audi':{'key':'Audi','count':None,'isApplied':False,
        'parentModels':[{'key':'A5','count':0,'isApplied':0,'modelIds':None}]}}},
])
def test_missing_null_zero_and_wrong_facet_shapes_survive_selection(facet_value):
    data=source();data['facetData']=facet_value
    assert public_source(data)==data
    assert public_source({'facetData':{}})=={'facetData':{}}


@pytest.mark.parametrize('missing',['facetData','year','makes','key','parentModels','modelIds'])
def test_exact_missing_public_field_remains_diagnosable_after_collector_failure(tmp_path,missing):
    data=source()
    if missing=='facetData':del data['facetData']
    elif missing in {'year','makes'}:del data['facetData'][missing]
    elif missing in {'key','parentModels'}:del data['facetData']['makes']['Audi'][missing]
    else:del data['facetData']['makes']['Audi']['parentModels'][0][missing]
    report=collect_search(filters={},zip_code='08542',destination=tmp_path/'capture',
        target_listings=None,post=Mock(return_value=response(data)),retain_facets=True)
    assert report['outcome_kind']=='schema_failure' and not report['query_complete']
    page=report['pages'][0];evidence=page['response_evidence']
    assert page['stored_rows']==0 and page.get('database_outcome') is None
    retained=verify_response_evidence(evidence)
    assert retained==data  # No missing values or context are filled in.
    with pytest.raises(KeyError) as error:
        select_facets(retained)
    assert error.value.args==(missing,)
    assert not list((tmp_path/'capture/facets').glob('*.json'))
    _,_,rows=read_query_evidence(tmp_path/'capture/run_report.json')
    assert rows.empty


def test_facets_durable_even_when_projection_fails_first(tmp_path):
    data=source();data['inventory']['pagination']['totalMatchedPages']=5
    report=collect_search(filters={},zip_code='08542',destination=tmp_path/'capture',
        post=Mock(return_value=response(data)),retain_facets=True)
    assert report['outcome_kind']=='schema_failure'
    retained=verify_response_evidence(report['pages'][0]['response_evidence'])
    assert retained['facetData']==data['facetData']
    assert select_facets(retained)==select_facets(data)
