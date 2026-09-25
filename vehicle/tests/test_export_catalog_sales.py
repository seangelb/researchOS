"""Offline catalog-sales export writes selected tables and never collects."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from test_sales_proxy import synthetic_cycles, synthetic_inventory


def _module():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'export_catalog_sales.py'
    spec = importlib.util.spec_from_file_location('export_catalog_sales', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_catalog_sales_writes_fresh_tables_and_refuses_overwrite(tmp_path, monkeypatch):
    module = _module()
    schedule = synthetic_cycles((1, 2, 3, 4))
    inventory = synthetic_inventory(*[(day, number, {}) for day in (1, 2, 3, 4) for number in range(1, 4)])

    def read(folder, *, as_of):
        return schedule.copy(), inventory.copy()

    monkeypatch.setattr(module, 'read_catalog_history', read)
    destination = tmp_path / 'catalog-sales'
    module.main(['--catalog-export', str(tmp_path / 'day-a'),
                 '--as-of', '2026-09-04T23:00:00Z', '--destination', str(destination)])
    names = {'inventory_flows.csv', 'exit_episodes.csv', 'disappearance_events.csv',
             'followup_queue.csv', 'sampled_exit_estimate.csv', 'manifest.json'}
    assert names <= {path.name for path in destination.iterdir()}
    import json
    manifest = json.loads((destination / 'manifest.json').read_text(encoding='utf-8'))
    assert 'not reported transactions' in manifest['interpretation']
    assert pd.read_csv(destination / 'inventory_flows.csv').flow_residual.eq(0).all()
    with pytest.raises(ValueError, match='new export directory'):
        module.main(['--catalog-export', str(tmp_path / 'day-a'),
                     '--as-of', '2026-09-04T23:00:00Z', '--destination', str(destination)])
