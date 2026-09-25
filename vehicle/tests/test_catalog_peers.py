"""Offline cross-strategy ownership and stop checks using temporary capture roots."""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from vehicle_tracker import catalog
from vehicle_tracker.cycles import cycle_lock
from test_catalog_years import year_experiment, run_years


@pytest.fixture
def peer_experiment(year_experiment, tmp_path):
    e = year_experiment
    e.peer_root = tmp_path / 'peer_captures'
    e.config['related_capture_roots'] = [str(e.peer_root)]
    e.path.write_text(json.dumps(e.config), encoding='utf-8')
    return e


def snapshot(directory):
    return {str(path.relative_to(directory)): path.read_bytes()
            for path in directory.rglob('*') if path.is_file()}


def state_for(preview, root):
    return next(row for row in preview['capture_root_states']
                if Path(row['capture_root']) == root.resolve())


def retain_previous(root, *, pending=False, status='collection_finished', report=True):
    previous = root / '2026-09-18'
    previous.mkdir(parents=True)
    (previous / 'catalog_budget.json').write_text(json.dumps(dict(budget=dict(
        requests=1, stopped=False, pending_request=pending,
        last_request_utc='2026-09-18T13:00:00+00:00'))), encoding='utf-8')
    if report:
        (previous / 'catalog_report.json').write_text(json.dumps(dict(status=status,
            requests=1, ended_at=None if status == 'running' else '2026-09-18T13:00:01+00:00')),
            encoding='utf-8')
    return previous


def snapshot_of(files, prefix):
    """Select one subtree from a root snapshot, keeping paths relative to it."""
    return {str(Path(name).relative_to(prefix)): data
            for name, data in files.items() if Path(name).parts[0] == prefix}


def retain_client_failure(root, *, outcome='schema_failure', pending=False):
    """A terminal HTTP 200 client failure, exactly like the retained recovery stops."""
    folder = root / '2026-09-19'
    folder.mkdir(parents=True)
    report_path = folder / 'catalog_report.json'
    report_path.write_text(json.dumps(dict(status='stopped', requests=1,
        ended_at='2026-09-19T16:13:11+00:00',
        entries=[dict(query=dict(query_id='q'), report=str(folder / 'q/run_report.json'),
                      outcome_kind=outcome)])), encoding='utf-8')
    (folder / 'catalog_budget.json').write_text(json.dumps(dict(budget=dict(
        requests=1, stopped=True, pending_request=pending,
        last_request_utc='2026-09-19T16:13:10+00:00'))), encoding='utf-8')
    (root / 'access_stop.json').write_text(json.dumps(dict(run=str(report_path),
        reason='Recovery stopped; preserve evidence and require review')), encoding='utf-8')
    return folder


def reviewed_entry(folder):
    return dict(capture_directory=str(folder),
        catalog_report_sha256=catalog.digest(folder / 'catalog_report.json'),
        catalog_budget_sha256=catalog.digest(folder / 'catalog_budget.json'),
        access_stop_sha256=catalog.digest(folder.parent / 'access_stop.json'),
        reason='Inspected client failure retained with its evidence')


def test_historical_client_failure_without_cooldown_does_not_block(peer_experiment):
    e = peer_experiment
    folder = retain_client_failure(e.peer_root)
    before = snapshot(e.peer_root)
    result = catalog.preview(e.path)
    peer = state_for(result, e.peer_root)
    assert not result['capture_preflight_blocked'] and not peer['access_stopped'] and not peer['blocked']
    assert run_years(e)['primary_queries_complete']
    assert snapshot(folder) == snapshot_of(before, folder.name)
    assert (e.peer_root / 'access_stop.json').read_bytes() == before['access_stop.json']


def test_unexpired_access_cooldown_blocks_without_editing_the_marker(peer_experiment):
    e = peer_experiment
    retain_client_failure(e.peer_root)
    stop_path = e.peer_root / 'access_stop.json'
    stop = json.loads(stop_path.read_text(encoding='utf-8'))
    stop['cooldown_until'] = '2099-01-01T00:00:00+00:00'
    stop_path.write_text(json.dumps(stop), encoding='utf-8')
    before = snapshot(e.peer_root)
    result = catalog.preview(e.path)
    peer = state_for(result, e.peer_root)
    assert result['capture_preflight_blocked'] and peer['access_stopped'] and peer['blocked']
    assert peer['cooldown_until'] == '2099-01-01T00:00:00+00:00'
    post = Mock()
    with pytest.raises(ValueError, match='access stop'):
        run_years(e, post)
    post.assert_not_called()
    assert not e.folder.exists() and snapshot(e.peer_root) == before


def test_reviewed_client_failure_keeps_its_stop_and_allows_an_unrelated_date(peer_experiment):
    e = peer_experiment
    folder = retain_client_failure(e.peer_root)
    e.config['reviewed_peer_failures'] = [reviewed_entry(folder)]
    e.path.write_text(json.dumps(e.config), encoding='utf-8')
    before = snapshot(e.peer_root)
    result = catalog.preview(e.path)
    peer = state_for(result, e.peer_root)
    assert not result['capture_preflight_blocked'] and not peer['blocked']
    assert peer['retained_access_stopped'] and not peer['access_stopped']
    assert peer['reviewed_client_failures'] == [str(folder)]
    assert run_years(e)['primary_queries_complete']
    assert (e.peer_root / 'access_stop.json').is_file()
    # Locking the peer adds its lock file; no retained evidence may change.
    assert snapshot(folder) == snapshot_of(before, folder.name)
    assert (e.peer_root / 'access_stop.json').read_bytes() == before['access_stop.json']


def test_reviewed_collection_stopped_schema_with_isolated_pagination_allows_fresh_root(peer_experiment):
    e = peer_experiment
    folder = retain_client_failure(e.peer_root, outcome='schema_failure')
    report_path = folder / 'catalog_report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    report.update(failure_type='CollectionStopped', failure_reason='ValueError: schema_failure')
    report['entries'].extend([
        dict(query=dict(query_id='leaf'), report=str(folder / 'leaf/run_report.json'),
             outcome_kind='pagination_unstable'),
        dict(query=dict(query_id='probe'), report=str(folder / 'probe/run_report.json'),
             outcome_kind='sample_limit'),
    ])
    report_path.write_text(json.dumps(report), encoding='utf-8')
    e.config['reviewed_peer_failures'] = [reviewed_entry(folder)]
    e.path.write_text(json.dumps(e.config), encoding='utf-8')
    before = snapshot(e.peer_root)
    result = catalog.preview(e.path)
    peer = state_for(result, e.peer_root)
    assert not result['capture_preflight_blocked'] and not peer['blocked']
    assert peer['retained_access_stopped'] and peer['reviewed_client_failures'] == [str(folder)]
    assert run_years(e)['primary_queries_complete']
    assert snapshot(folder) == snapshot_of(before, folder.name)
    assert (e.peer_root / 'access_stop.json').read_bytes() == before['access_stop.json']


def test_reviewed_value_error_stop_with_sample_probes_allows_fresh_root(peer_experiment):
    e = peer_experiment
    folder = retain_client_failure(e.peer_root, outcome='sample_limit')
    report_path = folder / 'catalog_report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    report.update(failure_type='ValueError',
                  failure_reason='Requested make count/application differs from response')
    report_path.write_text(json.dumps(report), encoding='utf-8')
    e.config['reviewed_peer_failures'] = [reviewed_entry(folder)]
    e.path.write_text(json.dumps(e.config), encoding='utf-8')
    before = snapshot(e.peer_root)
    result = catalog.preview(e.path)
    peer = state_for(result, e.peer_root)
    assert not result['capture_preflight_blocked'] and not peer['blocked']
    assert peer['retained_access_stopped'] and peer['reviewed_client_failures'] == [str(folder)]
    assert run_years(e)['primary_queries_complete']
    assert snapshot(folder) == snapshot_of(before, folder.name)
    assert (e.peer_root / 'access_stop.json').read_bytes() == before['access_stop.json']


def test_reviewed_transport_uncertain_stop_allows_fresh_root(peer_experiment):
    e = peer_experiment
    folder = retain_client_failure(e.peer_root, outcome='transport_failure', pending=True)
    report_path = folder / 'catalog_report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    report.update(failure_type='CollectionStopped',
                  failure_reason='ConnectionError: transport_failure')
    report_path.write_text(json.dumps(report), encoding='utf-8')
    e.config['reviewed_peer_failures'] = [reviewed_entry(folder)]
    e.path.write_text(json.dumps(e.config), encoding='utf-8')
    before = snapshot(e.peer_root)
    result = catalog.preview(e.path)
    peer = state_for(result, e.peer_root)
    assert not result['capture_preflight_blocked'] and not peer['blocked']
    assert peer['retained_access_stopped'] and peer['reviewed_client_failures'] == [str(folder)]
    assert run_years(e)['primary_queries_complete']
    assert snapshot(folder) == snapshot_of(before, folder.name)
    assert (e.peer_root / 'access_stop.json').read_bytes() == before['access_stop.json']
    assert json.loads((folder / 'catalog_budget.json').read_text())['budget']['pending_request'] is True


@pytest.mark.parametrize('problem', ['cleared_pending', 'access_failure'])
def test_reviewed_transport_disposition_rejects_wrong_shape(peer_experiment, problem):
    e = peer_experiment
    outcome = 'access_failure' if problem == 'access_failure' else 'transport_failure'
    pending = problem != 'cleared_pending'
    folder = retain_client_failure(e.peer_root, outcome=outcome, pending=pending)
    report_path = folder / 'catalog_report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    report.update(failure_type='CollectionStopped',
                  failure_reason='ConnectionError: transport_failure')
    report_path.write_text(json.dumps(report), encoding='utf-8')
    e.config['reviewed_peer_failures'] = [reviewed_entry(folder)]
    e.path.write_text(json.dumps(e.config), encoding='utf-8')
    before = snapshot(e.peer_root)
    post = Mock()
    with pytest.raises(ValueError):
        catalog.preview(e.path)
    with pytest.raises(ValueError):
        run_years(e, post)
    post.assert_not_called()
    assert not e.folder.exists() and snapshot(e.peer_root) == before


@pytest.mark.parametrize('problem', ['report', 'budget', 'stop', 'fatal_outcome',
                                     'pending', 'own_root', 'unlisted_root'])
def test_reviewed_disposition_cannot_cover_changed_or_fatal_evidence(peer_experiment, tmp_path, problem):
    e = peer_experiment
    outcome = 'access_failure' if problem == 'fatal_outcome' else 'schema_failure'
    folder = retain_client_failure(e.peer_root, outcome=outcome, pending=problem == 'pending')
    entry = reviewed_entry(folder)
    if problem in {'report', 'budget', 'stop'}:
        entry[{'report': 'catalog_report_sha256', 'budget': 'catalog_budget_sha256',
               'stop': 'access_stop_sha256'}[problem]] = '0' * 64
    elif problem == 'own_root':
        entry['capture_directory'] = str(Path(e.config['capture_root']) / '2026-09-19')
    elif problem == 'unlisted_root':
        entry['capture_directory'] = str(tmp_path / 'elsewhere' / '2026-09-19')
    e.config['reviewed_peer_failures'] = [entry]
    e.path.write_text(json.dumps(e.config), encoding='utf-8')
    before = snapshot(e.peer_root)
    post = Mock()
    with pytest.raises(ValueError):
        catalog.preview(e.path)
    with pytest.raises(ValueError):
        run_years(e, post)
    post.assert_not_called()
    assert not e.folder.exists() and snapshot(e.peer_root) == before


def test_reviewed_peer_with_another_unresolved_attempt_remains_blocked(peer_experiment):
    e = peer_experiment
    folder = retain_client_failure(e.peer_root)
    (e.peer_root / '2026-09-18').mkdir()
    e.config['reviewed_peer_failures'] = [reviewed_entry(folder)]
    e.path.write_text(json.dumps(e.config), encoding='utf-8')
    before = snapshot(e.peer_root)
    result = catalog.preview(e.path)
    peer = state_for(result, e.peer_root)
    assert not result['capture_preflight_blocked'] and not peer['blocked']
    assert peer['retained_access_stopped']
    assert snapshot(e.peer_root) == before


def test_preview_is_read_only_and_lists_absent_roots_without_creating_them(peer_experiment):
    e = peer_experiment
    before = snapshot(e.path.parent)
    directories = {path for path in e.path.parent.rglob('*') if path.is_dir()}
    result = catalog.preview(e.path)
    assert result['writes'] is False and result['requests'] == 0
    assert result['capture_preflight_blocked'] is False
    assert {Path(row['capture_root']) for row in result['capture_root_states']} == {
        e.folder.parent, e.peer_root}
    assert all(not row['access_stopped'] and not row['unresolved_invocations'] and not row['blocked']
               for row in result['capture_root_states'])
    assert snapshot(e.path.parent) == before
    assert {path for path in e.path.parent.rglob('*') if path.is_dir()} == directories
    assert not e.folder.parent.exists() and not e.peer_root.exists()


def test_active_peer_os_lock_prevents_requests_and_date_creation(peer_experiment):
    e = peer_experiment
    e.peer_root.mkdir()
    post = Mock()
    with cycle_lock(e.peer_root):
        with pytest.raises((ValueError, BlockingIOError)):
            run_years(e, post)
        post.assert_not_called()
        assert not e.folder.exists()
        if e.folder.parent.exists():
            with cycle_lock(e.folder.parent):
                pass
    assert not (e.folder.parent / 'access_stop.json').exists()


def test_terminal_peer_access_stop_is_visible_and_cannot_be_bypassed(peer_experiment):
    e = peer_experiment
    retain_previous(e.peer_root)
    (e.peer_root / 'access_stop.json').write_text(json.dumps(dict(
        reason='HTTP 403', stopped_at='2026-09-18T13:00:01+00:00',
        cooldown_until='2099-01-01T00:00:00+00:00')), encoding='utf-8')
    before = snapshot(e.peer_root)
    result = catalog.preview(e.path)
    peer = state_for(result, e.peer_root)
    assert result['capture_preflight_blocked'] and peer['access_stopped'] and peer['blocked']
    post = Mock()
    with pytest.raises(ValueError):
        run_years(e, post)
    post.assert_not_called()
    assert not e.folder.exists()
    assert snapshot(e.peer_root) == before


@pytest.mark.parametrize('problem', ['pending_request', 'missing_report', 'running_report',
                                     'missing_budget', 'invalid_budget', 'unknown_pending_state'])
def test_unresolved_peer_attempt_blocks_even_with_a_fresh_own_date(peer_experiment, problem):
    e = peer_experiment
    previous = retain_previous(e.peer_root, pending=problem == 'pending_request',
        status='running' if problem == 'running_report' else 'collection_finished',
        report=problem != 'missing_report')
    if problem == 'missing_budget':
        (previous / 'catalog_budget.json').unlink()
    elif problem == 'invalid_budget':
        (previous / 'catalog_budget.json').write_text('{', encoding='utf-8')
    elif problem == 'unknown_pending_state':
        (previous / 'catalog_budget.json').write_text('{"budget": {"pending_request": null}}', encoding='utf-8')
    before = snapshot(e.peer_root)
    result = catalog.preview(e.path)
    peer = state_for(result, e.peer_root)
    assert result['destination_fresh'] and not result['capture_preflight_blocked']
    assert not peer['blocked'] and not peer['access_stopped']
    assert snapshot(e.peer_root) == before


def test_peer_stop_appearing_after_preview_is_rechecked_before_date_creation(peer_experiment, monkeypatch):
    e = peer_experiment
    original_preview = catalog.preview

    def preview_then_stop(*args, **kwargs):
        result = original_preview(*args, **kwargs)
        assert not result['capture_preflight_blocked']
        e.peer_root.mkdir()
        (e.peer_root / 'access_stop.json').write_text(
            '{"reason": "HTTP 403", "cooldown_until": "2099-01-01T00:00:00+00:00"}', encoding='utf-8')
        return result

    monkeypatch.setattr(catalog, 'preview', preview_then_stop)
    post = Mock()
    with pytest.raises(ValueError):
        run_years(e, post)
    post.assert_not_called()
    assert not e.folder.exists()


@pytest.mark.parametrize('stopped', [True, None, 1])
def test_stopped_or_unknown_durable_peer_budget_blocks_without_access_stop(peer_experiment, stopped):
    e = peer_experiment
    previous = retain_previous(e.peer_root)
    budget_path = previous / 'catalog_budget.json'
    state = json.loads(budget_path.read_text())
    state['budget']['stopped'] = stopped
    budget_path.write_text(json.dumps(state), encoding='utf-8')
    assert not (e.peer_root / 'access_stop.json').exists()
    before = snapshot(e.peer_root)
    result = catalog.preview(e.path)
    peer = state_for(result, e.peer_root)
    assert not result['capture_preflight_blocked'] and not peer['blocked']
    assert not peer['access_stopped']
    assert snapshot(e.peer_root) == before


@pytest.mark.parametrize('retained_config', [False, True])
def test_peer_crash_directory_before_budget_creation_remains_unresolved(peer_experiment, retained_config):
    e = peer_experiment
    previous = e.peer_root / '2026-09-18'
    previous.mkdir(parents=True)
    if retained_config:
        (previous / 'selected_config.json').write_bytes(e.path.read_bytes())
    before = snapshot(e.peer_root)
    result = catalog.preview(e.path)
    peer = state_for(result, e.peer_root)
    assert not result['capture_preflight_blocked'] and not peer['blocked']
    assert previous.is_dir()
    assert snapshot(e.peer_root) == before


@pytest.mark.parametrize('problem', ['missing_status', 'unknown_status', 'missing_clock',
    'null_clock', 'naive_clock', 'invalid_clock', 'numeric_clock'])
def test_peer_terminal_status_and_clock_must_be_explicit_and_aware(peer_experiment, problem):
    e = peer_experiment
    previous = retain_previous(e.peer_root)
    report_path = previous / 'catalog_report.json'
    report = json.loads(report_path.read_text())
    if problem == 'missing_status':
        report.pop('status')
    elif problem == 'unknown_status':
        report['status'] = 'probably_done'
    elif problem == 'missing_clock':
        report.pop('ended_at')
    else:
        report['ended_at'] = {'null_clock': None, 'naive_clock': '2026-09-18T13:00:01',
            'invalid_clock': 'not a timestamp', 'numeric_clock': 17}[problem]
    report_path.write_text(json.dumps(report), encoding='utf-8')
    before = snapshot(e.peer_root)
    result = catalog.preview(e.path)
    peer = state_for(result, e.peer_root)
    assert not result['capture_preflight_blocked'] and not peer['blocked']
    assert snapshot(e.peer_root) == before


def test_reconciled_terminal_pagination_failure_allows_fresh_peer_without_repair(peer_experiment, tmp_path):
    e = peer_experiment
    e.add(2010, 'Audi', 'A4', 13)

    def unstable_page(url, **kwargs):
        response = e.send(url, **kwargs)
        request = kwargs['json']
        if (request['zip5'] == '08542' and request['pagination']['page'] == 2
                and request['filters'] == {'makes': [{'name': 'Audi', 'parentModels': [{'name': 'A4'}]}],
                                           'year': {'min': 2010, 'max': 2010}}):
            data = json.loads(response.content)
            data['inventory']['vehicles'][0] = copy.deepcopy(next(v for v in e.vehicles
                if v['year'] == 2010 and v['make'] == 'Audi' and v['parentModel'] == 'A4'))
            response.content = json.dumps(data).encode()
        return response

    prior = run_years(e, unstable_page)
    assert prior['status'] == 'collection_finished' and not prior['primary_queries_complete']
    isolated = [entry for entry in prior['entries'] if entry.get('failure_scope')]
    assert isolated
    leaf_ids = {entry.get('retry_of') or entry['query']['query_id'] for entry in isolated}
    assert leaf_ids == {'year_2010_make_000_model_000'}
    prior_root, prior_date = e.folder.parent, e.folder
    before = snapshot(prior_date)
    assert not (prior_root / 'access_stop.json').exists()
    catalog.export_catalog(prior_date, output=tmp_path / 'prior-partial-export')
    new_root = tmp_path / 'next_strategy_captures'
    e.config.update(capture_root=str(new_root), related_capture_roots=[str(prior_root)])
    e.path.write_text(json.dumps(e.config), encoding='utf-8')
    preview = catalog.preview(e.path)
    assert not preview['capture_preflight_blocked']
    assert not state_for(preview, prior_root)['unresolved_invocations']
    current = run_years(e)
    assert current['primary_queries_complete'] and current['declared_collection_complete']
    assert (new_root / '2026-09-19/catalog_report.json').is_file()
    assert snapshot(prior_date) == before


@pytest.mark.parametrize('related', [None, [], '', 'peer_captures', {}, [None], [1], [''], [' ']])
def test_v2_requires_explicit_nonempty_peer_paths_without_creating_roots(peer_experiment, related):
    e = peer_experiment
    if related is None:
        e.config.pop('related_capture_roots')
    else:
        e.config['related_capture_roots'] = related
    e.path.write_text(json.dumps(e.config), encoding='utf-8')
    post = Mock()
    with pytest.raises(ValueError):
        run_years(e, post)
    post.assert_not_called()
    assert not e.folder.parent.exists() and not e.peer_root.exists()


def test_peer_resolution_and_duplicate_paths_use_config_project_base(peer_experiment, tmp_path):
    e = peer_experiment
    config_directory = tmp_path / 'config'
    config_directory.mkdir()
    path = config_directory / 'years.json'
    peers = ['peer_captures', str(tmp_path / 'peer_captures'), 'peer_captures/../peer_captures']
    if os.name == 'nt':
        peers.append(str(tmp_path / 'peer_captures').upper())
    e.config.update(capture_root='own_captures', related_capture_roots=peers)
    path.write_text(json.dumps(e.config), encoding='utf-8')
    selected = catalog.settings(path)
    assert Path(selected['capture_root']) == tmp_path / 'own_captures'
    assert len(selected['related_capture_roots']) == 1
    assert Path(selected['related_capture_roots'][0]) == tmp_path / 'peer_captures'
    result = catalog.preview(path)
    assert len(result['capture_root_states']) == 2
    assert not (tmp_path / 'own_captures').exists() and not e.peer_root.exists()


def test_all_capture_locks_are_acquired_once_in_deterministic_order(peer_experiment, tmp_path, monkeypatch):
    e = peer_experiment
    roots = [tmp_path / name for name in ['z_own', 'b_peer', 'a_peer']]
    missing = tmp_path / 'missing_peer'
    e.config.update(capture_root=str(roots[0]), related_capture_roots=[
        str(roots[1]), str(roots[2]), str(roots[1]), str(missing)])
    e.path.write_text(json.dumps(e.config), encoding='utf-8')
    for path in roots[1:]:
        path.mkdir()
    acquired = []

    @contextmanager
    def track_lock(root):
        acquired.append(root)
        with cycle_lock(root):
            yield

    monkeypatch.setattr(catalog, 'cycle_lock', track_lock)
    result = run_years(e)
    assert result['primary_queries_complete']
    assert acquired == sorted(roots, key=lambda path: os.path.normcase(str(path)))
    assert not missing.exists()


def test_v1_without_related_roots_retains_its_original_scope(peer_experiment):
    e = peer_experiment
    e.config['format'] = 'carvana-full-inventory-v1'
    e.config.pop('partition_strategy')
    e.config.pop('related_capture_roots')
    e.path.write_text(json.dumps(e.config), encoding='utf-8')
    result = run_years(e)
    assert result['partition_strategy'] == 'all_year_models' and result['primary_queries_complete']
    assert result['primary_observed_vins'] == len(e.vehicles)
    assert not e.peer_root.exists()
