import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import pytest

from test_daily_notebook import checker
from test_daily_events import observations

VEHICLE = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('case', ['complete', 'missing_date', 'future_input', 'duplicate_input', 'future_benchmark'])
def test_actual_quarter_notebook_keeps_assumptions_and_estimates_separate(tmp_path, monkeypatch, case):
    import vehicle_tracker.cycles as cycle_module
    dates = pd.date_range('2026-07-01', '2026-09-02').strftime('%Y-%m-%d')
    days = pd.DataFrame([dict(cycle_id=day, cycle_date=day, timezone='America/New_York', scope_id='synthetic',
        window_start=day+'T14:00:00Z', window_end=day+'T15:00:00Z', available_at=day+'T15:01:00Z',
        coverage_complete=True, coverage_reason='Synthetic complete scope') for day in dates])
    rows = observations((1, 'VIN', 'L1'))
    rows['cycle_id'] = dates[0]
    rows['observed_at_utc'] = dates[0]+'T14:30:00Z'
    if case == 'missing_date': days = days[~days.cycle_date.eq('2026-08-01')]
    monkeypatch.setattr(cycle_module, 'read_cycle_history', lambda paths, database, **kwargs: (days.copy(), rows.copy()))
    base = dict(input_id='synthetic-v1', source='synthetic://analyst', available_at='2026-09-01T00:00:00Z',
                quarter='2026Q3', scope_id='synthetic')
    inputs = [dict(base, metric='absence_conversion', units='units_per_absence', value=.5),
              dict(base, metric='remaining_daily_units', units='vehicles_per_day', value=2.)]
    if case == 'future_input': inputs[0]['available_at'] = '2026-09-03T00:00:00Z'
    if case == 'duplicate_input': inputs.append(inputs[0])
    scope = dict(AS_OF_OVERRIDE='2026-09-02T23:59:00Z', ANALYST_INPUTS_OVERRIDE=inputs,
                 DATABASE_OVERRIDE=tmp_path/'absent.sqlite', CYCLE_REPORTS_OVERRIDE=[])
    if case == 'future_benchmark':
        scope['BENCHMARK_OVERRIDE'] = dict(base, metric='retail_units', units='vehicles', value=100,
                                         available_at='2026-09-03T00:00:00Z')
    monkeypatch.chdir(VEHICLE)
    book = json.loads((VEHICLE/'notebooks/30_carvana_sales_expectations.ipynb').read_text(encoding='utf-8'))
    with checker.offline_guards():
        for cell in book['cells']:
            if cell['cell_type'] == 'code':
                exec(compile(''.join(cell['source']), cell['id'], 'exec'), scope)
                plt.close('all')
    assert scope['research_status'].estimated_retail_units.isna().all()
    if case in ('complete', 'future_benchmark'):
        assert scope['analyst_scenario'].scenario_units.item() == 56.5
        assert scope['analyst_scenario'].status.item() == 'EXPLORATORY ASSUMPTION SCENARIO'
    else:
        assert scope['analyst_scenario'].empty
    if case == 'future_benchmark': assert scope['benchmark_value'] is None
    assert not (tmp_path/'absent.sqlite').exists()
