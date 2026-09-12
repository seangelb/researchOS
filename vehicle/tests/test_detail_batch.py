"""Offline browser-batch lifecycle tests; fixture statuses, synthetic clocks."""
from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest

from vehicle_tracker.detail_batch import (begin_visit, create_batch, fail_visit,
    load_browser_batches, preview_plan, record_visit)

ROOT = Path(__file__).resolve().parents[1]
BASE = pd.Timestamp('2099-01-01T12:00:00Z')


def clock(seconds):
    return (BASE + pd.Timedelta(seconds=seconds)).isoformat()


def write_json(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def study(tmp_path):
    captures = json.loads((ROOT / 'tests/fixtures/carvana_sale_pilot/chrome_projections_20260909.json').read_text())[:2]
    pages = [dict(c['expected'], url=c['requested_url'], selection_reason='synthetic selected target') for c in captures]
    plan = dict(as_of=clock(0), prepared_at=clock(0), expires_at=clock(120), pages=pages)
    source = tmp_path / 'selection.json'
    write_json(source, plan)
    cohort = dict(cohort_id='synthetic-one-member', selected_at=clock(-1),
        vehicles=[dict(pages[0], role='historical_control')])
    folder = tmp_path / 'data/experiments/carvana_detail_batches/test'
    return dict(root=tmp_path, folder=folder, source=source, plan=plan,
                captures=captures, cohorts=[cohort])


def create(study):
    return create_batch(study['source'], study['folder'], now=clock(0),
                        helper_path=ROOT / 'scripts/capture_carvana_page.js', cohorts=study['cohorts'])


def capture(study, index, seconds):
    result = deepcopy(study['captures'][index])
    result['checked_at'] = clock(seconds)
    return result


def load(study, seconds):
    return load_browser_batches(study['root'], study['cohorts'], as_of=clock(seconds))


def test_preview_is_read_only_and_partial_visits_keep_their_own_availability(study):
    _, selected = preview_plan(study['source'], now=clock(0))
    assert len(selected) == 2 and not study['folder'].exists()
    folder = create(study)
    assert load(study, -1)[0].empty
    begin_visit(folder, now=clock(1))
    record_visit(folder, capture(study, 0, 2), now=clock(3))
    begin_visit(folder, now=clock(18))
    record_visit(folder, capture(study, 1, 19), now=clock(20))
    before, _, health, _ = load(study, 2)
    assert before.empty and health.outcome.tolist() == ['started_unresolved', 'unattempted']
    first, plans, health, inputs = load(study, 19)
    assert len(first) == 1 and len(plans) == 2
    assert health.outcome.tolist() == ['matched', 'started_unresolved']
    assert first.available_at.iloc[0] == clock(3)
    assert folder / 'visit_01/capture.json' in inputs
    assert folder / 'visit_02/capture.json' not in inputs
    all_rows, _, health, _ = load(study, 20)
    assert len(all_rows) == 2 and health.outcome.eq('matched').all()
    assert all_rows.frozen_cohort_member.tolist() == [True, False]
    assert len(study['cohorts'][0]['vehicles']) == 1
    with pytest.raises(ValueError, match='complete'):
        begin_visit(folder, now=clock(35))


@pytest.mark.parametrize('problem,outcome', [('wrong_vin', 'identity_mismatch'),
    ('recommendation_only', 'target_not_found'), ('wrong_url', 'identity_mismatch')])
def test_invalid_native_target_is_retained_and_stops_batch(study, problem, outcome):
    folder = create(study)
    begin_visit(folder, now=clock(1))
    projection = capture(study, 0, 2)
    details = projection['contexts'][0]['forVehicleContext']['vehicleDetails']
    if problem == 'wrong_vin':
        details['vin'] = study['captures'][1]['expected']['vin']
    elif problem == 'recommendation_only':
        projection['contexts'] = deepcopy(study['captures'][1]['contexts'])
    else:
        projection['final_url'] = study['captures'][1]['final_url']
    result = record_visit(folder, projection, now=clock(3))
    assert result['parse_outcome'] == outcome
    rows, _, health, _ = load(study, 4)
    assert rows.parse_outcome.tolist() == [outcome]
    assert health.outcome.tolist() == [outcome, 'unattempted']
    with pytest.raises(ValueError, match='stopped'):
        begin_visit(folder, now=clock(18))


@pytest.mark.parametrize('reason,status', [('access_blocked', 'access_blocked'),
    ('navigation_failed', 'unknown'), ('interrupted', 'unknown'), ('page_not_ready', 'unknown')])
def test_explicit_failure_remains_a_real_attempt_without_native_status(study, reason, status):
    folder = create(study)
    begin_visit(folder, now=clock(1))
    fail_visit(folder, reason=reason, now=clock(2))
    rows, _, health, _ = load(study, 2)
    assert rows.parse_outcome.tolist() == ['access_failed']
    assert rows.observed_status.tolist() == [status]
    assert rows.saleStatus.isna().all()
    assert health.outcome.tolist() == ['access_failed', 'unattempted']
    with pytest.raises(ValueError, match='stopped'):
        begin_visit(folder, now=clock(20))


def test_started_visit_cannot_repeat_and_successful_visits_enforce_spacing(study):
    folder = create(study)
    begin_visit(folder, now=clock(1))
    with pytest.raises(ValueError, match='already started'):
        begin_visit(folder, now=clock(30))
    record_visit(folder, capture(study, 0, 2), now=clock(3))
    with pytest.raises(ValueError, match='unfinished'):
        record_visit(folder, capture(study, 0, 4), now=clock(4))
    with pytest.raises(ValueError, match='15 seconds'):
        begin_visit(folder, now=clock(17))
    begin_visit(folder, now=clock(18))
    assert len(list(folder.glob('visit_*'))) == 2


def test_expired_plan_cannot_create_or_start_another_visit(study):
    with pytest.raises(ValueError, match='Expired'):
        create_batch(study['source'], study['folder'], now=clock(120),
                     helper_path=ROOT / 'scripts/capture_carvana_page.js', cohorts=study['cohorts'])
    assert not study['folder'].exists()
    folder = create(study)
    with pytest.raises(ValueError, match='expired'):
        begin_visit(folder, now=clock(120))
    assert not list(folder.glob('visit_*'))
    assert load(study, 120)[2].window_expired.all()


@pytest.mark.parametrize('field', ['expected', 'undocumented', 'sensitive_context'])
def test_expected_identity_is_independent_and_only_allowlisted_projection_is_saved(study, field):
    folder = create(study)
    begin_visit(folder, now=clock(1))
    projection = capture(study, 0, 2)
    if field == 'expected':
        projection['expected'] = deepcopy(study['captures'][1]['expected'])
    elif field == 'undocumented':
        projection['cookies'] = 'synthetic forbidden field'
    else:
        projection['contexts'][0]['forVehicleContext']['vehicleDetails']['customer'] = 'synthetic'
    with pytest.raises(ValueError):
        record_visit(folder, projection, now=clock(3))
    assert not (folder / 'visit_01/capture.json').exists()
    assert load(study, 3)[2].outcome.tolist() == ['started_unresolved', 'unattempted']
    fail_visit(folder, reason='interrupted', now=clock(4))


@pytest.mark.parametrize('filename', ['plan.json', 'capture.js', 'visit_01/capture.json', 'visit_01/started.json'])
def test_frozen_sources_and_saved_capture_tampering_are_rejected(study, filename):
    folder = create(study)
    begin_visit(folder, now=clock(1))
    record_visit(folder, capture(study, 0, 2), now=clock(3))
    path = folder / filename
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='changed'):
        load(study, 4)


def test_capture_must_follow_its_reserved_visit_and_precede_availability(study):
    folder = create(study)
    begin_visit(folder, now=clock(5))
    for checked, available in [(4, 6), (7, 6)]:
        with pytest.raises(ValueError, match='clock'):
            record_visit(folder, capture(study, 0, checked), now=clock(available))
    assert not (folder / 'visit_01/capture.json').exists()


def orphan_capture(study, folder, *, malformed=False):
    """Simulate interruption between saving the projection and its completion."""
    projection = capture(study, 0, 2)
    path = folder / 'visit_01/capture.json'
    payload = b'{"format":' if malformed else (
        json.dumps(projection, indent=4) + '\n\n').encode('utf-8')
    path.write_bytes(payload)
    return path, payload, projection


def later_batch(study, *, seconds, name='later'):
    plan = deepcopy(study['plan'])
    plan.update(as_of=clock(seconds), prepared_at=clock(seconds),
                expires_at=clock(seconds + 120))
    source = study['root'] / f'{name}-selection.json'
    write_json(source, plan)
    return create_batch(source, study['folder'].with_name(name), now=clock(seconds),
                        helper_path=ROOT / 'scripts/capture_carvana_page.js', cohorts=study['cohorts'])


def test_recover_orphan_preserves_bytes_and_uses_recovery_availability_even_after_expiry(study):
    from vehicle_tracker.detail_batch import recover_visit

    folder = create(study)
    begin_visit(folder, now=clock(1))
    path, original, _ = orphan_capture(study, folder)
    # A saved projection alone is not a completed, usable visit.
    assert load(study, 10)[0].empty
    row = recover_visit(folder, now=clock(200))
    assert row['parse_outcome'] == 'matched'
    assert path.read_bytes() == original
    assert row['checked_at'] == clock(2)
    assert row['available_at'] == clock(200)
    assert load(study, 199)[0].empty
    rows, _, health, _ = load(study, 200)
    assert rows.available_at.tolist() == [clock(200)]
    assert health.outcome.tolist() == ['matched', 'unattempted']
    with pytest.raises(ValueError, match='unfinished'):
        recover_visit(folder, now=clock(201))


def test_record_identical_orphan_completes_it_without_rewriting(study):
    folder = create(study)
    begin_visit(folder, now=clock(1))
    path, original, projection = orphan_capture(study, folder)
    result = record_visit(folder, projection, now=clock(10))
    assert result['parse_outcome'] == 'matched'
    assert path.read_bytes() == original
    assert load(study, 9)[0].empty
    assert load(study, 10)[0].available_at.tolist() == [clock(10)]


def test_record_refuses_to_replace_a_different_orphan(study):
    folder = create(study)
    begin_visit(folder, now=clock(1))
    path, original, projection = orphan_capture(study, folder)
    replacement = deepcopy(projection)
    replacement['checked_at'] = clock(4)
    with pytest.raises(ValueError):
        record_visit(folder, replacement, now=clock(10))
    assert path.read_bytes() == original
    assert not (folder / 'visit_01/run.json').exists()
    assert load(study, 10)[2].outcome.tolist() == ['started_unresolved', 'unattempted']


def test_interrupted_explicit_failure_cannot_be_replaced_by_recording_original_capture(study, monkeypatch):
    import vehicle_tracker.detail_batch as batches

    folder = create(study)
    begin_visit(folder, now=clock(1))
    path, original, projection = orphan_capture(study, folder)
    real_write = batches.write_new

    def interrupt_completion(destination, value):
        if Path(destination).name == 'run.json':
            raise RuntimeError('Synthetic interruption before completion publication')
        return real_write(destination, value)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(batches, 'write_new', interrupt_completion)
        with pytest.raises(RuntimeError):
            fail_visit(folder, reason='access_blocked', now=clock(3))
    failure_path = folder / 'visit_01/failure.json'
    failure_original = failure_path.read_bytes()
    with pytest.raises(ValueError, match='[Rr]ecover'):
        record_visit(folder, projection, now=clock(4))
    assert not (folder / 'visit_01/run.json').exists()
    assert path.read_bytes() == original
    assert failure_path.read_bytes() == failure_original

    row = batches.recover_visit(folder, now=clock(5))
    assert row['parse_outcome'] == 'access_failed'
    assert row['checked_at'] == clock(3) and row['available_at'] == clock(5)
    assert path.read_bytes() == original
    assert failure_path.read_bytes() == failure_original
    assert load(study, 5)[0].observed_status.tolist() == ['access_blocked']
    with pytest.raises(ValueError, match='stopped'):
        begin_visit(folder, now=clock(20))


@pytest.mark.parametrize('malformed', [False, True])
def test_explicit_failure_preserves_orphan_and_publishes_separate_failure(study, malformed):
    from vehicle_tracker.detail_batch import recover_visit

    folder = create(study)
    begin_visit(folder, now=clock(1))
    path, original, _ = orphan_capture(study, folder, malformed=malformed)
    if malformed:
        with pytest.raises(ValueError):
            recover_visit(folder, now=clock(9))
        assert not (folder / 'visit_01/run.json').exists()
    result = fail_visit(folder, reason='interrupted', now=clock(10))
    assert result['parse_outcome'] == 'access_failed'
    assert path.read_bytes() == original
    report = json.loads((folder / 'visit_01/run.json').read_text())
    assert report['captures'][0]['file'] != 'capture.json'
    rows, _, health, _ = load(study, 10)
    assert rows.parse_outcome.tolist() == ['access_failed']
    assert rows.saleStatus.isna().all()
    assert health.outcome.tolist() == ['access_failed', 'unattempted']


@pytest.mark.parametrize('problem', ['identity', 'clock'])
def test_recovery_revalidates_orphan_identity_and_clock(study, problem):
    from vehicle_tracker.detail_batch import recover_visit

    folder = create(study)
    begin_visit(folder, now=clock(1))
    path, _, projection = orphan_capture(study, folder)
    if problem == 'identity':
        projection['expected'] = deepcopy(study['captures'][1]['expected'])
    else:
        projection['checked_at'] = clock(0)
    write_json(path, projection)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        recover_visit(folder, now=clock(10))
    assert path.read_bytes() == original
    assert not (folder / 'visit_01/run.json').exists()


def test_cross_batch_reservation_blocks_same_vin_even_after_old_window_expires(study):
    folder = create(study)
    begin_visit(folder, now=clock(1))
    other = later_batch(study, seconds=130)
    with pytest.raises(ValueError):
        begin_visit(other, now=clock(131))
    assert not list(other.glob('visit_*'))
    assert load(study, 131)[2].query("batch == 'test'").outcome.iloc[0] == 'started_unresolved'


@pytest.mark.parametrize('failure', [False, True])
def test_cross_batch_recent_check_requires_full_24_hours_after_success_or_failure(study, failure):
    folder = create(study)
    begin_visit(folder, now=clock(1))
    if failure:
        fail_visit(folder, reason='interrupted', now=clock(2))
    else:
        record_visit(folder, capture(study, 0, 2), now=clock(3))
    other = later_batch(study, seconds=86400)
    with pytest.raises(ValueError):
        begin_visit(other, now=clock(86401))
    assert not list(other.glob('visit_*'))
    reserved = begin_visit(other, now=clock(86402))
    assert reserved['expected']['vin'] == study['plan']['pages'][0]['vin']
    assert len(list(other.glob('visit_*'))) == 1


def test_unresolved_browser_reservation_excludes_vin_before_selection_and_refills_slots():
    from test_sales_proxy import synthetic_cohort, synthetic_cycles, synthetic_inventory, synthetic_vehicle
    from vehicle_tracker.sales_proxy import inventory_followup_queue

    cohorts = [synthetic_cohort(*(synthetic_vehicle(n) for n in range(1, 5)))]
    schedule = synthetic_cycles((1,))
    inventory = synthetic_inventory(*[(1, n, {}) for n in range(1, 9)])
    kwargs = dict(as_of='2026-09-02T12:00:00Z', batch_limit=4, control_count=1, seed='browser-reservation')
    before = inventory_followup_queue(schedule, inventory, pd.DataFrame(), cohorts, **kwargs)
    blocked = before.loc[before.selected_for_check & before.frozen_cohort_member].iloc[0]
    health = pd.DataFrame([dict(batch='synthetic-interrupted', retailer=blocked.retailer,
        vin=blocked.vin, listing_id=blocked.followup_listing_id, outcome='started_unresolved',
        started_at='2026-09-02T10:00:00Z', available_at=None, window_expired=True)])
    after = inventory_followup_queue(schedule, inventory, pd.DataFrame(), cohorts,
        browser_health=health, **kwargs)
    row = after.set_index('vin').loc[blocked.vin]
    assert not row.selected_for_check and not row.eligible_for_selection
    assert after.selected_for_check.sum() == 4
    assert after.random_control.sum() == 1
    assert pd.isna(row.last_attempt_at) and pd.isna(row.latest_native_status)
    assert pd.isna(row.latest_parse_outcome)  # A reservation is not a native visit.
    health['started_at'] = '2026-09-03T10:00:00Z'
    earlier = inventory_followup_queue(schedule, inventory, pd.DataFrame(), cohorts,
        browser_health=health, **kwargs)
    assert set(earlier.loc[earlier.selected_for_check, 'vin']) == set(before.loc[before.selected_for_check, 'vin'])
