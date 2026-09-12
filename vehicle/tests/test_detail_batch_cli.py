"""CLI outcomes and planner wiring; synthetic inputs, no collection."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def cli():
    path = Path(__file__).resolve().parents[1] / 'scripts/run_carvana_details.py'
    spec = importlib.util.spec_from_file_location('synthetic_detail_batch_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('outcomes,expired,expected', [
    (['matched', 'matched'], False, 0),
    (['matched', 'matched'], True, 0),
    (['matched', 'unattempted'], False, 1),
    (['started_unresolved', 'unattempted'], False, 1),
    (['matched', 'unattempted'], True, 3),
    (['started_unresolved', 'unattempted'], True, 3),
    (['target_not_found', 'unattempted'], False, 2),
    (['conflicting_records', 'unattempted'], False, 2),
    (['unrecognized_status', 'unattempted'], False, 2),
    (['malformed_fields', 'unattempted'], True, 2),
    (['access_failed', 'unattempted'], True, 2),
])
def test_status_codes_distinguish_completion_failure_and_expired_work(cli, outcomes, expired, expected):
    health = pd.DataFrame(dict(outcome=outcomes, window_expired=[expired] * len(outcomes)))
    assert cli.status_code(health) == expected


def test_cli_rejects_controls_equal_to_entire_batch_before_loading_inputs(cli, monkeypatch):
    def unexpected_read(*args, **kwargs):
        pytest.fail('Invalid slot settings must be rejected before reading collection inputs')

    monkeypatch.setattr(cli, 'cohort_inputs', unexpected_read)
    with pytest.raises(SystemExit) as error:
        cli.main(['preview', '--limit', '6', '--controls', '6'])
    assert error.value.code == 2


def test_fresh_plan_passes_unresolved_browser_health_into_queue(cli, monkeypatch, tmp_path):
    """An unfinished reservation must not disappear at the CLI/queue boundary."""
    from test_sales_proxy import native, synthetic_vehicle

    source = tmp_path / 'retained.json'
    source.write_text('{}', encoding='utf-8')
    settings = {key: source for key in ['config_path', 'plan', 'register', 'database']}
    inventory = pd.DataFrame([dict(source_path=str(source))])
    records = pd.DataFrame([dict(native(1), source=str(source))])
    target = synthetic_vehicle()
    health = pd.DataFrame([dict(batch='unfinished', retailer=target['retailer'], vin=target['vin'],
        listing_id=target['listing_id'], outcome='started_unresolved',
        started_at='2026-09-02T10:00:00Z', available_at=None, window_expired=False)])
    selections = pd.DataFrame([dict(target, selected_at='2026-09-01T00:00:00Z')])
    captured = {}

    monkeypatch.setattr(cli, 'tracking_settings', lambda path: settings)
    monkeypatch.setattr(cli, 'tracking_history', lambda settings, **kwargs: (pd.DataFrame(), inventory))
    monkeypatch.setattr(cli, 'load_pilot', lambda *args, **kwargs: records)
    monkeypatch.setattr(cli, 'load_selection_plan', lambda *args, **kwargs: selections)
    monkeypatch.setattr(cli, 'load_research_pass', lambda *args, **kwargs: ({}, records))
    monkeypatch.setattr(cli, 'load_browser_batches', lambda *args, **kwargs:
        (pd.DataFrame(), pd.DataFrame(), health, set()))
    monkeypatch.setattr(cli, 'digest', lambda path: 'synthetic-hash')

    def queue(*args, **kwargs):
        captured.update(kwargs)
        return pd.DataFrame([dict(target, selected_for_check=True, selection_reason='synthetic only')])

    monkeypatch.setattr(cli, 'inventory_followup_queue', queue)
    cli.fresh_plan([{}], as_of='2026-09-02T12:00:00Z', limit=6, controls=2,
                   seed='synthetic', minutes=45)
    pd.testing.assert_frame_equal(captured['browser_health'], health)
