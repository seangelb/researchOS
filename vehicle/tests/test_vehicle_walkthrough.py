"""Run the actual starter cells with writes and network blocked."""
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

VEHICLE = Path(__file__).resolve().parents[1]
ROOT = VEHICLE.parent
spec = importlib.util.spec_from_file_location('vehicle_offline_checker', ROOT / 'scripts/check_notebooks.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
NOTEBOOK = VEHICLE / 'notebooks/00_source_walkthrough.ipynb'


def execute(start, monkeypatch, alter=None):
    monkeypatch.chdir(start)
    scope = {}
    with checker.offline_guards():
        for index, cell in enumerate(json.loads(NOTEBOOK.read_text(encoding='utf-8'))['cells']):
            if cell['cell_type'] == 'code':
                exec(''.join(cell['source']), scope)
                if index == 1 and alter:
                    scope['observed'] = alter(scope['observed'])
    return scope


@pytest.mark.parametrize('start', [ROOT, VEHICLE, VEHICLE / 'notebooks'])
def test_starter_is_offline_and_keeps_retailers_and_missing_prices_separate(start, monkeypatch):
    scope = execute(start, monkeypatch)
    assert scope['listing_counts'].observed_listings.sum() == 3
    assert scope['latest'].vehicle_id.nunique() == 2
    assert scope['latest'].retailer.nunique() == 2
    assert scope['latest'].asking_price_usd.isna().sum() == 1
    assert 'sales' not in scope['latest'].columns


@pytest.mark.parametrize('problem', ['duplicate', 'missing_key', 'equivalent_date_duplicate'])
def test_starter_rejects_invalid_observation_keys(problem, monkeypatch):
    def alter(frame):
        if problem == 'missing_key':
            frame.loc[0, 'retailer'] = None
            return frame
        extra = frame.iloc[[0]].copy()
        if problem == 'equivalent_date_duplicate':
            extra['observed_at_utc'] = '2026-09-01T12:00:00+00:00'
        return pd.concat([frame, extra], ignore_index=True)
    with pytest.raises(AssertionError, match='observation key'):
        execute(ROOT, monkeypatch, alter)
