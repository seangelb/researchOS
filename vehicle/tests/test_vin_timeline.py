"""Offline chronology tests: observation clocks, corrections, gaps and identity."""
import pandas as pd
import pytest

from test_checks import check
from test_daily_events import cycles, observations
from vehicle_tracker.timeline import TIMELINE_COLUMNS, vin_timeline


def timeline(days, rows, checks=None, as_of='2026-09-06T23:00Z', **identity):
    return vin_timeline(days, rows, checks, as_of=as_of,
        retailer=identity.get('retailer', 'carvana'), vin=identity.get('vin', 'V1'))


def test_absences_are_derived_without_carrying_forward_physical_fields():
    days = cycles((1, 2, 3, 4, 5))
    rows = observations((1, 'V1', 'L1', {'asking_price_usd': 0, 'purchase_pending': None}),
                        (5, 'V1', 'L2'))
    story = timeline(days, rows, as_of='2026-09-05T23:00Z')
    assert story.derived_event.tolist() == ['first_observed', 'first_absence',
        'continued_absence', 'persistent_absence', 'relisted']
    missing = story.loc[story.evidence_type.eq('derived_absence')]
    assert missing[['observed_at', 'listing_id', 'capture_id', 'asking_price_usd',
                    'purchase_pending', 'vehicle_lock_type', 'native_text']].isna().all().all()
    assert missing.last_known_listing_id.eq('L1').all()
    assert missing.window_start.notna().all() and missing.window_end.notna().all()
    assert missing.source.tolist() == ['registered cycle c2', 'registered cycle c3', 'registered cycle c4']
    assert story.iloc[0].asking_price_usd == 0 and pd.isna(story.iloc[0].purchase_pending)
    assert story.iloc[-1].listing_id == 'L2' and bool(story.iloc[-1].reappeared_after_absence)


def test_physical_checks_keep_late_old_visit_and_choose_only_known_corrections():
    original = check(native_text='Purchase in progress', observed_status='pending')
    corrected = check(check_id='corrected', available_at='2026-09-04T17:00Z',
        native_text='On Hold', observed_status='pending')
    late_old = check(check_id='late-old', checked_at='2026-09-01T16:00Z',
        available_at='2026-09-04T18:00Z', native_text='Get Started', observed_status='available')
    newer = check(check_id='newer', checked_at='2026-09-03T16:00Z',
        available_at='2026-09-03T17:00Z', native_text='Sold', observed_status='sold_label')
    future = check(check_id='future', checked_at='2026-09-05T16:00Z',
        available_at='2026-09-05T17:00Z', observed_status='unknown')
    checks = pd.DataFrame([original, corrected, late_old, newer, future])
    days, rows = cycles((1, 2, 3, 4)), observations((1, 'V1', 'L1'))
    old = timeline(days, rows, checks, as_of='2026-09-03T23:00Z')
    old_pages = old.loc[old.evidence_type.eq('page_check')]
    assert old_pages.check_id.tolist() == ['check-V1', 'newer']
    assert old_pages.correction_versions.eq(0).all()
    new = timeline(days, rows, checks, as_of='2026-09-04T23:00Z')
    pages = new.loc[new.evidence_type.eq('page_check')]
    assert pages.check_id.tolist() == ['late-old', 'corrected', 'newer']
    assert pages.correction_versions.tolist() == [0, 1, 0]
    assert pages.observed_at.is_monotonic_increasing
    assert pages.iloc[0].available_at > pages.iloc[-1].available_at
    assert pages.loc[pages.check_id.eq('corrected'), 'native_text'].item() == 'On Hold'


def test_missing_partial_and_trailing_dates_preserve_unknown_inventory():
    story = timeline(cycles((1, 3, 4), partial=(4,)), observations((1, 'V1', 'L1')))
    gaps = story.loc[story.evidence_type.eq('collection_gap')]
    assert gaps.cycle_date.tolist() == ['2026-09-02', '2026-09-05', '2026-09-06']
    assert gaps[['observed_at', 'available_at', 'window_start', 'window_end',
                 'listing_id', 'purchase_pending', 'asking_price_usd']].isna().all().all()
    assert gaps.assessed_at.eq(pd.Timestamp('2026-09-06T23:00Z')).all()
    assert gaps.derived_event.eq('no_registered_cycle').all()
    incomplete = story.loc[story.cycle_date.eq('2026-09-04')].iloc[0]
    assert incomplete.derived_event == 'absence_unassessable'
    assert incomplete.timing_uncertain and not incomplete.coverage_complete
    assert incomplete.absence_streak == 0
    after_gap = story.loc[story.cycle_date.eq('2026-09-03')].iloc[0]
    assert after_gap.timing_uncertain and after_gap.absence_streak == 1


def test_gap_calendar_uses_local_date_not_utc_date():
    story = timeline(cycles((1,)), observations((1, 'V1', 'L1')),
        as_of='2026-09-03T02:00Z')
    assert story.cycle_date.tolist() == ['2026-09-01', '2026-09-02']
    assert story.iloc[-1].evidence_type == 'collection_gap'


def test_native_wording_is_not_parsed_into_sales_and_retailers_stay_separate():
    rows = observations((1, 'V1', 'L1'), (1, 'V1', 'L1', {'retailer': 'other'}),
                        (2, 'V1', 'L2'), (2, 'V1', 'L9', {'retailer': 'other'}))
    checks = pd.DataFrame([check(observed_status='available',
        native_text='Get Started. Equipment as originally sold.'),
        check(check_id='other', retailer='other', observed_status='sold_label', native_text='Sold')])
    result = timeline(cycles((1, 2)), rows, checks, as_of='2026-09-02T23:00Z')
    assert result.retailer.eq('carvana').all()
    assert result.loc[result.evidence_type.eq('inventory_observation'), 'listing_id'].tolist() == ['L1', 'L2']
    assert result.observed_status.dropna().tolist() == ['available']
    assert 'estimated_sales' not in result and 'sale_date' not in result


def test_conflicting_listing_vin_mapping_outside_selected_vin_is_not_hidden():
    rows = observations((1, 'V1', 'L1'), (1, 'V2', 'L2'), (2, 'V3', 'L2'))
    with pytest.raises(ValueError, match='Conflicting'):
        timeline(cycles((1, 2)), rows)


def test_cycle_cutoff_and_own_observation_availability_are_distinct_from_derivation():
    days = cycles((1, 2))
    days.loc[0, 'available_at'] = '2026-09-04T17:00Z'
    rows = observations((1, 'V1', 'L1'), (2, 'V1', 'L1'))
    early = timeline(days, rows, as_of='2026-09-02T23:00Z')
    assert early.cycle_date.tolist() == ['2026-09-02']
    assert early.derived_event.tolist() == ['first_observed']
    late = timeline(days, rows, as_of='2026-09-04T23:00Z')
    second = late.loc[late.cycle_id.eq('c2')].iloc[0]
    assert second.available_at == pd.Timestamp('2026-09-02T15:01Z')
    assert second.derived_available_at == pd.Timestamp('2026-09-04T17:00Z')


def test_empty_unknown_selection_and_inputs_remain_unchanged():
    empty = timeline(pd.DataFrame(), pd.DataFrame())
    assert empty.empty and empty.columns.tolist() == TIMELINE_COLUMNS
    days, rows = cycles((1,)), observations((1, 'V1', 'L1'))
    before_days, before_rows = days.copy(deep=True), rows.copy(deep=True)
    assert timeline(days, rows, vin='NOT-OBSERVED').empty
    assert timeline(days, rows, as_of='2026-08-30T23:00Z').empty
    timeline(days, rows)
    pd.testing.assert_frame_equal(days, before_days)
    pd.testing.assert_frame_equal(rows, before_rows)


@pytest.mark.parametrize('as_of', ['2026-09-03T00:00', None])
def test_cutoff_requires_explicit_timezone(as_of):
    with pytest.raises(ValueError):
        timeline(cycles(), observations((1, 'V1', 'L1')), as_of=as_of)


def test_visible_check_must_have_matching_retained_inventory():
    checks = pd.DataFrame([check(listing='NOT-OBSERVED')])
    with pytest.raises(ValueError, match='observed retailer'):
        timeline(cycles((1, 2)), observations((1, 'V1', 'L1')), checks)
    with pytest.raises(ValueError, match='matching inventory'):
        timeline(pd.DataFrame(), pd.DataFrame(), checks)
