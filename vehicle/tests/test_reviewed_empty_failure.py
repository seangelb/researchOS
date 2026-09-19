"""A reviewed client bug never clears other stops or extends its original allowance."""
import json
from pathlib import Path

import pytest

from vehicle_tracker import gap_recovery as recovery


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


@pytest.fixture
def reviewed(tmp_path):
    folder = tmp_path/'prior'/'2026-09-19'
    source = write(folder/'child/source.json', dict(inventory=dict(vehicles=[], pagination=dict(
        currentPage=1, pageSize=24, totalMatchedInventory=0, totalMatchedPages=1)),
        userDeliveryInfo=dict(zip5='08542')))
    child = write(folder/'child/run_report.json', dict(pages=[dict(stored_rows=0, http_status=200,
        response_evidence=dict(source_path=str(source), source_sha256=recovery.digest(source),
            http_diagnostics=dict(media_type='application/json', body_markers=dict(challenge_markup=False))))],
        outcome_kind='schema_failure', query_complete=False, unique_listings=0))
    original = write(folder/'selected_config.json', dict(max_requests=6000))
    report = write(folder/'catalog_report.json', dict(status='stopped', requests=1,
        ended_at='2026-09-19T16:13:11Z', window_end='2026-09-19T22:13:10Z',
        cycle_date='2026-09-19', plan_sha256='plan', config_sha256=recovery.digest(original),
        entries=[dict(report=str(child), report_sha256=recovery.digest(child))]+
        [dict(status='unattempted') for _ in range(499)]))
    budget = write(folder/'catalog_budget.json', dict(budget=dict(requests=1, stopped=True, pending_request=False)))
    stop = write(folder.parent/'access_stop.json', dict(run=str(report)))
    diagnosis = write(tmp_path/'diagnosis.json', dict(capture_directory=str(folder),
        one_request_fully_reconciled=True, native_applied_context_verified=False,
        exact_reproduced_error='Inconsistent search pagination; coverage cannot be established',
        bindings={str(p):recovery.digest(p) for p in [source, child, original, report, budget, stop]}))
    config = dict(capture_root=str(tmp_path/'fresh'), related_capture_roots=[str(folder.parent),str(tmp_path/'peer')],
        plan_sha256='plan', authorized_cycle_date='2026-09-19', max_requests=5999,
        absolute_window_end='2026-09-19T22:13:10Z',
        reviewed_empty_page_failure=dict(path=str(diagnosis), sha256=recovery.digest(diagnosis)))
    return config, folder, diagnosis


def test_review_only_recognizes_exact_retained_failure_without_mutation(reviewed):
    config, folder, diagnosis = reviewed
    before = {str(p):p.read_bytes() for p in folder.parent.rglob('*') if p.is_file()}
    states = recovery._recovery_root_states(config)
    recovery._require_clear_roots(states)
    prior = next(s for s in states if s['capture_root']==str(folder.parent))
    assert prior['retained_access_stopped'] and prior['reviewed_client_failure']==str(folder)
    assert recovery._capture_root_state(folder.parent)['blocked']
    assert before == {str(p):p.read_bytes() for p in folder.parent.rglob('*') if p.is_file()}


@pytest.mark.parametrize('name', ['access_stop.json','2026-09-19/catalog_budget.json',
                                '2026-09-19/child/source.json'])
def test_any_bound_original_mutation_blocks(reviewed, name):
    config, folder, _ = reviewed
    (folder.parent/name).write_text('{}')
    with pytest.raises(ValueError, match='evidence changed'):
        recovery._recovery_root_states(config)


def test_diagnosis_mutation_blocks(reviewed):
    config, _, diagnosis = reviewed
    diagnosis.write_text('{}')
    with pytest.raises(ValueError, match='diagnosis changed'):
        recovery._recovery_root_states(config)


@pytest.mark.parametrize('key,value', [('max_requests',6000),
    ('absolute_window_end','2026-09-19T22:13:11Z'),('authorized_cycle_date','2026-09-20')])
def test_no_budget_or_deadline_or_date_extension(reviewed, key, value):
    config, _, _ = reviewed
    config[key]=value
    with pytest.raises(ValueError, match='exact empty-page'):
        recovery._recovery_root_states(config)


def test_no_restart_or_excluded_original_root(reviewed):
    config, folder, _ = reviewed
    config['capture_root']=str(folder.parent)
    with pytest.raises(ValueError, match='locked peer'):
        recovery._recovery_root_states(config)
    config['capture_root']=str(folder.parent.parent/'fresh')
    config['related_capture_roots']=[]
    with pytest.raises(ValueError, match='locked peer'):
        recovery._recovery_root_states(config)


def test_another_invocation_in_reviewed_root_still_blocks(reviewed):
    config, folder, _ = reviewed
    (folder.parent/'another_attempt').mkdir()
    with pytest.raises(ValueError, match='another or changed'):
        recovery._recovery_root_states(config)


def test_other_root_stop_still_blocks(reviewed):
    config, _, _ = reviewed
    write(Path(config['related_capture_roots'][1])/'access_stop.json', dict(reason='HTTP403'))
    with pytest.raises(ValueError, match='access stop'):
        recovery._require_clear_roots(recovery._recovery_root_states(config))


def test_no_exception_without_explicit_review(reviewed):
    config, _, _ = reviewed
    del config['reviewed_empty_page_failure']
    with pytest.raises(ValueError, match='access stop'):
        recovery._require_clear_roots(recovery._recovery_root_states(config))


@pytest.mark.parametrize('suffix', ['-wal','-shm','-journal'])
def test_unbound_database_sidecar_still_blocks(reviewed, suffix):
    config, folder, diagnosis_path = reviewed
    database = folder/'child/vehicle.sqlite'
    database.write_bytes(b'synthetic settled database bytes')
    diagnosis = recovery.load(diagnosis_path)
    diagnosis['bindings'][str(database)] = recovery.digest(database)
    write(diagnosis_path,diagnosis)
    config['reviewed_empty_page_failure']['sha256'] = recovery.digest(diagnosis_path)
    Path(str(database)+suffix).write_bytes(b'unsettled')
    with pytest.raises(ValueError,match='unsettled sidecars'):
        recovery._recovery_root_states(config)
