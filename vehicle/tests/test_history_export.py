import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from test_search import response_data
from test_vehicle_history import retained_query

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('history_export_command',ROOT/'vehicle/scripts/build_carvana_history.py')
command=importlib.util.module_from_spec(spec)
spec.loader.exec_module(command)


@pytest.mark.parametrize('scenario',['valid','zero','missing_partition','duplicate_vin'])
def test_explicit_export_coverage_and_zero_paths(tmp_path,response_data,monkeypatch,scenario):
    if scenario=='zero':
        response_data['inventory']['vehicles']=[]
        response_data['inventory']['pagination'].update(totalMatchedInventory=0,totalMatchedPages=0)
    reports=[retained_query(tmp_path,response_data,name) for name in ['before','after']]
    database=tmp_path/'analysis.sqlite'
    config=tmp_path/'config.json'
    config.write_text(json.dumps(dict(database=str(database),reports=list(map(str,reports)),
        previous_reports=[str(reports[0])],current_reports=[str(reports[1])]+(['missing'] if scenario=='missing_partition' else []))),encoding='utf-8')
    assert command.main(['--config',str(config)])==0
    assert not database.exists()
    assert command.main(['--config',str(config),'--write'])==0
    if scenario=='duplicate_vin':
        original=command.read_history
        def conflicting(path):
            runs,captures,rows=original(path)
            ids=rows.index[rows.run_id.eq(runs.run_id.iloc[1])]
            rows.loc[ids[1],'vin']=rows.loc[ids[0],'vin']
            return runs,captures,rows
        monkeypatch.setattr(command,'read_history',conflicting)
    output=tmp_path/'tables'
    status=command.main(['--config',str(config),'--export',str(output)])
    assert status==(1 if scenario=='duplicate_vin' else 0)
    checks=pd.read_csv(output/'comparison_checks.csv')
    allowed=scenario in ('valid','zero')
    assert checks.passed.all()==allowed
    assert (output/'listing_changes.csv').exists()==allowed
    if scenario=='zero':
        assert pd.read_csv(output/'listing_changes.csv').empty
        counts=pd.read_csv(output/'inventory_by_query.csv')
        assert counts.unique_listings.eq(0).all() and counts.query_complete.eq(1).all()
    if scenario=='valid':
        with pytest.raises(FileExistsError): command.main(['--config',str(config),'--export',str(output)])
