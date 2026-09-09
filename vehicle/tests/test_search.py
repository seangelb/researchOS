import copy
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from vehicle_tracker.carvana import parse_capture


@pytest.fixture
def response_data():
    source=Path(__file__).parents[1]/'tests/fixtures/carvana_browser_sample_20260907.json'
    records=json.loads(source.read_text())['records']
    rows=[dict(vehicleId=int(r['offers']['url'].rstrip('/').split('/')[-1]),vin=r['vehicleIdentificationNumber'],
        year=r['modelDate'],make=r['brand'],model=r['model'],parentModel=r['model'],
        mileage=r['mileageFromOdometer'],price={'total':r['offers']['price']},isPurchasePending=False,
        vehicleLockType=0) for r in records]
    return {'userDeliveryInfo':{'zip5':'08542'},'inventory':{'pagination':{'currentPage':1,'pageSize':24,'totalMatchedInventory':3,'totalMatchedPages':1},'vehicles':rows}}


def packet(data):
    from vehicle_tracker.search import project_response
    return project_response(data, {'filters':{},'pagination':{'page':1,'pageSize':24},'sortBy':'MostPopular','zip5':'08542'},
                            observed_at='2026-09-08T12:00:00Z')


def test_search_parser_and_native_status(response_data):
    capture=packet(response_data)
    frame=parse_capture(capture)
    assert len(frame)==3 and frame.asking_price_usd.iloc[0]==14590
    assert frame.availability_native.isna().all()  # Never manufacture schema InStock.
    assert 'isPurchasePending' in frame.card_text.iloc[0]


@pytest.mark.parametrize('problem',['duplicate','invalid_vin','wrong_page','missing_inventory','secret'])
def test_payload_failures_and_allowlist(response_data,problem):
    if problem=='secret':
        response_data['browserCookieId']='not-a-real-secret'
        response_data['inventory']['vehicles'][0]['secretToken']='not-a-real-secret'
        assert 'not-a-real-secret' not in json.dumps(packet(response_data))
        return
    if problem=='duplicate': response_data['inventory']['vehicles'].append(response_data['inventory']['vehicles'][0])
    if problem=='invalid_vin': response_data['inventory']['vehicles'][0]['vin']='bad'
    if problem=='wrong_page': response_data['inventory']['pagination']['currentPage']=2
    if problem=='missing_inventory': response_data={}
    with pytest.raises((ValueError,KeyError)):
        parse_capture(packet(response_data))


def test_zero_and_missing_price(response_data):
    response_data['inventory']['vehicles'][0]['price']['total']=None
    response_data['inventory']['vehicles'][1]['price']['total']=0
    frame=parse_capture(packet(response_data))
    assert frame.asking_price_usd.isna().sum()==1 and frame.asking_price_usd.iloc[1]==0


@pytest.mark.parametrize('status,ctype,header,reason',[(200,'text/html',{},'unexpected_content'),
    (403,'text/html',{'cf-mitigated':'challenge'},'cloudflare_challenge'),
    (429,'application/json',{'retry-after':'60'},'rate_limited')])
def test_http_failure_stops_without_retry(tmp_path,response_data,status,ctype,header,reason):
    from vehicle_tracker.search import collect_search
    response=Mock(status_code=status,headers={'content-type':ctype,**header},content=b'example',json=Mock(return_value=response_data))
    post=Mock(return_value=response)
    result=collect_search(filters={},zip_code='08542',destination=tmp_path/'new',target_listings=1000,post=post)
    assert result['status']=='blocked' and reason in result['reason']
    assert post.call_count==1 and result['complete_query_count'] is None


def test_target_is_a_sample_not_complete_inventory(tmp_path,response_data,monkeypatch):
    from vehicle_tracker.search import collect_search
    response_data['inventory']['pagination'].update(totalMatchedInventory=100,totalMatchedPages=5)
    response=Mock(status_code=200,headers={'content-type':'application/json'},content=b'body',json=Mock(return_value=response_data))
    result=collect_search(filters={},zip_code='08542',destination=tmp_path/'new',target_listings=2,post=Mock(return_value=response))
    assert result['target_reached'] and result['unique_listings']==3
    assert not result['query_complete'] and result['complete_query_count'] is None


def test_existing_destination_is_refused(tmp_path):
    from vehicle_tracker.search import collect_search
    with pytest.raises(FileExistsError):
        collect_search(filters={},zip_code='08542',destination=tmp_path,target_listings=3,post=Mock())


@pytest.mark.parametrize('problem', ['wrong_zip', 'missing_zip', 'changing_total', 'duplicate_page', 'empty', 'timeout'])
def test_incomplete_or_wrong_context_stays_blocked(tmp_path,response_data,monkeypatch,problem):
    from vehicle_tracker.search import collect_search
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', lambda _: None)
    first=copy.deepcopy(response_data)
    second=copy.deepcopy(response_data)
    if problem in {'changing_total','duplicate_page'}:
        first['inventory']['pagination'].update(totalMatchedInventory=27,totalMatchedPages=2)
        second['inventory']['pagination'].update(currentPage=2,totalMatchedInventory=28 if problem=='changing_total' else 27,totalMatchedPages=2)
    if problem=='wrong_zip': first['userDeliveryInfo']['zip5']='90210'
    if problem=='missing_zip': first.pop('userDeliveryInfo')
    if problem=='empty': first['inventory']['vehicles']=[]
    replies=[Mock(status_code=200, headers={'content-type':'application/json'},content=b'example',json=Mock(return_value=d)) for d in (first,second)]
    post=Mock(side_effect=TimeoutError('do not log credential-bearing errors') if problem=='timeout' else replies)
    result=collect_search(filters={},zip_code='08542',destination=tmp_path/'new',post=post)
    assert result['status']=='blocked' and not result['query_complete']
    assert result['complete_query_count'] is None and 'credential' not in result['reason']
    if problem in {'changing_total', 'duplicate_page'}:
        assert post.call_count == 2 and result['unique_listings'] == 3


def test_inconsistent_page_count_cannot_claim_complete(response_data):
    response_data['inventory']['pagination']['totalMatchedPages']=2
    with pytest.raises(ValueError,match='pagination'):
        parse_capture(packet(response_data))


def test_plan_preserves_zip_observations_but_counts_union(tmp_path,response_data,monkeypatch):
    from vehicle_tracker.search_plan import collect_plan
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', lambda _: None)
    def post(url, **kwargs):
        data=copy.deepcopy(response_data)
        data['userDeliveryInfo']['zip5']=kwargs['json']['zip5']
        return Mock(status_code=200, headers={'content-type':'application/json'},content=b'example',json=Mock(return_value=data))
    plan=[dict(query_id=z,zip_code=z,filters={}) for z in ('08542','08540')]
    result=collect_plan(plan,destination=tmp_path/'new',post=post)
    assert result['all_queries_complete'] and result['unique_listings']==3 and result['unique_vins']==3
    assert len(result['outcomes'])==2 and result['daily_sales_estimate'] is None


def test_resume_skips_verified_complete_restarts_failed_without_overwriting(tmp_path,response_data,monkeypatch):
    from vehicle_tracker.search_plan import collect_plan
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', lambda _: None)
    success=Mock(status_code=200,headers={'content-type':'application/json'},content=b'ok',json=Mock(return_value=response_data))
    failure=Mock(status_code=503,headers={},content=b'failure')
    plan=[dict(query_id=q,zip_code='08542',filters={}) for q in ('first','second')]
    initial=collect_plan(plan,destination=tmp_path/'initial',post=Mock(side_effect=[success,failure]))
    old_bytes=(tmp_path/'initial/run_report.json').read_bytes()
    post=Mock(return_value=success)
    resumed=collect_plan(plan,destination=tmp_path/'resumed',resume_from=tmp_path/'initial/run_report.json',post=post)
    assert post.call_count==1 and post.call_args.kwargs['json']['pagination']['page']==1
    assert resumed['outcomes'][0]['resumed'] and resumed['all_queries_complete']
    assert (tmp_path/'initial/run_report.json').read_bytes()==old_bytes
    assert resumed['unique_listings']==3 and not resumed['national_coverage_verified']
    (tmp_path/'initial/first/run_report.json').write_text('{}')
    with pytest.raises(ValueError,match='artifact changed'):
        collect_plan(plan,destination=tmp_path/'invalid',resume_from=tmp_path/'initial/run_report.json',post=post)
    assert not (tmp_path/'invalid').exists()


def test_invalid_plan_never_opens_transport_or_writes(tmp_path):
    from vehicle_tracker.search_plan import collect_plan
    post=Mock()
    with pytest.raises(ValueError):
        collect_plan([dict(query_id='../escape',zip_code='08542',filters={})],destination=tmp_path/'new',post=post)
    assert not (tmp_path/'new').exists() and post.call_count==0


def test_explicit_valid_zero_is_distinct_from_missing_inventory(tmp_path,response_data):
    from vehicle_tracker.search import collect_search
    response_data['inventory']['vehicles']=[]
    response_data['inventory']['pagination'].update(totalMatchedInventory=0,totalMatchedPages=0)
    post=Mock(return_value=Mock(status_code=200,headers={'content-type':'application/json'},
        content=b'valid empty response',json=Mock(return_value=response_data)))
    report=collect_search(filters={},zip_code='08542',destination=tmp_path/'zero',post=post)
    assert report['query_complete'] and report['complete_query_count']==0 and report['requests']==1


def test_expanded_budget_is_explicit_and_bounded():
    from vehicle_tracker.collect import NavigationBudget
    assert NavigationBudget().max_requests == 120
    assert NavigationBudget(max_requests=600,max_seconds=3600).max_requests == 600
    with pytest.raises(ValueError): NavigationBudget(max_requests=601)
    with pytest.raises(ValueError): NavigationBudget(max_seconds=3601)


@pytest.mark.parametrize('location_filter',[False,True])
def test_location_filter_is_explicit_and_retained(tmp_path,response_data,location_filter):
    from vehicle_tracker.search import collect_search
    post=Mock(return_value=Mock(status_code=200,headers={'content-type':'application/json'},
        content=b'public inventory',json=Mock(return_value=response_data)))
    report=collect_search(filters={},zip_code='08542',destination=tmp_path/'new',post=post,
                          location_filter=location_filter)
    request=post.call_args.kwargs['json']
    assert request.get('requestedFeatures',[]) == (['LocationBasedPrefiltering'] if location_filter else [])
    source=json.loads(Path(report['pages'][0]['retained_source']).read_text())
    assert source['request'].get('requestedFeatures',[]) == request.get('requestedFeatures',[])


def test_missing_year_blocks_filtered_membership(response_data):
    capture=packet(response_data)
    capture['request']['filters']={'year':{'min':2010,'max':2025}}
    capture['vehicles'][0]['year']=None
    with pytest.raises(ValueError,match='year'):
        parse_capture(capture)


@pytest.mark.parametrize('failure',['html','timeout'])
def test_access_failure_stops_remaining_plan_queries(tmp_path,response_data,failure):
    from vehicle_tracker.search_plan import collect_plan
    first=Mock(status_code=200,headers={'content-type':'text/html'},content=b'not inventory')
    second=Mock(status_code=200,headers={'content-type':'application/json'},content=b'example',json=Mock(return_value=response_data))
    post=Mock(side_effect=[TimeoutError() if failure=='timeout' else first, second])
    plan=[dict(query_id=q,zip_code='08542',filters={}) for q in ('first','second')]
    report=collect_plan(plan,destination=tmp_path/'new',post=post)
    assert post.call_count==1 and report['outcomes'][1]['status']=='unattempted'


def test_overlapping_query_target_counts_new_ids_and_resume_makes_progress(tmp_path,response_data,monkeypatch):
    from vehicle_tracker.search_plan import collect_plan
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', lambda _: None)
    # First complete query has 3 vehicles; second's first page is those same 3.
    # Its next page has another 3. The plan target is 6 distinct vehicles.
    first=copy.deepcopy(response_data)
    overlap=copy.deepcopy(response_data)
    overlap['inventory']['pagination'].update(totalMatchedInventory=27,totalMatchedPages=2)
    new=copy.deepcopy(response_data)
    new['inventory']['pagination'].update(currentPage=2,totalMatchedInventory=27,totalMatchedPages=2)
    for i, vehicle in enumerate(new['inventory']['vehicles']):
        vehicle['vehicleId'] += 100
        vehicle['vin'] = vehicle['vin'][:-1] + str((int(vehicle['vin'][-1])+1)%10)
    def response(data):
        return Mock(status_code=200,headers={'content-type':'application/json'},content=b'example',json=Mock(return_value=data))
    plan=[dict(query_id=q,zip_code='08542',filters={}) for q in ('first','second')]
    limited=collect_plan(plan,destination=tmp_path/'limited',target_listings=6,
        post=Mock(side_effect=[response(first),response(overlap),TimeoutError()]))
    assert limited['unique_listings']==3 and not limited['target_reached']
    post=Mock(side_effect=[response(overlap),response(new)])
    resumed=collect_plan(plan,destination=tmp_path/'resume',target_listings=6,
        resume_from=tmp_path/'limited/run_report.json',post=post)
    assert post.call_count==2 and resumed['unique_listings']==6 and resumed['target_reached']
