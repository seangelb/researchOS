"""Daily orchestration uses retained fixtures and temporary storage only."""
import copy
from datetime import timedelta
import json
from unittest.mock import Mock

import pandas as pd
import pytest

from test_daily_cycles import clock, reply
from test_daily_events import cycles, observations
from test_search import response_data
from vehicle_tracker import daily


@pytest.fixture
def settings(tmp_path, clock, monkeypatch):
    monkeypatch.setattr(daily, 'utcnow', lambda: clock)
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'plan.json').write_text(json.dumps({'queries': [dict(
        query_id='all', zip_code='08542', filters={}, location_filter=False)]}))
    path = config / 'tracking.json'
    path.write_text(json.dumps(dict(plan='config/plan.json', timezone='America/New_York',
        capture_root='raw', database='analysis/history.sqlite', register='analysis/cycles.json',
        exports='analysis/tables', max_requests=5, max_seconds=60, followup_limit=2)))
    return daily.tracking_settings(path)


def test_preview_is_read_only_and_uses_local_date(settings, clock, tmp_path):
    before = set(tmp_path.rglob('*'))
    state, folder, tables = daily.run_tracking(settings, post=Mock(side_effect=AssertionError('No network')))
    assert state['cycle_date'] == '2026-09-08' and len(state['queries']) == 1
    assert folder is tables is None and set(tmp_path.rglob('*')) == before
    early_utc = clock.replace(hour=1)
    assert daily.preview(settings, now=early_utc)['cycle_date'] == '2026-09-07'


def test_live_import_export_and_repeat_are_safe(settings, response_data, clock):
    post = Mock(return_value=reply(response_data))
    state, folder, tables = daily.run_tracking(settings, live=True, post=post)
    assert state['coverage_complete'] and post.call_count == 1
    baseline = tables['daily_inventory'].iloc[0]
    assert baseline.coverage_status == 'complete' and baseline.inventory_count == 3
    assert pd.isna(baseline.new_since_previous_day) and pd.isna(baseline.estimated_sales)
    actual = tables['vehicle_observations']
    assert len(actual) == 3 and actual.cycle_date.eq('2026-09-08').all()
    assert actual.capture_id.notna().all() and actual.source_path.notna().all()
    assert len(daily.registered_cycles(settings)) == 1
    with pytest.raises(ValueError, match='already registered'):
        daily.run_tracking(settings, live=True, post=post)
    assert post.call_count == 1
    before = settings['database'].read_bytes()
    retained = settings['capture_root'] / '2026-09-08/cycle.json'
    _, second_folder, _ = daily.run_tracking(settings, import_path=retained, post=post)
    assert second_folder != folder and settings['database'].read_bytes() == before
    assert len(daily.registered_cycles(settings)) == 1 and post.call_count == 1
    manifest = json.loads((folder / 'manifest.json').read_text())
    assert manifest['outputs']['daily_inventory.csv'] == daily.digest(folder / 'daily_inventory.csv')
    early_days, early_rows = daily.tracking_history(settings, as_of=(clock-timedelta(days=1)).isoformat())
    assert early_days.empty and early_rows.empty


@pytest.mark.parametrize('status', [403, 429])
def test_failed_capture_is_retained_as_partial_not_zero_inventory(settings, response_data, status):
    post = Mock(return_value=reply(response_data, status))
    state, _, tables = daily.run_tracking(settings, live=True, post=post)
    assert post.call_count == 1 and not state['coverage_complete']
    assert len(daily.registered_cycles(settings)) == 1
    row = tables['daily_inventory'].iloc[0]
    assert row.coverage_status == 'partial' and row.observed_vins == 0
    assert pd.isna(row.inventory_count) and pd.isna(row.new_candidates)
    assert row.coverage_reason == ('http_access_failure' if status == 403 else 'rate_limited; no automatic retries')


@pytest.mark.parametrize('change', ['scope', 'timezone', 'root_bytes', 'query_bytes', 'database', 'duplicate_date'])
def test_register_changes_block_before_another_request(settings, response_data, change):
    post = Mock(return_value=reply(response_data))
    daily.run_tracking(settings, live=True, post=post)
    if change == 'scope':
        settings['queries'][0]['zip_code'] = '08540'
    if change == 'timezone':
        settings['timezone'] = 'UTC'
    if change == 'database':
        settings['database'] = settings['database'].with_name('other.sqlite')
    if change == 'root_bytes':
        path = settings['capture_root'] / '2026-09-08/cycle.json'
        path.write_text(path.read_text() + '\n')
    if change == 'query_bytes':
        path = settings['capture_root'] / '2026-09-08/attempt_0001/all/run_report.json'
        path.write_text(path.read_text() + '\n')
    if change == 'duplicate_date':
        register = json.loads(settings['register'].read_text())
        register['cycles'] *= 2
        settings['register'].write_text(json.dumps(register))
    with pytest.raises(ValueError):
        daily.run_tracking(settings, live=True, post=post)
    assert post.call_count == 1


def test_zero_inventory_missing_day_and_partial_day_are_distinct():
    days = cycles((1, 2, 4, 5), partial=(5,))
    rows = observations((1, 'V1', 'L1', {'asking_price_usd': 0, 'purchase_pending': None}))
    tables = daily.daily_tables(days, rows, as_of='2026-09-06T23:00Z', timezone_name='America/New_York')
    summary = tables['daily_inventory'].set_index('cycle_date')
    assert summary.coverage_status.tolist() == ['complete', 'complete', 'missing', 'complete', 'partial', 'missing']
    assert summary.loc['2026-09-01', 'average_asking_price_usd'] == 0
    assert summary.loc['2026-09-01', 'pending_unknown'] == 1
    assert summary.loc['2026-09-02', 'inventory_count'] == 0
    assert summary.loc['2026-09-02', 'absent_since_previous_day'] == 1
    assert summary.loc[['2026-09-03', '2026-09-05', '2026-09-06'], 'inventory_count'].isna().all()
    assert pd.isna(summary.loc['2026-09-04', 'absent_since_previous_day'])
    assert summary.estimated_sales.isna().all() and summary.site_marked_sold.isna().all()
    assert len(tables['vehicle_observations']) == 1


def test_conflicting_identities_keep_rows_and_withhold_inference():
    rows = observations((1, 'V1', 'L1'), (1, 'V1', 'L2'))
    tables = daily.daily_tables(cycles((1,)), rows, as_of='2026-09-01T23:00Z', timezone_name='America/New_York')
    summary = tables['daily_inventory'].iloc[0]
    assert summary.coverage_status == 'invalid' and 'Conflicting' in summary.analysis_error
    assert pd.isna(summary.inventory_count) and pd.isna(summary.new_candidates)
    assert len(tables['vehicle_observations']) == 2
    assert tables['vehicle_observations'].duplicate_vin_in_cycle.all()
    assert tables['detail_followups'].empty


def test_followup_queue_is_bounded_and_does_not_check_pages():
    rows = observations((1, 'V1', 'L1'), (1, 'V2', 'L2'), (2, 'V2', 'L2', {'vehicle_lock_type': 1}))
    tables = daily.daily_tables(cycles((1, 2)), rows, as_of='2026-09-02T23:00Z',
                               timezone_name='America/New_York', followup_limit=1)
    followups = tables['detail_followups']
    assert followups.followup_reason.tolist() == ['first_absence', 'native_status_changed']
    assert followups.selected_for_check.tolist() == [True, False]
    assert followups.detail_status.eq('not_checked').all()
    assert followups.last_observed_cycle_id.tolist() == ['c1', 'c2']


def test_table_cutoff_does_not_leak_future_or_mutate_inputs():
    days = cycles((1, 2))
    rows = observations((1, 'V1', 'L1'), (2, 'V2', 'L1'))
    before = rows.copy(deep=True)
    tables = daily.daily_tables(days, rows, as_of='2026-09-01T23:00Z', timezone_name='America/New_York')
    assert tables['daily_inventory'].coverage_status.tolist() == ['complete']
    assert tables['vehicle_observations'].vin.tolist() == ['V1']
    pd.testing.assert_frame_equal(rows, before)


def test_alternative_same_day_cycle_is_not_silently_selected(settings, response_data):
    post = Mock(return_value=reply(response_data))
    daily.run_tracking(settings, live=True, post=post)
    other = copy.deepcopy(settings)
    other['capture_root'] = settings['capture_root'] / 'other'
    config = daily.preview(other)
    daily.collect_cycle(other['queries'], destination=config.pop('destination'),
        **{key: config[key] for key in ['cycle_date', 'window_start', 'window_end', 'max_requests', 'max_seconds']},
        timezone_name=other['timezone'], post=post)
    with pytest.raises(ValueError, match='already registered with different evidence'):
        daily.run_tracking(settings, import_path=other['capture_root'] / '2026-09-08/cycle.json')
    assert len(daily.registered_cycles(settings)) == 1
