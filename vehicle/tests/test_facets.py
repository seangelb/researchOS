"""Synthetic transport, real durable files: no external requests in these tests."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from vehicle_tracker import facets


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    class Clock:
        seconds = 0
        def now(self): return datetime(2026,9,18,14,30,tzinfo=timezone.utc)+timedelta(seconds=self.seconds)
        def sleep(self, seconds): self.seconds += seconds
        def mono(self): return 1000+self.seconds
    clock = Clock()
    monkeypatch.setattr(facets, 'utcnow', clock.now)
    monkeypatch.setattr('vehicle_tracker.cycles.utcnow', clock.now)
    monkeypatch.setattr('vehicle_tracker.collect.time.monotonic', clock.mono)
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', clock.sleep)
    raw = dict(makes={'Tesla':dict(key='Tesla',count=1,isApplied=False,parentModels=[])},
               year=dict(min=2022,max=2022))
    basis = dict(request=facets.build_search_request(filters={},zip_code='08542'),facet_data=raw)
    basis_path = tmp_path/'basis.json';basis_path.write_text(json.dumps(basis),encoding='utf-8')
    plan = dict(format='carvana-facet-experiment-v1', basis_source='basis.json',
                basis_sha256=facets.digest(basis_path), makes=['Tesla'],years=[2022],zip_code='08542',
                location_filter=False, max_requests=3,max_seconds=3600,minimum_spacing_seconds=3,
                cycle_date='2026-09-18',timezone='America/New_York',window_start='2026-09-18T14:30:00Z',
                window_end='2026-09-18T15:30:00Z',destination='capture',
                latest_start='2026-09-18T14:35:00Z',
                queries=facets.facet_queries(['Tesla'],[2022],'08542'))
    path = tmp_path/'plan.json';path.write_text(json.dumps(plan),encoding='utf-8')
    vehicle = dict(vehicleId=1,vin='5YJ3E1EA0MF000001',year=2022,make='Tesla',model='Model 3',
                   parentModel='Model 3',mileage=10000,price={'total':25000},isPurchasePending=False,
                   vehicleLockType=0,vehiclePurchaseType='Purchase',isOnDemand=False)
    def reply(request, mutate=None, status=200, headers=None):
        data = dict(userDeliveryInfo={'zip5':'08542'},
                    inventory=dict(pagination=dict(currentPage=1,pageSize=24,totalMatchedInventory=1,totalMatchedPages=1),vehicles=[copy.deepcopy(vehicle)]),
                    facetData=copy.deepcopy(raw))
        if request['filters']:
            data['facetData']['year'].update(appliedMin=2022,appliedMax=2022)
            data['facetData']['makes']['Tesla'].update(isApplied=True,
                parentModels=[dict(key='Model 3',count=1,isApplied=False,modelIds=[7])])
        if mutate: mutate(data)
        clock.sleep(0.1)
        return SimpleNamespace(content=json.dumps(data).encode(),status_code=status,
            headers={'content-type':'application/json',**(headers or {})},json=lambda:copy.deepcopy(data))
    def send(url, **kwargs):
        assert url==facets.ENDPOINT and kwargs['allow_redirects'] is False
        assert 0 < kwargs['timeout'] <= 30
        return reply(kwargs['json'])
    return SimpleNamespace(path=path,plan=plan,folder=tmp_path/'capture',clock=clock,reply=reply,send=send)


def run(experiment, post=None):
    return facets.collect_facets(experiment.path,expected_sha256=facets.digest(experiment.path),
                                 post=post or Mock(side_effect=experiment.send))


def test_preview_is_read_only_and_exact_scope_changes_are_rejected(experiment):
    before={p:p.read_bytes() for p in experiment.path.parent.iterdir()}
    with patch('socket.socket',side_effect=AssertionError('No network')):
        result,plan=facets.preview_facets(experiment.path)
    assert result['query_count']==3 and result['destination_fresh'] and result['start_eligible']
    assert before=={p:p.read_bytes() for p in experiment.path.parent.iterdir()}
    plan['queries'][1]['filters']['year']['max']=2023
    experiment.path.write_text(json.dumps(plan),encoding='utf-8')
    with pytest.raises(ValueError,match='exact facet grid'): facets.preview_facets(experiment.path)


def test_one_fresh_invocation_counts_spacing_and_replays_every_source(experiment):
    post=Mock(side_effect=experiment.send);result=run(experiment,post)
    assert result['status']=='retained_all_facets' and result['requests']==post.call_count==3
    assert result['inventory_complete'] is False
    starts=[facets.aware(e['request_started_at']) for e in result['entries']]
    assert all((b-a).total_seconds()>=3 for a,b in zip(starts,starts[1:]))
    ledger=json.loads((experiment.folder/'facet_budget.json').read_text())['budget']
    assert ledger['requests']==3 and not ledger['pending_request']
    rows=facets.replay_facets(experiment.folder,as_of=experiment.clock.now().isoformat())
    assert rows.status.eq('retained').all() and rows.count_residual.eq(0).all()
    assert not (experiment.folder/'cycle.json').exists()
    assert not list(experiment.folder.rglob('*.sqlite'))
    with pytest.raises(ValueError,match='fresh destination'):run(experiment,post)
    assert post.call_count==3


@pytest.mark.parametrize('failure',['403','429','challenge','html','timeout','facet_schema','wrong_zip','wrong_year','applied_make','identity','storage','deadline','interrupt'])
def test_global_stops_keep_one_charged_attempt_and_never_retry(experiment,monkeypatch,failure):
    def send(url,**kwargs):
        if failure=='timeout': raise TimeoutError('private transport text')
        if failure=='interrupt': raise KeyboardInterrupt()
        mutate=None;status=200;headers={}
        if failure in {'403','429'}: status=int(failure)
        elif failure=='challenge':headers['cf-mitigated']='challenge'
        elif failure=='html':headers['content-type']='text/html'
        elif failure=='facet_schema':mutate=lambda d:d['facetData'].pop('year')
        elif failure=='wrong_zip':mutate=lambda d:d['userDeliveryInfo'].update(zip5='98101')
        elif failure=='applied_make':mutate=lambda d:d['facetData']['makes']['Tesla'].update(isApplied=True)
        elif failure=='deadline':experiment.clock.sleep(3601)
        elif failure=='wrong_year' and kwargs['json']['filters']:
            mutate=lambda d:d['facetData']['year'].update(appliedMin=2021)
        elif failure=='identity' and kwargs['json']['filters']:
            mutate=lambda d:d['inventory']['vehicles'][0].update(vehicleId=2)
        return experiment.reply(kwargs['json'],mutate,status,headers)
    post=Mock(side_effect=send)
    if failure=='storage':monkeypatch.setattr(facets,'retain_capture',Mock(side_effect=OSError('private path')))
    if failure=='interrupt':
        with pytest.raises(KeyboardInterrupt):run(experiment,post)
        result=json.loads((experiment.folder/'facet_report.json').read_text())
    else:result=run(experiment,post)
    expected=2 if failure in {'wrong_year','identity'} else 1
    assert result['status']=='stopped' and result['requests']==expected==post.call_count
    assert 'private' not in json.dumps(result)
    ledger=json.loads((experiment.folder/'facet_budget.json').read_text())['budget']
    assert ledger['stopped'] and ledger['requests']==expected
    assert ledger['pending_request'] is (failure in {'timeout','interrupt'})
    rows=facets.replay_facets(experiment.folder,as_of=experiment.clock.now().isoformat())
    assert len(rows)==3 and rows.status.eq('failed').sum()==1
    assert rows.loc[rows.status.ne('retained'),'reported_total'].isna().all()


@pytest.mark.parametrize('failure',['hash','expired','basis'])
def test_preflight_mismatch_does_not_create_destination_or_send(experiment,failure):
    expected=facets.digest(experiment.path)
    if failure=='hash':expected='0'*64
    elif failure=='expired':experiment.clock.sleep(3600)
    else:(experiment.path.parent/'basis.json').write_text('{}')
    post=Mock()
    with pytest.raises(ValueError):facets.collect_facets(experiment.path,expected_sha256=expected,post=post)
    assert not experiment.folder.exists() and not post.called


def test_unknown_fields_and_sensitive_values_never_enter_facet_projection(experiment):
    def send(url,**kwargs):
        def extra(data):
            data['privateToken']='not-public-value'
            data['facetData']['privateToken']='not-public-value'
            data['facetData']['makes']['Tesla']['privateToken']='not-public-value'
        return experiment.reply(kwargs['json'],extra)
    run(experiment,Mock(side_effect=send))
    assert all('not-public-value' not in p.read_text() for p in experiment.folder.rglob('*.json'))


def test_unknown_model_structure_at_native_zero_stays_unknown(experiment):
    def send(url,**kwargs):
        def zero(data):
            if kwargs['json']['filters']:
                data['inventory']['pagination'].update(totalMatchedInventory=0,totalMatchedPages=0)
                data['inventory']['vehicles']=[]
                data['facetData']['makes']={}
        return experiment.reply(kwargs['json'],zero)
    run(experiment,Mock(side_effect=send))
    rows=facets.replay_facets(experiment.folder,as_of=experiment.clock.now().isoformat())
    assert rows.iloc[1].reported_total==0 and pd.isna(rows.iloc[1].model_count_sum)


def test_changed_native_categories_are_gaps_and_do_not_add_requests(experiment):
    def send(url,**kwargs):
        def change(data):
            if not kwargs['json']['filters']:
                data['facetData']['year'].update(max=2023)
                data['facetData']['makes']['New Make']=dict(key='New Make',count=2,isApplied=False,parentModels=[])
        return experiment.reply(kwargs['json'],change)
    post=Mock(side_effect=send);run(experiment,post)
    rows=facets.replay_facets(experiment.folder,as_of=experiment.clock.now().isoformat())
    assert post.call_count==3 and rows.iloc[0].new_native_makes=='New Make'
    assert rows.iloc[0].year_boundary_changed and pd.isna(rows.iloc[0].outside_year_grid_count)
    assert rows.iloc[0].count_residual==-2


@pytest.mark.parametrize('native',[None,-1,True,-1.5])
def test_invalid_native_count_is_preserved_before_global_stop(experiment,native):
    def send(url,**kwargs):
        return experiment.reply(kwargs['json'],lambda d:d['facetData']['makes']['Tesla'].update(count=native))
    post=Mock(side_effect=send);result=run(experiment,post)
    assert post.call_count==1 and result['status']=='stopped'
    saved=json.loads(Path(result['entries'][0]['source_path']).read_text())
    saved_value=saved['facet_data']['makes']['Tesla']['count']
    assert type(saved_value) is type(native) and saved_value==native


@pytest.mark.parametrize('what',['source','plan','budget','diagnostic'])
def test_replay_rejects_changed_retained_evidence(experiment,what):
    result=run(experiment)
    if what=='source':Path(result['entries'][0]['source_path']).write_text('{}')
    elif what=='plan':(experiment.folder/'selected_plan.json').write_text('{}')
    elif what=='budget':
        path=experiment.folder/'facet_budget.json';d=json.loads(path.read_text());d['budget']['requests']=2;path.write_text(json.dumps(d))
    else:
        path=experiment.folder/'facet_report.json';d=json.loads(path.read_text());d['entries'][0]['diagnostic']['count_residual']=1;path.write_text(json.dumps(d))
    with pytest.raises(ValueError):facets.replay_facets(experiment.folder,as_of=experiment.clock.now().isoformat())


def test_cutoff_withholds_future_counts_and_failure_reasons(experiment):
    start=experiment.clock.now().isoformat()
    run(experiment,Mock(side_effect=TimeoutError('private')))
    report=json.loads((experiment.folder/'facet_report.json').read_text())
    report['entries'][0]['failed_at']='2026-09-18T14:31:00+00:00'
    (experiment.folder/'facet_report.json').write_text(json.dumps(report))
    rows=facets.replay_facets(experiment.folder,as_of=start)
    assert rows.reported_total.isna().all() and rows.iloc[0].status=='evidence unavailable at cutoff'
    assert 'reason' not in rows or rows.reason.isna().all()


def test_cli_preview_requires_no_writes_and_replay_export_is_fresh(experiment,tmp_path):
    script=Path(__file__).parents[1]/'scripts/collect_carvana_facets.py'
    spec=importlib.util.spec_from_file_location('facet_cli',script);cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    with patch('socket.socket',side_effect=AssertionError('Network forbidden')):
        assert cli.main(['--plan',str(experiment.path)])==0
    assert not experiment.folder.exists()
    run(experiment)
    out=tmp_path/'export';args=['--replay',str(experiment.folder),'--as-of',experiment.clock.now().isoformat(),'--export',str(out)]
    assert cli.main(args)==0
    manifest=json.loads((out/'manifest.json').read_text())
    assert manifest['summary']['planned_requests']==3
    with pytest.raises(FileExistsError):cli.main(args)
