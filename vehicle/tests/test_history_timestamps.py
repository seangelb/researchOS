"""Valid ISO precision changes do not change inventory comparison or cutoff gates."""
import pandas as pd
import pytest

from test_daily_events import cycles, observations
from vehicle_tracker.daily import daily_tables
from vehicle_tracker.history import comparison_checks


def comparison_runs(current_start='2026-09-02T12:00:00Z'):
    return pd.DataFrame([
        dict(run_id='p1', context_json='a', query_complete=1,
             observation_start='2026-09-01T12:00:00+00:00', observation_end='2026-09-01T12:00:01Z'),
        dict(run_id='p2', context_json='b', query_complete=1,
             observation_start='2026-09-01T12:00:02.123456Z', observation_end='2026-09-01T12:00:03.123456+00:00'),
        dict(run_id='c1', context_json='a', query_complete=1,
             observation_start=current_start, observation_end='2026-09-02T12:00:01Z'),
        dict(run_id='c2', context_json='b', query_complete=1,
             observation_start='2026-09-02T12:00:02.123456+00:00', observation_end='2026-09-02T12:00:03.123456Z'),
    ])


@pytest.mark.parametrize('current_start, expected', [
    ('2026-09-02T12:00:00Z', True),
    ('2026-09-01T12:00:03.123456Z', False),
    ('2026-09-01T12:00:03.123457Z', True),
])
def test_comparison_accepts_mixed_iso_precision_and_requires_fresh_interval(current_start, expected):
    checks = comparison_checks(comparison_runs(current_start), ['p1', 'p2'], ['c1', 'c2']).set_index('check')
    assert checks.loc[['selection', 'complete', 'context'], 'passed'].all()
    assert bool(checks.loc['fresh_intervals', 'passed']) is expected


@pytest.mark.parametrize('run_id, field', [
    ('p2', 'observation_start'), ('p2', 'observation_end'),
    ('c2', 'observation_start'), ('c2', 'observation_end'),
])
@pytest.mark.parametrize('clock_kind', ['naive', 'date_only', 'missing', 'malformed'])
def test_comparison_requires_every_clock_to_have_an_explicit_timezone(run_id, field, clock_kind):
    runs = comparison_runs()
    selected = runs.run_id.eq(run_id)
    value = runs.loc[selected, field].item()
    invalid = {'naive': value.replace('Z', '').replace('+00:00', ''),
               'date_only': value[:10], 'missing': None, 'malformed': 'unknown'}
    runs.loc[selected, field] = invalid[clock_kind]
    checks = comparison_checks(runs, ['p1', 'p2'], ['c1', 'c2']).set_index('check')
    assert checks.loc[['selection', 'complete', 'context'], 'passed'].all()
    assert not checks.loc['fresh_intervals', 'passed']


@pytest.mark.parametrize('cutoff, last_status, included_cycles', [
    ('2026-09-02T16:01:00.123455Z', 'missing', {'c1'}),
    ('2026-09-02T16:01:00.123456Z', 'complete', {'c1', 'c2'}),
])
def test_daily_mixed_precision_availability_preserves_exact_cutoff(cutoff, last_status, included_cycles):
    days = cycles((1, 2))
    days['available_at'] = ['2026-09-01T16:01:00+00:00', '2026-09-02T16:01:00.123456Z']
    rows = observations((1, 'V1', 'L1'), (2, 'V1', 'L1'))
    original_days, original_rows = days.copy(deep=True), rows.copy(deep=True)
    tables = daily_tables(days, rows, as_of=cutoff, timezone_name='America/New_York')
    assert tables['daily_inventory'].coverage_status.tolist() == ['complete', last_status]
    assert set(tables['vehicle_observations'].cycle_id) == included_cycles
    pd.testing.assert_frame_equal(days, original_days)
    pd.testing.assert_frame_equal(rows, original_rows)


@pytest.mark.parametrize('available_at', ['2026-09-02T16:01:00.123456', '2026-09-02'])
def test_daily_existing_cycle_validator_blocks_timezone_free_availability(available_at):
    days = cycles((1, 2))
    days.loc[1, 'available_at'] = available_at
    tables = daily_tables(days, observations((1, 'V1', 'L1'), (2, 'V1', 'L1')),
                          as_of='2026-09-03T23:00:00Z', timezone_name='America/New_York')
    known = tables['daily_inventory'].loc[lambda frame: frame.cycle_id.notna()]
    assert known.coverage_status.eq('invalid').all()
    assert known.inventory_count.isna().all()
    assert known.analysis_error.str.contains('timezone aware').all()
