"""Exercise the daily notebook as a reader, then its manual commands in temporary storage."""
import builtins
import io
import json
from pathlib import Path
import re
from unittest.mock import Mock

import pandas as pd
import pytest

from test_daily_cycles import clock, reply
from test_daily_tracking import settings
from test_daily_notebook import checker
from test_search import response_data
from vehicle_tracker import daily

VEHICLE = Path(__file__).resolve().parents[1]
BOOK = VEHICLE / 'notebooks/20_carvana_history_analysis.ipynb'


def book():
    return json.loads(BOOK.read_text(encoding='utf-8'))


def hashes(root):
    return {str(p.relative_to(root)): daily.digest(p) for p in root.rglob('*') if p.is_file()}


def run_all(monkeypatch, settings, *, as_of, scope=None):
    """Block low-level file writes too, including with filled entry globals."""
    scope = dict(scope or {}, TRACKING_CONFIG_OVERRIDE=settings['config_path'], AS_OF_OVERRIDE=as_of)
    original_open, original_io_open = builtins.open, io.open

    def guard_open(original):
        def guarded(file, mode='r', *args, **kwargs):
            assert not any(flag in mode for flag in 'wax+'), 'Run All attempted a file write'
            return original(file, mode, *args, **kwargs)
        return guarded

    deny = Mock(side_effect=AssertionError('Run All attempted a mutation'))
    monkeypatch.chdir(VEHICLE)
    with monkeypatch.context() as guarded, checker.offline_guards():
        guarded.setattr(builtins, 'open', guard_open(original_open))
        guarded.setattr(io, 'open', guard_open(original_io_open))
        for name in ['mkdir', 'write_text', 'write_bytes', 'replace', 'unlink']:
            guarded.setattr(Path, name, deny)
        guarded.setattr(daily, 'record_evidence', deny)
        guarded.setattr(daily, 'run_tracking', deny)
        guarded.setattr(daily, 'append_record', deny)
        for cell in book()['cells']:
            if cell['cell_type'] == 'code':
                exec(compile(''.join(cell['source']), cell['id'], 'exec'), scope)
    return scope


@pytest.mark.parametrize('scenario', ['empty', 'baseline', 'partial', 'trailing_gap'])
def test_full_notebook_keeps_data_unchanged_and_explains_missing_comparison(
        monkeypatch, tmp_path, settings, response_data, clock, scenario):
    if scenario != 'empty':
        response = reply(response_data, status=403 if scenario == 'partial' else 200)
        daily.run_tracking(settings, live=True, post=Mock(return_value=response))
    before = hashes(tmp_path)
    cutoff = '2026-09-09T23:00:00Z' if scenario == 'trailing_gap' else '2026-09-08T23:00:00Z'
    scope = run_all(monkeypatch, settings, as_of=cutoff)
    assert hashes(tmp_path) == before
    assert not scope['manual_recording_allowed']  # Nothing auto-selected.
    assert scope['selected_vehicle'].empty
    assert not scope['daily_comparison_allowed']
    if scenario == 'empty':
        assert scope['daily_review'].empty
    else:
        review = scope['daily_review'].iloc[0]
        assert pd.isna(review.new_vins) and pd.isna(review.sales_estimate)
        if scenario in ['partial', 'trailing_gap']:
            assert pd.isna(review.inventory_count)
        else:
            assert review.inventory_count == 3
            assert scope['detail_followups'].empty
            assert len(scope['selection_options']) == 3
    assert scope['synthetic_history_before'].observed_status.eq('sold_label').sum() == 0
    assert scope['synthetic_history_after'].observed_status.eq('sold_label').sum() == 1
    assert scope['synthetic_history_after'].reappeared_after_absence.eq(True).any()
    assert not settings['checks'].exists()


def test_baseline_selection_and_filled_draft_still_do_not_write(
        monkeypatch, tmp_path, settings, response_data, clock):
    daily.run_tracking(settings, live=True, post=Mock(return_value=reply(response_data)))
    _, rows = daily.tracking_history(settings, as_of='2026-09-08T23:00:00Z')
    identity = rows.iloc[0][['retailer', 'vin', 'listing_id']].to_dict()
    draft = dict(checked_at='2026-09-08T13:00:00Z', observed_status='pending',
        native_text='Purchase in progress', source=str(tmp_path/'page.txt'),
        reviewer='Temporary test analyst', note='Temporary evidence only')
    before = hashes(tmp_path)
    scope = run_all(monkeypatch, settings, as_of='2026-09-08T23:00:00Z', scope=dict(
        SELECTED_IDENTITY_OVERRIDE=identity, CHECK_DRAFT_OVERRIDE=draft,
        prepared_check={'stale': 'must never be saved by Run All'}))
    assert hashes(tmp_path) == before and not settings['checks'].exists()
    assert len(scope['selected_vehicle']) == 1
    assert scope['selected_vin_history'].vin.eq(identity['vin']).all()
    assert scope['stored_example'].vin.tolist() == [identity['vin']]
    assert scope['source_example'].vin.tolist() == [identity['vin']]
    price_trace = scope['evidence_trace'].set_index('source_field').loc['price.total']
    assert price_trace.retained_value == price_trace.sqlite_value
    assert scope['stored_example'].capture_id.iloc[0] == scope['selected_vehicle'].capture_id.iloc[0]
    assert scope['detail_followups'].empty  # A baseline control can be selected.
    assert not scope['manual_recording_allowed']  # Overrides isolate tests/examples.


@pytest.mark.parametrize('identity', [dict(retailer='carvana', vin='V', listing_id=''),
                                    dict(retailer='carvana', vin='V', listing_id='nonexistent')])
def test_incomplete_or_unmatched_selection_explained(
        monkeypatch, settings, response_data, clock, identity):
    daily.run_tracking(settings, live=True, post=Mock(return_value=reply(response_data)))
    scope = run_all(monkeypatch, settings, as_of='2026-09-08T23:00:00Z',
        scope={'SELECTED_IDENTITY_OVERRIDE': identity})
    assert scope['selection_error'] and scope['selected_vehicle'].empty


def test_actual_manual_markdown_commands_prepare_save_and_replay(
        monkeypatch, tmp_path, settings, response_data, clock):
    daily.run_tracking(settings, live=True, post=Mock(return_value=reply(response_data)))
    _, rows = daily.tracking_history(settings, as_of='2026-09-08T23:00:00Z')
    evidence = tmp_path/'page.txt'
    evidence.write_text('Temporary test page: Purchase in progress')
    identity = rows.iloc[0][['retailer', 'vin', 'listing_id']].to_dict()
    scope = run_all(monkeypatch, settings, as_of='2026-09-08T23:00:00Z')
    scope.update(SELECTED_IDENTITY=identity, manual_recording_allowed=True,
        CHECK_DRAFT=dict(checked_at='2026-09-08T13:00:00Z', observed_status='pending',
            native_text='Purchase in progress', source=str(evidence),
            reviewer='Temporary test analyst', note='Temporary fixture; not a real page check'))
    monkeypatch.setattr(daily, 'utcnow', lambda: pd.Timestamp.now(tz='UTC'))
    scope['record_evidence'] = daily.record_evidence  # Replace Run All's deny stub.
    instructions = next(c for c in book()['cells'] if c['id'] == 'daily-manual-save')
    prepare, save = re.findall(r'```python\n(.*?)```', ''.join(instructions['source']), re.S)
    protected = hashes(tmp_path)
    exec(prepare, scope)
    assert hashes(tmp_path) == protected
    exec(save, scope)
    first = settings['checks'].read_bytes()
    exec(save, scope)
    assert settings['checks'].read_bytes() == first and not scope['changed']
    after = hashes(tmp_path)
    assert all(after[p] == digest for p, digest in protected.items())
    assert set(after) - set(protected) == {str(settings['checks'].relative_to(tmp_path))}


@pytest.mark.parametrize('override', ['disabled_operating_view', 'explicit_cycles_current_cutoff'])
def test_alternate_retained_cycle_views_use_one_consistent_cutoff(
        monkeypatch, settings, response_data, clock, override):
    daily.run_tracking(settings, live=True, post=Mock(return_value=reply(response_data)))
    entry = daily.registered_cycles(settings)[0]
    _, rows = daily.tracking_history(settings, as_of='2026-09-08T23:00:00Z')
    inputs = dict(CYCLE_REPORTS_OVERRIDE=[entry['path']], DAILY_DATABASE_OVERRIDE=settings['database'],
                  SELECTED_IDENTITY_OVERRIDE=rows.iloc[0][['retailer', 'vin', 'listing_id']].to_dict())
    if override == 'disabled_operating_view':
        inputs['RUN_TRACKING_VIEW_OVERRIDE'] = False
    # run_all supplies AS_OF_OVERRIDE; its value must agree throughout the notebook.
    scope = run_all(monkeypatch, settings, as_of='2026-09-08T23:00:00Z', scope=inputs)
    assert len(scope['daily_observations']) == 3
    assert scope['AS_OF'] == scope['TRACKING_AS_OF']
    assert len(scope['selected_vehicle']) == 1
    assert not scope['selected_vin_history'].empty
    assert not scope['manual_recording_allowed']


def test_explicit_cycle_selection_without_cutoff_does_not_use_old_hardcoded_date(
        monkeypatch, settings, response_data, clock):
    daily.run_tracking(settings, live=True, post=Mock(return_value=reply(response_data)))
    entry = daily.registered_cycles(settings)[0]
    scope = dict(TRACKING_CONFIG_OVERRIDE=settings['config_path'],
                 CYCLE_REPORTS_OVERRIDE=[entry['path']], DAILY_DATABASE_OVERRIDE=settings['database'])
    monkeypatch.chdir(VEHICLE)
    with checker.offline_guards():
        for c in book()['cells']:
            if c['cell_type'] == 'code':
                exec(compile(''.join(c['source']), c['id'], 'exec'), scope)
    assert len(scope['daily_observations']) == 3
    assert scope['AS_OF'] == scope['TRACKING_AS_OF']


def test_population_display_accepts_implicit_location_filter_false(
        monkeypatch, settings, response_data, clock):
    settings['queries'][0].pop('location_filter')
    settings['plan'].write_text(json.dumps({'queries': settings['queries']}))
    daily.run_tracking(settings, live=True, post=Mock(return_value=reply(response_data)))
    scope = run_all(monkeypatch, settings, as_of='2026-09-08T23:00:00Z')
    assert scope['population'].location_filter.tolist() == [False]
    assert scope['query_quality'].coverage_complete.all()
