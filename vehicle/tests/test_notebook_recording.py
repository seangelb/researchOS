"""The manual notebook entry path shares the CLI writer; all saves use temporary storage."""
import copy
import json
from unittest.mock import Mock

import pandas as pd
import pytest

from test_daily_cycles import clock
from test_daily_tracking import settings
from test_daily_events import cycles, observations
from vehicle_tracker import daily
from vehicle_tracker.checks import CHECK_COLUMNS, read_records, select_checks


def draft(**changes):
    return dict(dict(checked_at='2026-09-02T16:00:00Z', observed_status='pending',
        native_text='Purchase in progress', source='retained/page-check.txt',
        reviewer='Test analyst', note='Temporary test observation; no real evidence is written'), **changes)


def prepare(rows=None, **changes):
    inputs = dict(retailer='carvana', vin='V1', listing_id='L1', draft=draft(),
                  available_at='2026-09-02T17:00:00Z')
    inputs.update(changes)
    return daily.prepare_check(observations((1, 'V1', 'L1')) if rows is None else rows, **inputs)


def test_preparation_is_pure_and_keeps_explicit_times_and_stable_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, 'utcnow', Mock(side_effect=AssertionError('No implicit clock')))
    rows = observations((1, 'V1', 'L1'), (3, 'V1', 'L2'))
    original = rows.copy(deep=True)
    entered = draft()
    entered_before = copy.deepcopy(entered)
    first = prepare(rows, draft=entered)
    same = prepare(rows, draft=draft(checked_at='2026-09-02T12:00:00-04:00'),
                   available_at='2026-09-02T13:00:00-04:00')
    assert first == same
    assert first['listing_id'] == 'L1' and first['check_id'].startswith('prepared-')
    assert first['checked_at'] != first['available_at']
    assert entered == entered_before and not list(tmp_path.iterdir())
    pd.testing.assert_frame_equal(rows, original)


@pytest.mark.parametrize('changes', [dict(retailer='other'), dict(vin='V2'),
    dict(listing_id='missing'), dict(listing_id=None), dict(vin='<VIN>'),
    dict(draft=draft(checked_at='2026-08-31T12:00:00Z'))])
def test_unknown_missing_and_predating_identities_are_rejected(changes):
    with pytest.raises(ValueError):
        prepare(**changes)


def test_empty_history_and_ambiguous_listing_identity_are_rejected():
    with pytest.raises(ValueError, match='empty'):
        prepare(pd.DataFrame())
    with pytest.raises(ValueError, match='unambiguous'):
        prepare(observations((1, 'V1', 'L1'), (2, 'V2', 'L1')))


@pytest.mark.parametrize('entered', [{}, draft(reviewer=''), draft(source='<retained evidence>'),
    draft(note='TODO'), draft(source='synthetic://lesson'), draft(checked_at='2026-09-02'),
    draft(observed_status=''), draft(native_text=''), dict(draft(), vin='V2')])
def test_incomplete_placeholder_and_synthetic_drafts_are_rejected(entered):
    with pytest.raises(ValueError):
        prepare(draft=entered)


def test_dictionary_and_json_use_one_locked_idempotent_write_path(settings, monkeypatch, tmp_path):
    rows = observations((1, 'V1', 'L1'))
    monkeypatch.setattr(daily, 'tracking_history', lambda *args, **kwargs: (cycles(), rows))
    monkeypatch.setattr(daily, 'collect_cycle', Mock(side_effect=AssertionError('No network')))
    prepared = prepare(rows)
    before = copy.deepcopy(prepared)
    assert daily.record_evidence(settings, prepared, kind='check')
    saved_bytes = settings['checks'].read_bytes()
    path = tmp_path/'prepared-check.json'
    path.write_text(json.dumps(prepared), encoding='utf-8')
    assert not daily.record_evidence(settings, prepared, kind='check')
    assert not daily.record_evidence(settings, path, kind='check')
    assert settings['checks'].read_bytes() == saved_bytes and prepared == before
    assert not settings['database'].exists() and not settings['register'].exists()
    assert not settings['exports'].exists()
    assert set(settings['checks'].parent.iterdir()) == {settings['checks'], settings['checks'].parent/'cycle.lock'}


def test_corrections_and_late_old_checks_preserve_history_and_cutoffs(settings, monkeypatch):
    rows = observations((1, 'V1', 'L1'))
    monkeypatch.setattr(daily, 'tracking_history', lambda *args, **kwargs: (cycles(), rows))
    first = prepare(rows)
    correction = prepare(rows, draft=draft(observed_status='unknown',
        note='Correcting the classification of the same physical check'), available_at='2026-09-03T17:00:00Z')
    late_old = prepare(rows, draft=draft(checked_at='2026-09-01T16:00:00Z',
        observed_status='available', native_text='Get started'), available_at='2026-09-04T17:00:00Z')
    assert len({row['check_id'] for row in [first, correction, late_old]}) == 3
    for record in [first, correction, late_old]:
        assert daily.record_evidence(settings, record, kind='check')
    saved = read_records(settings['checks'], CHECK_COLUMNS)
    assert len(saved) == 3
    assert select_checks(saved, as_of='2026-09-02T16:30:00Z').empty
    assert select_checks(saved, as_of='2026-09-02T23:00:00Z').observed_status.tolist() == ['pending']
    assert select_checks(saved, as_of='2026-09-04T23:00:00Z').observed_status.tolist() == ['unknown']
    assert saved.iloc[0].check_id == first['check_id']


@pytest.mark.parametrize('changes', [dict(vin='V2'), dict(checked_at='2026-09-02T15:00:00Z'),
    dict(available_at='2026-09-03T17:00:00Z'), dict(native_text='Different wording')])
def test_editing_a_prepared_check_requires_another_preview(settings, changes):
    changed = dict(prepare(), **changes)
    with pytest.raises(ValueError, match='prepare and preview'):
        daily.record_evidence(settings, changed, kind='check')
    assert not settings['checks'].exists() and not settings['register'].parent.exists()


@pytest.mark.parametrize('changes', [dict(source='synthetic://lesson'), dict(source='<evidence>'), dict(note='TODO')])
def test_direct_json_or_dict_cannot_bypass_entry_protection(settings, tmp_path, changes):
    record = dict(prepare(), check_id='manual-id', **changes)
    path = tmp_path/'manual.json'
    path.write_text(json.dumps(record))
    for supplied in [record, path]:
        with pytest.raises(ValueError):
            daily.record_evidence(settings, supplied, kind='check')
    assert not settings['checks'].exists() and not settings['register'].parent.exists()


def test_save_rechecks_prepared_identity_against_registered_evidence(settings, monkeypatch):
    record = prepare(observations((1, 'V1', 'L1')))
    monkeypatch.setattr(daily, 'tracking_history', lambda *args, **kwargs:
                        (cycles(), observations((1, 'REAL-VIN', 'REAL-LISTING'))))
    with pytest.raises(ValueError, match='observed retailer'):
        daily.record_evidence(settings, record, kind='check')
    assert not settings['checks'].exists()


def test_save_rejects_listing_identity_that_became_ambiguous_since_preview(settings, monkeypatch):
    record = prepare(observations((1, 'V1', 'L1')))
    monkeypatch.setattr(daily, 'tracking_history', lambda *args, **kwargs:
                        (cycles(), observations((1, 'V1', 'L1'), (2, 'V2', 'L1'))))
    with pytest.raises(ValueError, match='unambiguous'):
        daily.record_evidence(settings, record, kind='check')
    assert not settings['checks'].exists()


@pytest.mark.parametrize('wording', ['Equipment as originally sold', 'Equipment AS originally SOLD.',
                                   'As originally sold: unsold factory accessories'])
def test_generic_equipment_sold_wording_cannot_be_prepared_or_saved(settings, tmp_path, wording):
    with pytest.raises(ValueError, match='not a listing Sold label'):
        prepare(draft=draft(observed_status='sold_label', native_text=wording))
    record = dict(prepare(), check_id='manual-id', observed_status='sold_label', native_text=wording)
    path = tmp_path/'boilerplate.json'
    path.write_text(json.dumps(record))
    for supplied in [record, path]:
        with pytest.raises(ValueError, match='not a listing Sold label'):
            daily.record_evidence(settings, supplied, kind='check')
    assert not settings['checks'].exists() and not settings['register'].parent.exists()


def test_independent_sold_wording_and_other_explicit_statuses_remain_accepted():
    actual = prepare(draft=draft(observed_status='sold_label', native_text='Sold. Equipment as originally sold'))
    assert actual['observed_status'] == 'sold_label'
    for status in ['available', 'unknown']:
        record = prepare(draft=draft(observed_status=status, native_text='Equipment as originally sold'))
        assert record['observed_status'] == status
