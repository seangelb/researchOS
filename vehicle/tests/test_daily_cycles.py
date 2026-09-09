import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from test_search import response_data
from vehicle_tracker import cycles
from vehicle_tracker.search_plan import collect_plan


@pytest.fixture
def clock(monkeypatch):
    stamp = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(cycles, 'utcnow', lambda: stamp)
    class FixedDatetime:
        @staticmethod
        def now(tz=None):
            return stamp
    monkeypatch.setattr('vehicle_tracker.search.datetime', FixedDatetime)
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', lambda _: None)
    return stamp


def options(tmp_path):
    return dict(destination=tmp_path/'cycle', cycle_date='2026-09-08', timezone_name='UTC',
                window_start='2026-09-08T11:00:00Z', window_end='2026-09-08T15:00:00Z',
                max_requests=20, max_seconds=3600)


def plan():
    return [dict(query_id='all', zip_code='08542', filters={})]


def reply(data, status=200):
    return Mock(status_code=status, headers={'content-type':'application/json'},
                content=b'offline fixture', json=Mock(return_value=data))


def test_complete_cycle_root_import_is_idempotent_and_read_only(tmp_path, response_data, clock):
    result = cycles.collect_cycle(plan(), **options(tmp_path), post=Mock(return_value=reply(response_data)))
    path = tmp_path/'cycle/cycle.json'
    assert result['coverage_complete'] and result['budget']['requests'] == 1
    first = cycles.import_cycle(path, tmp_path/'history.sqlite')
    second = cycles.import_cycle(path, tmp_path/'history.sqlite')
    assert first.imported_rows.sum() == 3 and second.imported_rows.sum() == 0
    before = (tmp_path/'history.sqlite').read_bytes()
    days, rows = cycles.read_cycle_history([path], tmp_path/'history.sqlite')
    assert days.coverage_complete.all() and len(rows) == 3 and rows.cycle_id.nunique() == 1
    assert before == (tmp_path/'history.sqlite').read_bytes()
    from vehicle_tracker.events import vin_events
    assert len(vin_events(days,rows)) == 3


def test_full_mode_does_not_stop_at_sample_target(tmp_path, response_data, clock):
    first = copy.deepcopy(response_data)
    first['inventory']['vehicles'] = [dict(first['inventory']['vehicles'][0], vehicleId=100+i, vin=f'{i:017}') for i in range(24)]
    first['inventory']['pagination'].update(totalMatchedInventory=27,totalMatchedPages=2)
    second = copy.deepcopy(response_data)
    second['inventory']['pagination'].update(currentPage=2,totalMatchedInventory=27,totalMatchedPages=2)
    post = Mock(side_effect=[reply(first),reply(second)])
    result = collect_plan(plan(),destination=tmp_path/'full',target_listings=1,full_plan=True,post=post)
    assert result['all_queries_complete'] and post.call_count == 2
    assert not result['target_reached'] and result['target_listings'] is None


@pytest.mark.parametrize('status', [403,429])
def test_access_stop_and_consumed_requests_survive_restart(tmp_path, response_data, clock, status):
    post = Mock(return_value=reply(response_data,status))
    first = cycles.collect_cycle(plan(),**options(tmp_path),post=post)
    assert not first['coverage_complete'] and first['budget']['stopped']
    with pytest.raises(ValueError,match='stopped'):
        cycles.collect_cycle(plan(),**options(tmp_path),resume=True,post=post)
    assert post.call_count == 1


def test_uncertain_request_remains_counted_and_blocks_retry(tmp_path, response_data, clock):
    post = Mock(side_effect=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        cycles.collect_cycle(plan(),**options(tmp_path),post=post)
    state = json.loads((tmp_path/'cycle/cycle.json').read_text())
    assert state['budget']['requests'] == 1 and state['budget']['pending_request']
    with pytest.raises(ValueError,match='uncertain'):
        cycles.collect_cycle(plan(),**options(tmp_path),resume=True,post=post)
    assert post.call_count == 1


def test_completed_child_recovers_after_interruption_without_new_requests(tmp_path,response_data,clock,monkeypatch):
    import vehicle_tracker.search_plan as search_plan
    original = search_plan.query_outcome
    monkeypatch.setattr(search_plan,'query_outcome',Mock(side_effect=KeyboardInterrupt()))
    post = Mock(return_value=reply(response_data))
    with pytest.raises(KeyboardInterrupt):
        cycles.collect_cycle(plan(),**options(tmp_path),post=post)
    monkeypatch.setattr(search_plan,'query_outcome',original)
    retained = {p:p.read_bytes() for p in (tmp_path/'cycle/attempt_0001').rglob('*') if p.is_file()}
    result = cycles.collect_cycle(plan(),**options(tmp_path),resume=True,post=post)
    assert result['coverage_complete'] and result['budget']['requests'] == 1 and post.call_count == 1
    assert all(p.read_bytes() == value for p,value in retained.items())


@pytest.mark.parametrize('change',['date','plan','budget','window'])
def test_resume_cannot_relabel_old_evidence(tmp_path,response_data,clock,change):
    args = options(tmp_path)
    queries = plan()
    post = Mock(return_value=reply(response_data))
    cycles.collect_cycle(queries,**args,post=post)
    if change == 'date': args['cycle_date']='2026-09-09'
    if change == 'plan': queries[0]['zip_code']='08540'
    if change == 'budget': args['max_requests']=21
    if change == 'window': args['window_end']='2026-09-08T16:00:00Z'
    with pytest.raises(ValueError):
        cycles.collect_cycle(queries,**args,resume=True,post=post)
    assert post.call_count == 1


def test_stale_source_clock_blocks_daily_coverage(tmp_path,response_data,clock,monkeypatch):
    class Yesterday:
        @staticmethod
        def now(tz=None): return clock-timedelta(days=1)
    monkeypatch.setattr('vehicle_tracker.search.datetime',Yesterday)
    result=cycles.collect_cycle(plan(),**options(tmp_path),post=Mock(return_value=reply(response_data)))
    assert not result['coverage_complete'] and 'window' in result['coverage_reason']
    path=tmp_path/'cycle/cycle.json'
    cycles.import_cycle(path,tmp_path/'history.sqlite')
    days,rows=cycles.read_cycle_history([path],tmp_path/'history.sqlite')
    assert rows.empty and not days.coverage_complete.any()


def test_complete_zero_is_not_a_missing_query(tmp_path,response_data,clock):
    response_data['inventory']['vehicles']=[]
    response_data['inventory']['pagination'].update(totalMatchedInventory=0,totalMatchedPages=0)
    result=cycles.collect_cycle(plan(),**options(tmp_path),post=Mock(return_value=reply(response_data)))
    assert result['coverage_complete']
    cycles.import_cycle(tmp_path/'cycle/cycle.json',tmp_path/'history.sqlite')
    days,rows=cycles.read_cycle_history([tmp_path/'cycle/cycle.json'],tmp_path/'history.sqlite')
    assert days.coverage_complete.all() and rows.empty


def test_missing_import_is_a_coverage_diagnostic(tmp_path,response_data,clock):
    cycles.collect_cycle(plan(),**options(tmp_path),post=Mock(return_value=reply(response_data)))
    days,rows=cycles.read_cycle_history([tmp_path/'cycle/cycle.json'],tmp_path/'missing.sqlite')
    assert not days.coverage_complete.any() and rows.empty
    assert 'import' in days.coverage_reason.iloc[0].lower()
    assert not (tmp_path/'missing.sqlite').exists()


@pytest.mark.parametrize('field,value',[('max_requests',10001),('max_seconds',float('nan')),
    ('window_start','2026-09-08T11:00:00'),('cycle_date','2026-09-09')])
def test_invalid_configuration_never_writes(tmp_path,clock,field,value):
    args=options(tmp_path)
    args[field]=value
    with pytest.raises(ValueError): cycles.collect_cycle(plan(),**args,post=Mock())
    assert not args['destination'].exists()


def test_completed_recovery_needs_no_budget_or_live_window(tmp_path,response_data,clock,monkeypatch):
    args=options(tmp_path)
    args['max_requests']=1
    post=Mock(return_value=reply(response_data))
    cycles.collect_cycle(plan(),**args,post=post)
    monkeypatch.setattr(cycles,'utcnow',lambda:clock+timedelta(days=1))
    result=cycles.collect_cycle(plan(),**args,resume=True,post=post)
    assert result['coverage_complete'] and post.call_count == 1


def test_missing_stored_observation_blocks_absence_comparison(tmp_path,response_data,clock):
    import sqlite3
    cycles.collect_cycle(plan(),**options(tmp_path),post=Mock(return_value=reply(response_data)))
    path=tmp_path/'cycle/cycle.json'
    database=tmp_path/'history.sqlite'
    cycles.import_cycle(path,database)
    with sqlite3.connect(database) as connection:
        connection.execute('DELETE FROM observations WHERE rowid=(SELECT MIN(rowid) FROM observations)')
    days,_=cycles.read_cycle_history([path],database)
    assert not days.coverage_complete.any()


@pytest.mark.parametrize('case',['zero','missing_import'])
def test_empty_daily_reader_runs_notebook_cells(tmp_path,response_data,clock,monkeypatch,case):
    from test_daily_notebook import execute_daily
    if case == 'zero':
        response_data['inventory']['vehicles']=[]
        response_data['inventory']['pagination'].update(totalMatchedInventory=0,totalMatchedPages=0)
    cycles.collect_cycle(plan(),**options(tmp_path),post=Mock(return_value=reply(response_data)))
    path=tmp_path/'cycle/cycle.json'
    database=tmp_path/'history.sqlite'
    if case == 'zero': cycles.import_cycle(path,database)
    days,rows=cycles.read_cycle_history([path],database)
    scope=execute_daily(monkeypatch,tmp_path,days,rows)
    assert scope['daily_observations'].empty
    assert scope['daily_summary'].observed_vins.iloc[0] == 0


def test_partial_restart_budget_and_spacing_persist(tmp_path,response_data,clock,monkeypatch):
    bad=copy.deepcopy(response_data)
    bad['inventory']['vehicles'][0]['vin']='invalid'
    post=Mock(side_effect=[reply(bad),reply(response_data)])
    first=cycles.collect_cycle(plan(),**options(tmp_path),post=post)
    assert first['budget']['requests']==1 and not first['budget']['stopped']
    sleeps=[]
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep',sleeps.append)
    second=cycles.collect_cycle(plan(),**options(tmp_path),resume=True,post=post)
    assert second['coverage_complete'] and second['budget']['requests']==2
    assert sleeps and sleeps[0] > 2.9


def test_cumulative_limit_cannot_be_reset_by_resume(tmp_path,response_data,clock):
    response_data['inventory']['vehicles'][0]['vin']='invalid'
    args=options(tmp_path)
    args['max_requests']=1
    post=Mock(return_value=reply(response_data))
    cycles.collect_cycle(plan(),**args,post=post)
    with pytest.raises(ValueError,match='budget exhausted'):
        cycles.collect_cycle(plan(),**args,resume=True,post=post)
    assert post.call_count==1


def test_cycle_os_lock_refuses_a_second_owner(tmp_path):
    with cycles.cycle_lock(tmp_path):
        with pytest.raises((ValueError,OSError)):
            with cycles.cycle_lock(tmp_path):
                pytest.fail('A second owner acquired the same cycle')


@pytest.mark.parametrize('mode',['sample','full','cycle'])
def test_collection_cli_preview_never_collects_or_writes(tmp_path,monkeypatch,mode):
    import importlib.util
    module_path=Path(__file__).parents[1]/'scripts/collect_carvana_search.py'
    spec=importlib.util.spec_from_file_location('daily_collection_cli',module_path)
    command=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    monkeypatch.setattr(command,'ROOT',tmp_path)
    monkeypatch.setattr(command,'collect_plan',Mock(side_effect=AssertionError('No collection')))
    monkeypatch.setattr(command,'collect_cycle',Mock(side_effect=AssertionError('No collection')))
    manifest=tmp_path/'plan.json'
    manifest.write_text(json.dumps(dict(queries=plan())))
    args=['--plan',str(manifest),'--experiment','preview']
    if mode=='full': args+=['--full-plan']
    if mode=='cycle': args+=['--cycle-date','2026-09-08','--timezone','UTC',
        '--window-start','2026-09-08T07:00:00Z','--window-end','2026-09-08T13:00:00Z',
        '--max-requests','6000','--max-seconds','21600']
    assert command.main(args)==0 and not (tmp_path/'data').exists()


def test_cycle_cli_import_export_and_repeat(tmp_path,response_data,clock):
    from test_history_export import command
    cycles.collect_cycle(plan(),**options(tmp_path),post=Mock(return_value=reply(response_data)))
    path=tmp_path/'cycle/cycle.json'
    database=tmp_path/'history.sqlite'
    args=['--cycle-report',str(path),'--database',str(database)]
    assert command.main(args)==0 and not database.exists()
    assert command.main(args+['--write'])==0
    assert command.main(args+['--write'])==0
    before=database.read_bytes()
    assert command.main(args+['--export',str(tmp_path/'tables')])==0
    assert database.read_bytes()==before
    table=pd.read_csv(tmp_path/'tables/daily_counts.csv')
    assert table.observed_vins.iloc[0]==3 and table.estimated_sales.isna().all()
    with pytest.raises(FileExistsError): command.main(args+['--export',str(tmp_path/'tables')])



def test_resume_completed_and_unattempted_queries(tmp_path, response_data, clock, monkeypatch):
    import vehicle_tracker.search_plan as search_plan
    queries = [dict(query_id='first', zip_code='08542', filters={}),
               dict(query_id='second', zip_code='08542', filters={'year': {'min': 2010, 'max': 2027}})]
    original = search_plan.query_outcome
    monkeypatch.setattr(search_plan, 'query_outcome', Mock(side_effect=KeyboardInterrupt()))
    post = Mock(return_value=reply(response_data))
    with pytest.raises(KeyboardInterrupt):
        cycles.collect_cycle(queries, **options(tmp_path), post=post)
    monkeypatch.setattr(search_plan, 'query_outcome', original)
    path = tmp_path/'cycle/cycle.json'
    state, coverage, _ = cycles.cycle_evidence(path)
    assert coverage.coverage_complete.tolist() == [True, False]
    assert coverage.reason.iloc[1] == 'No attempted query'
    assert state['budget']['requests'] == 1 and not state['budget']['pending_request']
    retained = {p: p.read_bytes() for p in (tmp_path/'cycle/attempt_0001').rglob('*') if p.is_file()}
    result = cycles.collect_cycle(queries, **options(tmp_path), resume=True, post=post)
    assert result['coverage_complete'] and result['budget']['requests'] == 2
    assert post.call_count == 2
    assert all(p.read_bytes() == value for p, value in retained.items())
    _, complete, _ = cycles.cycle_evidence(path)
    assert complete.query_complete.eq(1).all()


@pytest.mark.parametrize('failure', ['403', '429', 'challenge', 'parser'])
def test_cycle_preserves_failure_without_observation_clock(tmp_path, response_data, clock, failure):
    response = reply(response_data, int(failure) if failure.isdigit() else 200)
    if failure == 'challenge':
        response.headers['cf-mitigated'] = 'challenge'
    if failure == 'parser':
        response_data['inventory']['vehicles'][0]['vin'] = 'invalid'
    post = Mock(return_value=response)
    result = cycles.collect_cycle(plan(), **options(tmp_path), post=post)
    state, coverage, reports = cycles.cycle_evidence(tmp_path/'cycle/cycle.json')
    native = json.loads(reports[0].read_text())
    assert native['reason']
    assert result['coverage_reason'] == state['coverage_reason'] == native['reason']
    assert coverage.reason.iloc[0] == native['reason']
    assert not coverage.within_window.any() and not state['coverage_complete']
    assert post.call_count == state['budget']['requests'] == 1
    assert state['budget']['stopped'] == (failure != 'parser')


def test_resume_stale_completed_query_still_blocks(tmp_path, response_data, clock, monkeypatch):
    class Yesterday:
        @staticmethod
        def now(tz=None):
            return clock-timedelta(days=1)
    monkeypatch.setattr('vehicle_tracker.search.datetime', Yesterday)
    post = Mock(return_value=reply(response_data))
    result = cycles.collect_cycle(plan(), **options(tmp_path), post=post)
    assert result['coverage_reason'] == 'Outside daily observation window'
    with pytest.raises(ValueError, match='Completed query is outside'):
        cycles.collect_cycle(plan(), **options(tmp_path), resume=True, post=post)
    assert post.call_count == 1
