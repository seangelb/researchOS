"""Presence history must not erase partial sightings or manufacture listing age."""
import pandas as pd
import pytest

from vehicle_tracker.timeline import observed_history


@pytest.fixture
def history_inputs():
    days = pd.DataFrame([dict(cycle_id='day'+day, cycle_date='2026-09-'+day,
        timezone='America/New_York', window_start=f'2026-09-{day}T10:00:00Z',
        window_end=f'2026-09-{day}T11:00:00Z', available_at=f'2026-09-{day}T11:05:00Z',
        scope_id=scope, coverage_complete=complete, coverage_reason='synthetic fixture')
        for day, scope, complete in [('01', 'original', False), ('03', 'expanded', True)]])
    rows = pd.DataFrame([dict(cycle_id='day'+day, retailer='carvana', vin=vin,
        listing_id=listing, capture_id=day+'-'+listing, run_id='query-'+day,
        observed_at_utc=f'2026-09-{day}T10:30:00Z', source_path='synthetic://'+listing,
        asking_price_usd=price) for day, vin, listing, price in [
            ('01', 'KNOWN', 'old', 20000), ('01', 'PARTIAL-ONLY', 'partial', None),
            ('03', 'KNOWN', 'relisted', 19000), ('03', 'LATER', 'later', 10000)]])
    return days, rows


def test_partial_presence_survives_scope_change_relisting_and_calendar_gap(history_inputs):
    days, rows = history_inputs
    before_days, before_rows = days.copy(deep=True), rows.copy(deep=True)
    history, cohorts, members = observed_history(days, rows, as_of='2026-09-08T12:00:00Z')
    known = history.set_index('vin').loc['KNOWN']
    assert known.first_observed_at == pd.Timestamp('2026-09-01T10:30Z')
    assert known.last_observed_at == pd.Timestamp('2026-09-03T10:30Z')
    assert known.observed_span_days == 2  # Does not age through the analysis cutoff.
    assert known.listing_ids == 'old;relisted' and known.observed_scopes == 2
    assert known.first_evidence_includes_partial and not known.known_only_from_partial_cycles
    assert known.first_cycle_ids == 'day01' and known.first_capture_ids == '01-old'
    partial = history.set_index('vin').loc['PARTIAL-ONLY']
    assert partial.known_only_from_partial_cycles and partial.observed_span_days == 0
    assert history.earlier_listing_history_unknown.all()
    assert cohorts.observed_vins.tolist() == [2, 1] and len(members) == len(rows)
    assert 'asking_price_usd' not in history
    assert members.asking_price_usd.isna().sum() == 1
    pd.testing.assert_frame_equal(days, before_days)
    pd.testing.assert_frame_equal(rows, before_rows)


def test_cutoff_withholds_unavailable_earlier_sources_and_future_vins(history_inputs):
    days, rows = history_inputs
    days.loc[0, 'available_at'] = '2026-09-09T00:00Z'
    history, cohorts, members = observed_history(days, rows, as_of='2026-09-03T12:00Z')
    assert set(history.vin) == {'KNOWN', 'LATER'}
    assert history.first_observed_date.eq('2026-09-03').all()
    assert history.observed_span_days.eq(0).all()
    assert len(members) == 2 and cohorts.observed_vins.sum() == 2
    early, _, _ = observed_history(days, rows, as_of='2026-08-31T00:00Z')
    assert early.empty


def test_all_context_quotes_remain_and_retailers_are_distinct(history_inputs):
    days, rows = history_inputs
    extra = rows.iloc[[2]].assign(capture_id='other-context', asking_price_usd=19500)
    other_retailer = rows.iloc[[2]].assign(retailer='other-retailer', capture_id='other-retailer')
    rows = pd.concat([rows, extra, other_retailer], ignore_index=True)
    history, cohorts, members = observed_history(days, rows, as_of='2026-09-03T12:00Z')
    known = history.loc[history.retailer.eq('carvana') & history.vin.eq('KNOWN')].iloc[0]
    assert known.retained_captures == 3 and known.observed_cycles == 2
    quotes = members.loc[members.retailer.eq('carvana') & members.vin.eq('KNOWN')]
    assert quotes.asking_price_usd.tolist() == [20000, 19000, 19500]
    assert len(history) == 4 and cohorts.observed_vins.sum() == 4


def test_first_cohort_uses_source_clock_local_date_not_collection_label(history_inputs):
    days, rows = history_inputs
    days.loc[0, ['window_start', 'window_end']] = ['2026-09-01T00:00Z', '2026-09-01T01:00Z']
    rows.loc[rows.cycle_id.eq('day01'), 'observed_at_utc'] = '2026-09-01T00:30Z'
    history, cohorts, _ = observed_history(days, rows, as_of='2026-09-03T12:00Z')
    assert history.set_index('vin').loc['KNOWN'].first_observed_date == '2026-08-31'
    assert cohorts.first_observed_date.tolist() == ['2026-08-31', '2026-09-03']


@pytest.mark.parametrize('defect', ['duplicate_cycle', 'unknown_cycle', 'duplicate_capture',
    'conflicting_vin', 'missing_identity', 'outside_window', 'naive_observation',
    'unknown_coverage', 'reversed_window', 'missing_scope', 'mixed_timezones'])
def test_ambiguous_or_invalid_evidence_fails_without_deduplication(history_inputs, defect):
    days, rows = history_inputs
    if defect == 'duplicate_cycle': days.loc[1, 'cycle_id'] = 'day01'
    elif defect == 'unknown_cycle': rows.loc[0, 'cycle_id'] = 'not-selected'
    elif defect == 'duplicate_capture': rows = pd.concat([rows, rows.iloc[[0]]])
    elif defect == 'conflicting_vin': rows.loc[1, 'listing_id'] = 'old'
    elif defect == 'missing_identity': rows.loc[0, 'vin'] = None
    elif defect == 'outside_window': rows.loc[0, 'observed_at_utc'] = '2026-09-01T12:00Z'
    elif defect == 'naive_observation': rows.loc[0, 'observed_at_utc'] = '2026-09-01T10:30'
    elif defect == 'unknown_coverage': days['coverage_complete'] = [None, True]
    elif defect == 'reversed_window': days.loc[0, 'window_start'] = '2026-09-01T12:00Z'
    elif defect == 'missing_scope': days.loc[0, 'scope_id'] = ' '
    else: days.loc[1, 'timezone'] = 'UTC'
    with pytest.raises(ValueError):
        observed_history(days, rows, as_of='2026-09-03T12:00Z')


def test_empty_evidence_has_defined_schemas_and_no_zero_inventory(history_inputs):
    days, rows = history_inputs
    history, cohorts, members = observed_history(days, rows.iloc[:0], as_of='2026-09-03T12:00Z')
    assert history.empty and cohorts.empty and members.empty
    assert 'first_observed_at' in history and 'observed_vins' in cohorts
    with pytest.raises(ValueError, match='matching cycle'):
        observed_history(days.iloc[:0], rows, as_of='2026-09-03T12:00Z')
    with pytest.raises(ValueError, match='timezone aware'):
        observed_history(days, rows, as_of='2026-09-03')
