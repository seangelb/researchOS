"""Small follow-up tables execute offline against temporary, invented observations."""
import json
from pathlib import Path

import pandas as pd
import pytest

from test_sale_pilot import (
    captures, cohort, extension, extension_record, parse, run_pilot_book,
)

BOOK = Path(__file__).resolve().parents[1] / 'notebooks/22_carvana_sale_status_validation.ipynb'
CUTOFF = '2026-09-11T12:00:00Z'


def rerun_selection(scope, **overrides):
    """Exercise the real settings and visible pandas selection cells, without writes."""
    from test_daily_notebook import checker
    scope = dict(scope, **overrides)
    cells = {c['id']: ''.join(c['source']) for c in json.loads(BOOK.read_text(encoding='utf-8'))['cells']}
    with checker.offline_guards():
        for identifier in ['pilot-settings', 'prospective-followup-plan']:
            exec(compile(cells[identifier], identifier, 'exec'), scope)
    return scope


def assert_accounting(scope):
    plan, batch, rest = (scope[name] for name in ['followup_plan', 'next_followup_batch', 'followup_remainder'])
    identity = ['cohort_id', 'retailer', 'vin']
    keys = lambda frame: set(frame[identity].itertuples(index=False, name=None))
    assert not plan.duplicated(identity).any()
    assert keys(batch).isdisjoint(keys(rest))
    assert keys(batch) | keys(rest) == keys(plan)
    assert len(batch) + len(rest) == len(plan)
    assert batch.eligible_for_batch.all() and batch.selected_for_batch.all()
    assert batch.deferred_reason.eq('').all()
    assert not rest.selected_for_batch.any() and rest.deferred_reason.str.len().gt(0).all()


@pytest.mark.parametrize('attempted_extension_vins', [17, 19])
def test_followup_groups_partition_membership_with_first_checks_then_oldest_due(
        tmp_path, monkeypatch, captures, cohort, extension, attempted_extension_vins):
    # Vary baseline coverage: the table must derive its counts from observations.
    extension_rows = [extension_record(captures, vehicle, '2026-09-10T01:00:00Z')
        for vehicle in extension['vehicles'][:attempted_extension_vins]]
    extension_rows[4].update(saleStatus='Sold', purchaseType='NotPurchasable',
        observed_status='sold_label', hero_badge='Sold', purchase_button=None)
    extension_rows[-1] = extension_record(captures,
        extension['vehicles'][attempted_extension_vins - 1],
        '2026-09-10T02:00:00Z', 'access_failed')
    records = {cohort['cohort_id']: pd.DataFrame([parse(capture) for capture in captures]),
        extension['cohort_id']: pd.DataFrame(extension_rows)}
    scope = run_pilot_book(tmp_path, monkeypatch, [cohort, extension], records)
    plan, batch, remainder = [scope[name] for name in
        ['followup_plan', 'next_followup_batch', 'followup_remainder']]

    selected_vins = {vehicle['vin'] for group in [cohort, extension]
        for vehicle in group['vehicles']}
    assert set(plan.vin) == selected_vins and plan.vin.is_unique
    assert len(batch) == 12 and batch.vin.is_unique
    assert set(batch.vin).isdisjoint(remainder.vin)
    assert set(batch.vin) | set(remainder.vin) == selected_vins
    assert batch.followup_url.notna().all() and batch.check_reason.str.len().gt(0).all()
    first_count = len(extension['vehicles']) - attempted_extension_vins
    assert batch.followup_group.iloc[:first_count].eq('no visit').all()
    assert batch.vin.iloc[first_count:].tolist() == [capture['expected']['vin']
        for capture in sorted(captures, key=lambda c: c['checked_at'])[:12 - first_count]]
    assert batch.eligible_for_batch.all() and batch.deferred_reason.eq('').all()
    assert remainder.deferred_reason.str.len().gt(0).all()

    counts = plan.followup_group.value_counts()
    assert counts['no visit'] == len(extension['vehicles']) - attempted_extension_vins
    assert counts['attempted: no usable native evidence'] == 1
    controls = plan.loc[plan.followup_group.eq('initially Sold or historical control')]
    expected_controls = {vehicle['vin'] for vehicle in cohort['vehicles']
        if vehicle['role'] == 'historical_control'} | {extension['vehicles'][4]['vin']}
    assert set(controls.vin) == expected_controls
    assert set(controls.vin) & set(batch.vin)  # Due controls are no longer deferred by their group.
    failed = plan.loc[plan.vin.eq(extension_rows[-1]['vin'])].iloc[0]
    assert pd.isna(failed.last_usable_native_at) and pd.notna(failed.last_attempt_at)
    assert pd.isna(failed.next_due_at) and failed.needs_review
    assert not failed.eligible_for_batch and not failed.repeat_due
    assert 'no usable native evidence' in failed.deferred_reason


def test_followup_preserves_new_verified_listing_after_failed_attempt(
        tmp_path, monkeypatch, captures, extension):
    vehicle = extension['vehicles'][0]
    usable = extension_record(captures, vehicle, '2026-09-10T01:00:00Z')
    newer_url = 'https://www.carvana.com/vehicle/9999999'
    usable.update(listing_id='9999999', observed_listing_id='9999999',
        requested_url=newer_url, final_url=newer_url)
    failed = extension_record(captures, vehicle, '2026-09-10T02:00:00Z', 'access_failed')
    scope = run_pilot_book(tmp_path, monkeypatch, [extension],
        {extension['cohort_id']: pd.DataFrame([usable, failed])})
    target = scope['followup_plan'].loc[lambda rows: rows.vin.eq(vehicle['vin'])].iloc[0]
    assert target.original_url == vehicle['url']
    assert target.followup_url == newer_url and target.followup_listing_id == '9999999'
    assert target.last_usable_native_at == pd.Timestamp(usable['checked_at'])
    assert target.last_attempt_at == pd.Timestamp(failed['checked_at'])
    assert target.followup_group == 'initially non-Sold: repeat observation'
    assert target.next_due_at == pd.Timestamp('2026-09-11T01:00:00Z')
    assert target.needs_review and not target.eligible_for_batch
    assert 'native clock unchanged' in target.deferred_reason


def test_displayed_study_observation_does_not_replace_native_pilot_evidence(
        tmp_path, monkeypatch, captures, cohort):
    pilot = parse(captures[4])
    folder = tmp_path / 'data/experiments/vehicle_history_study/20260910T034129Z'
    folder.mkdir(parents=True)
    study = dict(retailer=pilot['retailer'], vin=pilot['vin'], listing_id='8888888',
        website_status='Sold', checked_at='2026-09-10T03:00:00Z',
        available_at='2026-09-10T04:00:00Z', cohort_member=True,
        source_url='https://www.carvana.com/vehicle/8888888')
    (folder / 'detail_observations.json').write_text(json.dumps([study]), encoding='utf-8')
    scope = run_pilot_book(tmp_path, monkeypatch, [cohort],
        {cohort['cohort_id']: pd.DataFrame([pilot])})

    columns = {'page_listing_id', 'website_status', 'website_checked_at',
        'study_listing_id', 'study_website_status', 'study_checked_at'}
    # These are the explicit columns passed to display in the comparison cell.
    assert columns.issubset(scope['display_columns'])
    row = scope['vehicle_history_comparison'].loc[lambda rows: rows.vin.eq(pilot['vin'])].iloc[0]
    assert row.page_listing_id == pilot['listing_id'] and row.website_status == 'available'
    assert row.website_checked_at == pd.Timestamp(pilot['checked_at'])
    assert row.study_listing_id == '8888888' and row.study_website_status == 'Sold'
    assert row.study_checked_at == pd.Timestamp(study['checked_at'])
    assert len(scope['pilot_observations']) == 1 and scope['newly_observed_sold'].empty
    assert scope['pilot_summary'].checks.sum() == 1
    followup = scope['followup_plan'].loc[lambda rows: rows.vin.eq(pilot['vin'])].iloc[0]
    assert followup.followup_listing_id == pilot['listing_id']


def test_exactly_due_and_not_yet_due_do_not_fill_unused_slots(tmp_path, monkeypatch, captures, extension):
    extension['vehicles'] = extension['vehicles'][:3]
    times = ['2026-09-10T11:00:00Z', '2026-09-10T12:00:00Z', '2026-09-10T12:00:00.000001Z']
    rows = [extension_record(captures, v, time) for v, time in zip(extension['vehicles'], times)]
    scope = run_pilot_book(tmp_path, monkeypatch, [extension],
        {extension['cohort_id']: pd.DataFrame(rows)}, cutoff=CUTOFF)
    batch, rest = scope['next_followup_batch'], scope['followup_remainder']
    assert batch.vin.tolist() == [v['vin'] for v in extension['vehicles'][:2]]
    assert batch.next_due_at.iloc[-1] == pd.Timestamp(CUTOFF)  # Inclusive boundary.
    assert len(rest) == 1 and not rest.repeat_due.iloc[0]
    assert rest.deferred_reason.iloc[0] == 'Not yet due; wait until next_due_at'
    assert_accounting(scope)
    # A configured longer interval is used directly; zero eligible means zero selected.
    later = rerun_selection(scope, PILOT_RECHECK_HOURS_OVERRIDE=48)
    assert later['next_followup_batch'].empty and len(later['followup_remainder']) == 3
    assert_accounting(later)


def test_due_controls_precede_more_recent_prospective_checks(tmp_path, monkeypatch, captures, extension):
    extension['vehicles'] = extension['vehicles'][:4]
    extension['vehicles'][0]['role'] = 'historical_control'
    rows = [extension_record(captures, v, time) for v, time in zip(extension['vehicles'],
        ['2026-09-10T08:00Z', '2026-09-10T11:00Z', '2026-09-11T09:00Z', '2026-09-10T09:00Z'])]
    rows[3].update(saleStatus='Sold', purchaseType='NotPurchasable', observed_status='sold_label',
        hero_badge='Sold', purchase_button=None)  # Synthetic initially-Sold control.
    scope = run_pilot_book(tmp_path, monkeypatch, [extension],
        {extension['cohort_id']: pd.DataFrame(rows)}, cutoff=CUTOFF)
    limited = rerun_selection(scope, FOLLOWUP_BATCH_LIMIT_OVERRIDE=2)
    assert limited['next_followup_batch'].vin.tolist() == [rows[0]['vin'], rows[3]['vin']]
    assert limited['next_followup_batch'].followup_group.eq('initially Sold or historical control').all()
    overflow = limited['followup_remainder'].set_index('vin').loc[rows[1]['vin']]
    assert overflow.eligible_for_batch and 'batch limit reached' in overflow.deferred_reason
    assert_accounting(limited)


@pytest.mark.parametrize('failure', ['access_failed', 'conflicting_status', 'identity_mismatch'])
def test_first_checks_and_failed_attempts_keep_distinct_eligibility(
        tmp_path, monkeypatch, captures, extension, failure):
    extension['vehicles'] = extension['vehicles'][:3]
    old = extension_record(captures, extension['vehicles'][2], '2026-09-10T08:00Z')
    failed = [extension_record(captures, v, '2026-09-11T11:00Z', 'access_failed')
        for v in extension['vehicles'][1:]]
    for row in failed:
        row.update(parse_outcome=failure, observed_status='access_blocked' if failure == 'access_failed' else 'unknown')
    scope = run_pilot_book(tmp_path, monkeypatch, [extension],
        {extension['cohort_id']: pd.DataFrame([old, *failed])}, cutoff=CUTOFF)
    plan = scope['followup_plan'].set_index('vin')
    never, no_native, earlier_native = [plan.loc[v['vin']] for v in extension['vehicles']]
    assert never.eligible_for_batch and pd.isna(never.next_due_at) and not never.needs_review
    assert no_native.needs_review and not no_native.eligible_for_batch and pd.isna(no_native.next_due_at)
    assert no_native.followup_group == 'attempted: no usable native evidence'
    assert earlier_native.last_usable_native_at == pd.Timestamp('2026-09-10T08:00Z')
    assert earlier_native.next_due_at == pd.Timestamp('2026-09-11T08:00Z')
    assert earlier_native.repeat_due and earlier_native.needs_review and not earlier_native.eligible_for_batch
    assert earlier_native.last_attempt_at == pd.Timestamp('2026-09-11T11:00Z')
    assert scope['next_followup_batch'].vin.tolist() == [extension['vehicles'][0]['vin']]
    assert_accounting(scope)


def test_selection_is_deterministic_after_input_rows_are_shuffled(tmp_path, monkeypatch, captures, extension):
    extension['vehicles'] = extension['vehicles'][:6]
    rows = [extension_record(captures, v, '2026-09-10T12:00Z') for v in extension['vehicles'][:4]]
    scope = run_pilot_book(tmp_path, monkeypatch, [extension],
        {extension['cohort_id']: pd.DataFrame(rows)}, cutoff=CUTOFF)
    expected_first = sorted(v['vin'] for v in extension['vehicles'][4:])
    expected_repeats = sorted(v['vin'] for v in extension['vehicles'][:4])
    assert scope['next_followup_batch'].vin.tolist() == expected_first + expected_repeats
    for seed in [5, 19]:
        shuffled = dict(scope, pilot_summary=scope['pilot_summary'].sample(frac=1, random_state=seed),
            history_followups=scope['history_followups'].sample(frac=1, random_state=seed + 1))
        result = rerun_selection(shuffled, FOLLOWUP_BATCH_LIMIT_OVERRIDE=3)
        assert result['next_followup_batch'].vin.tolist() == expected_first + expected_repeats[:1]
        assert result['followup_remainder'].vin.tolist() == expected_repeats[1:]
        assert_accounting(result)


def test_unresolved_url_is_review_only_and_future_cohort_is_excluded(tmp_path, monkeypatch, captures, extension):
    extension['vehicles'] = extension['vehicles'][:1]
    scope = run_pilot_book(tmp_path, monkeypatch, [extension], cutoff=CUTOFF)
    unresolved = dict(scope, history_followups=scope['history_followups'].assign(followup_url=None))
    result = rerun_selection(unresolved)
    assert result['next_followup_batch'].empty
    assert result['followup_plan'].needs_review.all()
    assert result['followup_remainder'].deferred_reason.str.contains('unresolved follow-up identity').all()
    assert_accounting(result)
    extension['selected_at'] = '2026-09-12T00:00Z'
    future = run_pilot_book(tmp_path, monkeypatch, [extension], cutoff=CUTOFF)
    assert future['followup_plan'].empty and future['next_followup_batch'].empty


@pytest.mark.parametrize('limit', [0, 13, 33, -1, True, 2.0, '12', None])
def test_single_pass_batch_limit_rejects_invalid_values(limit):
    scope = dict(pd=pd, Path=Path, ROOT=BOOK.parents[1], AS_OF_OVERRIDE=CUTOFF,
        display=lambda *args: None, pilot_summary=pd.DataFrame())
    with pytest.raises(ValueError, match='1 to 12'):
        rerun_selection(scope, FOLLOWUP_BATCH_LIMIT_OVERRIDE=limit)


@pytest.mark.parametrize('limit', [1, 12])
def test_single_pass_limit_boundaries_and_full_accounting(tmp_path, monkeypatch, extension, limit):
    scope = run_pilot_book(tmp_path, monkeypatch, [extension], cutoff=CUTOFF)
    result = rerun_selection(scope, FOLLOWUP_BATCH_LIMIT_OVERRIDE=limit)
    assert len(result['next_followup_batch']) == limit
    assert len(result['followup_plan']) == len(extension['vehicles'])
    assert_accounting(result)


def test_late_available_check_does_not_reset_known_due_time(tmp_path, monkeypatch, captures, extension):
    extension['vehicles'] = extension['vehicles'][:1]
    old = extension_record(captures, extension['vehicles'][0], '2026-09-10T08:00Z')
    later = extension_record(captures, extension['vehicles'][0], '2026-09-11T11:00Z')
    later['available_at'] = '2026-09-11T13:00Z'
    scope = run_pilot_book(tmp_path, monkeypatch, [extension],
        {extension['cohort_id']: pd.DataFrame([old, later])}, cutoff=CUTOFF)
    row = scope['next_followup_batch'].iloc[0]
    assert row.checks == 1 and row.next_due_at == pd.Timestamp('2026-09-11T08:00Z')
    assert_accounting(scope)
