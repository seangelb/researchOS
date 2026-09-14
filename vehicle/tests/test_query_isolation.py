"""Opt-in query continuation must not relax evidence or shared-budget checks."""
import copy
import json
from unittest.mock import Mock

import pytest

from test_search import response_data
from test_search_evidence import response
from vehicle_tracker.collect import NavigationBudget
from vehicle_tracker.search_plan import collect_plan


def pages(data):
    first=copy.deepcopy(data)
    first['inventory']['pagination'].update(totalMatchedInventory=27,totalMatchedPages=2)
    second=copy.deepcopy(first)
    second['inventory']['pagination']['currentPage']=2
    return first,second  # Identical listing/VIN pairs on a later page.


@pytest.mark.parametrize('isolate',[False,True])
def test_duplicate_query_preserved_and_next_query_only_runs_when_enabled(tmp_path,response_data,monkeypatch,isolate):
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep',lambda _:None)
    first,second=pages(response_data)
    post=Mock(side_effect=[response(first),response(second),response(response_data)])
    budget=NavigationBudget(max_requests=3)
    result=collect_plan([dict(query_id=name,filters={},zip_code='08542') for name in ['a','b']],
        destination=tmp_path/'run',full_plan=True,budget=budget,post=post,isolate_pagination=isolate)
    assert post.call_count==budget.requests==(3 if isolate else 2)
    assert not result['all_queries_complete']
    assert result['outcomes'][0]['query_complete'] is False
    assert result['outcomes'][1]['query_complete'] is isolate
    failed=json.loads((tmp_path/'run/a/run_report.json').read_text())
    assert failed['pages'][-1]['stored_rows']==0 and failed['pages'][-1]['status']=='failed'
    assert failed['unique_vins']==3
    assert budget.stopped is (not isolate)
    if isolate:
        assert result['stop_reason']=='queries_finished_with_incomplete_coverage'


@pytest.mark.parametrize('failure',['403','429','schema','wrong_zip','transport','conflicting_vin','missing_vin','storage'])
def test_global_failures_do_not_continue(tmp_path,response_data,monkeypatch,failure):
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep',lambda _:None)
    first,second=pages(response_data)
    reply=response(second)
    if failure in ['403','429']:reply=response(second,status=int(failure))
    if failure=='schema':reply=response({'unexpected':'shape'})
    if failure=='wrong_zip':
        second['userDeliveryInfo']['zip5']='90012'
        reply=response(second)
    if failure=='transport':reply=TimeoutError('mock timeout')
    if failure=='conflicting_vin':
        second['inventory']['vehicles'][0]['vin']=second['inventory']['vehicles'][1]['vin']
        reply=response(second)
    if failure=='missing_vin':
        second['inventory']['vehicles'][0]['vin']=None
        reply=response(second)
    if failure=='storage':
        import vehicle_tracker.search as search
        original=search.store_capture
        def failing(*args,**kwargs):
            if kwargs.get('error'):raise OSError('mock failed capture write')
            return original(*args,**kwargs)
        monkeypatch.setattr(search,'store_capture',failing)
    budget=NavigationBudget(max_requests=5)
    post=Mock(side_effect=[response(first),reply,response(response_data)])
    # A pagination-labelled failure with invalid identity/storage evidence fails
    # reconciliation and propagates. Other global failures return a stopped plan.
    try:
        result=collect_plan([dict(query_id=name,filters={},zip_code='08542') for name in ['a','b']],
            destination=tmp_path/'run',full_plan=True,budget=budget,post=post,isolate_pagination=True)
        assert result['outcomes'][1]['status']=='unattempted'
    except (ValueError,AssertionError,KeyError):
        assert failure in ['conflicting_vin','missing_vin','storage']
    assert post.call_count==2 and budget.stopped


def test_request_limit_is_not_reset_by_isolation(tmp_path,response_data,monkeypatch):
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep',lambda _:None)
    first,second=pages(response_data)
    budget=NavigationBudget(max_requests=2)
    post=Mock(side_effect=[response(first),response(second)])
    result=collect_plan([dict(query_id=name,filters={},zip_code='08542') for name in ['a','b']],
        destination=tmp_path/'run',full_plan=True,budget=budget,post=post,isolate_pagination=True)
    assert budget.requests==2 and post.call_count==2
    assert result['outcomes'][1]['status']=='unattempted'


@pytest.mark.parametrize('pending',[False,True])
def test_durable_ledger_preserves_counts_and_rejects_unresolved_request(tmp_path,response_data,monkeypatch,pending):
    from datetime import datetime,timedelta,timezone
    from vehicle_tracker import cycles,search_plan
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep',lambda _:None)
    now=datetime.now(timezone.utc)
    queries=[dict(query_id=name,filters={},zip_code='08542') for name in ['a','b']]
    state=cycles.cycle_config(queries,cycle_date=now.date().isoformat(),timezone_name='UTC',
        window_start=now.isoformat(),window_end=(now+timedelta(minutes=10)).isoformat(),
        max_requests=3,max_seconds=600)
    state.update(cycle_id='fixture',created_at=now.isoformat(),attempts=['attempt_0001'],
        budget=dict(requests=0,stopped=False,pending_request=False,last_request_utc=None))
    path=tmp_path/'cycle.json';path.write_text(json.dumps(state))
    budget=cycles.CycleBudget(path)
    original=search_plan.verify_isolated_pagination
    def verify(*args,**kwargs):
        original(*args,**kwargs)
        if pending:
            changed=json.loads(path.read_text());changed['budget']['pending_request']=True
            path.write_text(json.dumps(changed))
    monkeypatch.setattr(search_plan,'verify_isolated_pagination',verify)
    first,second=pages(response_data)
    post=Mock(side_effect=[response(first),response(second),response(response_data)])
    if pending:
        with pytest.raises(ValueError,match='Unreconciled durable request'):
            collect_plan(queries,destination=tmp_path/'attempt_0001',full_plan=True,
                budget=budget,post=post,isolate_pagination=True)
        saved=json.loads(path.read_text())
        assert saved['budget']['stopped'] and saved['budget']['pending_request']
        assert saved['budget']['requests']==post.call_count==2
    else:
        collect_plan(queries,destination=tmp_path/'attempt_0001',full_plan=True,
            budget=budget,post=post,isolate_pagination=True)
        saved=json.loads(path.read_text())
        assert saved['budget']['requests']==post.call_count==3
        assert not saved['budget']['stopped'] and not saved['budget']['pending_request']
        assert len(saved['isolated_query_failures'])==1
        assert saved['max_requests']==3 and saved['window_end']==state['window_end']
        info,coverage,_=cycles.cycle_evidence(path)
        assert not info['coverage_complete'] and coverage.coverage_complete.tolist()==[False,True]
        cycles.import_cycle(path,tmp_path/'history.sqlite')
        from pandas.testing import assert_frame_equal
        for a,b in zip(cycles.read_cycle_history([path]),cycles.read_cycle_history([path],tmp_path/'history.sqlite')):
            if 'listing_id' in a:
                keys=['run_id','capture_id','listing_id']
                a=a.sort_values(keys).reset_index(drop=True)
                b=b.sort_values(keys).reset_index(drop=True)
            assert_frame_equal(a,b,check_dtype=False)


def test_access_failure_after_isolated_query_stops_remaining_work(tmp_path,response_data,monkeypatch):
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep',lambda _:None)
    first,second=pages(response_data)
    budget=NavigationBudget(max_requests=10)
    post=Mock(side_effect=[response(first),response(second),response(response_data,status=429)])
    result=collect_plan([dict(query_id=name,filters={},zip_code='08542') for name in ['a','b','c']],
        destination=tmp_path/'run',full_plan=True,budget=budget,post=post,isolate_pagination=True)
    assert post.call_count==budget.requests==3 and budget.stopped
    assert result['outcomes'][0]['failure_scope'].startswith('query;')
    assert result['outcomes'][1]['outcome_kind']=='access_failure'
    assert result['outcomes'][2]['status']=='unattempted'
    assert 'rate_limited' in result['stop_reason']


def test_even_small_source_database_difference_prevents_continuation(tmp_path,response_data,monkeypatch):
    from vehicle_tracker import search_plan
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep',lambda _:None)
    original=search_plan.read_snapshots
    def changed(database):
        captures,rows=original(database)
        rows.loc[rows.index[0],'asking_price_usd']+=0.001
        return captures,rows
    monkeypatch.setattr(search_plan,'read_snapshots',changed)
    first,second=pages(response_data)
    budget=NavigationBudget(max_requests=3)
    post=Mock(side_effect=[response(first),response(second),response(response_data)])
    with pytest.raises(AssertionError):
        collect_plan([dict(query_id=name,filters={},zip_code='08542') for name in ['a','b']],
            destination=tmp_path/'run',full_plan=True,budget=budget,post=post,isolate_pagination=True)
    assert post.call_count==budget.requests==2 and budget.stopped
