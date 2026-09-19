"""Observed native empty-page shape, without interpreting it as broader absence."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vehicle_tracker.history import read_query_evidence
from vehicle_tracker.search import build_search_request, collect_search, parse_search_capture, project_response
from vehicle_tracker.storage import read_snapshots
from test_search import response_data


def empty_source(pages=1):
    # Exact selected public shape retained as ed59823f...17eda2 on September 19.
    return {'inventory': {'pagination': {'currentPage': 1, 'pageSize': 24,
        'totalMatchedInventory': 0, 'totalMatchedPages': pages}, 'vehicles': []},
        'userDeliveryInfo': {'zip5': '08542'}}


def request():
    return build_search_request(filters={'makes':[{'name':'Audi','parentModels':[{'name':'A5'}]}],
                                          'year':{'max':2009}},zip_code='08542')


@pytest.mark.parametrize('pages',[0,1])
def test_native_empty_shape_preserves_zero_or_one_page(pages):
    source=empty_source(pages);before=copy.deepcopy(source)
    capture=project_response(source,request(),observed_at='2026-09-19T16:13:11.474032+00:00')
    assert capture['pagination']==source['inventory']['pagination']
    assert parse_search_capture(capture).empty
    assert source==before and capture['pagination']['totalMatchedPages']==pages


@pytest.mark.parametrize('field,value',[
    ('currentPage',0),('currentPage',2),('currentPage',True),('currentPage',1.0),
    ('pageSize',0),('pageSize',True),('pageSize',24.0),('pageSize',25),
    ('totalMatchedInventory',False),('totalMatchedInventory',0.0),('totalMatchedInventory',-1),
    ('totalMatchedPages',False),('totalMatchedPages',1.0),('totalMatchedPages',2),
])
def test_invalid_empty_pagination_rejected_in_projection_and_direct_parse(field,value):
    data=empty_source();capture=project_response(data,request(),observed_at='2026-09-19T16:13:11Z')
    data['inventory']['pagination'][field]=value;capture['pagination'][field]=value
    if field=='currentPage':
        # Even a matching request may not turn page zero/two into an empty first page.
        selected=request();selected['pagination']['page']=value;capture['request']=copy.deepcopy(selected)
    else:
        selected=request()
    with pytest.raises(ValueError):
        project_response(data,selected,observed_at='2026-09-19T16:13:11Z')
    with pytest.raises(ValueError):
        parse_search_capture(capture)


def test_zero_inventory_with_vehicle_and_positive_wrong_page_count_still_fail(response_data):
    for data in [dict(empty_source(),inventory=dict(empty_source()['inventory'],
                    vehicles=response_data['inventory']['vehicles'])),
                 copy.deepcopy(response_data)]:
        if data['inventory']['pagination']['totalMatchedInventory']:
            data['inventory']['pagination']['totalMatchedPages']=0
        with pytest.raises(ValueError):
            project_response(data,build_search_request(filters={},zip_code='08542'),observed_at='2026-09-19T16:13:11Z')


@pytest.mark.parametrize('pages',[0,1])
def test_empty_collection_completes_one_request_and_replays_native_count(tmp_path,pages):
    data=empty_source(pages);calls=[]
    def send(url,**kwargs):
        calls.append(kwargs['json'])
        return SimpleNamespace(status_code=200,headers={'content-type':'application/json'},
            content=json.dumps(data).encode(),json=lambda:data,close=lambda:None)
    selected=request()
    result=collect_search(filters=selected['filters'],zip_code='08542',destination=tmp_path/'capture',
                          target_listings=None,post=send)
    assert len(calls)==1 and result['query_complete'] and result['unique_listings']==0
    page=result['pages'][0]
    assert page['status']=='parsed' and page['stored_rows']==0
    capture=json.loads(Path(page['retained_source']).read_text())
    assert capture['pagination']['totalMatchedPages']==pages
    run,captures,rows=read_query_evidence(tmp_path/'capture/run_report.json')
    assert run['query_complete']==1 and rows.empty and captures.row_count.tolist()==[0]
    stored,observations=read_snapshots(tmp_path/'capture/vehicle.sqlite')
    assert stored.status.tolist()==['parsed'] and observations.empty
