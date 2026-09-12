"""Synthetic study-to-browser bridge checks; no network or retained-data writes."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from test_sales_proxy import synthetic_vehicle
from test_status_experiment import AS_OF, PREPARED, at, frozen_study, study_inventory
from vehicle_tracker.detail_batch import create_batch, digest, read_batch, write_new
from vehicle_tracker.status_experiment import freeze_plan, inventory_frame, read_experiment


@pytest.fixture
def cli():
    path = Path(__file__).resolve().parents[1] / 'scripts/run_status_experiment.py'
    spec = importlib.util.spec_from_file_location('synthetic_status_experiment_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(cli, plan, rows=(), health=None, now='2026-09-03T12:00Z', **kwargs):
    return cli.study_batch(plan, pd.DataFrame(rows), pd.DataFrame() if health is None else health,
                           now=now, **kwargs)


def test_batches_interleave_arms_and_preserve_frozen_membership_without_resampling(cli):
    cycles, inventory = study_inventory(8)
    frame = inventory_frame(cycles, inventory, as_of=AS_OF)
    plan = freeze_plan(frame, as_of=AS_OF, prepared_at=PREPARED, seed='synthetic', per_arm=5)
    original = deepcopy(plan)
    batch, deferred = prepare(cli, plan)
    assert len(batch['pages']) == 12 and not deferred
    for offset in [0, 4, 8]:
        assert len({page['arm'] for page in batch['pages'][offset:offset + 4]}) == 4
    assert {p['vin'] for p in batch['pages']} <= {p['vin'] for p in plan['pages']}
    assert plan == original
    completed = [at(int(page['vin'][-6:]), 'Available', '2026-09-03T11:00Z') for page in batch['pages']]
    next_batch, deferred = prepare(cli, plan, completed)
    assert len(next_batch['pages']) == 8 and not deferred
    assert not {p['vin'] for p in batch['pages']} & {p['vin'] for p in next_batch['pages']}
    assert {p['vin'] for p in [*batch['pages'], *next_batch['pages']]} == {p['vin'] for p in plan['pages']}


def test_recent_physical_check_blocks_original_vin_even_on_another_listing(cli):
    plan, _, _ = frozen_study()
    replacement = at(1, 'Available', '2026-09-03T10:00Z', listing_id='999999')
    batch, deferred = prepare(cli, plan, [replacement])
    target = synthetic_vehicle(1)
    assert target['vin'] not in {p['vin'] for p in batch['pages']}
    assert deferred == [dict(vin=target['vin'], reason='24-hour physical-check spacing')]
    eligible, _ = prepare(cli, plan, [replacement], now='2026-09-04T10:00Z')
    returned = next(p for p in eligible['pages'] if p['vin'] == target['vin'])
    assert returned['listing_id'] == target['listing_id']
    assert returned['url'] == target['url']


def test_unresolved_reservation_defers_without_replacing_the_selected_vin(cli):
    plan, _, _ = frozen_study()
    target = synthetic_vehicle(1)
    health = pd.DataFrame([dict(target, outcome='started_unresolved', started_at='2026-09-03T11:00Z')])
    batch, deferred = prepare(cli, plan, health=health)
    assert len(batch['pages']) == 3
    assert deferred == [dict(vin=target['vin'], reason='unresolved browser reservation')]
    assert target['vin'] in {p['vin'] for p in plan['pages']}


def test_future_reservation_cannot_change_a_replayed_batch(cli):
    plan, _, _ = frozen_study()
    health = pd.DataFrame([dict(synthetic_vehicle(1), outcome='started_unresolved',
                               started_at='2026-09-04T11:00Z')])
    batch, deferred = prepare(cli, plan, health=health)
    assert len(batch['pages']) == 4 and not deferred


@pytest.mark.parametrize('wave', ['primary', 'repeat'])
def test_wave_boundaries_are_fixed_and_batch_expiry_cannot_extend_them(cli, wave):
    plan, _, _ = frozen_study()
    start, end = pd.Timestamp(plan[wave + '_start']), pd.Timestamp(plan[wave + '_end'])
    for blocked in [start - pd.Timedelta(seconds=1), end]:
        with pytest.raises(ValueError, match='window'):
            prepare(cli, plan, now=blocked, wave=wave)
    batch, _ = prepare(cli, plan, now=start, wave=wave)
    assert len(batch['pages']) == 4
    near_end, _ = prepare(cli, plan, now=end - pd.Timedelta(minutes=1), wave=wave)
    assert pd.Timestamp(near_end['expires_at']) == end


def test_successful_wave_does_not_repeat_but_same_original_vins_enter_repeat_wave(cli):
    plan, _, _ = frozen_study()
    primary = [at(int(p['vin'][-6:]), 'Sold', '2026-09-03T10:00Z') for p in plan['pages']]
    batch, deferred = prepare(cli, plan, primary)
    assert batch['pages'] == [] and deferred == []
    repeat, deferred = prepare(cli, plan, primary, wave='repeat', now=plan['repeat_start'])
    assert {p['vin'] for p in repeat['pages']} == {p['vin'] for p in plan['pages']}
    completed = [at(int(p['vin'][-6:]), 'Available', '2026-09-10T10:00Z') for p in plan['pages']]
    done, deferred = prepare(cli, plan, [*primary, *completed], wave='repeat', now='2026-09-10T12:00Z')
    assert done['pages'] == [] and deferred == []


def test_matched_unavailable_finishes_ascertainment_without_revisiting_for_a_binary_label(cli):
    plan, _, _ = frozen_study()
    unavailable = at(1, 'Unavailable', '2026-09-03T10:00Z',
                     purchaseType='NotPurchasable', observed_status='unavailable')
    batch, deferred = prepare(cli, plan, [unavailable], now='2026-09-04T12:00Z')
    assert synthetic_vehicle(1)['vin'] not in {p['vin'] for p in batch['pages']}
    assert not deferred  # Completed check, not a temporary 24-hour spacing block.
    repeat, _ = prepare(cli, plan, [unavailable], wave='repeat', now=plan['repeat_start'])
    assert synthetic_vehicle(1)['vin'] in {p['vin'] for p in repeat['pages']}


def test_failed_attempt_waits_24_hours_but_does_not_move_fixed_deadline(cli):
    plan, _, _ = frozen_study()
    failure = at(1, None, '2026-09-03T10:00Z', parse_outcome='access_failed', observed_status='access_blocked')
    early, deferred = prepare(cli, plan, [failure])
    assert synthetic_vehicle(1)['vin'] not in {p['vin'] for p in early['pages']}
    assert len(deferred) == 1
    later, deferred = prepare(cli, plan, [failure], now='2026-09-04T10:00Z')
    assert synthetic_vehicle(1)['vin'] in {p['vin'] for p in later['pages']} and not deferred
    assert pd.Timestamp(later['expires_at']) <= pd.Timestamp(plan['primary_end'])
    with pytest.raises(ValueError, match='window'):
        prepare(cli, plan, [failure], now=plan['primary_end'])


def test_bridge_output_is_accepted_by_existing_batch_reader_without_rebinding(cli, tmp_path):
    plan, _, _ = frozen_study()
    batch, _ = prepare(cli, plan)
    source = tmp_path / 'batch_plan.json'
    write_new(source, batch)
    folder = tmp_path / 'browser_batch'
    helper = Path(__file__).resolve().parents[1] / 'scripts/capture_carvana_page.js'
    create_batch(source, folder, helper_path=helper, cohorts=[], now=batch['prepared_at'])
    _, retained, selected = read_batch(folder)
    assert retained == batch
    assert selected[['retailer', 'vin', 'listing_id', 'url']].to_dict('records') == [
        {key: page[key] for key in ['retailer', 'vin', 'listing_id', 'url']} for page in batch['pages']]
    with pytest.raises(FileExistsError):
        create_batch(source, folder, helper_path=helper, cohorts=[], now=batch['prepared_at'])


@pytest.mark.parametrize('changed', ['plan', 'source'])
def test_retained_study_rejects_changed_plan_or_source_and_replays_availability(tmp_path, changed):
    plan, _, _ = frozen_study()
    source = tmp_path / 'retained_inventory.json'
    source.write_text('{"synthetic": true}', encoding='utf-8')
    path = tmp_path / 'plan.json'
    write_new(path, plan)
    write_new(tmp_path / 'manifest.json', dict(plan_sha256=digest(path), cycles=[],
                                             input_hashes={str(source): digest(source)}))
    assert read_experiment(path, as_of='2026-09-02T16:04Z') is None
    assert read_experiment(path, as_of=PREPARED) == plan
    if changed == 'plan':
        altered = deepcopy(plan)
        altered['primary_end'] = '2026-09-05T00:00Z'
        path.write_text(json.dumps(altered), encoding='utf-8')
    else:
        source.write_text('{"synthetic": "changed"}', encoding='utf-8')
    with pytest.raises(ValueError, match='changed'):
        read_experiment(path, as_of='2026-09-03T12:00Z')


def mock_freeze(cli, tmp_path, monkeypatch, per_arm):
    cycles, inventory = study_inventory(per_arm)
    class FrozenClock:
        @staticmethod
        def now(tz):
            return pd.Timestamp(PREPARED).to_pydatetime()
    monkeypatch.setattr(cli, 'datetime', FrozenClock)
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    monkeypatch.setattr(cli, 'read_cycle_history', lambda *args, **kwargs: (cycles, inventory))
    monkeypatch.setattr(cli, 'load_detail_evidence', lambda *args, **kwargs: {'records': pd.DataFrame()})
    source = tmp_path/'inventory/cycle.json'
    source.parent.mkdir()
    source.write_text('{"synthetic":true}')
    return ['freeze', '--cycles', str(source), '--per-arm', str(per_arm), '--destination', str(tmp_path/'study')]


def test_infeasible_future_freeze_prints_shortfall_and_creates_no_study(cli, tmp_path, monkeypatch, capsys):
    argv = mock_freeze(cli, tmp_path, monkeypatch, per_arm=7)
    with pytest.raises(ValueError, match='infeasible'):
        cli.main(argv)
    output = capsys.readouterr().out
    assert 'required_checks' in output and 'available_capacity' in output and 'shortfall' in output
    assert not (tmp_path/'study').exists()


def test_future_freeze_saves_its_capacity_and_effort_assumptions(cli, tmp_path, monkeypatch):
    argv = mock_freeze(cli, tmp_path, monkeypatch, per_arm=3)
    assert cli.main(argv) == 0
    plan = read_experiment(tmp_path/'study/plan.json', as_of=PREPARED)
    feasibility = plan['feasibility_at_freeze']
    assert len(plan['pages']) == 12 and feasibility['feasible']
    assert [r['shortfall'] for r in feasibility['table']] == [0, 0]
    assert feasibility['minutes_per_check'] == 1.5 and feasibility['operator_minutes_per_day'] == 20


def test_operator_effort_shortfall_blocks_freeze_without_overriding_local_cap(cli, tmp_path, monkeypatch):
    argv = mock_freeze(cli, tmp_path, monkeypatch, per_arm=3)
    with pytest.raises(ValueError, match='infeasible'):
        cli.main([*argv, '--minutes-per-check', '10'])
    assert not (tmp_path/'study').exists()
