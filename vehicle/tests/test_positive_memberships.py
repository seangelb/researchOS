"""Non-cycle sightings have publication clocks and never become invented cycles."""
import pandas as pd
import pytest

from test_observed_history import history_inputs
from vehicle_tracker.timeline import observed_history, summarize_observed_memberships


def sample(price=19000, capture='older-dom'):
    return dict(retailer='carvana', vin='KNOWN', listing_id='old', capture_id=capture,
        observed_at_utc='2026-08-30T12:00Z', asking_price_usd=price,
        source_group_kind='dom_sample', source_group_id='dom:'+capture,
        cycle_id=None, cycle_date=None, scope_id='sample-scope', timezone='America/New_York',
        coverage_complete=False, source_query_complete=None,
        original_evidence_available_at=None, analysis_available_at='2026-09-19T12:00Z',
        available_at='2026-09-19T12:00Z')


def test_late_backfill_separates_earliest_sighting_from_first_known(history_inputs):
    days, rows = history_inputs
    _, _, cycles = observed_history(days, rows, as_of='2026-09-20T12:00Z')
    members = pd.concat([cycles, pd.DataFrame([sample()])], ignore_index=True)
    early, _, _ = summarize_observed_memberships(members, as_of='2026-09-10T12:00Z', earliest_cycle_ids=['day01'])
    later, _, retained = summarize_observed_memberships(members, as_of='2026-09-20T12:00Z', earliest_cycle_ids=['day01'])
    assert early.set_index('vin').loc['KNOWN', 'first_observed_at'] == pd.Timestamp('2026-09-01T10:30Z')
    known = later.set_index('vin').loc['KNOWN']
    assert known.first_observed_at == pd.Timestamp('2026-08-30T12:00Z')
    assert known.first_available_at == pd.Timestamp('2026-09-19T12:00Z')
    assert known.first_known_at == pd.Timestamp('2026-09-01T11:05Z')
    assert known.observed_cycles == 2 and known.retained_captures == 3
    assert retained.loc[retained.source_group_kind.eq('dom_sample'), 'cycle_id'].isna().all()


def test_noncycle_flags_and_cohort_denominators_remain_unknown():
    original = pd.DataFrame([sample()])
    history, cohorts, members = summarize_observed_memberships(original, as_of='2026-09-20T12:00Z')
    row, cohort = history.iloc[0], cohorts.iloc[0]
    assert row.observed_cycles == 0 and pd.isna(row.first_cycle_ids)
    assert pd.isna(row.known_only_from_partial_cycles) and pd.isna(row.present_in_earliest_selected_cycle)
    assert pd.isna(cohort.known_only_from_partial_cycles) and pd.isna(cohort.present_in_earliest_selected_cycle)
    assert cohort.known_only_from_partial_cycles_known_vins == cohort.present_in_earliest_selected_cycle_known_vins == 0
    assert cohort.observed_vins == 1 and members.original_evidence_available_at.isna().all()
    assert not {'estimated_sales', 'absence_streak', 'days_on_market'} & set(history)
    assert original.observed_at_utc.iloc[0] == '2026-08-30T12:00Z'


def test_mixed_selection_keeps_noncycle_vin_out_of_cycle_flag_denominators(history_inputs):
    days, rows = history_inputs
    _, _, cycles = observed_history(days, rows, as_of='2026-09-20T12:00Z')
    sighting = dict(sample(), vin='DOM-ONLY', listing_id='dom-only',
                    observed_at_utc='2026-09-01T12:00Z')
    history, cohorts, _ = summarize_observed_memberships(
        pd.concat([cycles, pd.DataFrame([sighting])], ignore_index=True),
        as_of='2026-09-20T12:00Z', earliest_cycle_ids=['day01'])
    row = history.set_index('vin').loc['DOM-ONLY']
    assert pd.isna(row.present_in_earliest_selected_cycle)
    assert pd.isna(row.known_only_from_partial_cycles)
    cohort = cohorts.set_index('first_observed_date').loc['2026-09-01']
    assert cohort.observed_vins == 3
    assert cohort.present_in_earliest_selected_cycle == 2
    assert cohort.present_in_earliest_selected_cycle_known_vins == 2
    assert cohort.known_only_from_partial_cycles_known_vins == 2


def test_distinct_physical_captures_keep_each_quote_and_unknown_availability_is_withheld():
    rows = pd.DataFrame([sample(19000), sample(18500, 'second-dom'),
                         dict(sample(18000, 'unknown-publication'), available_at=None, analysis_available_at=None)])
    history, _, members = summarize_observed_memberships(rows, as_of='2026-09-20T12:00Z')
    assert history.retained_captures.item() == 2 and history.observed_cycles.item() == 0
    assert members.asking_price_usd.tolist() == [19000, 18500]
    assert 'asking_price_usd' not in history


@pytest.mark.parametrize('change', [
    {'cycle_id': 'invented'}, {'coverage_complete': True},
    {'observed_at_utc': '2026-08-30T12:00'},
    {'analysis_available_at': '2026-09-18T12:00Z'},
])
def test_noncycle_claims_and_invalid_clocks_fail(change):
    with pytest.raises(ValueError):
        summarize_observed_memberships(pd.DataFrame([dict(sample(), **change)]), as_of='2026-09-20T12:00Z')
