"""Execute the appended daily analysis offline; existing history cells stay separate."""
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

import vehicle_tracker.history as history_module

VEHICLE = Path(__file__).resolve().parents[1]
ROOT = Path(history_module.__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('daily_notebook_guards', ROOT / 'scripts/check_notebooks.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
CELL_IDS = ['daily-operating-view', 'daily-cycle-data', 'daily-operating-tables',
            'daily-vin-analysis', 'daily-asking-prices', 'daily-price-composition', 'daily-price-bridge',
            'daily-observed-age-prices', 'sale-review-data',
            'daily-synthetic-example', 'sale-review-example']


def daily_code():
    book = json.loads((VEHICLE / 'notebooks/20_carvana_history_analysis.ipynb').read_text(encoding='utf-8'))
    cells = {cell['id']: ''.join(cell['source']) for cell in book['cells'] if cell['cell_type'] == 'code'}
    assert set(CELL_IDS) <= set(cells), 'Daily cycle notebook analysis is missing'
    return cells


def execute_daily(monkeypatch, tmp_path, cycles=None, rows=None):
    code = daily_code()
    import vehicle_tracker.cycles as cycle_module
    calls = []
    database = tmp_path / 'explicit-existing.sqlite'
    database.write_bytes(b'unchanged sentinel; reader is injected for this notebook test')

    def read_selected(paths, db, *, as_of=None):
        assert as_of == "2026-09-08T13:00:00Z"
        calls.append((paths, db))
        assert cycles is not None, 'An empty cycle selection must never read the database'
        return cycles.copy(deep=True), rows.copy(deep=True)

    monkeypatch.setattr(cycle_module, 'read_cycle_history', read_selected)
    selected = [tmp_path / 'explicit-cycle.json'] if cycles is not None else []
    scope = dict(pd=pd, Path=Path, ROOT=VEHICLE, display=lambda *args: None,
                 AS_OF_OVERRIDE='2026-09-08T13:00:00Z', DAILY_DATABASE_OVERRIDE=database,
                 CYCLE_REPORTS_OVERRIDE=selected, RUN_TRACKING_VIEW_OVERRIDE=False)
    before = database.read_bytes()
    with checker.offline_guards():
        for identifier in CELL_IDS:
            exec(compile(code[identifier], identifier, 'exec'), scope)
    assert database.read_bytes() == before
    assert calls == ([(selected, database)] if selected else [])
    return scope


@pytest.fixture
def two_cycle_rows():
    cycles = pd.DataFrame([dict(cycle_id='cycle-' + day, cycle_date='2026-09-' + day,
        timezone='UTC', window_start=f'2026-09-{day}T10:00:00Z', window_end=f'2026-09-{day}T11:00:00Z',
        available_at=f'2026-09-{day}T11:05:00Z', scope_id='explicit-scope', coverage_complete=True,
        coverage_reason='Complete synthetic test fixture') for day in ['01', '02']])
    rows = pd.DataFrame([dict(cycle_id='cycle-' + day, retailer='carvana', vin='TESTVIN',
        listing_id=listing, capture_id='capture-' + day, run_id='run-' + day,
        observed_at_utc=f'2026-09-{day}T10:30:00Z', source_url='synthetic://test/' + day,
        listing_url='synthetic://listing/' + listing, asking_price_usd=price,
        purchase_pending=pending, vehicle_lock_type=0)
        for day, listing, price, pending in [('01', 'old', 20000, True), ('02', 'new', 19500, False)]])
    return cycles, rows


def test_daily_default_withholds_results(monkeypatch, tmp_path, capsys):
    scope = execute_daily(monkeypatch, tmp_path)
    for name in ['daily_cycles', 'daily_observations', 'daily_events', 'daily_summary',
                 'daily_timeline', 'daily_identity_join',
                 'sale_candidate_rows', 'sale_daily_report']:
        assert scope[name].empty
    assert 'NO DAILY CYCLES' in capsys.readouterr().out
    assert not list(tmp_path.glob('*.json'))


def test_daily_selected_cycles_show_visible_vin_join_and_pending_change(monkeypatch, tmp_path, two_cycle_rows):
    scope = execute_daily(monkeypatch, tmp_path, *two_cycle_rows)
    joined = scope['daily_identity_join'].iloc[0]
    assert joined['vin'] == 'TESTVIN' and joined['_merge'] == 'both'
    assert joined['listing_id_before'] == 'old' and joined['listing_id_after'] == 'new'
    assert joined['visible_asking_price_change_usd'] == -500
    assert scope['daily_timeline'].event_type.tolist() == ['first_observed', 'relisted']
    assert scope['daily_source_rows'].capture_id.tolist() == ['capture-01', 'capture-02']
    assert scope['daily_source_rows'].cycle_date.tolist() == ['2026-09-01', '2026-09-02']
    assert bool(joined['pending_changed'])
    assert scope['daily_change_counts'].loc['both', 'VINs'] == 1
    assert scope['daily_summary'].pending_cleared.tolist() == [0, 1]


def test_daily_incomplete_cycle_exposes_unknown_absence(monkeypatch, tmp_path, two_cycle_rows, capsys):
    cycles, rows = two_cycle_rows
    cycles.loc[1, ['coverage_complete', 'coverage_reason']] = [False, 'Missing requested partition']
    scope = execute_daily(monkeypatch, tmp_path, cycles, rows.iloc[:1])
    assert scope['daily_events'].event_type.tolist() == ['first_observed', 'absence_unassessable']
    assert pd.isna(scope['daily_summary'].iloc[-1].persistent_absence)
    assert scope['daily_identity_join'].empty
    assert 'BLOCKED DAILY JOIN' in capsys.readouterr().out
    assert not scope['daily_comparison_allowed']
    assert scope['asking_price_means'].empty and scope['price_change_counts'].empty


def test_daily_synthetic_example_relisting_gap_and_missing_are_separate(monkeypatch, tmp_path):
    scope = execute_daily(monkeypatch, tmp_path)
    assert scope['daily_observations'].empty
    example = scope['synthetic_events']
    assert example.vin.eq('SYNTHETIC-VIN-A').all()
    assert example.event_type.iloc[1] == 'relisted'
    assert example.asking_price_change_usd.iloc[1] == -500
    assert example.event_type.iloc[2] == 'absence_unassessable'
    assert example.absence_streak.iloc[2] == 0
    assert example.estimated_sales.isna().all()
    counts = scope['synthetic_summary']
    assert counts.skipped_days_before.iloc[2] == 1
    assert pd.isna(counts.persistent_absence.iloc[2])
    assert counts.pending_started.iloc[1] == 1
    assert example.event_type.eq('persistent_absence').sum() == 1
    assert example.event_type.iloc[-1] == 'relisted'
    assert example.reappeared_after_absence.iloc[-1]
    assert scope['example_candidates_before'].followup_state.tolist() == ['still_absent']
    assert scope['example_candidates_after'].followup_state.tolist() == ['reappeared']
    assert scope['example_candidates_before'].candidate_id.tolist() == scope['example_candidates_after'].candidate_id.tolist()


def test_visible_join_preserves_additions_absences_and_unknown_pending(monkeypatch, tmp_path, two_cycle_rows):
    days, rows = two_cycle_rows
    removed = rows.iloc[[0]].assign(vin='REMOVED', listing_id='removed')
    added = rows.iloc[[1]].assign(vin='ADDED', listing_id='added')
    rows['purchase_pending'] = rows.purchase_pending.astype('boolean')
    rows.loc[1, 'purchase_pending'] = pd.NA
    rows = pd.concat([rows, removed, added], ignore_index=True)
    scope = execute_daily(monkeypatch, tmp_path, days, rows)
    counts = scope['daily_change_counts']['VINs'].to_dict()
    assert counts == {'left_only': 1, 'right_only': 1, 'both': 1}
    joined = scope['daily_identity_join'].set_index('vin')
    assert pd.isna(joined.loc['TESTVIN', 'pending_changed'])
    assert pd.isna(joined.loc['REMOVED', 'pending_changed'])
    assert pd.isna(joined.loc['ADDED', 'visible_asking_price_change_usd'])
    assert scope['sale_daily_report'].estimated_sales.isna().all()


@pytest.mark.parametrize('problem', ['gap', 'partial', 'duplicate', 'scope'])
def test_daily_notebook_withholds_invalid_daily_comparison(monkeypatch, tmp_path, two_cycle_rows, problem):
    days, rows = two_cycle_rows
    if problem == 'gap':
        for field in ['cycle_date', 'window_start', 'window_end', 'available_at']:
            days.loc[1, field] = days.loc[1, field].replace('09-02', '09-04')
        rows.loc[1, 'observed_at_utc'] = rows.loc[1, 'observed_at_utc'].replace('09-02', '09-04')
    elif problem == 'partial':
        days.loc[1, 'coverage_complete'] = False
    elif problem == 'duplicate':
        rows = pd.concat([rows, rows.iloc[[0]]], ignore_index=True)
    else:
        days.loc[1, 'scope_id'] = 'a-different-population'
    scope = execute_daily(monkeypatch, tmp_path, days, rows)
    assert not scope['daily_comparison_allowed']
    assert scope['daily_identity_join'].empty
    assert scope['daily_change_counts'].empty
    assert scope['asking_price_means'].empty
    assert scope['age_price_summary'].empty and scope['age_model_breakdown'].empty
    if problem == 'duplicate':
        assert len(scope['identity_issues']) == 2


def test_inventory_composition_moves_mean_without_matched_repricing(monkeypatch, tmp_path, two_cycle_rows):
    days, rows = two_cycle_rows
    rows['asking_price_usd'] = 20000
    removed = rows.iloc[[0]].assign(vin='REMOVED', listing_id='removed', asking_price_usd=10000)
    added = rows.iloc[[1]].assign(vin='ADDED', listing_id='added', asking_price_usd=30000)
    scope = execute_daily(monkeypatch, tmp_path, days, pd.concat([rows, removed, added], ignore_index=True))
    means = scope['asking_price_means'].set_index('sample')
    assert means.mean_asking_price_usd.tolist() == [15000, 25000, 20000, 20000]
    assert means.known_asking_prices.tolist() == [2, 2, 1, 1]
    assert scope['price_change_counts'].matched_VINs.to_dict() == {
        'unchanged': 1, 'cut': 0, 'increase': 0, 'unavailable': 0}
    assert scope['composition_prices'].mean_asking_price_usd.tolist() == [30000, 10000]
    assert scope['additions'].listing_id_after.tolist() == ['added']
    assert scope['disappearances'].listing_id_before.tolist() == ['removed']
    breakdown = scope['price_change_breakdown'].set_index('component').change_usd
    assert breakdown['Common-sample repricing'] == 0
    assert breakdown['inventory composition'] == 10000
    assert breakdown['Total observed mean change'] == 10000
    assert scope['reconciliation_difference'] == 0


def test_matched_price_changes_missing_and_zero_denominators(monkeypatch, tmp_path, two_cycle_rows):
    days, template = two_cycle_rows
    parts = []
    for vin, previous, current in [('CUT', 20000, 19500), ('UP', 10000, 11000),
                                  ('MISSING', None, 12000), ('ZERO', 0, 0), ('NEGATIVE', -10, -8)]:
        part = template.copy().assign(vin=vin)
        part['listing_id'] = [vin + '-old', vin + '-new']
        part['asking_price_usd'] = [previous, current]
        parts.append(part)
    scope = execute_daily(monkeypatch, tmp_path, days, pd.concat(parts, ignore_index=True))
    changes = scope['matched_price_rows'].set_index('vin')
    assert changes.loc['CUT', 'visible_asking_price_change_usd'] == -500
    assert changes.loc['CUT', 'asking_price_change_pct'] == -2.5
    assert changes.loc['UP', 'asking_price_change_pct'] == 10
    assert changes.loc['MISSING', 'price_comparison'] == 'unavailable'
    assert pd.isna(changes.loc['MISSING', 'visible_asking_price_change_usd'])
    assert pd.isna(changes.loc['ZERO', 'asking_price_change_pct'])
    assert changes.loc['ZERO', 'price_comparison'] == 'unchanged'
    assert changes.loc['NEGATIVE', 'asking_price_usd_before'] == -10
    assert changes.loc['CUT', 'listing_id_before'] == 'CUT-old'
    assert changes.loc['CUT', 'listing_id_after'] == 'CUT-new'
    means = scope['asking_price_means']
    assert means.known_asking_prices.tolist() == [4, 5, 4, 4]
    assert means.missing_asking_prices.tolist() == [1, 0, 0, 0]
    assert scope['price_change_counts'].matched_VINs.to_dict() == {
        'unchanged': 1, 'cut': 1, 'increase': 2, 'unavailable': 1}
    assert not scope['all_prices_known']
    assert 'composition plus price coverage' in scope['price_change_breakdown'].component.tolist()
    assert scope['reconciliation_difference'] == pytest.approx(0)


def test_price_bridge_separates_repricing_and_composition(monkeypatch, tmp_path, two_cycle_rows):
    days, rows = two_cycle_rows  # One common VIN reprices from 20,000 to 19,500.
    removed = rows.iloc[[0]].assign(vin='REMOVED', listing_id='removed', asking_price_usd=10000)
    added = rows.iloc[[1]].assign(vin='ADDED', listing_id='added', asking_price_usd=30000)
    scope = execute_daily(monkeypatch, tmp_path, days, pd.concat([rows, removed, added], ignore_index=True))
    changes = scope['price_change_breakdown'].set_index('component').change_usd
    assert changes['Common-sample repricing'] == -500
    assert changes['inventory composition'] == 10250
    assert changes['Total observed mean change'] == 9750
    assert scope['price_bridge'].change_usd.sum() == 9750


def test_price_bridge_is_unknown_without_common_priced_vehicles(monkeypatch, tmp_path, two_cycle_rows):
    days, rows = two_cycle_rows
    rows['vin'] = ['OLD', 'NEW']
    scope = execute_daily(monkeypatch, tmp_path, days, rows)
    assert scope['price_bridge'].empty and scope['price_change_breakdown'].empty
    assert scope['asking_price_means'].known_asking_prices.tolist() == [1, 1, 0, 0]


def test_irregular_windows_are_not_scaled_to_a_day(monkeypatch, tmp_path, two_cycle_rows, capsys):
    days, rows = two_cycle_rows
    days.loc[0, ['window_start', 'window_end', 'available_at']] = [
        '2026-09-01T21:45:00Z', '2026-09-01T21:46:30Z', '2026-09-01T21:47:00Z']
    days.loc[1, ['window_start', 'window_end', 'available_at']] = [
        '2026-09-02T07:00:00Z', '2026-09-02T07:02:00Z', '2026-09-02T07:03:00Z']
    rows['observed_at_utc'] = ['2026-09-01T21:46:00Z', '2026-09-02T07:01:00Z']
    scope = execute_daily(monkeypatch, tmp_path, days, rows)
    windows = scope['collection_windows']
    assert windows.sweep_seconds.tolist() == [90, 120]
    assert pd.isna(windows.hours_since_previous_start.iloc[0])
    assert scope['interval_hours'] == 9.25
    assert windows.timezone.tolist() == ['UTC', 'UTC']
    assert windows.window_start_local.iloc[0] == '2026-09-01T21:45:00+00:00'
    assert scope['matched_price_rows'].visible_asking_price_change_usd.tolist() == [-500]
    age = scope['age_price_rows'].iloc[0]
    assert age.elapsed_observation_hours == 9.25
    assert age.days_since_first_observed == pytest.approx(9.25 / 24)
    assert age.observed_age_group == '<1 day'
    assert age.observed_at_utc_before == pd.Timestamp('2026-09-01T21:46:00Z')
    assert age.observed_at_utc_after == pd.Timestamp('2026-09-02T07:01:00Z')
    assert 'not a measured 24-hour flow' in capsys.readouterr().out


@pytest.mark.parametrize('blank', ['', '   ', None])
def test_invalid_identity_rows_are_visible_without_a_price_join(monkeypatch, tmp_path, two_cycle_rows, blank):
    days, rows = two_cycle_rows
    rows.loc[0, 'vin'] = blank
    scope = execute_daily(monkeypatch, tmp_path, days, rows)
    assert len(scope['identity_issues']) == 1
    assert scope['daily_identity_join'].empty and scope['asking_price_means'].empty


def run_page_cell(days, rows, checks, cutoff):
    from vehicle_tracker.daily import daily_tables
    from vehicle_tracker.checks import CHECK_COLUMNS, validate_checks
    from vehicle_tracker.events import vin_events

    known_days = days.loc[pd.to_datetime(days.available_at, utc=True).le(pd.Timestamp(cutoff))]
    known_rows = rows.loc[rows.cycle_id.isin(known_days.cycle_id)]
    report = daily_tables(known_days, known_rows, as_of=cutoff, timezone_name='America/New_York', checks=checks)
    scope = dict(pd=pd, display=lambda *args: None, tracking_tables=report,
        daily_source_rows=report['vehicle_observations'], saved_check_evidence=report['listing_checks'],
        CHECKS_OVERRIDE=checks.to_dict('records'), CHECK_COLUMNS=CHECK_COLUMNS,
        check_history_input=checks,
        validate_checks=validate_checks, TRACKING_AS_OF=cutoff,
        daily_events=vin_events(known_days, known_rows))
    with checker.offline_guards():
        exec(daily_code()['daily-check-history'], scope)
        exec(daily_code()['daily-page-checks'], scope)
    return scope


def test_page_table_does_not_infer_sold_from_boilerplate_or_use_future_evidence():
    from test_checks import check
    from test_daily_events import cycles, observations

    days = cycles((1, 2, 3, 4))
    rows = observations((1, 'V1', 'L1'), (3, 'V1', 'L1', {'purchase_pending': True}))
    checks = pd.DataFrame([check(observed_status='available',
        native_text='Get Started. Equipment is the configuration as originally sold.'),
        check(check_id='future-sold', checked_at='2026-09-04T16:00:00.123Z',
            available_at='2026-09-05T17:00Z', observed_status='sold_label', native_text='Sold')])
    scope = run_page_cell(days, rows, checks, '2026-09-04T23:00Z')
    context = scope['check_context']
    assert len(context) == 1 and not bool(context.iloc[0].purchase_pending)
    assert scope['page_check_counts'].index.tolist() == ['available']
    assert len(scope['check_history_visible']) == 1
    assert scope['tracking_tables']['daily_inventory'].estimated_sales.isna().all()


def test_page_history_counts_physical_checks_and_keeps_relisting_followup():
    from test_checks import check
    from test_daily_events import cycles, observations

    days = cycles((1, 2, 3, 4))
    rows = observations((1, 'V1', 'L1'), (3, 'V1', 'L2'), (4, 'V1', 'L2'))
    checks = pd.DataFrame([check(),
        check(check_id='correction', available_at='2026-09-03T17:00Z', observed_status='unknown'),
        check(listing='L2', check_id='new-listing-check', checked_at='2026-09-03T16:00:00.123Z',
            available_at='2026-09-03T17:00:00.456Z', observed_status='available', native_text='Get Started')])
    scope = run_page_cell(days, rows, checks, '2026-09-04T23:00Z')
    assert len(scope['check_context']) == 2
    assert scope['page_check_counts'].checked_listings.sum() == 2  # Two listings, one VIN.
    assert len(scope['check_history_visible']) == 2  # Correction is not another physical check.
    later = scope['later_inventory']
    old_listing_followup = later.loc[later.checked_listing_id.eq('L1')]
    assert old_listing_followup.listing_id.eq('L2').all()
    assert old_listing_followup.reappeared_after_absence.any()


def test_age_reduction_denominator_counts_only_known_matched_prices(monkeypatch, tmp_path, two_cycle_rows):
    days, template = two_cycle_rows
    parts = []
    for vin, previous, current, model in [('CUT1', 20000, 19500, 'Model A'),
            ('CUT2', 20000, 19000, 'Model B'), ('FLAT', 20000, 20000, 'Model A'),
            ('MISSING', None, 10000, 'Model B')]:
        part = template.copy().assign(vin=vin, make='Synthetic', model=model)
        part['listing_id'] = [vin + '-old', vin + '-new']
        part['asking_price_usd'] = [previous, current]
        parts.append(part)
    parts.extend([template.iloc[[0]].assign(vin='GONE', listing_id='gone'),
                  template.iloc[[1]].assign(vin='NEW', listing_id='new')])
    scope = execute_daily(monkeypatch, tmp_path, days, pd.concat(parts, ignore_index=True))
    summary = scope['age_price_summary'].iloc[0]
    assert summary.eligible_matched_vehicles == 3 and summary.price_reductions == 2
    assert summary.price_reduction_pct == pytest.approx(200 / 3)
    assert summary.median_reduction_usd == 750
    groups = scope['age_model_breakdown'].set_index('model')
    assert groups.loc['Model A', 'price_reduction_pct'] == 50
    assert groups.loc['Model B', 'price_reduction_pct'] == 100
    assert scope['age_price_rows'].days_since_first_observed.eq(1).all()
    assert scope['age_price_rows'].observed_age_group.eq('1 to <3 days').all()
    assert scope['age_price_rows'].present_in_initial_collection.all()
    assert set(scope['age_price_rows'].vin) == {'CUT1', 'CUT2', 'FLAT'}


@pytest.mark.parametrize('condition', ['no_cuts', 'missing_prices', 'no_matches'])
def test_age_empty_or_no_reductions_does_not_invent_a_median(monkeypatch, tmp_path, two_cycle_rows, condition):
    days, rows = two_cycle_rows
    rows['asking_price_usd'] = 20000 if condition != 'missing_prices' else None
    if condition == 'no_matches':
        rows['vin'] = ['BEFORE', 'AFTER']
    scope = execute_daily(monkeypatch, tmp_path, days, rows)
    if condition == 'no_cuts':
        result = scope['age_price_summary'].iloc[0]
        assert result.eligible_matched_vehicles == 1 and result.price_reduction_pct == 0
        assert pd.isna(result.median_reduction_usd)
    else:
        assert scope['age_price_summary'].empty and scope['age_price_rows'].empty


@pytest.mark.parametrize('early_evidence', ['known', 'late_available', 'partial'])
def test_first_observed_age_uses_selected_complete_history_at_cutoff(
        monkeypatch, tmp_path, two_cycle_rows, early_evidence):
    days, rows = two_cycle_rows
    # Synthetic third observation creates a valid last pair on days 2/3.
    third = days.iloc[[1]].copy()
    third['cycle_id'] = 'cycle-03'
    for field in ['cycle_date', 'window_start', 'window_end', 'available_at']:
        third[field] = third[field].str.replace('09-02', '09-03', regex=False)
    last_row = rows.iloc[[1]].assign(cycle_id='cycle-03', asking_price_usd=19000,
        observed_at_utc='2026-09-03T10:30:00Z', capture_id='capture-03', run_id='run-03')
    days = pd.concat([days, third], ignore_index=True)
    rows = pd.concat([rows, last_row], ignore_index=True)
    if early_evidence == 'late_available': days.loc[0, 'available_at'] = '2026-09-09T00:00:00Z'
    if early_evidence == 'partial': days.loc[0, 'coverage_complete'] = False
    scope = execute_daily(monkeypatch, tmp_path, days, rows)
    age = scope['age_price_rows'].iloc[0]
    assert age.days_since_first_observed == (2 if early_evidence == 'known' else 1)
    assert age.reduction_usd == 500 and age.elapsed_observation_hours == 24
    assert scope['age_price_summary'].eligible_matched_vehicles.iloc[0] == 1
