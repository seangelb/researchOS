import copy
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from test_search import response_data
from vehicle_tracker.search import collect_search


def retained_query(tmp_path,data,name='source'):
    result=collect_search(filters={},zip_code='08542',destination=tmp_path/name,
        post=Mock(return_value=Mock(status_code=200,headers={'content-type':'application/json'},
            content=b'fixture',json=Mock(return_value=data))))
    return tmp_path/name/'run_report.json'


def test_import_exposes_native_fields_and_is_idempotent(tmp_path,response_data):
    from vehicle_tracker.history import import_reports, read_history
    source=retained_query(tmp_path,response_data)
    preserved={str(p):p.read_bytes() for p in source.parent.rglob('*') if p.is_file()}
    database=tmp_path/'analysis.sqlite'
    first=import_reports([source],database)
    second=import_reports([source],database)
    runs,captures,observations=read_history(database)
    assert first.imported_runs.sum()==1 and second.imported_runs.sum()==0
    assert len(runs)==len(captures)==1 and len(observations)==3
    assert observations.purchase_pending.eq(0).all() and observations.vehicle_lock_type.eq(0).all()
    assert observations.asking_price_usd.iloc[0]==14590
    assert runs.original_normalizer_sha256.notna().all()
    assert all(Path(p).read_bytes()==content for p,content in preserved.items())
    raw=Path(json.loads(source.read_text())['pages'][0]['retained_source'])
    raw.write_text('{}')
    with pytest.raises(ValueError,match='hash'):
        import_reports([source],database)


def test_import_partial_and_explicit_zero_stay_distinct(tmp_path,response_data):
    from vehicle_tracker.history import import_reports, read_history
    partial=copy.deepcopy(response_data)
    partial['inventory']['pagination'].update(totalMatchedInventory=27,totalMatchedPages=2)
    source=retained_query(tmp_path,partial,'partial')  # Repeated response blocks page 2.
    zero=copy.deepcopy(response_data)
    zero['inventory']['vehicles']=[]
    zero['inventory']['pagination'].update(totalMatchedInventory=0,totalMatchedPages=0)
    empty=retained_query(tmp_path,zero,'zero')
    import_reports([source,empty],tmp_path/'analysis.sqlite')
    runs,captures,observations=read_history(tmp_path/'analysis.sqlite')
    assert sorted(runs.query_complete)==[0,1] and len(observations)==3
    assert runs.loc[runs.query_complete.eq(1),'reported_total'].iloc[0]==0


def test_history_refuses_unrelated_database(tmp_path,response_data):
    from vehicle_tracker.history import import_reports
    database=tmp_path/'unrelated.sqlite'
    with sqlite3.connect(database) as connection: connection.execute('CREATE TABLE private_work (value TEXT)')
    before=database.read_bytes()
    with pytest.raises(ValueError,match='unrelated'):
        import_reports([retained_query(tmp_path,response_data)],database)
    assert database.read_bytes()==before


def query_rows():
    return pd.DataFrame([
        dict(run_id='before',context_json='same',query_complete=1,observation_start='2026-09-07T10:00:00Z',observation_end='2026-09-07T10:01:00Z'),
        dict(run_id='after',context_json='same',query_complete=1,observation_start='2026-09-08T10:00:00Z',observation_end='2026-09-08T10:01:00Z')])


@pytest.mark.parametrize('problem',['partial','changed_context','unknown_run','stale','duplicate_query','empty'])
def test_comparison_coverage_blocks_incomplete_or_changed_inputs(problem):
    from vehicle_tracker.history import comparison_checks
    runs=query_rows()
    before,after=['before'],['after']
    if problem=='partial': runs.loc[1,'query_complete']=0
    if problem=='changed_context': runs.loc[1,'context_json']='other ZIP/filter'
    if problem=='unknown_run': after=['missing']
    if problem=='stale': after=['before']
    if problem=='duplicate_query': after=['after','after']
    if problem=='empty': after=[]
    checks=comparison_checks(runs,before,after)
    assert not checks.passed.all() and checks.loc[~checks.passed,'reason'].str.len().gt(0).all()


def test_classification_preserves_absence_reappearance_and_missing_prices():
    from vehicle_tracker.history import classify_changes
    def rows(ids,prices):
        return pd.DataFrame(dict(retailer='carvana',listing_id=ids,vin=['VIN'+i for i in ids],
            asking_price_usd=prices,purchase_pending=False,vehicle_lock_type=0))
    previous=rows(['one','two'],[100,None])
    current=rows(['one','three','four'],[90,80,70])
    current.loc[0,'purchase_pending']=True
    changes=classify_changes(previous,current,seen_before={'four'}).set_index('listing_id')
    assert changes.loc['one','asking_price_change_usd']==-10
    assert changes.loc['one','native_status_changed']
    assert changes.loc['two','observation_change']=='not_observed'
    assert changes.loc['three','observation_change']=='first_observed'
    assert changes.loc['four','observation_change']=='reappearing'
    assert pd.isna(changes.loc['two','asking_price_change_usd'])


def test_overlapping_zip_rows_and_conflicting_vins_are_not_silently_collapsed():
    from vehicle_tracker.history import classify_changes
    row=dict(retailer='carvana',listing_id='one',vin='a',asking_price_usd=100,purchase_pending=False,vehicle_lock_type=0)
    with pytest.raises(ValueError,match='Duplicate'):
        classify_changes(pd.DataFrame([row,row]),pd.DataFrame([row]))
    changes=classify_changes(pd.DataFrame([row]),pd.DataFrame([dict(row,vin='b',asking_price_usd=90)]))
    assert changes.identity_conflict.iloc[0] and pd.isna(changes.asking_price_change_usd.iloc[0])


@pytest.mark.parametrize('problem',['vin_alias','missing_id','missing_vin'])
def test_invalid_period_identities_block_absence_classification(problem):
    from vehicle_tracker.history import classify_changes
    rows=pd.DataFrame([dict(retailer='carvana',listing_id='one',vin='a',asking_price_usd=100,purchase_pending=False,vehicle_lock_type=0)])
    bad=rows.copy()
    if problem=='vin_alias': bad=pd.concat([bad,bad.assign(listing_id='other')],ignore_index=True)
    if problem=='missing_id': bad.loc[0,'listing_id']=None
    if problem=='missing_vin': bad.loc[0,'vin']=None
    with pytest.raises(ValueError,match='identit'):
        classify_changes(bad,rows)


@pytest.mark.parametrize('key,value',[('isPurchasePending','false'),('isOnDemand',0),
    ('vehicleLockType','unknown'),('vehicleInventoryType',True),('transportCost',float('nan')),
    ('parentModel',42),('vehiclePurchaseType',1)])
def test_native_type_drift_is_rejected_before_coverage_and_not_imported(tmp_path,response_data,key,value):
    from vehicle_tracker.history import import_reports, read_history
    response_data['inventory']['vehicles'][0][key]=value
    report=retained_query(tmp_path,response_data)
    outcome=json.loads(report.read_text())
    assert not outcome['query_complete'] and outcome['unique_listings']==0
    assert outcome['pages'][0]['outcome_kind']=='schema_failure'
    assert outcome['pages'][0]['status']=='failed'
    # Failure evidence remains importable, but it must not become inventory.
    import_reports([report],tmp_path/'analysis.sqlite')
    runs,captures,observations=read_history(tmp_path/'analysis.sqlite')
    assert len(runs)==len(captures)==1 and observations.empty
    assert not runs.query_complete.any()


def test_import_closes_its_database_connection(tmp_path,response_data,monkeypatch):
    from vehicle_tracker.history import import_reports
    report=retained_query(tmp_path,response_data)
    connect=sqlite3.connect
    opened=[]
    def tracked(*args,**kwargs):
        connection=connect(*args,**kwargs)
        opened.append(connection)
        return connection
    monkeypatch.setattr(sqlite3,'connect',tracked)
    import_reports([report],tmp_path/'analysis.sqlite')
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError,match='closed'):
            connection.execute('SELECT 1')


@pytest.mark.parametrize('row,column,value',[(1,'observation_end',None),
    (1,'observation_end','2026-09-08T09:00:00Z'),(0,'observation_start',None)])
def test_comparison_requires_valid_whole_intervals(row,column,value):
    from vehicle_tracker.history import comparison_checks
    runs=query_rows()
    runs.loc[row,column]=value
    assert not comparison_checks(runs,['before'],['after']).passed.all()
