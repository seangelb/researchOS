"""Saved review workflow, exercised with synthetic evidence and temporary files."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from test_daily_cycles import clock, reply
from test_daily_tracking import settings
from test_daily_events import cycles, observations
from test_search import response_data
from vehicle_tracker import daily
from vehicle_tracker.checks import (CHECK_COLUMNS, append_record, read_records,
    select_checks, select_reviews, validate_checks)
from vehicle_tracker.sales import REVIEW_COLUMNS, sale_candidates


def check(vin='V1', listing='L1', **changes):
    return dict(dict(check_id='check-' + vin, retailer='carvana', vin=vin, listing_id=listing,
        checked_at='2026-09-02T16:00:00Z', available_at='2026-09-02T17:00:00Z',
        observed_status='unavailable', native_text='This vehicle is no longer available',
        source='synthetic://retained-check', reviewer='Test analyst', note='Synthetic observation only'), **changes)


def review(candidate, **changes):
    return dict(dict(candidate_id=candidate, outcome='confirmed_sale', sale_date='2026-09-03',
        reviewer='Test analyst', source='synthetic://dated-outcome', available_at='2026-09-04T18:00:00Z',
        note='Invented independent outcome for test only'), **changes)


def tables(days, rows, *, day=4, checks=None, reviews=None):
    return daily.daily_tables(days, rows, as_of=f'2026-09-{day:02}T23:00:00Z',
        timezone_name='America/New_York', checks=checks, reviews=reviews)


def test_missing_files_do_not_create_records(tmp_path):
    assert read_records(tmp_path/'checks.csv', CHECK_COLUMNS).empty
    assert select_checks(None, as_of='2026-09-04T23:00Z').empty
    assert select_reviews(None, as_of='2026-09-04T23:00Z').empty
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('kind', ['check', 'review'])
def test_append_replay_conflict_and_literal_text(tmp_path, kind):
    path = tmp_path/(kind + '.csv')
    record = check(vin='000001', native_text='NA', note='Line one\nLine two, with commas') if kind=='check' else review('000123')
    columns = CHECK_COLUMNS if kind=='check' else REVIEW_COLUMNS
    assert append_record(path, record, kind=kind)
    before = path.read_bytes()
    assert not append_record(path, record, kind=kind)
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match='Conflicting'):
        append_record(path, dict(record, note='Conflicting replacement'), kind=kind)
    assert path.read_bytes() == before
    saved = read_records(path, columns)
    assert saved.iloc[0]['vin' if kind=='check' else 'candidate_id'] == ('000001' if kind=='check' else '000123')
    if kind=='check':
        assert saved.iloc[0].native_text == 'NA' and saved.iloc[0].note == record['note']


def test_queue_rotates_to_previously_skipped_vin_and_retains_overflow():
    rows = observations(*[(1, f'V{i:02}', f'L{i:02}') for i in range(21)])
    before = tables(cycles((1, 2)), rows, day=2)['detail_followups']
    assert before.selected_for_check.sum() == 20
    assert before.iloc[-1].vin == 'V20' and not before.iloc[-1].selected_for_check
    checks = pd.DataFrame([check(f'V{i:02}', f'L{i:02}') for i in range(20)])
    after = tables(cycles((1, 2, 3)), rows, day=3, checks=checks)['detail_followups']
    assert len(after) == 21 and after.loc[after.selected_for_check, 'vin'].tolist() == ['V20']
    assert after.iloc[0].queue_state == 'first_check'
    assert after.iloc[1:].queue_state.eq('up_to_date').all()
    overdue = tables(cycles(), rows, checks=checks)['detail_followups']
    assert overdue.iloc[0].vin == 'V20' and overdue.queue_state.eq('recheck_due').sum() == 20


def test_unchecked_status_change_survives_the_next_unchanged_cycle():
    rows = observations((1, 'V1', 'L1'), (2, 'V1', 'L1', {'purchase_pending': True}),
                        (3, 'V1', 'L1', {'purchase_pending': True}))
    q = tables(cycles((1, 2, 3)), rows, day=3)['detail_followups']
    assert q.queue_state.tolist() == ['first_check']
    assert q.followup_reason.tolist() == ['native_status_changed']
    assert q.selected_for_check.all()


def test_new_listing_does_not_inherit_old_listing_check():
    rows = observations((1, 'V1', 'L1'), (3, 'V1', 'L2'))
    q = tables(cycles((1, 2, 3)), rows, day=3, checks=pd.DataFrame([check()]))['detail_followups']
    assert q.listing_id.tolist() == ['L2'] and q.detail_status.tolist() == ['not_checked']
    assert q.check_id.isna().all() and q.selected_for_check.all()


def test_reappearance_after_check_is_prioritized_without_waiting_for_interval():
    rows = observations((1, 'V1', 'L1'), (3, 'V1', 'L1'))
    q = tables(cycles((1, 2, 3)), rows, day=3, checks=pd.DataFrame([check()]))['detail_followups']
    assert q.queue_state.tolist() == ['changed_since_check'] and q.selected_for_check.all()


def test_check_availability_corrections_and_old_page_evidence():
    first = check()
    corrected = check(check_id='correction', available_at='2026-09-03T17:00Z', observed_status='unknown')
    stale = check(check_id='late-old-page', checked_at='2026-09-01T16:00Z', available_at='2026-09-04T17:00Z')
    rows = pd.DataFrame([first, corrected, stale])
    assert select_checks(rows, as_of='2026-09-02T16:30Z').empty
    assert select_checks(rows, as_of='2026-09-02T23:00Z').observed_status.tolist() == ['unavailable']
    assert select_checks(rows, as_of='2026-09-04T23:00Z').observed_status.tolist() == ['unknown']
    with pytest.raises(ValueError, match='Conflicting'):
        validate_checks(pd.DataFrame([first, dict(first, check_id='other-id', observed_status='unknown')]))


def test_fractional_timestamps_and_equivalent_offsets_are_supported(tmp_path):
    data = pd.DataFrame([check('V1', 'L1'), check('V2', 'L2',
        checked_at='2026-09-02T16:00:00.123Z', available_at='2026-09-02T17:00:00.456Z')])
    result = tables(cycles(), observations((1, 'V1', 'L1'), (1, 'V2', 'L2')), checks=data)
    assert len(result['listing_checks']) == 2 and result['detail_followups'].selected_for_check.all()
    path = tmp_path/'checks.csv'
    assert append_record(path, check(), kind='check')
    assert not append_record(path, check(checked_at='2026-09-02T12:00:00-04:00',
        available_at='2026-09-02T13:00:00-04:00'), kind='check')


@pytest.mark.parametrize('status', ['available', 'pending', 'sold_label', 'unavailable', 'access_blocked', 'unknown'])
def test_check_status_is_not_a_sale_review(status):
    result = tables(cycles(), observations((1, 'V1', 'L1')), checks=pd.DataFrame([check(observed_status=status)]))
    assert result['listing_checks'].observed_status.tolist() == [status]
    assert result['sale_candidates'].review_outcome.eq('unreviewed').all()
    assert result['daily_inventory'].reviewed_sales_with_known_date.eq(0).all()
    assert result['daily_inventory'].estimated_sales.isna().all()
    assert result['daily_inventory'].site_marked_sold.isna().all()


def test_review_revision_and_return_preserve_prior_cutoff(tmp_path):
    days, rows = cycles((1, 2, 3, 4, 5)), observations((1, 'V1', 'L1'), (5, 'V1', 'L2'))
    candidates, _ = sale_candidates(days, rows, as_of='2026-09-04T23:00Z')
    candidate = candidates.candidate_id.iloc[0]
    path = tmp_path/'reviews.csv'
    append_record(path, review(candidate), kind='review')
    original = read_records(path, REVIEW_COLUMNS)
    returned = tables(days, rows, day=5, reviews=original)
    assert returned['sale_candidates'].review_needs_followup.all()
    assert returned['sale_candidates'].review_outcome.eq('confirmed_sale').all()
    append_record(path, review(candidate, outcome='not_sale', sale_date=None,
        available_at='2026-09-05T18:00Z', note='Synthetic correction'), kind='review')
    history = read_records(path, REVIEW_COLUMNS)
    old = tables(days, rows, day=4, reviews=history)
    pd.testing.assert_frame_equal(old['sale_candidates'], tables(days, rows, day=4, reviews=original)['sale_candidates'])
    assert old['daily_inventory'].reviewed_sales_with_known_date.sum() == 1
    updated = tables(days, rows, day=5, reviews=history)
    assert updated['sale_candidates'].review_outcome.eq('not_sale').all()
    assert updated['daily_inventory'].reviewed_sales_with_known_date.sum() == 0 and len(history) == 2


@pytest.mark.parametrize('change', [dict(observed_status='not_checked'), dict(checked_at='2026-09-03T00:00Z'),
    dict(available_at='2026-09-02'), dict(native_text=''), dict(source=''), dict(listing_id=123)])
def test_invalid_check_is_rejected_without_writing(tmp_path, change):
    path = tmp_path/'checks.csv'
    with pytest.raises(ValueError):
        append_record(path, check(**change), kind='check')
    assert not path.exists()


def test_command_records_and_refreshes_without_collecting(settings, clock, response_data, monkeypatch, tmp_path):
    daily.run_tracking(settings, live=True, post=Mock(return_value=reply(response_data)))  # Offline transport fixture.
    _, rows = daily.tracking_history(settings, as_of=clock.isoformat())
    row = rows.iloc[0]
    evidence = tmp_path/'retained-page.txt'
    evidence.write_text('Temporary page fixture: This vehicle is no longer available')
    record = check(row.vin, row.listing_id, checked_at=clock.isoformat(), available_at=clock.isoformat(), source=str(evidence))
    source = tmp_path/'check.json'
    source.write_text(json.dumps(record), encoding='utf-8-sig')
    module_path = Path(__file__).parents[1]/'scripts/run_carvana_daily.py'
    spec = importlib.util.spec_from_file_location('record_command', module_path)
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    monkeypatch.setattr(daily, 'collect_cycle', Mock(side_effect=AssertionError('No requests')))
    protected = {p:p.read_bytes() for p in [settings['database'], settings['register'], *settings['capture_root'].rglob('*')] if p.is_file()}
    args = ['--config', str(settings['config_path'])]
    assert command.main(args+['--record-check', str(source)]) == 0
    assert command.main(args+['--record-check', str(source)]) == 0
    assert len(read_records(settings['checks'], CHECK_COLUMNS)) == 1
    before_exports = set(settings['exports'].iterdir())
    assert command.main(args+['--refresh']) == 0
    exported, = set(settings['exports'].iterdir()) - before_exports
    manifest = json.loads((exported/'manifest.json').read_text())
    assert manifest['review_inputs'][str(settings['checks'])] == daily.digest(settings['checks'])
    assert 'listing_checks.csv' in manifest['outputs'] and 'selected_reviews.csv' in manifest['outputs']
    assert all(p.read_bytes()==before for p,before in protected.items())
    bad = dict(record, check_id='unknown-vin', vin='UNKNOWN')
    source.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match='observed retailer'):
        command.main(args+['--record-check', str(source)])
    source.write_text(json.dumps(dict(record, check_id='future', available_at='2099-01-01T00:00Z')))
    with pytest.raises(ValueError, match='future availability'):
        command.main(args+['--record-check', str(source)])


def test_record_review_requires_a_real_candidate_in_selected_evidence(settings, monkeypatch, tmp_path):
    days, rows = cycles(), observations((1, 'V1', 'L1'))
    monkeypatch.setattr(daily, 'tracking_history', lambda *args, **kwargs: (days, rows))
    candidate = sale_candidates(days, rows, as_of='2026-09-04T23:00Z')[0].candidate_id.iloc[0]
    path = tmp_path/'review.json'
    path.write_text(json.dumps(review(candidate)))
    assert daily.record_evidence(settings, path, kind='review')
    assert not daily.record_evidence(settings, path, kind='review')
    path.write_text(json.dumps(review('unknown')))
    with pytest.raises(ValueError, match='Unknown candidate'):
        daily.record_evidence(settings, path, kind='review')


def test_notebook_and_command_share_saved_checks_reviews_and_cutoff(settings, monkeypatch):
    from test_daily_notebook import checker, daily_code
    days = cycles((1, 2, 3, 4, 5))
    rows = observations((1, 'V1', 'L1'), (5, 'V1', 'L2')).assign(source_path='synthetic://source')
    monkeypatch.setattr(daily, 'tracking_history', lambda *args, **kwargs: (days, rows))
    cutoff = '2026-09-04T23:00:00Z'
    monkeypatch.setattr(daily, 'utcnow', lambda: pd.Timestamp(cutoff).to_pydatetime())
    candidate = sale_candidates(days, rows, as_of=cutoff)[0].candidate_id.iloc[0]
    append_record(settings['checks'], check(), kind='check')
    append_record(settings['reviews'], review(candidate), kind='review')
    append_record(settings['reviews'], review(candidate, outcome='not_sale', sale_date=None,
        available_at='2026-09-05T18:00Z'), kind='review')
    protected = {p:p.read_bytes() for p in [settings['checks'], settings['reviews']]}
    _, folder, command_tables = daily.run_tracking(settings, refresh=True)
    scope = dict(pd=pd, Path=Path, ROOT=Path(__file__).parents[1], display=lambda *args: None,
        TRACKING_CONFIG_OVERRIDE=settings['config_path'], AS_OF_OVERRIDE=cutoff)
    code = daily_code()
    with checker.offline_guards():
        exec(code['daily-operating-view'], scope)
        scope.update(daily_settings=settings, daily_cycles=days, daily_observations=rows, AS_OF=cutoff)
        exec(code['daily-operating-tables'], scope)
        exec(code['sale-review-data'], scope)
        for name, frame in command_tables.items():
            pd.testing.assert_frame_equal(frame, scope['tracking_tables'][name])
        pd.testing.assert_frame_equal(scope['sale_candidate_rows'], command_tables['sale_candidates'])
        scope['SALES_REVIEWS_OVERRIDE'] = []
        exec(code['daily-operating-view'], scope)
        exec(code['daily-operating-tables'], scope)
        exec(code['sale-review-data'], scope)
    assert command_tables['daily_inventory'].reviewed_sales_with_known_date.sum() == 1
    assert scope['operating_candidates'].review_outcome.eq('unreviewed').all()
    assert scope['sale_candidate_rows'].review_outcome.eq('unreviewed').all()
    assert all(p.read_bytes()==before for p,before in protected.items())
    assert pd.read_csv(folder/'selected_reviews.csv').outcome.tolist() == ['confirmed_sale']
