"""Entirely synthetic sales-proxy cases; no network, retained data, or database writes."""
import time

import pandas as pd
import pytest

from vehicle_tracker.events import CYCLE_COLUMNS
from vehicle_tracker.history import OBSERVATION_COLUMNS
from vehicle_tracker.sales_proxy import (allocate_intervals, cohort_estimates, disappearance_events,
                                        inventory_flows, inventory_exit_episodes, inventory_followup_queue, native_estimate_revisions,
                                        native_transition_events, native_visits, reappearance_summary,
                                        sampled_exit_estimate, wilson_resolved_interval)


def synthetic_vehicle(number=1, role='prospective_inventory'):
    listing = str(100000 + number)
    return dict(retailer='carvana', vin=f'1HGCM82633A{number:06d}', listing_id=listing,
                role=role, selection_reason='Synthetic fixture only',
                url=f'https://www.carvana.com/vehicle/{listing}')


def synthetic_cohort(*vehicles):
    return dict(cohort_id='synthetic-proxy-cohort', selected_at='2026-09-01T00:00:00Z',
                vehicles=list(vehicles or [synthetic_vehicle()]))


def native(day, status='Available', *, vehicle=None, **changes):
    vehicle = vehicle or synthetic_vehicle()
    time = f'2026-09-{day:02}T10:00:00Z'
    row = dict(retailer='carvana', vin=vehicle['vin'], listing_id=vehicle['listing_id'],
               checked_at=time, available_at=time, source=f'synthetic://native/{day}/{vehicle["vin"]}',
               saleStatus=status, purchaseType='NotPurchasable' if status == 'Sold' else 'Purchasable',
               parse_outcome='matched', observed_status='sold_label' if status == 'Sold' else 'available')
    return dict(row, **changes)


def synthetic_cycles(days=(1, 2, 3, 4), *, partial=()):
    return pd.DataFrame([dict(cycle_id=f'synthetic-{day}', cycle_date=f'2026-09-{day:02}',
        timezone='America/New_York', window_start=f'2026-09-{day:02}T14:00:00Z',
        window_end=f'2026-09-{day:02}T15:00:00Z', scope_id='synthetic-four-model-scope',
        coverage_complete=day not in partial, coverage_reason='Synthetic fixture only',
        available_at=f'2026-09-{day:02}T15:01:00Z') for day in days], columns=CYCLE_COLUMNS)


def synthetic_inventory(*specs):
    output = []
    for day, number, changes in specs:
        vehicle = synthetic_vehicle(number)
        row = dict.fromkeys(OBSERVATION_COLUMNS)
        row.update(cycle_id=f'synthetic-{day}', retailer='carvana', vin=vehicle['vin'],
            listing_id=vehicle['listing_id'], capture_id=f'synthetic-capture-{day}-{number}',
            run_id=f'synthetic-run-{day}', observed_at_utc=f'2026-09-{day:02}T14:30:00Z',
            source_url=f'synthetic://inventory/{day}', source_path=f'synthetic://raw/{day}',
            listing_url=vehicle['url'], asking_price_usd=10000, purchase_pending=False,
            vehicle_lock_type=0)
        row.update(changes)
        output.append(row)
    return pd.DataFrame(output, columns=[*OBSERVATION_COLUMNS, 'cycle_id', 'source_path'])


def test_reappearance_fraction_counts_the_same_population_on_both_sides():
    departures = pd.DataFrame([
        dict(retailer='carvana', vin='one', first_absent_snapshot='day2'),
        dict(retailer='carvana', vin='one', first_absent_snapshot='day4'),
        dict(retailer='carvana', vin='two', first_absent_snapshot='day4'),
        dict(retailer='carvana', vin='three', first_absent_snapshot='day6')])
    returns = departures.iloc[[0, 1]].copy()
    summary = reappearance_summary(departures, returns, latest_snapshot='day6').iloc[0]
    assert summary.earlier_departure_episodes == 3 and summary.later_reappeared_episodes == 2
    assert summary.earlier_departed_vins == 2 and summary.later_reappeared_vins == 1
    assert summary.observed_return_fraction == 0.5
    assert summary.episode_return_fraction == pytest.approx(2 / 3)
    assert summary.latest_departures_right_censored == 1
    with pytest.raises(ValueError, match='unique'):
        reappearance_summary(departures, pd.concat([returns, returns]), latest_snapshot='day6')
    with pytest.raises(ValueError, match='earlier departure'):
        reappearance_summary(departures, departures.iloc[[3]], latest_snapshot='day6')


def test_followup_queue_finds_broad_events_and_preserves_cohort_and_visit_spacing():
    cohort = synthetic_cohort(synthetic_vehicle(1), synthetic_vehicle(20, 'historical_control'))
    schedule = synthetic_cycles((1, 2, 4), partial=(2,))
    inventory = synthetic_inventory(
        *[(1, n, {}) for n in range(1, 10)], (2, 99, {}),
        (4, 1, {'purchase_pending': True}),
        (4, 3, {'listing_id': '200003', 'listing_url': 'https://www.carvana.com/vehicle/200003'}),
        *[(4, n, {}) for n in range(4, 10)])
    records = pd.DataFrame([native(4)])  # Recent pending candidate must wait until day 5.
    queue = inventory_followup_queue(schedule, inventory, records, [cohort],
        as_of='2026-09-04T23:00Z', batch_limit=5, control_count=2)
    keyed = queue.set_index('vin')
    assert int(queue.frozen_cohort_member.sum()) == 2
    assert synthetic_vehicle(99)['vin'] not in keyed.index  # Partial-only observations excluded.
    assert 'pending started' in keyed.loc[synthetic_vehicle(1)['vin'], 'selection_reason']
    assert not keyed.loc[synthetic_vehicle(1)['vin'], 'selected_for_check']
    assert keyed.loc[synthetic_vehicle(1)['vin'], 'next_check_at'] == pd.Timestamp('2026-09-05T10:00Z')
    assert 'outstanding inventory exit' in keyed.loc[synthetic_vehicle(2)['vin'], 'selection_reason']
    assert keyed.loc[synthetic_vehicle(3)['vin'], 'original_listing_id'] == '100003'
    assert keyed.loc[synthetic_vehicle(3)['vin'], 'followup_listing_id'] == '200003'
    assert keyed.loc[synthetic_vehicle(3)['vin'], 'selected_for_check']
    assert queue.selected_for_check.sum() == 5 and queue.random_control.sum() == 2
    assert queue.intervening_calendar_dates.eq(2).all()
    assert queue.loc[queue.random_control, 'selection_probability'].eq(2 / 6).all()
    assert queue.loc[queue.selection_group.eq('persistence'), 'selection_probability'].isna().all()
    reordered = inventory_followup_queue(schedule.iloc[::-1], inventory.iloc[::-1], records, [cohort],
        as_of='2026-09-04T23:00Z', batch_limit=5, control_count=2)
    pd.testing.assert_frame_equal(queue, reordered)


def test_followup_new_listing_needs_its_own_check_and_failed_attempt_does_not_refresh_native_age():
    old = synthetic_vehicle()
    schedule = synthetic_cycles((1, 2))
    inventory = synthetic_inventory((1, 1, {}), (2, 1, {
        'listing_id': '200001', 'listing_url': 'https://www.carvana.com/vehicle/200001'}))
    records = pd.DataFrame([native(1), native(2, 'Sold', parse_outcome='access_failed',
        observed_status='unresolved', listing_id='200001')])
    queue = inventory_followup_queue(schedule, inventory, records, [synthetic_cohort(old)],
        as_of='2026-09-02T23:00Z', control_count=0)
    row = queue.iloc[0]
    assert row.original_listing_id == '100001' and row.followup_listing_id == '200001'
    assert pd.isna(row.last_usable_native_at) and pd.isna(row.hours_since_usable_native)
    assert row.latest_parse_outcome == 'access_failed'
    assert not row.check_due and not row.selected_for_check
    assert row.next_check_at == pd.Timestamp('2026-09-03T10:00Z')
    later = inventory_followup_queue(schedule, inventory, records, [synthetic_cohort(old)],
        as_of='2026-09-03T23:00Z', control_count=0)
    assert later.selected_for_check.item()
    # A recent visit to the old listing neither refreshes the replacement's
    # native evidence nor bypasses the minimum VIN-level visit interval.
    old_recent = inventory_followup_queue(schedule, inventory, pd.DataFrame([native(2)]),
        [synthetic_cohort(old)], as_of='2026-09-02T23:00Z', control_count=0).iloc[0]
    assert old_recent.followup_listing_id == '200001'
    assert pd.isna(old_recent.last_usable_native_at)
    assert old_recent.last_attempt_listing_id == '100001'
    assert not old_recent.selected_for_check


def test_followup_retains_all_33_cohort_members_without_expanding_membership():
    cohort = synthetic_cohort(*[synthetic_vehicle(n) for n in range(1, 34)])
    inventory = synthetic_inventory(*[(1, n, {}) for n in range(1, 37)])
    queue = inventory_followup_queue(synthetic_cycles((1,)), inventory,
        pd.DataFrame([native(1)]), [cohort], as_of='2026-09-02T23:00Z')
    assert queue.frozen_cohort_member.sum() == 33
    assert queue.random_control.sum() == 2
    assert queue.selected_for_check.sum() == 12
    assert len(cohort['vehicles']) == 33
    assert not queue.duplicated(['retailer', 'vin']).any()


def test_followup_excludes_future_inventory_and_cohort_and_has_no_baseline_assumption():
    schedule = synthetic_cycles((1, 2))
    schedule.loc[schedule.cycle_date.eq('2026-09-02'), 'available_at'] = '2026-09-03T15:00Z'
    inventory = synthetic_inventory((1, 1, {}), (1, 2, {}), (2, 1, {'purchase_pending': True}))
    cohort = dict(synthetic_cohort(), selected_at='2026-09-03T00:00Z')
    queue = inventory_followup_queue(schedule, inventory, pd.DataFrame([native(1)]), [cohort],
        as_of='2026-09-02T23:00Z', control_count=1)
    assert queue.random_control.sum() == 1 and not queue.candidate_event.any()
    assert not queue.frozen_cohort_member.any()
    assert queue.latest_complete_date.eq('2026-09-01').all()
    assert queue.comparison_start.isna().all()


def test_latest_inventory_listing_wins_over_later_verified_obsolete_page():
    old = synthetic_vehicle()
    schedule = synthetic_cycles((1, 2))
    inventory = synthetic_inventory((1, 1, {}), (2, 1, {
        'listing_id': '200001', 'listing_url': 'https://www.carvana.com/vehicle/200001'}))
    obsolete = native(3, 'Sold', observed_vin=old['vin'], observed_listing_id=old['listing_id'],
        requested_url=old['url'], final_url=old['url'])
    queue = inventory_followup_queue(schedule, inventory, pd.DataFrame([native(1), obsolete]),
        [synthetic_cohort(old)], as_of='2026-09-04T23:00Z', control_count=0)
    row = queue.iloc[0]
    assert row.followup_listing_id == '200001'
    assert row.followup_url == 'https://www.carvana.com/vehicle/200001'
    assert row.conflicting_page_listing_id == row.last_attempt_listing_id == '100001'
    assert 'page listing conflicts' in row.selection_reason
    assert row.candidate_event and pd.isna(row.last_usable_native_at)


def test_known_sold_is_candidate_and_cannot_be_a_random_noncandidate_control():
    schedule = synthetic_cycles((1,))
    inventory = synthetic_inventory((1, 1, {}), (1, 2, {}))
    queue = inventory_followup_queue(schedule, inventory, pd.DataFrame([native(2, 'Sold')]), [],
        as_of='2026-09-04T23:00Z', batch_limit=2, control_count=1)
    sold = queue.loc[queue.vin.eq(synthetic_vehicle()['vin'])].iloc[0]
    assert sold.candidate_event and not sold.random_control
    assert 'known native Sold' in sold.selection_reason
    assert queue.loc[queue.random_control, 'vin'].tolist() == [synthetic_vehicle(2)['vin']]


def planned_vehicle(number=1, selected_at='2026-09-02T16:00:00Z', **changes):
    vehicle = synthetic_vehicle(number)
    return dict(vehicle, selected_at=selected_at, available_at=selected_at,
                plan_source=f'synthetic://plan/{number}', **changes)


@pytest.mark.parametrize('status', [None, 'Sold'])
@pytest.mark.parametrize('partial', [(), (3,)])
def test_outstanding_exit_survives_another_complete_sweep(status, partial):
    schedule = synthetic_cycles((1, 2, 3, 4), partial=partial)
    inventory = synthetic_inventory((1, 1, {}))
    records = pd.DataFrame() if status is None else pd.DataFrame([native(2, status,
        checked_at='2026-09-02T20:00Z', available_at='2026-09-02T20:00Z')])
    first = inventory_followup_queue(schedule, inventory, records, [],
        as_of='2026-09-02T23:00Z', control_count=0).iloc[0]
    later = inventory_followup_queue(schedule, inventory, records, [],
        as_of='2026-09-04T23:00Z', control_count=0).iloc[0]
    assert first.exit_episode_id == later.exit_episode_id
    assert later.first_disappearance_at == '2026-09-02T15:00:00Z'
    assert later.followup_outstanding and later.selected_for_check
    assert later.selection_group == ('new_exit' if status is None else 'persistence')
    assert later.latest_native_status == status


def test_selected_control_and_unchecked_plan_remain_visible_after_later_sweeps():
    schedule = synthetic_cycles((1, 2, 4))
    inventory = synthetic_inventory(*[(day, 1, {}) for day in (1, 2, 4)])
    plans = pd.DataFrame([planned_vehicle(1), planned_vehicle(2)])
    records = pd.DataFrame([native(2, checked_at='2026-09-02T20:00Z', available_at='2026-09-02T20:00Z')])
    queue = inventory_followup_queue(schedule, inventory, records, [], selection_plans=plans,
        as_of='2026-09-04T23:00Z', control_count=0)
    assert len(queue) == 2 and queue.previously_selected.all()
    assert queue.followup_outstanding.all() and queue.selected_for_check.all()
    assert queue.selection_group.eq('persistence').all()
    assert queue.selection_probability.isna().all()
    assert queue.set_index('vin').loc[synthetic_vehicle(2)['vin'], 'completion_reason'].startswith('Needs a matched')


def test_study_24_hour_then_seven_day_milestones_require_successful_native_checks():
    schedule = synthetic_cycles((1, 2))
    inventory = synthetic_inventory((1, 1, {}))
    baseline = native(2, 'Sold', checked_at='2026-09-02T20:00Z', available_at='2026-09-02T20:00Z')
    repeat = native(3, 'Sold', checked_at='2026-09-03T20:00Z', available_at='2026-09-03T20:00Z')
    failed = native(9, 'Sold', checked_at='2026-09-09T20:00Z', available_at='2026-09-09T20:00Z',
        parse_outcome='access_failed', observed_status='unresolved')
    mature = native(10, 'Sold', checked_at='2026-09-10T20:00Z', available_at='2026-09-11T01:00Z')
    records = pd.DataFrame([baseline, repeat, failed, mature])
    def row_at(cutoff):
        return inventory_followup_queue(schedule, inventory, records, [], as_of=cutoff, control_count=0).iloc[0]
    first = row_at('2026-09-02T23:00Z')
    assert first.next_check_at == pd.Timestamp('2026-09-03T20:00Z')
    repeated = row_at('2026-09-03T23:00Z')
    assert repeated.next_check_at == pd.Timestamp('2026-09-09T20:00Z')
    assert repeated.followup_outstanding and not repeated.check_due
    after_failure = row_at('2026-09-10T19:00Z')
    assert after_failure.followup_outstanding and not after_failure.check_due
    assert after_failure.next_check_at == pd.Timestamp('2026-09-10T20:00Z')
    unavailable_import = row_at('2026-09-10T23:00Z')
    assert unavailable_import.followup_outstanding  # Later saved interpretation is still unavailable.
    complete = row_at('2026-09-11T01:00Z')
    assert not complete.followup_outstanding and not complete.selected_for_check
    assert pd.isna(complete.next_check_at)
    assert complete.completion_reason == 'Seven-day native follow-up observed'


def test_reappearance_replacement_and_second_exit_keep_separate_episode_boundaries():
    schedule = synthetic_cycles((1, 2, 3, 4, 5), partial=(3,))
    inventory = synthetic_inventory((1, 1, {}), (3, 1, {
        'listing_id': '200001', 'listing_url': 'https://www.carvana.com/vehicle/200001'}),
        (4, 1, {'listing_id': '200001', 'listing_url': 'https://www.carvana.com/vehicle/200001'}))
    records = pd.DataFrame([native(2, 'Sold', checked_at='2026-09-02T20:00Z', available_at='2026-09-02T20:00Z')])
    before = inventory_followup_queue(schedule, inventory, records, [],
        as_of='2026-09-02T23:00Z', control_count=0).iloc[0]
    returned = inventory_followup_queue(schedule, inventory, records, [],
        as_of='2026-09-04T23:00Z', control_count=0).iloc[0]
    assert returned.exit_episode_id == before.exit_episode_id
    assert returned.followup_episode_id != before.followup_episode_id
    assert returned.episode_listing_id == '100001' and returned.followup_listing_id == '200001'
    assert returned.reappeared_at == pd.Timestamp('2026-09-03T14:30Z')
    assert pd.isna(returned.baseline_checked_at) and returned.followup_outstanding
    assert returned.followup_trigger_at == pd.Timestamp('2026-09-03T14:30Z')
    episodes = inventory_exit_episodes(schedule, inventory, as_of='2026-09-05T23:00Z')
    assert len(episodes) == 2 and episodes.exit_episode_id.nunique() == 2
    assert episodes.listing_id.tolist() == ['100001', '200001']
    again = inventory_followup_queue(schedule, inventory, records, [],
        as_of='2026-09-05T23:00Z', control_count=0).iloc[0]
    assert again.exit_episode_id != before.exit_episode_id
    assert again.first_disappearance_at == '2026-09-05T15:00:00Z'


def test_late_plan_availability_and_late_inventory_reappearance_are_cutoff_safe():
    schedule = synthetic_cycles((1, 2, 3))
    schedule.loc[2, 'available_at'] = '2026-09-04T15:00:00Z'
    inventory = synthetic_inventory((1, 1, {}), (3, 1, {}))
    plan = planned_vehicle(2)
    plan['available_at'] = '2026-09-04T12:00:00Z'
    plans = pd.DataFrame([plan])
    early = inventory_followup_queue(schedule, inventory, pd.DataFrame(), [], selection_plans=plans,
        as_of='2026-09-03T23:00Z', control_count=0)
    assert early.vin.tolist() == [synthetic_vehicle()['vin']]
    assert early.reappeared_at.isna().all()
    later = inventory_followup_queue(schedule, inventory, pd.DataFrame(), [], selection_plans=plans,
        as_of='2026-09-04T23:00Z', control_count=0)
    assert set(later.vin) == {synthetic_vehicle(n)['vin'] for n in (1, 2)}
    assert later.set_index('vin').loc[synthetic_vehicle()['vin'], 'reappeared_at'] == pd.Timestamp('2026-09-03T14:30Z')


def test_sampling_reserves_persistence_and_random_exit_slots_with_exclusive_frames():
    schedule = synthetic_cycles((1, 2))
    inventory = synthetic_inventory(*[(1, n, {}) for n in range(1, 31)],
                                    *[(2, n, {}) for n in [3, *range(21, 31)]])
    records = pd.DataFrame([native(2, 'Sold', vehicle=synthetic_vehicle(n),
        checked_at='2026-09-02T20:00Z', available_at='2026-09-02T20:00Z') for n in (1, 2)]
        + [native(1, 'Sold', vehicle=synthetic_vehicle(3))])
    plans = pd.DataFrame([planned_vehicle(1), planned_vehicle(2)])
    kwargs = dict(as_of='2026-09-04T23:00Z', selection_plans=plans, seed='synthetic-seed')
    queue = inventory_followup_queue(schedule, inventory, records, [], **kwargs)
    selected = queue.loc[queue.selected_for_check]
    assert len(selected) == 12 and selected.vin.nunique() == 12
    assert selected.selection_group.value_counts().to_dict() == {
        'priority_conflict': 1, 'persistence': 2, 'new_exit': 7, 'control': 2}
    assert queue.loc[queue.selection_group.isin(['priority_conflict', 'persistence']), 'selection_probability'].isna().all()
    assert queue.loc[queue.selection_group.eq('new_exit'), 'selection_probability'].eq(7/17).all()
    assert queue.loc[queue.selection_group.eq('control'), 'selection_probability'].eq(2/10).all()
    shuffled = inventory_followup_queue(schedule.iloc[::-1], inventory.iloc[::-1], records.iloc[::-1], [],
        **dict(kwargs, selection_plans=plans.iloc[::-1]))
    pd.testing.assert_frame_equal(queue, shuffled)
    reseeded = inventory_followup_queue(schedule, inventory, records, [], **dict(kwargs, seed='another-seed'))
    assert set(reseeded.loc[reseeded.selected_for_check, 'vin']) != set(selected.vin)


def test_new_verified_native_listing_is_not_replaced_by_older_inventory_identity():
    vehicle = synthetic_vehicle()
    schedule = synthetic_cycles((1, 2))
    inventory = synthetic_inventory((1, 1, {}), (2, 1, {}))
    new_id = '200001'
    new_url = 'https://www.carvana.com/vehicle/' + new_id
    replacement = native(3, listing_id=new_id, observed_vin=vehicle['vin'],
        observed_listing_id=new_id, requested_url=new_url, final_url=new_url)
    row = inventory_followup_queue(schedule, inventory, pd.DataFrame([native(1), replacement]), [],
        as_of='2026-09-04T23:00Z', control_count=0).iloc[0]
    assert row.followup_listing_id == new_id and row.followup_url == new_url
    assert pd.isna(row.conflicting_page_listing_id)
    assert row.baseline_checked_at == pd.Timestamp('2026-09-03T10:00Z')
    assert 'new native listing' in row.selection_reason


def test_later_inventory_conflict_reopens_completed_native_coverage():
    schedule = synthetic_cycles((1, 10))
    inventory = synthetic_inventory((1, 1, {}), (10, 1, {}))
    records = pd.DataFrame([native(1, 'Sold'), native(8, 'Sold')])
    row = inventory_followup_queue(schedule, inventory, records, [],
        as_of='2026-09-11T23:00Z', control_count=0).iloc[0]
    assert row.selection_group == 'priority_conflict'
    assert row.followup_outstanding and pd.isna(row.baseline_checked_at)
    assert row.followup_trigger_at == pd.Timestamp('2026-09-10T14:30Z')
    assert row.selected_for_check and pd.isna(row.selection_probability)


def test_unresolved_plan_identity_is_visible_but_cannot_be_selected():
    plan = planned_vehicle(2)
    plan['url'] = None
    queue = inventory_followup_queue(synthetic_cycles((1,)), synthetic_inventory((1, 1, {})),
        pd.DataFrame(), [], selection_plans=pd.DataFrame([plan]), as_of='2026-09-04T23:00Z')
    unresolved = queue.loc[queue.vin.eq(synthetic_vehicle(2)['vin'])].iloc[0]
    assert pd.notna(unresolved.deferred_reason)
    assert unresolved.followup_outstanding and not unresolved.eligible_for_selection
    assert not unresolved.selected_for_check and pd.isna(unresolved.followup_url)
    assert queue.loc[queue.vin.eq(synthetic_vehicle()['vin']), 'selected_for_check'].item()


def test_cross_source_listing_collision_defers_only_the_implicated_vins():
    inventory = synthetic_inventory(*[(1, number, {}) for number in (1, 2, 3)])
    second = synthetic_vehicle(2)
    # Each source is internally consistent, but the page assigns VIN 2 the
    # listing ID that inventory assigned to VIN 1. VIN 3 has no shared identity.
    conflicting_page = native(2, vehicle=second, listing_id='100001', observed_vin=second['vin'],
        observed_listing_id='100001', requested_url='https://www.carvana.com/vehicle/100001',
        final_url='https://www.carvana.com/vehicle/100001')
    queue = inventory_followup_queue(synthetic_cycles((1,)), inventory,
        pd.DataFrame([conflicting_page]), [], as_of='2026-09-04T23:00Z', control_count=1)
    implicated = queue.loc[queue.vin.isin([synthetic_vehicle(n)['vin'] for n in (1, 2)])]
    assert implicated.deferred_reason.str.contains('conflicting VINs').all()
    assert not implicated.eligible_for_selection.any() and not implicated.selected_for_check.any()
    independent = queue.loc[queue.vin.eq(synthetic_vehicle(3)['vin'])].iloc[0]
    assert pd.isna(independent.deferred_reason)
    assert independent.followup_listing_id == '100003'
    assert independent.eligible_for_selection and independent.selected_for_check


def test_queue_preserves_frozen_cohort_source_metadata_and_current_missing_date():
    vehicle = dict(synthetic_vehicle(), pending_group='pending_true', source_cycle_id='synthetic-1',
        source_observed_at='2026-09-01T14:30Z', source_capture_id='synthetic-capture-1-1',
        source_path='synthetic://retained-cohort-source')
    row = inventory_followup_queue(synthetic_cycles((1,)), synthetic_inventory((1, 1, {})),
        pd.DataFrame(), [synthetic_cohort(vehicle)], as_of='2026-09-04T23:00Z', control_count=0).iloc[0]
    for name in ['pending_group', 'source_cycle_id', 'source_observed_at', 'source_capture_id', 'source_path']:
        assert row[name] == vehicle[name]
    assert row.latest_complete_date == '2026-09-01'
    assert row.latest_calendar_date_at_cutoff == '2026-09-04'
    assert pd.isna(row.conflicting_page_listing_id)


def test_revisions_preserve_corrected_away_event_and_each_cutoff():
    sold = native(2, 'Sold')
    correction = dict(sold, saleStatus='Available', purchaseType='Purchasable', observed_status='available',
        available_at='2026-09-03T12:00:00Z', source='synthetic://corrected-first-sold')
    records = pd.DataFrame([native(1), sold, correction])
    schedule = synthetic_cycles((1,))
    inventory = synthetic_inventory((1, 1, {}))
    kwargs = dict(cycles=schedule, observations=inventory)
    before = native_estimate_revisions(records, [synthetic_cohort()], as_of='2026-09-02T09:59Z', **kwargs)
    early = native_estimate_revisions(records, [synthetic_cohort()], as_of='2026-09-02T23:00Z', **kwargs)
    late = native_estimate_revisions(records, [synthetic_cohort()], as_of='2026-09-04T00:00Z', **kwargs)
    assert before.empty
    assert early.provisional_event_units.item() == early.current_event_units.item() == 1
    assert late.provisional_event_units.item() == 1 and late.current_event_units.item() == 0
    assert late.unit_revision.item() == -1
    assert late.event_id.item() == early.event_id.item()
    assert 'source correction' in late.reason.item()
    assert late.provisional_available_at.item() == pd.Timestamp('2026-09-02T10:00Z')
    # Same source versions and input row order cannot create another event/vintage.
    duplicate = native_estimate_revisions(pd.concat([records.iloc[::-1], records]),
        [synthetic_cohort()], as_of='2026-09-04T00:00Z', **kwargs)
    pd.testing.assert_frame_equal(late, duplicate)


def test_revisions_preserve_original_unit_then_suppress_inventory_return():
    records = pd.DataFrame([native(1), native(2, 'Sold')])
    schedule = synthetic_cycles((1, 3))
    inventory = synthetic_inventory((1, 1, {}), (3, 1, {}))
    revisions = native_estimate_revisions(records, [synthetic_cohort()], as_of='2026-09-04T00:00Z',
        cycles=schedule, observations=inventory)
    assert revisions.provisional_event_units.item() == 1
    assert revisions.current_event_units.item() == 0
    assert revisions.inventory_reappearance_at.item() == pd.Timestamp('2026-09-03T14:30Z')


def test_synthetic_first_native_transition_excludes_initial_sold_and_controls():
    prospective, initial, control = synthetic_vehicle(), synthetic_vehicle(2), synthetic_vehicle(3, 'historical_control')
    records = pd.DataFrame([native(1), native(2, 'Sold'), native(3, 'Sold'),
        native(1, 'Sold', vehicle=initial), native(2, 'Sold', vehicle=initial),
        native(1, vehicle=control), native(2, 'Sold', vehicle=control)])
    events, summary = native_transition_events(records, [synthetic_cohort(prospective, initial, control)],
                                               as_of='2026-09-04T23:00Z')
    assert events.vin.tolist() == [prospective['vin']]
    assert events.repeat_sold_checks.tolist() == [1]
    assert events.central_eligible.all() and events.conservative_eligible.all()
    assert events.interval_start.item() == pd.Timestamp('2026-09-01T10:00Z')
    assert events.interval_end.item() == pd.Timestamp('2026-09-02T10:00Z')
    assert summary.set_index('vin').loc[initial['vin'], 'first_encountered_sold']


def test_synthetic_version_dedup_is_cutoff_safe_and_later_available_suppresses():
    repeated = native(3, 'Sold')
    correction = dict(repeated, available_at='2026-09-03T12:00Z', saleStatus='Available',
                      purchaseType='Purchasable', observed_status='available', source='synthetic://correction')
    records = pd.DataFrame([native(1), native(2, 'Sold'), repeated,
                            dict(repeated, source='synthetic://duplicate-import'), correction])
    early, _ = native_transition_events(records, [synthetic_cohort()], as_of='2026-09-03T11:00Z')
    late, _ = native_transition_events(records, [synthetic_cohort()], as_of='2026-09-03T13:00Z')
    assert len(early) == len(late) == 1
    assert early.repeat_sold_checks.item() == 1 and early.conservative_eligible.item()
    assert late.repeat_sold_checks.item() == 0 and not late.central_eligible.item()
    assert early.event_id.item() == late.event_id.item()
    assert late.contrary_native_at.item() == pd.Timestamp('2026-09-03T10:00Z')
    visits = native_visits(records, as_of='2026-09-03T13:00Z')
    assert len(visits) == 3 and visits.iloc[-1].source == 'synthetic://correction'


def test_synthetic_conflicting_equal_version_and_invalid_native_clock_rejected():
    original = native(1)
    conflicting = dict(original, saleStatus='Sold', observed_status='sold_label')
    with pytest.raises(ValueError, match='Conflicting versions'):
        native_visits(pd.DataFrame([original, conflicting]), as_of='2026-09-04T00:00Z')
    invalid = dict(original, available_at='2026-09-01T09:00Z')
    with pytest.raises(ValueError, match='availability precedes'):
        native_visits(pd.DataFrame([invalid]), as_of='2026-09-04T00:00Z')


def test_synthetic_failed_native_check_does_not_create_transition_or_persistence():
    failed = native(2, 'Sold', parse_outcome='access_failed', observed_status='unresolved')
    events, _ = native_transition_events(pd.DataFrame([native(1), failed]), [synthetic_cohort()],
                                         as_of='2026-09-04T00:00Z')
    assert events.empty
    failed_later = dict(failed, checked_at='2026-09-04T10:00Z', available_at='2026-09-04T10:00Z')
    events, _ = native_transition_events(pd.DataFrame([native(1), native(2, 'Sold'), failed_later]),
                                         [synthetic_cohort()], as_of='2026-09-04T23:00Z')
    assert events.central_eligible.item() and not events.conservative_eligible.item()
    assert events.repeat_sold_checks.item() == 0


def test_synthetic_inventory_reappearance_waits_for_available_evidence():
    records = pd.DataFrame([native(1), native(2, 'Sold')])
    schedule = synthetic_cycles((3,))
    schedule.loc[0, 'available_at'] = '2026-09-04T15:00Z'
    inventory = synthetic_inventory((3, 1, {'listing_id': '200001'}))
    early, _ = native_transition_events(records, [synthetic_cohort()], as_of='2026-09-03T23:00Z',
                                         cycles=schedule, observations=inventory)
    late, _ = native_transition_events(records, [synthetic_cohort()], as_of='2026-09-04T23:00Z',
                                        cycles=schedule, observations=inventory)
    assert early.central_eligible.item() and pd.isna(early.inventory_reappearance_at.item())
    assert not late.central_eligible.item()
    assert late.inventory_reappearance_at.item() == pd.Timestamp('2026-09-03T14:30Z')


@pytest.mark.parametrize('start,end,expected', [
    ('2026-03-07T12:00:00-05:00', '2026-03-09T12:00:00-04:00',
     {'2026-03-07': 12 / 47, '2026-03-08': 23 / 47, '2026-03-09': 12 / 47}),
    ('2026-10-31T12:00:00-04:00', '2026-11-02T12:00:00-05:00',
     {'2026-10-31': 12 / 49, '2026-11-01': 25 / 49, '2026-11-02': 12 / 49}),
    ('2026-09-01T23:59:59-04:00', '2026-09-02T00:00:03-04:00',
     {'2026-09-01': 0.25, '2026-09-02': 0.75}),
])
def test_synthetic_interval_allocation_uses_seconds_and_dst(start, end, expected):
    ledger = pd.DataFrame([dict(event_id='synthetic-event', interval_start=start, interval_end=end,
                               evidence_available_at=end, estimated_units=1.0)])
    allocation = allocate_intervals(ledger, as_of='2026-12-01T00:00Z')
    assert allocation.set_index('date').allocated_units.to_dict() == pytest.approx(expected)
    assert allocation.allocated_units.sum() == pytest.approx(1)
    assert not allocation.date.duplicated().any()
    observed = allocate_intervals(ledger, as_of='2026-12-01T00:00Z', allocation='first_observed')
    assert observed.date.tolist() == [list(expected)[-1]]
    assert observed.allocated_units.tolist() == [1.0]


def test_synthetic_allocation_excludes_later_evidence_and_rejects_invalid_clocks():
    event = dict(event_id='synthetic-event', interval_start='2026-09-01T10:00Z',
                 interval_end='2026-09-02T10:00Z', evidence_available_at='2026-09-03T10:00Z',
                 estimated_units=1.0)
    assert allocate_intervals(pd.DataFrame([event]), as_of='2026-09-02T23:00Z').empty
    result = allocate_intervals(pd.DataFrame([event]), as_of='2026-09-03T10:00Z')
    assert result.allocated_units.sum() == pytest.approx(1)
    with pytest.raises(ValueError, match='Invalid interval'):
        allocate_intervals(pd.DataFrame([dict(event, interval_end=event['interval_start'])]),
                           as_of='2026-09-04T00:00Z')
    with pytest.raises(ValueError, match='availability clock'):
        allocate_intervals(pd.DataFrame([dict(event, evidence_available_at=event['interval_start'])]),
                           as_of='2026-09-04T00:00Z')


def test_synthetic_inventory_gap_preserves_reconciled_flow_and_vin_listing_identity():
    schedule = synthetic_cycles((1, 2, 4), partial=(2,))
    inventory = synthetic_inventory((1, 1, {}), (1, 2, {}), (2, 99, {}),
        (4, 1, {'listing_id': '200001', 'asking_price_usd': 9500, 'purchase_pending': True}), (4, 3, {}))
    original = inventory.copy(deep=True)
    flows = inventory_flows(schedule, inventory, as_of='2026-09-04T23:00Z')
    row = flows.iloc[0]
    assert len(flows) == 1
    assert (row.beginning_vins, row.additions, row.departures, row.ending_vins) == (2, 1, 1, 2)
    assert row.flow_residual == 0 and row.listing_id_changes == 1 and row.pending_started == 1
    assert row.matched_mean_price_change_usd == -500 and row.repriced_vins == 1
    assert row.intervening_calendar_dates == 2 and row.hours_between_sweep_starts == 72
    assert not row.complete_consecutive_dates
    pd.testing.assert_frame_equal(inventory, original)


@pytest.mark.parametrize('problem', ['scope', 'duplicate_vin', 'listing_rebound', 'missing_vin', 'clock'])
def test_synthetic_inventory_invalid_scope_or_identity_fails_closed(problem):
    schedule = synthetic_cycles((1, 2))
    inventory = synthetic_inventory((1, 1, {}), (2, 1, {}))
    if problem == 'scope': schedule.loc[1, 'scope_id'] = 'synthetic-different-scope'
    if problem == 'duplicate_vin': inventory = pd.concat([inventory, inventory.iloc[[0]]], ignore_index=True)
    if problem == 'listing_rebound': inventory.loc[1, 'vin'] = synthetic_vehicle(2)['vin']
    if problem == 'missing_vin': inventory.loc[1, 'vin'] = None
    if problem == 'clock': inventory.loc[1, 'observed_at_utc'] = '2026-09-02T13:00Z'
    with pytest.raises(ValueError):
        inventory_flows(schedule, inventory, as_of='2026-09-02T23:00Z')


def test_synthetic_disappearance_needs_three_complete_dates_and_gaps_reset():
    inventory = synthetic_inventory((1, 1, {'purchase_pending': True}))
    ordinary, _ = disappearance_events(synthetic_cycles(), inventory, as_of='2026-09-04T23:00Z')
    assert ordinary.eligible.item()
    assert ordinary.first_absent_date.item() == '2026-09-02'
    assert ordinary.detected_date.item() == '2026-09-04'
    assert pd.Timestamp(ordinary.event_available_at.item()) == pd.Timestamp('2026-09-04T15:01Z')
    gapped = synthetic_cycles((1, 2, 4, 5, 6, 7, 8), partial=(5,))
    early, _ = disappearance_events(gapped, inventory, as_of='2026-09-07T23:00Z')
    assert early.empty
    late, calendar = disappearance_events(gapped, inventory, as_of='2026-09-08T23:00Z')
    assert late.detected_date.item() == '2026-09-08'
    assert late.timing_uncertain.item() and late.eligible.item()
    assert late.followup_state.item() == 'still_absent'
    current_gap, _ = disappearance_events(gapped, inventory, as_of='2026-09-09T23:00Z')
    assert current_gap.followup_state.item() == 'coverage_gap'
    assert not current_gap.eligible.item()
    continued_schedule = synthetic_cycles((1, 2, 4, 5, 6, 7, 8, 9, 10), partial=(5, 10))
    fourth_absent, _ = disappearance_events(continued_schedule, inventory, as_of='2026-09-09T23:00Z')
    assert fourth_absent.timing_uncertain.item() and fourth_absent.eligible.item()
    assert fourth_absent.candidate_id.item() == late.candidate_id.item()
    current_partial, _ = disappearance_events(continued_schedule, inventory, as_of='2026-09-10T23:00Z')
    assert not current_partial.eligible.item()
    assert calendar.set_index('cycle_date').loc[['2026-09-03', '2026-09-05'], 'new_candidates'].isna().all()


def test_synthetic_combined_same_vin_dedup_never_rescales_to_cohort_size():
    one, two, never_seen = synthetic_vehicle(), synthetic_vehicle(2), synthetic_vehicle(3)
    cohort = synthetic_cohort(one, two, never_seen)
    records = pd.DataFrame([native(1), native(2, 'Sold'), native(3, 'Sold'), native(4, 'Sold'),
                            native(1, vehicle=two)])
    inventory = synthetic_inventory((1, 1, {'purchase_pending': True}),
                                     (1, 2, {'purchase_pending': True}))
    absences, _ = disappearance_events(synthetic_cycles(), inventory, as_of='2026-09-04T23:00Z')
    events, _ = native_transition_events(records, [cohort], as_of='2026-09-04T23:00Z')
    ledger = cohort_estimates(events, absences, records, [cohort], as_of='2026-09-04T23:00Z')
    totals = ledger.groupby('method').estimated_units.sum().to_dict()
    assert totals == {'conservative': 1, 'central_native': 1, 'combined_pending_3d': 2, 'expansive_all_3d': 2}
    assert not ledger.duplicated(['method', 'retailer', 'vin']).any()
    assert ledger.estimated_units.eq(1).all() and ledger.prospective_vins.eq(3).all()
    assert ledger.initially_non_sold_vins.eq(2).all()  # Never observed is not initially non-Sold.
    assert events.repeat_sold_checks.item() == 2
    assert set(ledger.loc[ledger.vin.eq(one['vin']), 'basis']) == {'native Sold transition'}
    allocated = allocate_intervals(ledger, as_of='2026-09-04T23:00Z')
    assert allocated.groupby('method').allocated_units.sum().to_dict() == pytest.approx(totals)


def test_synthetic_later_available_vetoes_absence_and_initial_sold_stays_excluded():
    one, two, initial = synthetic_vehicle(), synthetic_vehicle(2), synthetic_vehicle(3)
    cohort = synthetic_cohort(one, two, initial)
    records = pd.DataFrame([native(1), native(1, vehicle=two), native(1, 'Sold', vehicle=initial),
        native(3, vehicle=two, available_at='2026-09-05T10:00Z')])
    inventory = synthetic_inventory(*[(1, n, {'purchase_pending': True}) for n in (1, 2, 3)])
    absences, _ = disappearance_events(synthetic_cycles(), inventory, as_of='2026-09-04T23:00Z')
    events, _ = native_transition_events(records, [cohort], as_of='2026-09-04T23:00Z')
    early = cohort_estimates(events, absences, records, [cohort], as_of='2026-09-04T23:00Z')
    late = cohort_estimates(events, absences, records, [cohort], as_of='2026-09-05T23:00Z')
    assert early.groupby('method').estimated_units.sum().to_dict() == {'combined_pending_3d': 2, 'expansive_all_3d': 2}
    assert late.groupby('method').estimated_units.sum().to_dict() == {'combined_pending_3d': 1, 'expansive_all_3d': 1}
    assert set(late.vin) == {one['vin']}


def test_synthetic_combined_rejects_non_three_day_absence_input():
    records = pd.DataFrame([native(1)])
    inventory = synthetic_inventory((1, 1, {'purchase_pending': True}))
    absences, _ = disappearance_events(synthetic_cycles((1, 2, 3)), inventory,
                                       as_of='2026-09-03T23:00Z', absence_days=2)
    events, _ = native_transition_events(records, [synthetic_cohort()], as_of='2026-09-03T23:00Z')
    with pytest.raises(ValueError, match='3-day absence'):
        cohort_estimates(events, absences, records, [synthetic_cohort()], as_of='2026-09-03T23:00Z')


@pytest.mark.parametrize('source_kind', ['native', 'absence'])
def test_synthetic_future_vintage_ledger_input_cannot_leak_backward(source_kind):
    inventory = synthetic_inventory((1, 1, {'purchase_pending': True}))
    records = pd.DataFrame([native(1), native(2, 'Sold')]) if source_kind == 'native' else pd.DataFrame([native(1)])
    # First build a valid current vintage whose latest supporting evidence is
    # later than the requested replay cutoff. Pass it deliberately to the ledger.
    if source_kind == 'native':
        records = pd.concat([records, pd.DataFrame([native(5, 'Sold')])], ignore_index=True)
    current_events, _ = native_transition_events(records, [synthetic_cohort()], as_of='2026-09-05T23:00Z')
    current_absences, _ = disappearance_events(synthetic_cycles((1, 2, 3, 4, 5)), inventory,
                                               as_of='2026-09-05T23:00Z')
    if source_kind == 'native':
        current_absences = current_absences.iloc[:0]
    old_from_new = cohort_estimates(current_events, current_absences, records,
                                    [synthetic_cohort()], as_of='2026-09-04T23:00Z')
    assert old_from_new.empty
    assert {'method', 'estimated_units', 'evidence_available_at'} <= set(old_from_new.columns)
    current = cohort_estimates(current_events, current_absences, records,
                               [synthetic_cohort()], as_of='2026-09-05T23:00Z')
    assert not current.empty and current.estimated_units.eq(1).all()
    # An actual replay recomputes the older vintage and can admit its known event.
    old_events, _ = native_transition_events(records, [synthetic_cohort()], as_of='2026-09-04T23:00Z')
    old_absences, _ = disappearance_events(synthetic_cycles((1, 2, 3, 4, 5)), inventory,
                                           as_of='2026-09-04T23:00Z')
    if source_kind == 'native':
        old_absences = old_absences.iloc[:0]
    old_replayed = cohort_estimates(old_events, old_absences, records,
                                    [synthetic_cohort()], as_of='2026-09-04T23:00Z')
    assert old_replayed.estimated_units.max() == 1


@pytest.mark.parametrize('source_kind', ['native', 'absence'])
def test_synthetic_not_yet_detected_event_input_is_excluded_at_early_cutoff(source_kind):
    records = pd.DataFrame([native(1), native(3, 'Sold')]) if source_kind == 'native' else pd.DataFrame([native(1)])
    events, _ = native_transition_events(records, [synthetic_cohort()], as_of='2026-09-04T23:00Z')
    absences, _ = disappearance_events(synthetic_cycles(),
        synthetic_inventory((1, 1, {'purchase_pending': True})), as_of='2026-09-04T23:00Z')
    if source_kind == 'native':
        absences = absences.iloc[:0]
    ledger = cohort_estimates(events, absences, records, [synthetic_cohort()], as_of='2026-09-02T23:00Z')
    assert ledger.empty


def test_synthetic_unobserved_or_only_failed_cohort_member_is_not_initially_non_sold():
    observed, unobserved, failed, initially_sold = [synthetic_vehicle(n) for n in (1, 2, 3, 4)]
    cohort = synthetic_cohort(observed, unobserved, failed, initially_sold)
    records = pd.DataFrame([native(1), native(2, 'Sold'),
        native(1, vehicle=failed, parse_outcome='access_failed', observed_status='unresolved', saleStatus=None),
        native(1, 'Sold', vehicle=initially_sold)])
    events, _ = native_transition_events(records, [cohort], as_of='2026-09-04T23:00Z')
    absences, _ = disappearance_events(synthetic_cycles(),
        synthetic_inventory(*[(1, n, {'purchase_pending': True}) for n in (1, 2, 3, 4)]),
        as_of='2026-09-04T23:00Z')
    ledger = cohort_estimates(events, absences, records, [cohort], as_of='2026-09-04T23:00Z')
    assert ledger.prospective_vins.eq(4).all()
    assert ledger.initially_non_sold_vins.eq(1).all()
    assert set(ledger.vin) == {observed['vin']}


def test_synthetic_native_listing_rebound_conflict_is_cutoff_aware():
    records = pd.DataFrame([native(1), native(2, 'Sold'),
        native(3, vehicle=synthetic_vehicle(2), listing_id=synthetic_vehicle()['listing_id'],
               available_at='2026-09-04T10:00Z')])
    early = native_visits(records, as_of='2026-09-03T23:00Z')
    assert len(early) == 2
    with pytest.raises(ValueError, match='conflicting VIN'):
        native_visits(records, as_of='2026-09-04T23:00Z')
    with pytest.raises(ValueError, match='conflicting VIN'):
        native_transition_events(records, [synthetic_cohort()], as_of='2026-09-04T23:00Z')


def test_synthetic_simultaneous_old_sold_new_available_is_not_eligible():
    records = pd.DataFrame([native(1), native(2, 'Sold'),
        native(2, listing_id='200001', source='synthetic://same-time-new-listing'), native(3, 'Sold')])
    events, _ = native_transition_events(records, [synthetic_cohort()], as_of='2026-09-04T23:00Z')
    assert len(events) == 1  # Preserve the ambiguous observed event for review.
    assert not events.central_eligible.item() and not events.conservative_eligible.item()
    assert events.contrary_native_at.item() == events.interval_end.item()
    ledger = cohort_estimates(events, pd.DataFrame(), records, [synthetic_cohort()], as_of='2026-09-04T23:00Z')
    assert ledger.estimated_units.eq(0).all()


def test_synthetic_seven_day_absence_requires_its_own_complete_streak():
    inventory = synthetic_inventory((1, 1, {'purchase_pending': True}))
    schedule = synthetic_cycles(range(1, 9))
    before, _ = disappearance_events(schedule, inventory, as_of='2026-09-07T23:00Z', absence_days=7)
    assert before.empty
    seven, _ = disappearance_events(schedule, inventory, as_of='2026-09-08T23:00Z', absence_days=7)
    three, _ = disappearance_events(schedule, inventory, as_of='2026-09-08T23:00Z', absence_days=3)
    assert seven.eligible.item() and seven.detected_date.item() == '2026-09-08'
    assert seven.first_absent_date.item() == three.first_absent_date.item() == '2026-09-02'
    assert seven.interval_end.item() == three.interval_end.item()
    assert pd.Timestamp(seven.event_available_at.item()) > pd.Timestamp(three.event_available_at.item())
    assert seven.candidate_id.item() != three.candidate_id.item()
    # A missing fifth date forces a fresh seven-date streak on dates 6 through 12.
    gapped = synthetic_cycles([1, 2, 3, 4, *range(6, 13)])
    too_early, _ = disappearance_events(gapped, inventory, as_of='2026-09-11T23:00Z', absence_days=7)
    assert too_early.empty
    recovered, _ = disappearance_events(gapped, inventory, as_of='2026-09-12T23:00Z', absence_days=7)
    assert recovered.detected_date.item() == '2026-09-12'
    assert recovered.timing_uncertain.item() and recovered.eligible.item()
    current_gap, _ = disappearance_events(gapped, inventory, as_of='2026-09-13T23:00Z', absence_days=7)
    assert not current_gap.eligible.item()
    continued_schedule = synthetic_cycles([1, 2, 3, 4, *range(6, 15)], partial=(14,))
    eighth_absent, _ = disappearance_events(continued_schedule, inventory, as_of='2026-09-13T23:00Z', absence_days=7)
    assert eighth_absent.timing_uncertain.item() and eighth_absent.eligible.item()
    assert eighth_absent.candidate_id.item() == recovered.candidate_id.item()
    current_partial, _ = disappearance_events(continued_schedule, inventory, as_of='2026-09-14T23:00Z', absence_days=7)
    assert not current_partial.eligible.item()


def test_followup_queue_finishes_quickly_at_catalog_scale():
    n_keep, n_exit, days = 49900, 100, 4
    vins = [synthetic_vehicle(i + 1) for i in range(n_keep + n_exit)]
    schedule = synthetic_cycles(range(1, days + 1))
    rows = []
    for day in range(1, days + 1):
        start, stop = (0, n_keep) if day > 1 else (0, n_keep + n_exit)
        for number in range(start, stop):
            vehicle = vins[number]
            rows.append(dict.fromkeys(OBSERVATION_COLUMNS) | dict(
                cycle_id=f'synthetic-{day}', retailer='carvana', vin=vehicle['vin'],
                listing_id=vehicle['listing_id'], capture_id=f'synthetic-capture-{day}-{number}',
                run_id=f'synthetic-run-{day}', observed_at_utc=f'2026-09-{day:02}T14:30:00Z',
                source_url=f'synthetic://inventory/{day}', source_path=f'synthetic://raw/{day}',
                listing_url=vehicle['url'], asking_price_usd=10000, purchase_pending=False,
                vehicle_lock_type=0))
    inventory = pd.DataFrame(rows)
    started = time.perf_counter()
    queue = inventory_followup_queue(schedule, inventory, pd.DataFrame(), [],
                                     as_of='2026-09-04T23:00:00Z', control_count=2)
    elapsed = time.perf_counter() - started
    assert elapsed < 90
    assert queue.selection_group.eq('new_exit').any()
    assert queue.random_control.sum() == 2
    assert int(queue.control_frame_vins.iloc[0]) == n_keep
    assert len(queue) < 400


def test_sampled_exit_estimate_weights_wilson_and_zero_resolved_day():
    def episode(number, date='2026-09-04'):
        vehicle = synthetic_vehicle(number)
        return dict(retailer='carvana', vin=vehicle['vin'], listing_id=vehicle['listing_id'],
                    detected_date=date, interval_end='2026-09-02T15:00:00Z',
                    first_disappearance_at='2026-09-02T15:00:00Z')

    absences = pd.DataFrame([episode(i) for i in range(1, 11)] + [episode(11, '2026-09-05')])
    selected = [
        dict(synthetic_vehicle(1), selection_group='new_exit', selected_for_check=True,
             selection_probability=0.2),
        dict(synthetic_vehicle(2), selection_group='new_exit', selected_for_check=True,
             selection_probability=0.2),
        dict(synthetic_vehicle(11), selection_group='new_exit', selected_for_check=True,
             selection_probability=0.5)]
    queue = pd.DataFrame(selected)
    records = pd.DataFrame([
        native(3, 'Sold', vehicle=synthetic_vehicle(1), checked_at='2026-09-03T10:00:00Z',
               available_at='2026-09-03T10:00:00Z'),
        native(3, 'Available', vehicle=synthetic_vehicle(2), checked_at='2026-09-03T10:00:00Z',
               available_at='2026-09-03T10:00:00Z')])
    estimate = sampled_exit_estimate(absences, queue, records, as_of='2026-09-05T23:00:00Z')
    by_date = estimate.set_index('detection_date')
    first = by_date.loc['2026-09-04']
    assert first.exits == 10 and first.checked == 2 and first.sold == 1 and first.available == 1
    assert first.unresolved == 0
    assert first.sold_share == pytest.approx(0.5)
    low, high = wilson_resolved_interval(5.0, 10.0)
    assert first.sold_share_wilson95_low == pytest.approx(low)
    assert first.sold_share_wilson95_high == pytest.approx(high)
    assert first.sold_share_full_sample_low == pytest.approx(0.5)
    assert first.sold_share_full_sample_high == pytest.approx(0.5)
    assert first.estimated_sold_exits == pytest.approx(5.0)
    assert first.estimated_sold_exits_low == pytest.approx(10 * low)
    assert first.estimated_sold_exits_high == pytest.approx(10 * high)
    empty = by_date.loc['2026-09-05']
    assert empty.exits == 1 and empty.checked == 1 and empty.unresolved == 1
    assert pd.isna(empty.sold_share) and pd.isna(empty.estimated_sold_exits)
    assert empty.sold_share_full_sample_low == 0 and empty.sold_share_full_sample_high == 1
    pooled = by_date.loc['pooled']
    assert pooled.exits == 11 and pooled.checked == 3 and pooled.sold == 1
    z = 1.959963984540054
    fraction, resolved = 0.5, 10.0
    center = (fraction + z * z / (2 * resolved)) / (1 + z * z / resolved)
    half = z * ((fraction * (1 - fraction) / resolved + z * z / (4 * resolved ** 2)) ** 0.5) / (
        1 + z * z / resolved)
    assert low == pytest.approx(max(0.0, center - half))
    assert high == pytest.approx(min(1.0, center + half))
    assert 'not reported transactions' in first.interpretation
