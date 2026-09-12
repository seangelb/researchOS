"""Synthetic global browser budgets: no network and no writes to retained evidence."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from threading import Event

import pandas as pd
import pytest

from test_sales_proxy import synthetic_vehicle
from vehicle_tracker import detail_batch
from vehicle_tracker.detail_batch import (begin_visit, browser_workload, create_batch,
                                         browser_capacity, fail_visit, record_visit, write_new)

ROOT = Path(__file__).resolve().parents[1]
BASE = pd.Timestamp('2099-01-01T23:50:00Z')


def clock(seconds):
    return (BASE + pd.Timedelta(seconds=seconds)).isoformat()


def batch(tmp_path, number, *, prepared=0):
    vehicle = synthetic_vehicle(number)
    plan = dict(as_of=clock(prepared), prepared_at=clock(prepared), expires_at=clock(prepared+3600),
                pages=[dict(vehicle, selection_reason='synthetic global budget test')])
    source = tmp_path/f'plan_{number}.json'
    write_new(source, plan)
    folder = tmp_path/'batches'/str(number)
    create_batch(source, folder, helper_path=ROOT/'scripts/capture_carvana_page.js',
                 cohorts=[], now=clock(prepared))
    return folder, vehicle


def capture(vehicle, seconds):
    value = deepcopy(json.loads((ROOT/'tests/fixtures/carvana_sale_pilot/chrome_projections_20260909.json').read_text())[0])
    value.update(expected={k: vehicle[k] for k in ['retailer', 'vin', 'listing_id']},
                 requested_url=vehicle['url'], final_url=vehicle['url'], checked_at=clock(seconds))
    value['contexts'][0]['forVehicleContext']['vehicleDetails'].update(
        vin=vehicle['vin'], vehicleId=int(vehicle['listing_id']))
    return value


def fill_day(tmp_path):
    for number in range(1, 13):
        offset = (number-1)*30
        folder, vehicle = batch(tmp_path, number, prepared=offset)
        begin_visit(folder, now=clock(offset+1))
        if number <= 6:
            fail_visit(folder, reason='navigation_failed', now=clock(offset+2))
        else:
            record_visit(folder, capture(vehicle, offset+2), now=clock(offset+3))


def test_new_batches_and_midnight_cannot_bypass_daily_cap_and_failures_count(tmp_path):
    fill_day(tmp_path)
    folder, _ = batch(tmp_path, 13, prepared=600)  # New calendar day, same rolling day.
    before = {p: p.read_bytes() for p in (tmp_path/'batches').rglob('*') if p.is_file()}
    summary = browser_workload(tmp_path/'batches', now=clock(601))
    assert summary['attempted_last_24h'] == 12 and summary['remaining_starts'] == 0
    assert summary['next_start_at'] == clock(86401)
    with pytest.raises(ValueError, match='Rolling 24-hour limit'):
        begin_visit(folder, now=clock(601))
    assert not (folder/'visit_01').exists()
    assert before == {p: p.read_bytes() for p in (tmp_path/'batches').rglob('*') if p.is_file()}


def test_rolling_boundary_releases_exactly_one_start_without_resetting_whole_day(tmp_path):
    fill_day(tmp_path)
    folder, _ = batch(tmp_path, 13, prepared=86400)
    summary = browser_workload(tmp_path/'batches', now=clock(86401))
    assert summary['attempted_last_24h'] == 11 and summary['remaining_starts'] == 1
    begin_visit(folder, now=clock(86401))
    after = browser_workload(tmp_path/'batches', now=clock(86401))
    assert after['attempted_last_24h'] == 12 and after['unresolved_visits'] == 1


def test_unresolved_visit_stops_different_vin_and_global_spacing_survives_batch_change(tmp_path):
    first, _ = batch(tmp_path, 1)
    second, _ = batch(tmp_path, 2)
    begin_visit(first, now=clock(1))
    with pytest.raises(ValueError, match='Unresolved browser visit'):
        begin_visit(second, now=clock(20))
    fail_visit(first, reason='interrupted', now=clock(21))
    with pytest.raises(ValueError, match='15 seconds'):
        begin_visit(second, now=clock(35))
    begin_visit(second, now=clock(36))
    summary = browser_workload(tmp_path/'batches', now=clock(36))
    assert summary['attempted_last_24h'] == 2 and summary['unresolved_visits'] == 1


def test_future_batch_reservations_do_not_change_earlier_budget(tmp_path):
    future, _ = batch(tmp_path, 1, prepared=3600)
    begin_visit(future, now=clock(3601))
    present, _ = batch(tmp_path, 2, prepared=100)
    summary = browser_workload(tmp_path/'batches', now=clock(101))
    assert summary['attempted_last_24h'] == summary['unresolved_visits'] == 0
    begin_visit(present, now=clock(101))


def test_access_challenge_stops_all_batches_for_24_hours_without_transport_retry(tmp_path):
    first, _ = batch(tmp_path, 1)
    second, _ = batch(tmp_path, 2)
    begin_visit(first, now=clock(1))
    fail_visit(first, reason='access_blocked', now=clock(2))
    summary = browser_workload(tmp_path/'batches', now=clock(20))
    assert summary['access_blocks_last_24h'] == 1 and summary['next_start_at'] == clock(86402)
    with pytest.raises(ValueError, match='Access challenge'):
        begin_visit(second, now=clock(20))
    tomorrow, _ = batch(tmp_path, 3, prepared=86400)
    with pytest.raises(ValueError, match='Access challenge'):
        begin_visit(tomorrow, now=clock(86401))
    begin_visit(tomorrow, now=clock(86402))


def test_abandoned_lock_is_visible_read_only_and_never_automatically_cleared(tmp_path):
    folder, _ = batch(tmp_path, 1)
    lock = folder.parent/detail_batch.RESERVATION_LOCK
    lock.write_text('{"pid": 999999999, "synthetic": true}', encoding='utf-8')
    saved = lock.read_bytes()
    summary = browser_workload(folder.parent, now=clock(1))
    assert summary['reservation_lock_present'] and summary['remaining_starts'] is None
    with pytest.raises(ValueError, match='reservation lock'):
        begin_visit(folder, now=clock(1))
    assert lock.read_bytes() == saved and not (folder/'visit_01').exists()


def test_concurrent_different_batch_starts_have_one_reservation_owner(tmp_path, monkeypatch):
    first, _ = batch(tmp_path, 1)
    second, _ = batch(tmp_path, 2)
    entered, release = Event(), Event()
    original = detail_batch._workload

    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(5), 'Synthetic owner did not receive release signal'
        return original(*args, **kwargs)

    monkeypatch.setattr(detail_batch, '_workload', paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(begin_visit, first, now=clock(1))
        try:
            assert entered.wait(5)
            contender = pool.submit(begin_visit, second, now=clock(1))
            with pytest.raises(ValueError, match='reservation lock'):
                contender.result(timeout=5)
        finally:
            release.set()
        assert owner.result(timeout=5)['expected']['vin'] == synthetic_vehicle(1)['vin']
    assert not (second/'visit_01').exists()
    assert not (first.parent/detail_batch.RESERVATION_LOCK).exists()


def test_recording_an_existing_reservation_is_allowed_at_full_budget(tmp_path):
    for number in range(1, 13):
        offset = (number-1)*30
        folder, vehicle = batch(tmp_path, number, prepared=offset)
        begin_visit(folder, now=clock(offset+1))
        if number < 12:
            record_visit(folder, capture(vehicle, offset+2), now=clock(offset+3))
    summary = browser_workload(tmp_path/'batches', now=clock(332))
    assert summary['remaining_starts'] == 0 and summary['unresolved_visits'] == 1
    result = record_visit(folder, capture(vehicle, 332), now=clock(333))
    assert result['parse_outcome'] == 'matched'
    after = browser_workload(tmp_path/'batches', now=clock(334))
    assert after['attempted_last_24h'] == 12 and after['unresolved_visits'] == 0


def requests(count, *, start=0, end=48*3600, wave='primary'):
    return [dict(wave=wave, retailer='carvana', vin=synthetic_vehicle(n)['vin'],
                 start=clock(start), end=clock(end)) for n in range(1, count+1)]


def test_future_capacity_uses_rolling_starts_and_complete_capture_deadlines(tmp_path):
    result = browser_capacity(tmp_path, requests(25), now=clock(0))
    assert sum(r['fits'] for r in result['checks']) == 24
    starts = sorted(pd.Timestamp(r['planned_start']) for r in result['checks'] if r['fits'])
    assert all(sum(t-pd.Timedelta(hours=24) < earlier <= t for earlier in starts) <= 12 for t in starts)
    assert all(pd.Timestamp(r['planned_capture_at']) < pd.Timestamp(r['end']) for r in result['checks'] if r['fits'])
    assert not list(tmp_path.iterdir())
    # Starting inside the window is insufficient when the assumed capture ends at its excluded end.
    boundary = browser_capacity(tmp_path, requests(1, end=90), now=clock(0))
    assert not boundary['checks'][0]['fits']


def test_capacity_counts_preexisting_failed_starts_without_resetting_budget(tmp_path):
    fill_day(tmp_path)
    short = browser_capacity(tmp_path/'batches', requests(2, start=600, end=86493), now=clock(601))
    assert sum(r['fits'] for r in short['checks']) == 1
    assert short['checks'][0]['planned_start'] == clock(86402)  # Failed capture still imposes VIN spacing.
    assert short['workload']['attempted_last_24h'] == 12


@pytest.mark.parametrize('problem', ['unresolved', 'lock'])
def test_no_assumed_recovery_releases_future_capacity(tmp_path, problem):
    folder, _ = batch(tmp_path, 1)
    if problem == 'unresolved':
        begin_visit(folder, now=clock(1))
    else:
        (folder.parent/detail_batch.RESERVATION_LOCK).write_text('{}')
    result = browser_capacity(folder.parent, requests(2, start=7*86400, end=9*86400, wave='repeat'), now=clock(2))
    assert not any(r['fits'] for r in result['checks'])
    assert all(r['reason'] for r in result['checks'])


def test_capacity_tracks_vin_spacing_across_listings_and_proposed_waves(tmp_path):
    first = requests(1, end=1000)
    repeat = requests(1, start=86400, end=86581, wave='repeat')
    result = browser_capacity(tmp_path, [*first, *repeat], now=clock(0))
    assert [r['planned_start'] for r in result['checks']] == [clock(0), clock(86490)]
    existing = [dict(retailer='carvana', vin=first[0]['vin'], checked_at=clock(0), listing_id='other')]
    result = browser_capacity(tmp_path, first, now=clock(0), prior_checks=existing)
    assert not result['checks'][0]['fits']


def test_effort_can_reduce_capacity_below_local_start_setting(tmp_path):
    result = browser_capacity(tmp_path, requests(12, end=86400), now=clock(0),
                              minutes_per_check=3, operator_minutes_per_day=20)
    assert result['effective_starts_per_rolling_day'] == 6
    assert sum(r['fits'] for r in result['checks']) == 6
    with pytest.raises(ValueError, match='effort'):
        browser_capacity(tmp_path, [], now=clock(0), minutes_per_check=float('nan'))


def test_capacity_does_not_treat_repeat_vin_as_new_independent_target(tmp_path):
    with pytest.raises(ValueError, match='per VIN and wave'):
        browser_capacity(tmp_path, requests(1)*2, now=clock(0))
