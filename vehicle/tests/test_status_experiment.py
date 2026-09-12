"""Synthetic prospective-study checks: selection precedes native outcomes."""

import copy
import json

import pandas as pd
import pytest

from test_sales_proxy import native, synthetic_cycles, synthetic_inventory, synthetic_vehicle
from vehicle_tracker.status_experiment import (
    arm_outcomes, fisher_greater, freeze_plan, hypothesis_tests, inventory_frame, sample_frame, score_plan,
    study_feasibility,
)


AS_OF = '2026-09-02T16:00:00Z'
PREPARED = '2026-09-02T16:05:00Z'
ARM_NUMBERS = {
    'exit_pending': 1, 'exit_nonpending': 101,
    'listed_pending': 201, 'listed_nonpending': 301,
}


def study_inventory(per_arm=1):
    specs = []
    for arm, start in ARM_NUMBERS.items():
        pending = arm.endswith('_pending')
        for number in range(start, start + per_arm):
            specs.append((1, number, {'purchase_pending': pending}))
            if arm.startswith('listed'):
                # The exposure uses BEFORE status even when pending later changes.
                specs.append((2, number, {'purchase_pending': not pending}))
    specs.append((2, 901, {}))  # Newly listed VIN is outside the BEFORE population.
    return synthetic_cycles((1, 2)), synthetic_inventory(*specs)


def frozen_study(per_arm=1):
    cycles, inventory = study_inventory(per_arm)
    frame = inventory_frame(cycles, inventory, as_of=AS_OF)
    plan = freeze_plan(frame, as_of=AS_OF, prepared_at=PREPARED,
                       seed='synthetic-study', per_arm=per_arm)
    return plan, cycles, inventory


def at(number, status, checked_at, **changes):
    return native(3, status, vehicle=synthetic_vehicle(number),
                  checked_at=checked_at, available_at=checked_at, **changes)


def score(plan, cycles, inventory, rows, as_of='2026-09-05T16:00:00Z'):
    return score_plan(plan, pd.DataFrame(rows), cycles, inventory, as_of=as_of)


def test_inventory_arms_use_before_pending_and_do_not_admit_new_listings():
    cycles, inventory = study_inventory(3)
    frame = inventory_frame(cycles, inventory, as_of=AS_OF)
    assert len(frame) == 12
    assert frame.arm.value_counts().to_dict() == dict.fromkeys(ARM_NUMBERS, 3)
    assert synthetic_vehicle(901)['vin'] not in set(frame.vin)
    for arm, number in ARM_NUMBERS.items():
        assert frame.set_index('vin').loc[synthetic_vehicle(number)['vin'], 'arm'] == arm


@pytest.mark.parametrize('problem', ['partial_before', 'partial_after', 'missing_date', 'changed_scope'])
def test_fresh_inventory_frame_rejects_incomparable_latest_pair(problem):
    cycles, inventory = study_inventory()
    if problem.startswith('partial'):
        cycles.loc[0 if problem == 'partial_before' else 1, 'coverage_complete'] = False
    elif problem == 'missing_date':
        cycles.loc[1, 'cycle_date'] = '2026-09-03'
        cycles.loc[1, ['window_start', 'window_end', 'available_at']] = [
            '2026-09-03T14:00Z', '2026-09-03T15:00Z', '2026-09-03T15:01Z']
        inventory.loc[inventory.cycle_id.eq('synthetic-2'), 'observed_at_utc'] = '2026-09-03T14:30Z'
    else:
        cycles.loc[1, 'scope_id'] = 'different-population'
    with pytest.raises(ValueError):
        inventory_frame(cycles, inventory, as_of='2026-09-03T16:00Z')


def test_unknown_pending_and_relisting_are_visible_exclusions():
    cycles, inventory = study_inventory()
    inventory['purchase_pending'] = inventory.purchase_pending.astype('object')
    inventory.loc[inventory.vin.eq(synthetic_vehicle(1)['vin']), 'purchase_pending'] = None
    replacement = inventory.cycle_id.eq('synthetic-2') & inventory.vin.eq(synthetic_vehicle(201)['vin'])
    inventory.loc[replacement, 'listing_id'] = '999201'
    inventory.loc[replacement, 'listing_url'] = 'https://www.carvana.com/vehicle/999201'
    frame = inventory_frame(cycles, inventory, as_of=AS_OF)
    selected = sample_frame(frame, per_arm=3, seed='synthetic')
    for number in [1, 201]:
        row = selected.set_index('vin').loc[synthetic_vehicle(number)['vin']]
        assert isinstance(row.exclusion_reason, str) and row.exclusion_reason.strip()
        assert not row.selected_for_check
    assert len(selected) == 4


def test_sampling_is_order_invariant_without_replacement_with_explicit_probabilities():
    cycles, inventory = study_inventory(8)
    frame = inventory_frame(cycles, inventory, as_of=AS_OF)
    original = frame.copy(deep=True)
    sampled = sample_frame(frame, per_arm=3, seed='synthetic')
    shuffled = sample_frame(frame.sample(frac=1, random_state=27), per_arm=3, seed='synthetic')
    key = ['retailer', 'vin']
    pd.testing.assert_frame_equal(sampled.sort_values(key).reset_index(drop=True),
                                  shuffled.sort_values(key).reset_index(drop=True))
    pd.testing.assert_frame_equal(frame, original)
    assert sampled.loc[sampled.selected_for_check].groupby('arm').size().to_dict() == dict.fromkeys(ARM_NUMBERS, 3)
    assert sampled.stratum_population.eq(8).all()
    assert sampled.selection_probability.eq(3 / 8).all()
    assert not sampled.duplicated(key).any()
    reseeded = sample_frame(frame, per_arm=3, seed='different-synthetic')
    assert set(sampled.loc[sampled.selected_for_check, 'vin']) != set(reseeded.loc[reseeded.selected_for_check, 'vin'])


def test_larger_study_can_use_bounded_collection_waves_without_changing_selection():
    cycles, inventory = study_inventory(30)
    frame = inventory_frame(cycles, inventory, as_of=AS_OF)
    sampled = sample_frame(frame, per_arm=25, seed='synthetic')
    assert sampled.selected_for_check.sum() == 100
    assert sampled.loc[sampled.selected_for_check].groupby('arm').size().eq(25).all()
    assert sampled.selection_probability.eq(25 / 30).all()
    with pytest.raises(ValueError):
        sample_frame(frame, per_arm=26, seed='synthetic')


def test_frozen_plan_is_json_serializable_and_future_inventory_cannot_change_selection():
    plan, cycles, inventory = frozen_study(2)
    saved = json.loads(json.dumps(plan))
    future_cycles = synthetic_cycles((3,))
    future_inventory = synthetic_inventory((3, 999, {}))
    expanded_frame = inventory_frame(pd.concat([cycles, future_cycles], ignore_index=True),
        pd.concat([inventory, future_inventory], ignore_index=True), as_of=AS_OF)
    second = freeze_plan(expanded_frame, as_of=AS_OF, prepared_at=PREPARED,
                         seed='synthetic-study', per_arm=2)
    assert second == saved
    assert len(saved['frame']) == len(saved['pages']) == 8


def test_first_matched_primary_is_not_replaced_by_later_sold_or_repeat_check():
    plan, cycles, inventory = frozen_study()
    rows = [at(1, 'Available', '2026-09-03T10:00Z'),
            at(1, 'Sold', '2026-09-04T10:00Z'),
            at(1, 'Sold', '2026-09-10T10:00Z')]
    result = score(plan, cycles, inventory, rows, as_of='2026-09-12T10:00Z')
    row = result.set_index('vin').loc[synthetic_vehicle(1)['vin']]
    assert row.primary_status == 'Available' and row.primary_outcome == 'matched'
    assert pd.Timestamp(row.primary_checked_at) == pd.Timestamp('2026-09-03T10:00Z')
    assert row.repeat_status == 'Sold'
    assert pd.Timestamp(row.first_sold_at) == pd.Timestamp('2026-09-04T10:00Z')
    assert pd.Timestamp(row.last_available_before_sold_at) == pd.Timestamp('2026-09-03T10:00Z')
    assert len(result) == 4 and not result.duplicated(['retailer', 'vin']).any()


def test_native_unavailable_is_a_retained_unresolved_endpoint_not_a_negative_or_later_sold():
    plan, cycles, inventory = frozen_study()
    rows = [at(1, 'Unavailable', '2026-09-03T10:00Z', purchaseType='NotPurchasable', observed_status='unavailable'),
            at(1, 'Sold', '2026-09-04T10:00Z'),
            *[at(number, 'Available', '2026-09-03T11:00Z') for number in [101, 201, 301]]]
    outcomes = score(plan, cycles, inventory, rows)
    row = outcomes.set_index('vin').loc[synthetic_vehicle(1)['vin']]
    assert row.primary_native_status == 'Unavailable' and pd.isna(row.primary_status)
    assert row.primary_outcome == 'unresolved status'
    assert pd.Timestamp(row.primary_checked_at) == pd.Timestamp('2026-09-03T10:00Z')
    assert pd.Timestamp(row.first_sold_at) == pd.Timestamp('2026-09-04T10:00Z')
    tests = hypothesis_tests(outcomes, plan, as_of='2026-09-05T16:00Z')
    blocked = tests.loc[tests.unresolved.gt(0)]
    assert len(blocked) == 2 and blocked.p_value.isna().all()


def test_window_boundaries_and_wrong_listing_never_supply_primary_outcomes():
    plan, cycles, inventory = frozen_study()
    rows = [at(1, 'Sold', '2026-09-02T16:04:59Z'),
            at(1, 'Sold', '2026-09-04T16:05:00Z'),  # Half-open endpoint.
            at(101, 'Sold', '2026-09-03T10:00Z', listing_id='777777'),
            at(201, 'Sold', PREPARED)]
    result = score(plan, cycles, inventory, rows).set_index('vin')
    for number in [1, 101]:
        assert pd.isna(result.loc[synthetic_vehicle(number)['vin'], 'primary_status'])
        assert result.loc[synthetic_vehicle(number)['vin'], 'primary_outcome'] == 'missing'
    assert result.loc[synthetic_vehicle(201)['vin'], 'primary_status'] == 'Sold'


def test_unfinished_failed_and_missing_checks_are_not_available_negatives():
    plan, cycles, inventory = frozen_study()
    rows = [at(1, None, '2026-09-03T10:00Z', parse_outcome='blocked', observed_status='access_challenge')]
    early = score(plan, cycles, inventory, rows, as_of='2026-09-03T12:00Z')
    assert early.primary_status.isna().all()
    assert early.loc[early.vin.eq(synthetic_vehicle(101)['vin']), 'primary_outcome'].item() == 'pending'
    mature = score(plan, cycles, inventory, rows)
    assert mature.primary_status.isna().all()
    assert mature.loc[mature.vin.eq(synthetic_vehicle(1)['vin']), 'primary_outcome'].item() == 'failed'
    assert mature.loc[mature.vin.eq(synthetic_vehicle(101)['vin']), 'primary_outcome'].item() == 'missing'


def test_late_availability_and_future_checks_respect_scoring_cutoff():
    plan, cycles, inventory = frozen_study()
    late = at(1, 'Sold', '2026-09-03T10:00Z')
    late['available_at'] = '2026-09-05T10:00Z'
    future = at(101, 'Sold', '2026-09-04T10:00Z')
    early = score(plan, cycles, inventory, [late, future], as_of='2026-09-03T12:00Z')
    assert early.primary_status.isna().all()
    mature = score(plan, cycles, inventory, [late, future], as_of='2026-09-05T12:00Z')
    assert mature.loc[mature.vin.isin([synthetic_vehicle(n)['vin'] for n in [1, 101]]), 'primary_status'].eq('Sold').all()


def test_reappearance_uses_available_inventory_and_stays_separate_from_sold_label():
    plan, cycles, inventory = frozen_study()
    returned_cycle = synthetic_cycles((3,))
    returned_cycle.loc[:, 'available_at'] = '2026-09-05T10:00Z'
    all_cycles = pd.concat([cycles, returned_cycle], ignore_index=True)
    returned_rows = pd.concat([inventory, synthetic_inventory((3, 1, {}))], ignore_index=True)
    rows = [at(1, 'Sold', '2026-09-03T10:00Z')]
    early = score(plan, all_cycles, returned_rows, rows, as_of='2026-09-04T12:00Z')
    assert early.inventory_reappeared_at.isna().all()
    later = score(plan, all_cycles, returned_rows, rows).set_index('vin').loc[synthetic_vehicle(1)['vin']]
    assert later.primary_status == 'Sold'
    assert pd.Timestamp(later.inventory_reappeared_at) == pd.Timestamp('2026-09-03T14:30Z')


def test_hypothesis_tests_wait_for_closed_window_and_resolved_pair_outcomes():
    plan, cycles, inventory = frozen_study()
    rows = [at(number, 'Sold' if arm.startswith('exit') else 'Available', '2026-09-03T10:00Z')
            for arm, number in ARM_NUMBERS.items()]
    early = score(plan, cycles, inventory, rows, as_of='2026-09-03T12:00Z')
    early_test = hypothesis_tests(early, plan, as_of='2026-09-03T12:00Z')
    assert len(early_test) == 3 and early_test.p_value.isna().all()
    unresolved = score(plan, cycles, inventory, [])
    unresolved_test = hypothesis_tests(unresolved, plan, as_of='2026-09-05T16:00Z')
    assert unresolved_test.p_value.isna().all()
    resolved = score(plan, cycles, inventory, rows)
    result = hypothesis_tests(resolved, plan, as_of='2026-09-05T16:00Z')
    assert len(result) == 3 and result.p_value.notna().all()
    assert sorted(result.risk_difference.tolist()) == [0.0, 1.0, 1.0]
    assert result.p_adjusted.tolist() == [min(1.0, p * 3) for p in result.p_value]


def test_hypothesis_replay_rejects_a_table_scored_with_later_available_evidence():
    plan, cycles, inventory = frozen_study()
    rows = [at(number, 'Sold' if arm.startswith('exit') else 'Available', '2026-09-03T10:00Z')
            for arm, number in ARM_NUMBERS.items()]
    for row in rows:
        row['available_at'] = '2026-09-05T10:00Z'
    earlier = score(plan, cycles, inventory, rows, as_of='2026-09-04T18:00Z')
    assert earlier.primary_status.isna().all()
    later = score(plan, cycles, inventory, rows, as_of='2026-09-06T10:00Z')
    assert later.primary_status.notna().all()
    with pytest.raises(ValueError):
        hypothesis_tests(later, plan, as_of='2026-09-04T18:00Z')


@pytest.mark.parametrize('corruption', ['dropped_unresolved', 'duplicate_vin', 'changed_arm'])
def test_notebook_filtering_cannot_change_frozen_hypothesis_denominators(corruption):
    plan, cycles, inventory = frozen_study(2)
    rows = [at(number, 'Sold', '2026-09-03T10:00Z') for number in ARM_NUMBERS.values()]
    outcomes = score(plan, cycles, inventory, rows)
    if corruption == 'dropped_unresolved':
        outcomes = outcomes.dropna(subset=['primary_status'])
    elif corruption == 'duplicate_vin':
        outcomes = pd.concat([outcomes, outcomes.iloc[[0]]], ignore_index=True)
    else:
        outcomes.loc[outcomes.index[0], 'arm'] = 'listed_nonpending'
    with pytest.raises(ValueError):
        hypothesis_tests(outcomes, plan, as_of='2026-09-05T16:00Z')


def test_scoring_does_not_mutate_frozen_plan_or_input_evidence():
    plan, cycles, inventory = frozen_study()
    original_plan = copy.deepcopy(plan)
    original_cycles, original_inventory = cycles.copy(deep=True), inventory.copy(deep=True)
    rows = pd.DataFrame([at(1, 'Sold', '2026-09-03T10:00Z')])
    original_rows = rows.copy(deep=True)
    score_plan(plan, rows, cycles, inventory, as_of='2026-09-05T16:00Z')
    assert plan == original_plan
    pd.testing.assert_frame_equal(cycles, original_cycles)
    pd.testing.assert_frame_equal(inventory, original_inventory)
    pd.testing.assert_frame_equal(rows, original_rows)


def test_fisher_exact_matches_reference_and_handles_no_contrast():
    assert fisher_greater(6, 2, 1, 4) == pytest.approx(0.08624708624708627)
    assert fisher_greater(0, 4, 0, 4) == pytest.approx(1.0)
    assert fisher_greater(4, 0, 4, 0) == pytest.approx(1.0)


def test_arm_counts_keep_missing_and_unavailable_in_full_selected_bounds():
    plan, cycles, inventory = frozen_study(4)
    rows = [at(1, 'Sold', '2026-09-03T10:00Z'),
            at(2, 'Available', '2026-09-03T10:00Z'),
            at(3, 'Unavailable', '2026-09-03T10:00Z', purchaseType='NotPurchasable', observed_status='unavailable')]
    outcomes = score(plan, cycles, inventory, rows)
    counts = arm_outcomes(outcomes, plan, as_of='2026-09-05T16:00Z').set_index('arm')
    row = counts.loc['exit_pending']
    assert [row.selected, row.attempted, row.identity_matched, row.sold, row.available,
            row.unavailable, row.failed, row.unvisited, row.unresolved] == [4, 3, 3, 1, 1, 1, 0, 1, 2]
    assert row.sold_among_resolved == 0.5
    assert row.resolved_wilson95_low == pytest.approx(0.09453120573423074)
    assert row.resolved_wilson95_high == pytest.approx(0.9054687942657693)
    assert (row.selected_sold_lower, row.selected_sold_upper) == (0.25, 0.75)
    absent = counts.loc['listed_pending']
    assert pd.isna(absent.sold_among_resolved) and pd.isna(absent.resolved_wilson95_low)
    assert (absent.selected_sold_lower, absent.selected_sold_upper) == (0, 1)


def test_repeated_checks_never_increase_arm_denominator_or_replace_endpoint():
    plan, cycles, inventory = frozen_study()
    rows = [at(1, 'Available', '2026-09-03T09:00Z'), at(1, 'Sold', '2026-09-03T11:00Z'),
            at(1, 'Sold', '2026-09-10T10:00Z')]
    outcomes = score(plan, cycles, inventory, rows, as_of='2026-09-12T10:00Z')
    for wave, expected in [('primary', 0), ('repeat', 1)]:
        counts = arm_outcomes(outcomes, plan, as_of='2026-09-12T10:00Z', wave=wave).set_index('arm')
        assert counts.loc['exit_pending', 'selected'] == counts.loc['exit_pending', 'attempted'] == 1
        assert counts.loc['exit_pending', 'sold'] == expected


def test_failed_and_unfinished_reservations_are_attempted_vins_not_unvisited():
    plan, cycles, inventory = frozen_study(2)
    health = pd.DataFrame([dict(synthetic_vehicle(1), outcome='started_unresolved', started_at='2026-09-03T10:00Z'),
                           dict(synthetic_vehicle(101), outcome='started_unresolved', started_at='2026-09-06T10:00Z')])
    rows = pd.DataFrame([at(2, None, '2026-09-03T10:00Z', parse_outcome='access_failed', observed_status='unknown')])
    outcomes = score_plan(plan, rows, cycles, inventory, as_of='2026-09-05T16:00Z', browser_health=health)
    counts = arm_outcomes(outcomes, plan, as_of='2026-09-05T16:00Z').set_index('arm')
    row = counts.loc['exit_pending']
    assert (row.attempted, row.failed, row.started_unresolved, row.unvisited) == (2, 1, 1, 0)
    assert counts.loc['exit_nonpending', 'unvisited'] == 2  # Future reservation remains unknown.
    assert (row.selected_sold_lower, row.selected_sold_upper) == (0, 1)


@pytest.mark.parametrize('corruption', ['filtered', 'duplicate', 'cutoff'])
def test_arm_summary_rejects_changed_frozen_denominator_or_cutoff(corruption):
    plan, cycles, inventory = frozen_study()
    outcomes = score(plan, cycles, inventory, [])
    if corruption == 'filtered':
        outcomes = outcomes.iloc[1:]
    elif corruption == 'duplicate':
        outcomes = pd.concat([outcomes, outcomes.iloc[[0]]])
    with pytest.raises(ValueError):
        arm_outcomes(outcomes, plan, as_of='2026-09-06T16:00Z' if corruption == 'cutoff' else '2026-09-05T16:00Z')


def test_feasibility_preserves_existing_selection_and_completed_unavailable(tmp_path):
    plan, _, _ = frozen_study(7)
    saved = copy.deepcopy(plan)
    rows = pd.DataFrame([at(1, 'Unavailable', '2026-09-03T10:00Z', purchaseType='NotPurchasable', observed_status='unavailable')])
    result = study_feasibility(plan, rows, browser_root=tmp_path, now='2026-09-03T12:00Z')
    primary, repeat = result['table']
    assert primary['selected'] == 28 and primary['identity_matched_completed'] == 1
    assert primary['required_checks'] == 27 and primary['shortfall'] > 0
    assert repeat['required_checks'] == 28 and repeat['available_capacity'] == 24 and repeat['shortfall'] == 4
    assert not result['feasible'] and plan == saved and not list(tmp_path.iterdir())


def test_expired_window_stays_missing_with_future_repeat_capacity(tmp_path):
    plan, _, _ = frozen_study()
    result = study_feasibility(plan, pd.DataFrame(), browser_root=tmp_path, now='2026-09-05T12:00Z')
    primary, repeat = result['table']
    assert primary['window_state'] == 'closed' and primary['available_capacity'] == 0 and primary['shortfall'] == 4
    assert repeat['window_state'] == 'not started' and repeat['available_capacity'] == 4


@pytest.mark.parametrize('failed', [False, True])
def test_in_window_start_with_late_completion_is_attempted_but_has_no_endpoint(failed):
    plan, cycles, inventory = frozen_study()
    end = pd.Timestamp(plan['primary_end'])
    checked, cutoff = end+pd.Timedelta(minutes=1), end+pd.Timedelta(hours=1)
    record = at(1, None if failed else 'Sold', checked.isoformat(),
        **({'parse_outcome': 'access_failed', 'observed_status': 'unknown'} if failed else {}))
    health = pd.DataFrame([dict(synthetic_vehicle(1), outcome=record['parse_outcome'],
                               started_at=(end-pd.Timedelta(minutes=1)).isoformat())])
    outcomes = score_plan(plan, pd.DataFrame([record]), cycles, inventory, as_of=cutoff,
                          browser_health=health)
    row = outcomes.set_index('vin').loc[synthetic_vehicle(1)['vin']]
    assert row.primary_attempts == 1 and row.primary_outcome == 'late completion'
    assert row.primary_late_completions == 1 and pd.isna(row.primary_status)
    assert row.primary_failed_attempts == int(failed)
    counts = arm_outcomes(outcomes, plan, as_of=cutoff).set_index('arm').loc['exit_pending']
    assert (counts.attempted, counts.unvisited, counts.identity_matched, counts.sold) == (1, 0, 0, 0)
    assert counts.late_completion == 1 and counts.late_identity_matched == int(not failed)
    assert (counts.selected_sold_lower, counts.selected_sold_upper) == (0, 1)


def test_started_attempt_replays_before_late_completion_and_deduplicates_afterward():
    plan, cycles, inventory = frozen_study()
    end = pd.Timestamp(plan['primary_end'])
    record = at(1, 'Sold', (end+pd.Timedelta(minutes=1)).isoformat())
    health = pd.DataFrame([dict(synthetic_vehicle(1), outcome='matched',
        started_at=(end-pd.Timedelta(minutes=1)).isoformat(), checked_at=record['checked_at'],
        available_at=record['available_at'], source=record['source'])])
    for cutoff, late in [(end, 0), (end+pd.Timedelta(hours=1), 1)]:
        outcomes = score_plan(plan, pd.DataFrame([record, record]), cycles, inventory,
                              as_of=cutoff, browser_health=health)
        row = outcomes.set_index('vin').loc[synthetic_vehicle(1)['vin']]
        assert row.primary_attempts == 1 and row.primary_late_completions == late
        assert row.primary_unresolved_visits == 1-late and pd.isna(row.primary_status)


def test_multiple_completed_reservations_are_each_counted_once_by_start():
    plan, cycles, inventory = frozen_study()
    rows = [at(1, 'Available', '2026-09-03T10:01Z'), at(1, 'Sold', '2026-09-04T10:01Z')]
    health = pd.DataFrame([dict(synthetic_vehicle(1), outcome='matched',
        started_at=started, checked_at=row['checked_at'], available_at=row['available_at'])
        for started, row in zip(['2026-09-03T10:00Z', '2026-09-04T10:00Z'], rows)])
    outcomes = score_plan(plan, pd.DataFrame(rows), cycles, inventory,
                          as_of='2026-09-05T16:00Z', browser_health=health)
    row = outcomes.set_index('vin').loc[synthetic_vehicle(1)['vin']]
    assert row.primary_attempts == 2 and row.primary_status == 'Available'
    counts = arm_outcomes(outcomes, plan, as_of='2026-09-05T16:00Z').set_index('arm').loc['exit_pending']
    assert counts.attempted == counts.selected == counts.identity_matched == 1
