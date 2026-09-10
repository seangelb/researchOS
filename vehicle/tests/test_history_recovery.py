import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from test_search import response_data
from vehicle_tracker.search_plan import collect_plan


def reply(data):
    return Mock(status_code=200,headers={'content-type':'application/json'},content=b'fixture',json=Mock(return_value=data))


@pytest.mark.parametrize('boundary',['after_retention','after_commit','second_request'])
def test_first_query_interruption_keeps_plan_and_restarts(tmp_path,response_data,monkeypatch,boundary):
    import vehicle_tracker.search as search
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep',lambda _:None)
    plan=[dict(query_id='first',zip_code='08542',filters={})]
    data=copy.deepcopy(response_data)
    data['inventory']['pagination'].update(totalMatchedInventory=27,totalMatchedPages=2)
    with monkeypatch.context() as patcher:
        if boundary=='after_retention':
            original=search.retain_capture
            def interrupted(*args,**kwargs):
                original(*args,**kwargs)
                raise KeyboardInterrupt()
            patcher.setattr(search,'retain_capture',interrupted)
        if boundary=='after_commit':
            original=search.store_capture
            def interrupted(*args,**kwargs):
                original(*args,**kwargs)
                raise KeyboardInterrupt()
            patcher.setattr(search,'store_capture',interrupted)
        with pytest.raises(KeyboardInterrupt):
            collect_plan(plan,destination=tmp_path/'old',post=Mock(side_effect=[reply(data),KeyboardInterrupt()]))
    assert (tmp_path/'old/query_plan.json').is_file()
    assert json.loads((tmp_path/'old/run_report.json').read_text())['queries']==plan
    retained={str(p):p.read_bytes() for p in (tmp_path/'old').rglob('*') if p.is_file()}
    post=Mock(return_value=reply(response_data))
    if boundary == 'second_request':
        with pytest.raises(ValueError, match='blocks resume'):
            collect_plan(plan,destination=tmp_path/'new',resume_from=tmp_path/'old/run_report.json',post=post)
        post.assert_not_called()
        assert not (tmp_path/'new').exists()
        assert all(Path(p).read_bytes()==content for p,content in retained.items())
        return
    resumed=collect_plan(plan,destination=tmp_path/'new',resume_from=tmp_path/'old/run_report.json',post=post)
    assert resumed['all_queries_complete'] and post.call_args.kwargs['json']['pagination']['page']==1
    assert all(Path(p).read_bytes()==content for p,content in retained.items())


def test_complete_child_recovers_when_plan_missing_or_behind(tmp_path,response_data,monkeypatch):
    import vehicle_tracker.search_plan as module
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep',lambda _:None)
    plan=[dict(query_id='first',zip_code='08542',filters={})]
    original=module.collect_search
    def interrupted(**kwargs):
        original(**kwargs)
        raise KeyboardInterrupt()
    with monkeypatch.context() as patcher:
        patcher.setattr(module,'collect_search',interrupted)
        with pytest.raises(KeyboardInterrupt):
            collect_plan(plan,destination=tmp_path/'old',post=Mock(return_value=reply(response_data)))
    for mode in ('behind','missing'):
        if mode=='missing': (tmp_path/'old/run_report.json').unlink()
        post=Mock(side_effect=AssertionError('Complete child must not be downloaded again'))
        resumed=collect_plan(plan,destination=tmp_path/mode,resume_from=tmp_path/'old/run_report.json',post=post)
        assert resumed['all_queries_complete'] and resumed['requests']==0
        assert resumed['observation_started_utc'] < resumed['started_utc']
        assert resumed['outcomes'][0]['resumed']


def test_atomic_checkpoint_failure_preserves_previous_file(tmp_path,monkeypatch):
    import os
    from vehicle_tracker.storage import write_json_atomic
    target=tmp_path/'checkpoint.json'
    write_json_atomic(target,{'generation':1})
    def interrupted(*args): raise OSError('simulated replace failure')
    monkeypatch.setattr(os,'replace',interrupted)
    with pytest.raises(OSError): write_json_atomic(target,{'generation':2})
    assert json.loads(target.read_text())=={'generation':1}


def test_recovered_child_modified_database_is_rejected(tmp_path,response_data,monkeypatch):
    import sqlite3
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep',lambda _:None)
    plan=[dict(query_id='first',zip_code='08542',filters={})]
    collect_plan(plan,destination=tmp_path/'old',post=Mock(return_value=reply(response_data)))
    (tmp_path/'old/run_report.json').unlink()
    with sqlite3.connect(tmp_path/'old/first/vehicle.sqlite') as connection:
        connection.execute('UPDATE vehicle_observations SET asking_price_usd=1')
    with pytest.raises(ValueError):
        collect_plan(plan,destination=tmp_path/'new',resume_from=tmp_path/'old/run_report.json',post=Mock())
    assert not (tmp_path/'new').exists()


def test_recovered_child_claimed_total_must_reconcile(tmp_path,response_data):
    plan=[dict(query_id='first',zip_code='08542',filters={})]
    collect_plan(plan,destination=tmp_path/'old',post=Mock(return_value=reply(response_data)))
    (tmp_path/'old/run_report.json').unlink()
    child=tmp_path/'old/first/run_report.json'
    report=json.loads(child.read_text())
    report['reported_total']=99
    child.write_text(json.dumps(report))
    post=Mock()
    with pytest.raises(ValueError,match='reconcile'):
        collect_plan(plan,destination=tmp_path/'new',resume_from=tmp_path/'old/run_report.json',post=post)
    post.assert_not_called()
    assert not (tmp_path/'new').exists()
