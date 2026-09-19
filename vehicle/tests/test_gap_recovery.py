"""Synthetic fixed-plan recovery; no live endpoint support or coverage is implied."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

from vehicle_tracker import gap_recovery as recovery
from vehicle_tracker.search import build_search_request, collect_search
from vehicle_tracker.storage import read_snapshots


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    class Clock:
        seconds = 0
        def now(self, tz=None):
            return datetime(2026, 9, 19, 13, tzinfo=timezone.utc)+timedelta(seconds=self.seconds)
        def sleep(self, seconds):
            self.seconds += seconds
        def monotonic(self):
            return 1000+self.seconds
    clock = Clock()
    monkeypatch.setattr(recovery, 'utcnow', clock.now)
    monkeypatch.setattr('vehicle_tracker.cycles.utcnow', clock.now)
    monkeypatch.setattr('vehicle_tracker.search.datetime', SimpleNamespace(now=clock.now))
    monkeypatch.setattr('vehicle_tracker.collect.time.monotonic', clock.monotonic)
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', clock.sleep)
    basis = write(tmp_path/'basis.json', dict(request=build_search_request(filters={}, zip_code='08542'),
        captured_at_utc='2026-09-19T12:00:00Z', zip_code='08542',
        pagination=dict(currentPage=1,pageSize=24,totalMatchedInventory=0,totalMatchedPages=0),
        facet_data=dict(year=dict(min=2010,max=2027),makes={})))
    parents, children, entries, isolated, sources = [], [], [], [], [basis]
    for i in range(25):
        identity = f'parent_{i:03d}'
        filters = {'makes':[{'name':f'Make{i}', 'parentModels':[{'name':f'Model{i}'}]}]}
        source = write(tmp_path/'original'/identity/'run_report.json', dict(query_complete=False,
            outcome_kind='pagination_unstable', filters=filters,zip_code='08542',location_filter=False))
        sha = recovery.digest(source)
        sources.append(source)
        parents.append(dict(parent_id=identity,report_path=str(source),report_sha256=sha,filters=filters))
        entries.append(dict(query=dict(query_id=identity,filters=filters), report=str(source),
                            report_sha256=sha,outcome_kind='pagination_unstable'))
        isolated.append(dict(report=str(source),report_sha256=sha))
        bounds = [{'max':2009}]+[dict(min=y,max=y) for y in range(2010,2028)]+[{'min':2028}]
        for number, year in enumerate(bounds):
            children.append(dict(query_id=identity+f'_year_{number:02d}',parent_id=identity,
                filters=dict(filters,year=year),zip_code='08542',location_filter=False))
    catalog = write(tmp_path/'original/catalog_report.json', dict(status='collection_finished',
        declared_collection_complete=False,capture_directory=str(tmp_path/'original'),requests=25,
        entries=entries,leaf_queries=[e['query'] for e in entries]))
    budget = write(tmp_path/'original/catalog_budget.json', dict(budget=dict(pending_request=False,requests=25),
        isolated_query_failures=isolated))
    audit = write(tmp_path/'audit.json',dict(audit_complete=True,capture=str(tmp_path/'original')))
    sources.extend([catalog,budget,audit])
    plan = dict(format='carvana-gap-recovery-plan-v1',parents=parents,children=children,
        source_hashes={str(p):recovery.digest(p) for p in sources})
    for prefix,path in [('original_catalog',catalog),('original_budget',budget),
                        ('original_terminal_audit',audit),('broad_facet',basis)]:
        plan[prefix+'_path'],plan[prefix+'_sha256']=str(path),recovery.digest(path)
    plan_path = write(tmp_path/'plan.json',plan)
    config = dict(format='carvana-gap-recovery-v1',endpoint=recovery.ENDPOINT,primary_zip='08542',
        location_filter=False,page_size=24,sort='MostPopular',minimum_spacing_seconds=3,
        max_requests=6000,max_seconds=21600,timezone='America/New_York',authorized_cycle_date='2026-09-19',capture_root=str(tmp_path/'capture'),
        related_capture_roots=[str(tmp_path/'peer')],plan_path=str(plan_path),plan_sha256=recovery.digest(plan_path))
    path = write(tmp_path/'config.json',config)
    e = SimpleNamespace(path=path,config=config,plan=plan,plan_path=plan_path,clock=clock,
        folder=tmp_path/'capture/2026-09-19',requests=[],mode='zero')
    def send(url, **kwargs):
        request=copy.deepcopy(kwargs['json']);e.requests.append(request)
        qmake=request['filters']['makes'][0];bounds=request['filters']['year'];page=request['pagination']['page']
        models=qmake.get('parentModels',[])
        year=dict(min=2010,max=2027,**{'applied'+k.title():v for k,v in bounds.items()})
        vehicles=[];total=0
        if e.mode in {'positive','pagination'}:
            total=48 if e.mode=='pagination' and len(e.requests)<=2 else 1
            for i in range(min(24,total)):
                identity=i+1 if e.mode=='pagination' else len(e.requests)
                vehicles.append(dict(vehicleId=identity,vin=f'5YJ3E1EA0MF{identity:06d}',
                    year=bounds.get('min',2009),make=qmake['name'],model=models[0]['name'] if models else 'Any',
                    parentModel=models[0]['name'] if models else 'Any',mileage=10000,
                    price={'total':20000},isPurchasePending=False,vehicleLockType=0))
        if e.mode=='schema':
            year['appliedMin']=2008
        data=dict(userDeliveryInfo={'zip5':'08542'},inventory=dict(vehicles=vehicles,
            pagination=dict(currentPage=page,pageSize=24,totalMatchedInventory=total,totalMatchedPages=(total+23)//24)),
            facetData=dict(year=year,makes={qmake['name']:dict(key=qmake['name'],count=total,isApplied=True,
                parentModels=[dict(key=m['name'],count=total,isApplied=True,modelIds=[1]) for m in models])}))
        clock.sleep(.1)
        return SimpleNamespace(content=json.dumps(data).encode(),status_code=403 if e.mode=='access' else 200,
                               headers={'content-type':'application/json'},close=lambda:None,json=lambda:data)
    e.send=send
    return e


def run(e, *, limit=None, mode=None):
    if limit is not None:
        e.config['max_requests']=limit
        write(e.path,e.config)
    if mode:
        e.mode=mode
    return recovery.collect_recovery(e.path,expected_sha256=recovery.digest(e.path),post=e.send)


def test_preview_full_denominator_no_writes_and_plan_mutation_blocks(experiment):
    e=experiment
    before={str(p):p.read_bytes() for p in e.path.parent.rglob('*') if p.is_file()}
    preview=recovery.preview(e.path)
    assert preview['parents']==25 and preview['children']==500 and not e.requests
    assert before=={str(p):p.read_bytes() for p in e.path.parent.rglob('*') if p.is_file()}
    e.plan['children'].pop()
    write(e.plan_path,e.plan);e.config['plan_sha256']=recovery.digest(e.plan_path);write(e.path,e.config)
    with pytest.raises(ValueError,match='denominator'):
        recovery.preview(e.path)


@pytest.mark.parametrize('mode',['schema','access'])
def test_fatal_stops_and_preserves_zero_admitted_499_unattempted(experiment,tmp_path,mode):
    e=experiment;report=run(e,mode=mode)
    assert len(e.requests)==1 and report['status']=='stopped'
    first=report['entries'][0];child=recovery.load(first['report'])
    assert child['pages'][0]['stored_rows']==0 and not child['query_complete']
    assert child['pages'][0].get('database_outcome') is None
    assert sum(x['status']=='unattempted' for x in report['entries'])==499
    assert (e.folder.parent/'access_stop.json').exists()
    with pytest.raises(ValueError):
        run(e)
    output=recovery.export_recovery(e.folder,output=tmp_path/'export')
    assert len(pd.read_csv(output/'child_coverage.csv'))==500
    assert not recovery.load(output/'summary.json')['recovery_contexts_complete']


def test_zero_context_validation_is_durable_before_storage(experiment,tmp_path):
    e=experiment;q=e.plan['children'][0]
    def reject(capture,facets):
        assert list((tmp_path/'direct/raw').glob('*.json'))
        assert list((tmp_path/'direct/facets').glob('*.json'))
        assert list((tmp_path/'direct/response_sources').glob('*.json'))
        assert not (tmp_path/'direct/vehicle.sqlite').exists()
        raise ValueError('Synthetic wrong context')
    result=collect_search(filters=q['filters'],zip_code=q['zip_code'],destination=tmp_path/'direct',
        target_listings=None,post=e.send,retain_facets=True,first_page_validator=reject)
    assert result['outcome_kind']=='schema_failure' and result['pages'][0]['stored_rows']==0
    assert result['pages'][0].get('database_outcome') is None
    captured,rows=read_snapshots(tmp_path/'direct/vehicle.sqlite')
    assert captured.status.tolist()==['failed'] and rows.empty


def test_make_only_and_one_sided_zero_contexts(experiment):
    e=experiment;q=copy.deepcopy(e.plan['children'][0]);q['filters']['makes']=[{'name':'Chevrolet'}]
    request=build_search_request(filters=q['filters'],zip_code='08542')
    response=e.send(recovery.ENDPOINT,json=request)
    from vehicle_tracker.search import project_response
    from vehicle_tracker.facets import select_facets
    data=json.loads(response.content)
    capture=project_response(data,request,observed_at='2026-09-19T13:00:00Z')
    facet=dict(request=request,zip_code='08542',captured_at_utc=capture['captured_at_utc'],
               pagination=capture['pagination'],facet_data=select_facets(data))
    recovery.validate_context(capture,facet,q)
    facet['facet_data']['year']['appliedMin']=2010
    with pytest.raises(ValueError,match='open tail'):
        recovery.validate_context(capture,facet,q)


def test_shared_lock_and_peer_stop_prevent_requests(experiment):
    e=experiment;peer=Path(e.config['related_capture_roots'][0]);peer.mkdir()
    with recovery.cycle_lock(peer):
        with pytest.raises((ValueError,BlockingIOError)):
            run(e)
    assert not e.requests and not e.folder.exists()
    write(peer/'access_stop.json',{'reason':'retained stop'})
    with pytest.raises(ValueError,match='access stop'):
        run(e)
    assert not e.requests


def test_authorized_date_and_any_completed_own_attempt_block_reuse(experiment):
    e=experiment
    with pytest.raises(ValueError,match='explicit local date'):
        recovery.preview(e.path,now=datetime(2026,9,20,13,tzinfo=timezone.utc))
    prior=e.folder.parent/'2026-09-18'
    write(prior/'catalog_budget.json',dict(budget=dict(stopped=False,pending_request=False)))
    write(prior/'catalog_report.json',dict(status='collection_finished',ended_at='2026-09-18T18:00:00Z'))
    assert not recovery.preview(e.path)['destination_fresh']
    with pytest.raises(ValueError,match='existing recovery'):
        run(e)
    assert not e.requests


def test_pagination_isolated_continues_next_child_without_admitting_failed_page(experiment,tmp_path):
    e=experiment;report=run(e,limit=3,mode='pagination')
    assert len(e.requests)==3
    assert report['entries'][0]['outcome_kind']=='pagination_unstable'
    assert report['entries'][1]['query_complete']
    output=recovery.export_recovery(e.folder,output=tmp_path/'export')
    rows=pd.read_csv(output/'observations.csv')
    assert len(rows)==25
    assert rows.query_id.value_counts().max()==24
    assert not pd.read_csv(output/'parent_coverage.csv').recovery_contexts_complete.any()


@pytest.mark.parametrize('mutation',['missing_child','pending','reservation','budget_limit','code',
    'orphan','orphan_journal','last_clock','sqlite_page','sqlite_run','sqlite_zip','future_reservation'])
def test_replay_rejects_ledger_identity_and_same_count_sqlite_mutations(experiment,tmp_path,mutation):
    e=experiment;report=run(e,limit=1,mode='positive')
    state=recovery.load(e.folder/'catalog_budget.json')
    if mutation=='missing_child':
        report['entries'].pop();write(e.folder/'catalog_report.json',report)
    elif mutation in {'pending','reservation','budget_limit','last_clock'}:
        if mutation=='pending':state['budget']['pending_request']=True
        elif mutation=='reservation':state['budget']['requests']=2
        elif mutation=='last_clock':state['budget']['last_request_utc']='2026-09-19T13:00:00.050000+00:00'
        else:state['max_requests']=6001
        write(e.folder/'catalog_budget.json',state)
    elif mutation=='code':
        report['code_hashes'][next(iter(report['code_hashes']))]='0'*64;write(e.folder/'catalog_report.json',report)
    elif mutation=='orphan':
        (e.folder/'unexpected-child').mkdir()
    elif mutation=='orphan_journal':
        write(Path(report['entries'][0]['report']).parent/'attempts/9999.json',{})
    elif mutation=='future_reservation':
        child_path=Path(report['entries'][0]['report']);child=recovery.load(child_path)
        child['pages'][0]['request_reserved_at_utc']='2026-09-19T18:00:00+00:00'
        write(child_path,child)
        journal_path=child_path.parent/'attempts/0001.json';journal=recovery.load(journal_path)
        journal['request_reserved_at_utc']=child['pages'][0]['request_reserved_at_utc'];write(journal_path,journal)
        report['entries'][0]['report_sha256']=recovery.digest(child_path);write(e.folder/'catalog_report.json',report)
    else:
        database=Path(report['entries'][0]['report']).parent/'vehicle.sqlite'
        with sqlite3.connect(database) as connection:
            if mutation=='sqlite_page':connection.execute('UPDATE vehicle_observations SET page_number=2')
            elif mutation=='sqlite_run':connection.execute("UPDATE vehicle_observations SET run_id='wrong'")
            else:connection.execute("UPDATE vehicle_captures SET zip_code='90210'")
    with pytest.raises(ValueError):
        recovery.export_recovery(e.folder,output=tmp_path/'export')
    assert not (tmp_path/'export').exists()


def test_complete_fixed_grid_and_publication_clock(experiment,tmp_path):
    e=experiment;report=run(e)
    assert len(e.requests)==500 and report['status']=='collection_finished'
    output=recovery.export_recovery(e.folder,output=tmp_path/'export')
    parents=pd.read_csv(output/'parent_coverage.csv')
    assert len(parents)==25 and parents.complete_children.eq(20).all()
    assert parents.recovery_contexts_complete.all()
    summary=recovery.load(output/'summary.json');manifest=recovery.load(output/'manifest.json')
    assert summary['recovery_contexts_complete'] and summary['distinct_vins']==0
    assert not summary['national_coverage_verified'] and summary['estimated_sales'] is None
    assert recovery.aware(manifest['created_at'])>=recovery.aware(report['ended_at'])
    assert not parents.original_parent_reclassified.any()
    with pytest.raises(FileExistsError):
        recovery.export_recovery(e.folder,output=output)
    # A final post-response stop cannot be promoted to CLI success by all child flags.
    report['status']='stopped';write(e.folder/'catalog_report.json',report)
    state=recovery.load(e.folder/'catalog_budget.json');state['budget']['stopped']=True
    write(e.folder/'catalog_budget.json',state)
    stopped=recovery.export_recovery(e.folder,output=tmp_path/'stopped_export')
    assert not recovery.load(stopped/'summary.json')['recovery_contexts_complete']
