"""Offline whole-catalog transport tests with real immutable sources and SQLite."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from vehicle_tracker import catalog


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    class Clock:
        seconds = 0
        def now(self, tz=None):
            return datetime(2026,9,19,13,tzinfo=timezone.utc)+timedelta(seconds=self.seconds)
        def sleep(self, seconds): self.seconds += seconds
        def monotonic(self): return 1000+self.seconds
    clock = Clock()
    monkeypatch.setattr(catalog, 'utcnow', clock.now)
    monkeypatch.setattr('vehicle_tracker.cycles.utcnow', clock.now)
    monkeypatch.setattr('vehicle_tracker.search.datetime', SimpleNamespace(now=clock.now))
    monkeypatch.setattr('vehicle_tracker.collect.time.monotonic', clock.monotonic)
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', clock.sleep)
    config = json.loads((Path(__file__).parents[1]/'config/carvana_full_inventory.json').read_text())
    config['capture_root'] = str(tmp_path/'captures')
    path = tmp_path/'config.json'
    path.write_text(json.dumps(config))
    vehicles = [dict(vehicleId=i, vin=f'5YJ3E1EA0MF{i:06d}', year=2005 if i == 1 else 2022,
        make='Tesla', model='Model 3' if i <= 13 else 'Model Y',
        parentModel='Model 3' if i <= 13 else 'Model Y', mileage=10000,
        price={'total':25000}, isPurchasePending=False, vehicleLockType=0,
        vehiclePurchaseType='Purchase', isOnDemand=False) for i in range(1,27)]
    starts = []
    def send(url, **kwargs):
        starts.append(clock.now())
        request = kwargs['json']
        assert url == catalog.ENDPOINT and kwargs['allow_redirects'] is False
        assert 0 < kwargs['timeout'] <= 30
        models = request['filters'].get('makes', [{}])[0].get('parentModels', [])
        rows = [v for v in vehicles if not models or v['parentModel'] == models[0]['name']]
        page = request['pagination']['page']
        data = dict(userDeliveryInfo={'zip5':request['zip5']},
            inventory=dict(pagination=dict(currentPage=page,pageSize=24,
                totalMatchedInventory=len(rows),totalMatchedPages=(len(rows)+23)//24),
                vehicles=copy.deepcopy(rows[(page-1)*24:page*24])),
            facetData=dict(year={'min':2010,'max':2027},makes={'Tesla':dict(key='Tesla',
                isApplied=bool(request['filters']),count=len(rows),parentModels=[
                    dict(key='Model 3',count=sum(v['parentModel']=='Model 3' for v in vehicles),isApplied=False,modelIds=[3]),
                    dict(key='Model Y',count=sum(v['parentModel']=='Model Y' for v in vehicles),isApplied=False,modelIds=[4])] if request['filters'] else [])}))
        clock.sleep(.1)
        response = SimpleNamespace(content=json.dumps(data).encode(),status_code=200,
            headers={'content-type':'application/json'},close=lambda:None)
        response.json = lambda: json.loads(response.content)
        return response
    return SimpleNamespace(path=path,config=config,folder=tmp_path/'captures/2026-09-19',
        send=send,clock=clock,vehicles=vehicles,starts=starts)


def run(e, post=None):
    return catalog.collect_catalog(e.path,expected_sha256=catalog.digest(e.path),post=post or e.send)


def test_preview_no_writes_or_transport_and_larger_explicit_budget(experiment):
    e=experiment
    before={p:p.read_bytes() for p in e.path.parent.rglob('*') if p.is_file()}
    result=catalog.preview(e.path)
    assert result['request_ceiling']==6000 and result['effective_seconds']==21600
    assert result['requests']==0 and result['writes'] is False
    assert before=={p:p.read_bytes() for p in e.path.parent.rglob('*') if p.is_file()}


def test_all_years_models_geo_replay_and_one_attempt(experiment,tmp_path):
    e=experiment
    report=run(e)
    assert report['status']=='collection_finished'
    assert report['primary_queries_complete'] and report['primary_observed_vins']==26
    assert report['opening_count_residual']==report['closing_count_residual']==0
    assert report['requests']==11
    assert report['national_coverage_verified'] is False and report['estimated_sales'] is None
    assert all('year' not in q['filters'] for q in report['leaf_queries'])
    assert len(report['geographic_checks'])==4
    assert all(c['additional_vins']==[] for c in report['geographic_checks'])
    assert all((b-a).total_seconds()>=3 for a,b in zip(e.starts,e.starts[1:]))
    before={p:p.read_bytes() for p in e.folder.rglob('*') if p.is_file()}
    output=catalog.export_catalog(e.folder,output=tmp_path/'export')
    observations=pd.read_csv(output/'observations.csv')
    assert len(observations)==26 and observations.year.min()==2005
    assert before=={p:p.read_bytes() for p in e.folder.rglob('*') if p.is_file()}
    post=Mock()
    with pytest.raises(ValueError,match='Existing date'):run(e,post)
    post.assert_not_called()


def test_small_make_reuses_probe_instead_of_refetching(experiment):
    e=experiment;e.vehicles[:]=e.vehicles[:13]
    report=run(e)
    assert report['primary_queries_complete'] and report['primary_observed_vins']==13
    assert report['leaf_queries'][0]['query_id']=='make_000'
    assert not any(x['role']=='primary_inventory' for x in report['entries'])
    assert report['requests']==7


@pytest.mark.parametrize('kind',['403','429','challenge','transport','schema','context','identity'])
def test_access_stops_cool_down_and_other_failures_do_not_block_the_next_date(experiment,kind):
    e=experiment
    access = kind in {'403','429','challenge'}
    if access:
        e.config['retry_policy'] = dict(cooldown_minutes_after_access_stop=[2880, 2880, 2880])
        e.path.write_text(json.dumps(e.config))
    def send(url,**kwargs):
        if kind=='transport':raise TimeoutError('private message')
        response=e.send(url,**kwargs)
        if kind in ['403','429']:response.status_code=int(kind)
        elif kind=='challenge':response.headers['cf-mitigated']='challenge'
        else:
            data=json.loads(response.content)
            if kind=='schema': data.pop('inventory')
            if kind=='context': data['userDeliveryInfo']['zip5']='00000'
            if kind=='identity' and kwargs['json']['filters']:
                data['inventory']['vehicles'][0]['vin']='5YJ3E1EA0MF999999'
            response.content=json.dumps(data).encode()
        return response
    post=Mock(side_effect=send)
    result=run(e,post)
    assert result['status']=='stopped'
    assert post.call_count==(2 if kind=='identity' else 1)
    stop = e.folder.parent/'access_stop.json'
    outcome = json.loads((e.folder/'attempt_outcome.json').read_text())
    if access:
        assert stop.is_file() and outcome['kind']=='access_stop' and outcome['cooldown_until']
        e.clock.seconds+=86400
        with pytest.raises(ValueError,match='access stop'):run(e,post)
    else:
        assert not stop.exists() and outcome['kind']=='client_failure'
        e.clock.seconds+=86400
        assert run(e,post)['status']=='stopped'
    assert 'private message' not in (e.folder/'catalog_report.json').read_text()


def test_model_residual_falls_back_to_whole_make_without_year_exclusions(experiment):
    e=experiment
    def send(url,**kwargs):
        response=e.send(url,**kwargs)
        data=json.loads(response.content)
        if kwargs['json']['filters']:
            data['facetData']['makes']['Tesla']['parentModels'][0]['count']=12
        response.content=json.dumps(data).encode()
        return response
    result=run(e,send)
    assert result['primary_queries_complete'] and result['primary_observed_vins']==26
    assert result['leaf_queries']==[catalog.query('make_000_all','08542',{'makes':[{'name':'Tesla'}]})]


def test_charged_request_cap_includes_discovery_and_validation(experiment):
    e=experiment;e.config['max_requests']=3;e.path.write_text(json.dumps(e.config))
    result=run(e)
    assert len(e.starts)==result['requests']==3
    assert not result['primary_queries_complete']
    assert any(x['status']=='unattempted' for x in result['entries'])


def test_changed_config_blocks_before_request(experiment):
    e=experiment;post=Mock()
    with pytest.raises(ValueError,match='changed after preview'):
        catalog.collect_catalog(e.path,expected_sha256='0'*64,post=post)
    post.assert_not_called()
    assert not e.folder.exists()


def test_changed_source_rejected_by_replay(experiment,tmp_path):
    e=experiment;run(e)
    report=json.loads((e.folder/'broad_open/run_report.json').read_text())
    Path(report['pages'][0]['facet_source']).write_text('{}')
    with pytest.raises(ValueError,match='facet changed'):
        catalog.export_catalog(e.folder,output=tmp_path/'invalid-export')
    assert not (tmp_path/'invalid-export').exists()


def test_partial_pagination_isolated_and_not_retried(experiment,tmp_path):
    e=experiment
    for model in ['Model 3','Model Y']:
        for i in range(13):
            v=copy.deepcopy(e.vehicles[0]);n=len(e.vehicles)+1
            v.update(vehicleId=n,vin=f'5YJ3E1EA0MF{n:06d}',model=model,parentModel=model)
            e.vehicles.append(v)
    def send(url,**kwargs):
        response=e.send(url,**kwargs)
        q=kwargs['json']
        if q['filters'].get('makes',[{}])[0].get('parentModels')==[{'name':'Model 3'}] and q['pagination']['page']==2:
            data=json.loads(response.content)
            data['inventory']['vehicles'][0]=copy.deepcopy(e.vehicles[0])
            response.content=json.dumps(data).encode()
        return response
    result=run(e,send)
    assert not result['primary_queries_complete']
    isolated=[x for x in result['entries'] if x.get('failure_scope')]
    assert isolated and isolated[0]['query']['query_id']=='make_000_model_000'
    assert any(x.get('retry_of')=='make_000_model_000' for x in isolated)
    assert next(x for x in result['entries'] if x['query']['query_id']=='make_000_model_001')['query_complete']
    assert not (e.folder.parent/'access_stop.json').exists()
    output=catalog.export_catalog(e.folder,output=tmp_path/'partial')
    coverage=pd.read_csv(output/'coverage.csv')
    assert not coverage.loc[coverage.query_id.eq('make_000_model_000'),'query_complete'].item()


def test_database_corruption_is_not_hidden_by_fresh_import(experiment,tmp_path):
    import sqlite3
    e=experiment;run(e)
    with sqlite3.connect(e.folder/'make_000_model_000/vehicle.sqlite') as connection:
        connection.execute('UPDATE vehicle_observations SET asking_price_usd=1')
    with pytest.raises(ValueError,match='SQLite differs'):
        catalog.export_catalog(e.folder,output=tmp_path/'corrupt-export')
    assert not (tmp_path/'corrupt-export').exists()


def test_deadline_response_is_not_success_even_when_last_query(experiment):
    e=experiment
    def send(url,**kwargs):
        response=e.send(url,**kwargs)
        if len(e.starts)==11: e.clock.seconds+=21601
        return response
    report=run(e,send)
    assert report['status']=='stopped' and report['requests']==11


def test_unresolved_previous_date_does_not_block_a_new_attempt(experiment):
    e=experiment
    previous=e.folder.parent/'2026-09-18';previous.mkdir(parents=True)
    budget_text=json.dumps({'budget':{'pending_request':False}})
    report_text=json.dumps({'status':'running'})
    (previous/'catalog_budget.json').write_text(budget_text)
    (previous/'catalog_report.json').write_text(report_text)
    report=run(e)
    assert report['status']=='collection_finished'
    assert (previous/'catalog_budget.json').read_text()==budget_text
    assert (previous/'catalog_report.json').read_text()==report_text


def test_empty_catalog_keeps_zero_and_does_not_invent_inventory(experiment,tmp_path):
    e=experiment;e.vehicles.clear()
    report=run(e)
    assert report['primary_queries_complete'] and report['primary_observed_vins']==0
    assert not report['geographic_membership_stable']
    output=catalog.export_catalog(e.folder,output=tmp_path/'empty')
    assert pd.read_csv(output/'observations.csv').empty
