"""Crash boundaries and recovery use retained offline fixtures and temporary paths."""
import copy
from datetime import timedelta
import json
from unittest.mock import Mock

import pandas as pd
import pytest

from test_daily_cycles import clock, options, plan, reply
from test_daily_tracking import settings
from test_search import response_data
from vehicle_tracker import cycles, daily


@pytest.mark.parametrize('field,value', [
    ('requests', -1), ('requests', True), ('requests', 21),
    ('pending_request', 'false'), ('stopped', 0), ('last_request_utc', None),
    ('last_request_utc', '2026-09-08T11:59:00Z'),
])
def test_invalid_durable_budget_cannot_resume(tmp_path, response_data, clock, field, value):
    bad = copy.deepcopy(response_data)
    bad['inventory']['vehicles'][0]['vin'] = 'invalid'
    post = Mock(return_value=reply(bad))
    cycles.collect_cycle(plan(), **options(tmp_path), post=post)
    path = tmp_path / 'cycle/cycle.json'
    state = json.loads(path.read_text())
    state['budget'][field] = value
    path.write_text(json.dumps(state))
    before = {p: p.read_bytes() for p in path.parent.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='durable'):
        cycles.collect_cycle(plan(), **options(tmp_path), resume=True, post=post)
    assert post.call_count == 1
    assert all(p.read_bytes() == content for p, content in before.items())


def test_clock_reversal_cannot_increase_cycle_time(tmp_path, response_data, clock, monkeypatch):
    response_data['inventory']['vehicles'][0]['vin'] = 'invalid'
    post = Mock(return_value=reply(response_data))
    cycles.collect_cycle(plan(), **options(tmp_path), post=post)
    monkeypatch.setattr(cycles, 'utcnow', lambda: clock - timedelta(seconds=1))
    with pytest.raises(ValueError, match='clock precedes'):
        cycles.collect_cycle(plan(), **options(tmp_path), resume=True, post=post)
    assert post.call_count == 1


def test_stopping_preserves_request_uncertainty_until_response_is_durable(tmp_path, clock):
    config = cycles.cycle_config(plan(), cycle_date='2026-09-08', timezone_name='UTC',
        window_start='2026-09-08T11:00:00Z', window_end='2026-09-08T15:00:00Z')
    path = tmp_path / 'cycle.json'
    path.write_text(json.dumps(dict(config, created_at=clock.isoformat(),
        budget=dict(requests=0, pending_request=False, stopped=False, last_request_utc=None))))
    budget = cycles.CycleBudget(path)
    budget.before_navigation()
    budget.stop()
    saved = json.loads(path.read_text())['budget']
    assert saved['requests'] == 1 and saved['pending_request'] and saved['stopped']
    budget.response_received()
    budget.stop()
    saved = json.loads(path.read_text())['budget']
    assert saved['requests'] == 1 and not saved['pending_request'] and saved['stopped']


def test_damaged_stop_flags_cannot_override_retained_access_failure(tmp_path, response_data, clock):
    post = Mock(return_value=reply(response_data, 403))
    cycles.collect_cycle(plan(), **options(tmp_path), post=post)
    path = tmp_path / 'cycle/cycle.json'
    state = json.loads(path.read_text())
    state['budget'].update(stopped=False, pending_request=False)
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match='access/transport'):
        cycles.collect_cycle(plan(), **options(tmp_path), resume=True, post=post)
    assert post.call_count == 1
    # The stop rule governs new requests, while retained failed evidence can be reviewed.
    cycles.import_cycle(path, tmp_path / 'history.sqlite')


def test_durable_request_total_cannot_be_less_than_retained_attempts(tmp_path, response_data, clock):
    response_data['inventory']['vehicles'][0]['vin'] = 'invalid'
    post = Mock(return_value=reply(response_data))
    cycles.collect_cycle(plan(), **options(tmp_path), post=post)
    path = tmp_path / 'cycle/cycle.json'
    state = json.loads(path.read_text())
    state['budget'].update(requests=0, last_request_utc=None)
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match='understates'):
        cycles.collect_cycle(plan(), **options(tmp_path), resume=True, post=post)
    assert post.call_count == 1


def test_import_before_registration_crash_has_offline_idempotent_recovery(settings, response_data, monkeypatch):
    original_write = daily.write_json_atomic
    def fail_registration(path, data):
        if path == settings['register']:
            raise OSError('injected registration interruption')
        return original_write(path, data)
    monkeypatch.setattr(daily, 'write_json_atomic', fail_registration)
    post = Mock(return_value=reply(response_data))
    with pytest.raises(OSError, match='registration interruption'):
        daily.run_tracking(settings, live=True, post=post)
    assert settings['database'].is_file() and not settings['register'].exists()
    database_before = settings['database'].read_bytes()
    retained = settings['capture_root'] / '2026-09-08/cycle.json'
    before = {p: p.read_bytes() for p in settings['capture_root'].rglob('*') if p.is_file()}
    preview, _, _ = daily.run_tracking(settings, post=post)
    recovery = preview['recovery_candidates'][0]
    assert recovery['path'] == str(retained.resolve()) and recovery['import_allowed']
    assert not recovery['live_resume_allowed'] and '--import-cycle' in recovery['offline_action']
    monkeypatch.setattr(daily, 'write_json_atomic', original_write)
    state, folder, _ = daily.run_tracking(settings, import_path=retained, post=post)
    assert state['coverage_complete'] and (folder / 'manifest.json').is_file()
    assert post.call_count == 1 and settings['database'].read_bytes() == database_before
    assert all(p.read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize('case', ['access_stop', 'access_flags_reset', 'uncertain', 'expired', 'settings'])
def test_recovery_preview_is_read_only_and_does_not_authorize_unsafe_resume(settings, response_data, clock, monkeypatch, case):
    config = daily.preview(settings)
    if case == 'uncertain':
        post = Mock(side_effect=KeyboardInterrupt())
    elif case in {'access_stop', 'access_flags_reset'}:
        post = Mock(return_value=reply(response_data, 403))
    else:
        response_data['inventory']['vehicles'][0]['vin'] = 'invalid'
        post = Mock(return_value=reply(response_data))
    arguments = dict(destination=config['destination'], cycle_date=config['cycle_date'], timezone_name=settings['timezone'],
        window_start=config['window_start'], window_end=config['window_end'],
        max_requests=config['max_requests'], max_seconds=config['max_seconds'], post=post)
    if case == 'uncertain':
        with pytest.raises(KeyboardInterrupt):
            cycles.collect_cycle(settings['queries'], **arguments)
    else:
        cycles.collect_cycle(settings['queries'], **arguments)
    if case == 'access_flags_reset':
        path = settings['capture_root'] / '2026-09-08/cycle.json'
        state = json.loads(path.read_text())
        state['budget'].update(stopped=False, pending_request=False)
        path.write_text(json.dumps(state))
    if case == 'expired':
        monkeypatch.setattr(daily, 'utcnow', lambda: clock + timedelta(days=1))
    if case == 'settings':
        settings['queries'][0]['zip_code'] = '90210'
    root = settings['config_path'].parent.parent
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    preview, _, _ = daily.run_tracking(settings, post=post)
    result = preview['recovery_candidates'][0]
    assert not result['live_resume_allowed']
    assert result['import_allowed'] == (case != 'settings')
    assert {p: p.read_bytes() for p in root.rglob('*') if p.is_file()} == before
    assert post.call_count == 1


def test_registration_cannot_bind_an_active_cycle(settings, response_data):
    config = daily.preview(settings)
    cycles.collect_cycle(settings['queries'], destination=config['destination'],
        cycle_date=config['cycle_date'], timezone_name=settings['timezone'],
        window_start=config['window_start'], window_end=config['window_end'],
        max_requests=config['max_requests'], max_seconds=config['max_seconds'],
        post=Mock(return_value=reply(response_data)))
    path = settings['capture_root'] / '2026-09-08/cycle.json'
    with cycles.cycle_lock(path.parent):
        with pytest.raises((ValueError, OSError)):
            daily.run_tracking(settings, import_path=path)
    assert not settings['database'].exists() and not settings['register'].exists()


def test_preview_exposes_destination_created_before_cycle_checkpoint(settings):
    folder = settings['capture_root'] / '2026-09-08'
    folder.mkdir(parents=True)
    before = list(folder.iterdir())
    preview, _, _ = daily.run_tracking(settings)
    result = preview['recovery_candidates'][0]
    assert result['path'] == str(folder / 'cycle.json')
    assert not result['import_allowed'] and not result['live_resume_allowed']
    assert 'validation failed' in result['reason']
    assert list(folder.iterdir()) == before


def test_later_source_availability_is_not_replaced_by_an_earlier_report_clock(tmp_path, response_data, clock):
    cycles.collect_cycle(plan(), **options(tmp_path), post=Mock(return_value=reply(response_data)))
    path = tmp_path / 'cycle/cycle.json'
    report_path = path.parent / 'attempt_0001/all/run_report.json'
    report = json.loads(report_path.read_text())
    later = (clock + timedelta(minutes=1)).isoformat()
    report['pages'][0]['evidence_available_at_utc'] = later
    report_path.write_text(json.dumps(report))
    full, coverage, _ = cycles.cycle_evidence(path)
    assert full['available_at'] == later and coverage.evidence_available_at_utc.iloc[0] == later
    early, early_coverage, _ = cycles.cycle_evidence(path, as_of=clock.isoformat())
    assert not early['coverage_complete'] and early_coverage.reason.tolist() == ['No attempted query']
    report['pages'][0].pop('evidence_available_at_utc')
    report_path.write_text(json.dumps(report))
    _, legacy, _ = cycles.cycle_evidence(path)
    assert legacy.evidence_available_at_utc.iloc[0] is None


@pytest.mark.parametrize('boundary', ['csv', 'manifest', 'publication'])
def test_interrupted_export_is_unpublished_and_refresh_recovers(settings, response_data, monkeypatch, boundary):
    post = Mock(return_value=reply(response_data))
    _, first_folder, _ = daily.run_tracking(settings, live=True, post=post)
    database_before = settings['database'].read_bytes()
    register_before = settings['register'].read_bytes()
    original_csv = pd.DataFrame.to_csv
    original_write = daily.write_json_atomic
    from pathlib import Path
    original_rename = Path.rename
    calls = 0
    def fail_csv(frame, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('injected CSV interruption')
        return original_csv(frame, *args, **kwargs)
    def fail_manifest(path, data):
        if path.name == 'manifest.json':
            raise OSError('injected manifest interruption')
        return original_write(path, data)
    def fail_publication(path, target):
        if path.name.endswith('.partial'):
            raise OSError('injected publication interruption')
        return original_rename(path, target)
    with monkeypatch.context() as patch:
        if boundary == 'csv':
            patch.setattr(pd.DataFrame, 'to_csv', fail_csv)
        if boundary == 'manifest':
            patch.setattr(daily, 'write_json_atomic', fail_manifest)
        if boundary == 'publication':
            patch.setattr(Path, 'rename', fail_publication)
        with pytest.raises(OSError, match='injected'):
            daily.run_tracking(settings, refresh=True, post=post)
    published = [p for p in settings['exports'].iterdir() if not p.name.startswith('.')]
    partial = list(settings['exports'].glob('.*.partial'))
    assert published == [first_folder] and len(partial) == 1
    partial_before = {p: p.read_bytes() for p in partial[0].iterdir() if p.is_file()}
    preview, _, _ = daily.run_tracking(settings, post=post)
    assert preview['incomplete_exports'] == [str(partial[0])]
    _, repaired, _ = daily.run_tracking(settings, refresh=True, post=post)
    manifest = json.loads((repaired / 'manifest.json').read_text())
    assert set(manifest['outputs']) == {p.name for p in repaired.glob('*.csv')}
    assert all(daily.digest(repaired / name) == sha for name, sha in manifest['outputs'].items())
    assert settings['database'].read_bytes() == database_before
    assert settings['register'].read_bytes() == register_before and post.call_count == 1
    assert all(p.read_bytes() == content for p, content in partial_before.items())
