import copy
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3

import matplotlib.pyplot as plt
plt.switch_backend('Agg')
import pandas as pd
import pytest

from test_search import response_data
from test_vehicle_history import retained_query
from vehicle_tracker.history import import_reports,read_history

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('history_offline_guard',ROOT/'scripts/check_notebooks.py')
checker=importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


@pytest.mark.parametrize('scenario',['valid','missing','missing_source','changed_price','missing_column',
                                     'partial','zero','failed_only'])
def test_analysis_notebook_clean_kernel_paths(tmp_path,response_data,monkeypatch,scenario,capsys):
    database=tmp_path/'analysis.sqlite'
    config=dict(database=str(database),reports=[],previous_reports=[],current_reports=[],collection_reports=[])
    if scenario!='missing':
        data=copy.deepcopy(response_data)
        if scenario=='zero':
            data['inventory']['vehicles']=[]
            data['inventory']['pagination'].update(totalMatchedInventory=0,totalMatchedPages=0)
        if scenario=='failed_only': data['userDeliveryInfo']['zip5']='90210'
        if scenario=='partial':
            # A real incomplete retained query: page 1 is admitted, but the mock
            # repeats page 1 when page 2 is requested, so completeness is false.
            data['inventory']['pagination'].update(totalMatchedInventory=27,totalMatchedPages=2)
        before=retained_query(tmp_path,data,'before')
        after=retained_query(tmp_path,data,'after')
        import_reports([before,after],database)
        config.update(previous_reports=[str(before)],current_reports=[str(after)])
        if scenario=='missing_source':
            Path(json.loads(before.read_text())['pages'][0]['retained_source']).unlink()
        if scenario in ('changed_price','missing_column'):
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute('UPDATE observations SET asking_price_usd=asking_price_usd+12345'
                    if scenario=='changed_price' else 'ALTER TABLE observations DROP COLUMN asking_price_usd')
    scope={'CONFIG_OVERRIDE':config,'DATABASE_OVERRIDE':database,
           'CYCLE_REPORTS_OVERRIDE':[], 'RUN_TRACKING_VIEW_OVERRIDE':False}
    monkeypatch.chdir(ROOT/'vehicle')
    notebook=json.loads((ROOT/'vehicle/notebooks/21_carvana_intraday_reference.ipynb').read_text(encoding='utf-8'))
    with checker.offline_guards():
        for i,cell in enumerate(notebook['cells']):
            if cell['cell_type']=='code':
                exec(compile(''.join(cell['source']),f'notebook:cell{i}','exec'),scope)
                plt.close('all')
    assert scope['comparison_allowed']==(scenario in ('valid','zero'))
    if scenario=='valid':
        assert len(scope['stored_example'])==1
        assert scope['listing_changes'].asking_price_change_usd.eq(0).all()
    if scenario=='zero': assert scope['listing_changes'].empty
    if scenario in ('missing_source','changed_price','missing_column'):
        assert scope['history_validation_error']
        assert scope['observations'].empty and scope['listing_changes'].empty
        assert not scope['history_available']
        assert 'BLOCKED RETAINED HISTORY:' in capsys.readouterr().out
    if scenario=='partial':
        assert scope['history_validation_error'] is None  # Valid partial evidence remains inspectable.
        assert scope['query_runs'].query_complete.eq(0).all()
        assert not scope['observations'].empty and scope['listing_changes'].empty


@pytest.mark.parametrize('wrong_context',[False,True])
def test_notebook_zip_audit_requires_matching_context(wrong_context):
    names=['parent','child_2024','child_2025','nearby_zip']
    paths={name:Path('audit')/name/'report.json' for name in names}
    context=dict(endpoint='search',filters={'makes':[{'name':'Tesla','parentModels':[{'name':'Model 3'}]}],
        'year':{'min':2024,'max':2025}},zip_code='08542',location_filter=False,sort='MostPopular')
    runs=[]
    rows=[]
    for name in names:
        current=copy.deepcopy(context)
        if name.startswith('child'): current['filters']['year']={'min':int(name[-4:]),'max':int(name[-4:])}
        if name=='nearby_zip': current['zip_code']='08540'
        if wrong_context and name=='nearby_zip': current['location_filter']=True
        runs.append(dict(run_id=name,report_path=str(paths[name]),context_json=json.dumps(current),query_complete=1,observation_start='a',observation_end='b'))
        for id in ([name[-4:]] if name.startswith('child') else ['2024','2025']):
            rows.append(dict(run_id=name,retailer='carvana',listing_id=id,vin='VIN'+id))
    scope=dict(history_available=True,CONFIG={'audit_reports':{name:str(path) for name,path in paths.items()}},
        ROOT=ROOT,report_to_run={str((ROOT/path).resolve()):name for name,path in paths.items()},
        query_runs=pd.DataFrame(runs),observations=pd.DataFrame(rows),pd=pd,json=json,display=lambda *args:None)
    book=json.loads((ROOT/'vehicle/notebooks/21_carvana_intraday_reference.ipynb').read_text(encoding='utf-8'))
    cell=next(cell for cell in book['cells'] if cell['id']=='zip-audit')
    exec(''.join(cell['source']),scope)
    assert ('audit_summary' in scope)==(not wrong_context)


def test_api_evidence_requires_its_source_appropriate_comparison(tmp_path,response_data):
    from vehicle_tracker.coverage import compare_runs
    from vehicle_tracker.history import comparison_checks,classify_changes
    reports=[retained_query(tmp_path,response_data,name) for name in ['before','after']]
    captures=[[json.loads(Path(json.loads(p.read_text())['pages'][0]['retained_source']).read_text())] for p in reports]
    with pytest.raises(ValueError,match='Incomplete'):
        compare_runs(*captures)
    import_reports(reports,tmp_path/'analysis.sqlite')
    runs,_,rows=read_history(tmp_path/'analysis.sqlite')
    before,after=runs.run_id
    assert comparison_checks(runs,[before],[after]).passed.all()
    assert classify_changes(rows[rows.run_id.eq(before)],rows[rows.run_id.eq(after)]).observation_change.eq('observed_both').all()
