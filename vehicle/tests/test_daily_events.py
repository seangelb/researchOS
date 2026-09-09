"""Offline VIN event examples; no retained data or database writes."""
import pandas as pd
import pytest

from vehicle_tracker.events import daily_counts, vin_events
from vehicle_tracker.history import OBSERVATION_COLUMNS


def cycles(days=(1, 2, 3, 4), partial=()):
    return pd.DataFrame([dict(cycle_id=f'c{day}', cycle_date=f'2026-09-{day:02}',
        timezone='America/New_York', window_start=f'2026-09-{day:02}T14:00:00Z',
        window_end=f'2026-09-{day:02}T15:00:00Z', scope_id='national',
        coverage_complete=day not in partial, coverage_reason='fixture',
        available_at=f'2026-09-{day:02}T15:01:00Z') for day in days])


def observations(*specs):
    rows = []
    for spec in specs:
        day, vin, listing, *extra = spec
        row = dict.fromkeys(OBSERVATION_COLUMNS)
        row.update(cycle_id=f'c{day}', retailer='carvana', vin=vin, listing_id=listing,
            capture_id=f'capture{day}', run_id=f'run{day}',
            observed_at_utc=f'2026-09-{day:02}T14:30:00Z',
            source_url=f'https://example.test/source/{day}', listing_url=f'https://example.test/{listing}',
            asking_price_usd=10000, purchase_pending=False, vehicle_lock_type=0)
        if extra:
            row.update(extra[0])
        rows.append(row)
    return pd.DataFrame(rows, columns=[*OBSERVATION_COLUMNS, 'cycle_id'])


def test_same_vin_new_listing_is_relisted_with_references():
    result = vin_events(cycles((1, 2)), observations((1, 'V1', 'L1'), (2, 'V1', 'L2')))
    assert result.event_type.tolist() == ['first_observed', 'relisted']
    row = result.iloc[1]
    assert row.previous_listing_id == 'L1' and row.listing_id == 'L2'
    assert row.previous_capture_id == 'capture1' and row.capture_id == 'capture2'
    assert row.previous_source_url.endswith('/1') and row.source_url.endswith('/2')
    assert result.left_censored.tolist() == [True, False]
    assert result.estimated_sales.isna().all() and result.rule_version.notna().all()


def test_reappearance_and_relisting_do_not_add_another_vehicle():
    result = vin_events(cycles(), observations((1, 'V1', 'L1'), (3, 'V1', 'L1'), (4, 'V1', 'L2')))
    assert result.event_type.tolist() == ['first_observed', 'first_absence', 'reappeared', 'relisted']
    assert result.loc[2, 'absence_streak'] == 0
    assert result.first_observed_cycle_id.nunique() == 1
    counts = daily_counts(cycles(), result)
    assert counts.first_observed.tolist() == [1, 0, 0, 0]
    assert counts.reappeared.tolist() == [0, 0, 1, 0]


def test_thresholds_have_current_availability_and_no_backdating():
    source = observations((1, 'V1', 'L1'))
    strict = vin_events(cycles(), source, absence_days=3)
    faster = vin_events(cycles(), source, absence_days=2)
    assert strict.event_type.tolist() == ['first_observed', 'first_absence', 'continued_absence', 'persistent_absence']
    assert faster.loc[faster.event_type.eq('persistent_absence'), 'cycle_id'].tolist() == ['c3']
    row = strict.iloc[-1]
    assert row.cycle_id == 'c4' and row.absence_streak == 3
    assert pd.Timestamp(row.available_at) == pd.Timestamp('2026-09-04T15:01:00Z')
    prefix = vin_events(cycles((1, 2, 3)), source)
    pd.testing.assert_frame_equal(prefix, strict.iloc[:3].reset_index(drop=True))


def test_gaps_and_partial_days_break_absence_streak_and_expose_uncertainty():
    schedule = cycles((1, 2, 4, 5, 6, 7), partial=(5,))
    result = vin_events(schedule, observations((1, 'V1', 'L1')), absence_days=2)
    assert result.absence_streak.tolist() == [0, 1, 1, 0, 1, 2]
    assert result.loc[result.event_type.eq('persistent_absence'), 'cycle_id'].tolist() == ['c7']
    assert result.loc[result.cycle_id.eq('c5'), 'event_type'].item() == 'absence_unassessable'
    assert result.loc[result.cycle_id.eq('c4'), 'timing_uncertain'].item()
    assert result.loc[result.cycle_id.eq('c6'), 'timing_uncertain'].item()
    counts = daily_counts(schedule, result)
    assert pd.isna(counts.loc[counts.cycle_id.eq('c5'), 'first_absence'].item())
    assert counts.loc[counts.cycle_id.eq('c4'), 'skipped_days_before'].item() == 1


def test_partial_presence_is_visible_and_resets_absence():
    schedule = cycles((1, 2, 3), partial=(2,))
    result = vin_events(schedule, observations((1, 'V1', 'L1'), (2, 'V1', 'L1')), absence_days=2)
    assert result.observed_in_cycle.tolist() == [True, True, False]
    assert result.absence_streak.tolist() == [0, 0, 1]
    assert daily_counts(schedule, result).observed_vins.tolist() == [1, 1, 0]


def test_partial_missing_then_return_is_not_a_confirmed_reappearance():
    schedule = cycles((1, 2, 3), partial=(2,))
    result = vin_events(schedule, observations((1, 'V1', 'L1'), (3, 'V1', 'L1')))
    assert result.event_type.tolist() == ['first_observed', 'absence_unassessable', 'observed']
    assert not result.reappeared_after_absence.any()
    assert result.iloc[-1].timing_uncertain


def test_one_day_threshold_keeps_absence_onset_visible():
    schedule = cycles((1, 2))
    result = vin_events(schedule, observations((1, 'V1', 'L1')), absence_days=1)
    counts = daily_counts(schedule, result)
    assert result.iloc[-1].event_type == 'persistent_absence'
    assert result.iloc[-1].absence_started
    assert counts.iloc[-1].first_absence == 1 and counts.iloc[-1].persistent_absence == 1


@pytest.mark.parametrize('timestamp', [None, '2026-09-01T14:30:00',
                                      '2026-09-01T13:59:00Z', '2026-09-01T15:01:00Z'])
def test_missing_naive_or_outside_window_observations_are_rejected(timestamp):
    with pytest.raises(ValueError):
        vin_events(cycles((1,)), observations((1, 'V1', 'L1', {'observed_at_utc': timestamp})))


def test_availability_includes_late_prior_evidence():
    schedule = cycles((1, 2))
    schedule.loc[0, 'available_at'] = '2026-09-03T15:01:00Z'
    result = vin_events(schedule, observations((1, 'V1', 'L1')))
    assert pd.to_datetime(result.available_at, utc=True).eq(pd.Timestamp('2026-09-03T15:01:00Z')).all()
    counts = daily_counts(schedule, result)
    assert pd.to_datetime(counts.available_at, utc=True).eq(pd.Timestamp('2026-09-03T15:01:00Z')).all()


def test_cycle_window_matching_handles_shuffled_indexes_and_timezone_offsets():
    schedule = cycles((2, 1)).set_axis([20, 10])
    source = observations(
        (2, 'V1', 'L1', {'observed_at_utc': '2026-09-02T10:30:00-04:00'}),
        (1, 'V1', 'L1', {'observed_at_utc': '2026-09-01T16:30:00+02:00'})).set_axis([7, 7])
    original_schedule, original_source = schedule.copy(deep=True), source.copy(deep=True)
    result = vin_events(schedule, source)
    assert result.event_type.tolist() == ['first_observed', 'observed']
    assert result.cycle_id.tolist() == ['c1', 'c2']
    assert result.observed_at_utc.tolist() == ['2026-09-01T16:30:00+02:00', '2026-09-02T10:30:00-04:00']
    pd.testing.assert_frame_equal(schedule, original_schedule)
    pd.testing.assert_frame_equal(source, original_source)
    source.iloc[0, source.columns.get_loc('observed_at_utc')] = '2026-09-01T10:30:00-04:00'
    with pytest.raises(ValueError, match='outside'):
        vin_events(schedule, source)


def test_native_nulls_and_zero_prices_are_preserved():
    source = observations((1, 'V1', 'L1', {'asking_price_usd': 0, 'purchase_pending': None}),
        (2, 'V1', 'L1', {'asking_price_usd': None, 'purchase_pending': True}),
        (3, 'V1', 'L1', {'asking_price_usd': 0, 'purchase_pending': False}))
    result = vin_events(cycles((1, 2, 3)), source)
    assert result.loc[0, 'asking_price_usd'] == 0 and pd.isna(result.loc[1, 'asking_price_usd'])
    assert pd.isna(result.loc[0, 'purchase_pending'])
    assert result.asking_price_change_usd.isna().all()
    assert pd.isna(result.loc[1, 'native_status_changed']) and result.loc[2, 'native_status_changed']
    counts = daily_counts(cycles((1, 2, 3)), result)
    assert counts.pending_true.tolist() == [0, 1, 0]
    assert counts.pending_unknown.tolist() == [1, 0, 0]
    assert counts.estimated_sales.isna().all()


def test_retailers_remain_distinct_and_complete_empty_cycles_preserve_zeros():
    source = observations((1, 'V1', 'L1'), (1, 'V1', 'L1', {'retailer': 'other'}),
        (2, 'V1', 'L1', {'retailer': 'other'}))
    result = vin_events(cycles((1, 2)), source)
    assert len(result) == 4
    assert result.loc[result.cycle_id.eq('c2') & result.retailer.eq('carvana'), 'event_type'].item() == 'first_absence'
    empty = vin_events(cycles((1, 2)), observations())
    counts = daily_counts(cycles((1, 2)), empty)
    assert counts.observed_vins.tolist() == [0, 0]
    assert counts.first_absence.tolist() == [0, 0]
    assert counts.pending_unknown.tolist() == [0, 0]
    assert counts.estimated_sales.isna().all()


@pytest.mark.parametrize('source', [
    observations((1, 'V1', 'L1'), (1, 'V1', 'L2')),
    observations((1, 'V1', 'L1'), (1, 'V2', 'L1')),
    observations((1, 'V1', 'L1'), (1, 'V1', 'L1')),
    observations((1, 'V1', 'L1'), (2, 'V2', 'L1')),
    observations((1, None, 'L1')),
    observations((9, 'V1', 'L1')),
])
def test_conflicting_or_invalid_identity_keys_are_rejected(source):
    with pytest.raises(ValueError):
        vin_events(cycles((1, 2)), source)


@pytest.mark.parametrize('change', ['scope', 'duplicate_date', 'duplicate_id'])
def test_incomparable_cycles_are_rejected(change):
    schedule = cycles((1, 2))
    column = {'scope': 'scope_id', 'duplicate_date': 'cycle_date', 'duplicate_id': 'cycle_id'}[change]
    schedule.loc[1, column] = 'other' if change == 'scope' else schedule.loc[0, column]
    with pytest.raises(ValueError):
        vin_events(schedule, observations((1, 'V1', 'L1')))


@pytest.mark.parametrize('days', [0, -1, 1.5, True])
def test_invalid_threshold_rejected(days):
    with pytest.raises(ValueError):
        vin_events(cycles(), observations(), absence_days=days)



@pytest.mark.parametrize('before,after,expected', [
    ((False, 0), (True, None), True),
    ((None, 0), (None, 1), True),
    ((False, 0), (False, 0), False),
    ((False, 0), (False, None), None),
    ((None, None), (None, None), None),
])
def test_native_status_change_with_partial_information(before, after, expected):
    fields = ['purchase_pending', 'vehicle_lock_type']
    source = observations((1, 'V1', 'L1', dict(zip(fields, before))),
                          (2, 'V1', 'L1', dict(zip(fields, after))))
    original = source.copy(deep=True)
    schedule = cycles((1, 2))
    result = vin_events(schedule, source)
    actual = result.iloc[-1].native_status_changed
    if expected is None:
        assert pd.isna(actual)
    else:
        assert pd.notna(actual) and bool(actual) == expected
    counts = daily_counts(schedule, result).iloc[-1]
    assert counts.native_status_changed == int(expected is True)
    assert counts.native_status_unknown == int(expected is None)
    assert pd.isna(counts.estimated_sales)
    pd.testing.assert_frame_equal(source, original)


def test_pending_start_and_clear_count_with_unknown_lock():
    schedule = cycles((1, 2, 3))
    source = observations((1, 'V1', 'L1'),
        (2, 'V1', 'L1', {'purchase_pending': True, 'vehicle_lock_type': None}),
        (3, 'V1', 'L1'))
    result = vin_events(schedule, source)
    counts = daily_counts(schedule, result)
    assert counts.native_status_changed.tolist() == [0, 1, 1]
    assert counts.native_status_unknown.tolist() == [1, 0, 0]
    assert counts.pending_started.tolist() == [0, 1, 0]
    assert counts.pending_cleared.tolist() == [0, 0, 1]
    assert pd.isna(result.loc[1, 'vehicle_lock_type'])
    assert result.estimated_sales.isna().all()
