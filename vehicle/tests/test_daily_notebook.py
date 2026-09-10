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
CELL_IDS = ['daily-cycle-data', 'daily-vin-analysis', 'sale-review-data',
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
    scope = dict(pd=pd, Path=Path, display=lambda *args: None, DATABASE=database,
                 CYCLE_REPORTS_OVERRIDE=selected)
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
    assert counts.pending_cleared.iloc[1] == 1
    assert example.event_type.eq('persistent_absence').sum() == 1
    assert example.event_type.iloc[-1] == 'reappeared'
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
        validate_checks=validate_checks, TRACKING_AS_OF=cutoff,
        daily_events=vin_events(known_days, known_rows))
    with checker.offline_guards():
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
