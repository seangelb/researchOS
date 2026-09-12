"""Focused cutoff, preservation and missing-evidence checks for Notebook 20 readers."""
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from test_daily_cycles import clock, reply
from test_daily_tracking import settings
from test_search import response_data
from vehicle_tracker import daily
from vehicle_tracker.research_inputs import load_trial_review, load_operating_runs, load_operating_exports


def audit(tmp_path):
    source = tmp_path / 'source.json'
    source.write_text('{"retained": true}')
    data = dict(audit_finished_at='2026-09-12T14:00:00Z', verdict='PASS',
        source_artifact_sha256={str(source): hashlib.sha256(source.read_bytes()).hexdigest()},
        counts=dict(planned_queries=2, complete_queries=2, distinct_vins=40, attempted_requests=2),
        timing=dict(collector_elapsed_seconds=5, minimum_request_start_gap_seconds=3.01),
        checkpoints=[])
    path = tmp_path / 'audit.json'
    path.write_text(json.dumps(data))
    return path, source


def test_trial_reader_retains_missing_and_future_rows_without_reading_future_source(tmp_path, monkeypatch):
    path, source = audit(tmp_path)
    original = Path.read_bytes
    def guarded(p):
        assert p != source, 'A future audit must not read underlying source'
        return original(p)
    monkeypatch.setattr(Path, 'read_bytes', guarded)
    table, checkpoints, inputs = load_trial_review(
        [('future', path), ('missing', tmp_path/'absent.json')], as_of='2026-09-12T13:00:00Z')
    assert table.review_status.tolist() == ['unavailable_at_cutoff', 'reconciliation_missing']
    assert table.distinct_vins.isna().all() and checkpoints.empty
    assert inputs == {path}


def test_trial_reader_preserves_small_complete_plan_semantics_and_rejects_changed_source(tmp_path):
    path, source = audit(tmp_path)
    rows, _, inputs = load_trial_review([('small', path)], as_of='2026-09-12T14:00:00Z')
    assert rows.iloc[0].verified_vins_in_complete_queries == 40
    assert rows.iloc[0].partial_queries == 0 and pd.isna(rows.iloc[0].target_vins)
    assert inputs == {path, source}
    source.write_text('changed')
    with pytest.raises(ValueError, match='Trial source changed'):
        load_trial_review([('small', path)], as_of='2026-09-12T14:00:00Z')


@pytest.mark.parametrize('status', [200, 403])
def test_daily_review_includes_complete_and_failed_attempts_without_writes(settings, response_data, clock, status):
    daily.run_tracking(settings, live=True, post=Mock(return_value=reply(response_data, status=status)))
    root = settings['config_path'].parent.parent
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    rows, inputs = load_operating_runs(settings, as_of='2026-09-08T23:00:00Z')
    assert len(rows) == 1 and rows.iloc[0].requests == 1
    assert rows.iloc[0].complete_queries == (1 if status == 200 else 0)
    assert rows.iloc[0].observed_vins == (3 if status == 200 else 0)
    assert rows.iloc[0].collection_status == ('complete' if status == 200 else 'partial or failed')
    assert rows.iloc[0].recovery_status == 'registered in current register'
    exports, export_inputs = load_operating_exports(settings, as_of='2026-09-08T23:00:00Z')
    assert exports.actual_date.tolist() == ['2026-09-08']
    assert all(p.is_file() for p in inputs | export_inputs)
    assert {p: p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_unregistered_run_is_visible_and_later_cycle_hidden(settings, response_data, clock, monkeypatch):
    daily.run_tracking(settings, live=True, post=Mock(return_value=reply(response_data)))
    settings['register'].unlink()  # Isolated fixture simulates interruption before register.
    rows, _ = load_operating_runs(settings, as_of='2026-09-08T23:00:00Z')
    assert rows.iloc[0].recovery_status == 'unregistered; preview recovery'
    monkeypatch.setattr('vehicle_tracker.cycles.read_cycle_history', Mock(side_effect=AssertionError('future cycle read')))
    early, _ = load_operating_runs(settings, as_of='2026-09-08T11:00:00Z')
    assert early.empty


def test_wrong_scope_is_not_admitted_and_unresolved_request_count_is_not_zero(settings, response_data, clock):
    daily.run_tracking(settings, live=True, post=Mock(return_value=reply(response_data)))
    entry = daily.registered_cycles(settings)[0]
    settings['register'].unlink()
    path = Path(entry['path']); state = json.loads(path.read_text())
    state['budget']['requests'] += 1; state['budget']['pending_request'] = True
    path.write_text(json.dumps(state))
    rows, _ = load_operating_runs(settings, as_of='2026-09-08T23:00:00Z')
    assert pd.isna(rows.iloc[0].requests) and rows.iloc[0].reported_requests == 1
    assert 'unresolved' in rows.iloc[0].recovery_status
    other = copy.deepcopy(settings); other['queries'][0]['zip_code'] = '10001'
    blocked, _ = load_operating_runs(other, as_of='2026-09-08T23:00:00Z')
    assert blocked.iloc[0].collection_status == 'invalid retained evidence'
    assert pd.isna(blocked.iloc[0].observed_vins)


def test_export_hashes_and_scope_are_checked(settings, response_data, clock):
    _, folder, _ = daily.run_tracking(settings, live=True, post=Mock(return_value=reply(response_data)))
    other = copy.deepcopy(settings)
    alternate = settings['config_path'].with_name('other.json')
    alternate.write_text(settings['config_path'].read_text())
    other['config_path'] = alternate
    assert load_operating_exports(other, as_of='2026-09-08T23:00:00Z')[0].empty
    assert load_operating_exports(settings, as_of='2026-09-08T11:00:00Z')[0].empty
    (folder/'daily_inventory.csv').write_text('changed')
    with pytest.raises(ValueError, match='Operating export changed'):
        load_operating_exports(settings, as_of='2026-09-08T23:00:00Z')


def test_folder_without_checkpoint_is_visible_not_a_zero(settings):
    folder = settings['capture_root']/'2026-09-08'
    folder.mkdir(parents=True)
    rows, _ = load_operating_runs(settings, as_of='2026-09-08T23:00:00Z')
    assert rows.iloc[0].collection_status == 'missing cycle metadata'
    assert pd.isna(rows.iloc[0].requests) and pd.isna(rows.iloc[0].observed_vins)


def test_notebook_calendar_keeps_planned_missing_and_measured_zero_separate(settings, response_data, clock):
    from test_daily_notebook import checker
    daily.run_tracking(settings, live=True, post=Mock(return_value=reply(response_data)))
    proposal = settings['config_path'].parent / 'proposal.json'
    proposal.write_text(json.dumps(dict(recorded_at='2026-09-01T00:00:00Z',
        bindings={key: dict(path=str(settings[field]), sha256=daily.digest(settings[field]))
                  for key, field in [('config', 'config_path'), ('query_plan', 'plan')]},
        planned_local_dates=['2026-09-08', '2026-09-09', '2026-09-10'], scope_id='synthetic test scope')))
    book = Path(__file__).parents[1] / 'notebooks/20_carvana_history_analysis.ipynb'
    code = {c['id']: ''.join(c['source']) for c in json.loads(book.read_text(encoding='utf-8'))['cells']}
    scope = dict(pd=pd, Path=Path, json=json, display=lambda *args: None,
        tracking_settings=daily.tracking_settings, PLANNED_VALIDATION=proposal,
        AS_OF='2026-09-09T23:00:00Z', OPERATOR_MINUTES_BY_DATE={'2026-09-08': 0})
    with checker.offline_guards():
        for name in ['planned-operating-inputs', 'planned-date-review']:
            exec(compile(code[name], name, 'exec'), scope)
    rows = scope['planned_date_review'].set_index('planned_date')
    assert rows.collection_status.tolist() == ['complete', 'missing at cutoff', 'planned; not yet due']
    assert rows.loc['2026-09-08', 'active_operator_minutes'] == 0
    assert rows.loc['2026-09-08', 'effort_status'] == 'analyst supplied measurement'
    assert rows.loc[['2026-09-09', '2026-09-10'], 'active_operator_minutes'].isna().all()
    assert rows.loc['2026-09-08', 'export_status'] == 'retained export verified'
    assert rows.loc[['2026-09-09', '2026-09-10'], 'observed_vins'].isna().all()
    (settings['capture_root']/'2026-09-09').mkdir()
    with checker.offline_guards():
        for name in ['planned-operating-inputs', 'planned-date-review']:
            exec(compile(code[name], name, 'exec'), scope)
    unknown = scope['planned_date_review'].set_index('planned_date').loc['2026-09-09']
    assert unknown.collection_status == 'missing cycle metadata'
    assert pd.isna(unknown.within_15_minute_start_window)


def test_mid_request_cutoff_keeps_attempt_total_unknown():
    vehicle = Path(__file__).parents[1]
    settings = daily.tracking_settings(vehicle/'config/carvana_daily_tracking.json')
    rows, _ = load_operating_runs(settings, as_of='2026-09-11T10:53:28.118556+00:00')
    row = rows.loc[rows.actual_date.eq('2026-09-11')].iloc[0]
    assert row.reported_requests == 0  # No completed query report yet.
    assert pd.isna(row.requests)  # A physical request is in flight; zero is incorrect.
    assert 'unresolved' in row.recovery_status
