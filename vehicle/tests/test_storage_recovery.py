"""Offline recovery boundaries: retained bytes, atomic imports and distinct clocks."""
import hashlib
import json
import sqlite3
from unittest.mock import Mock

import pandas as pd
import pytest

from test_search import response_data
from vehicle_tracker.history import import_reports, read_history
from vehicle_tracker.search import project_response
from vehicle_tracker.storage import read_snapshots, retain_bytes, retain_capture, store_capture


def legacy_report(tmp_path, response_data, name='one', observed_at='2026-09-08T12:00:00Z'):
    """Deliberately old projection-only evidence; never invent original response bytes."""
    folder = tmp_path/name
    request = dict(filters={}, pagination=dict(page=1, pageSize=24), sortBy='MostPopular', zip5='08542')
    capture = project_response(response_data, request, observed_at=observed_at)
    source = retain_capture(capture, folder/'raw')
    report = dict(run_id=name, filters={}, zip_code='08542', query_complete=True,
        unique_listings=3, reported_total=3, started_utc=observed_at, ended_utc=observed_at,
        pages=[dict(page=1, status='parsed', stored_rows=3, retained_source=str(source),
                    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())])
    path = folder/'run_report.json'
    path.write_text(json.dumps(report), encoding='utf-8')
    return path, source


@pytest.mark.parametrize('boundary', ['before_publish', 'after_publish'])
def test_interrupted_retention_never_publishes_truncated_bytes(tmp_path, monkeypatch, boundary):
    import vehicle_tracker.storage as storage
    original = storage.os.link
    body = b'{"public": "complete response"}'
    expected = tmp_path/(hashlib.sha256(body).hexdigest()+'.bin')
    def interrupted(source, destination):
        if boundary == 'after_publish':
            original(source, destination)
        raise OSError('simulated interruption')
    with monkeypatch.context() as patcher:
        patcher.setattr(storage.os, 'link', interrupted)
        with pytest.raises(OSError):
            retain_bytes(body, tmp_path)
    assert not expected.exists() if boundary == 'before_publish' else expected.read_bytes() == body
    assert list(tmp_path.glob('*.tmp')) == []
    assert retain_bytes(body, tmp_path) == expected
    assert expected.read_bytes() == body


def test_identical_page_import_is_noop_but_different_observation_is_new(tmp_path, response_data):
    _, source = legacy_report(tmp_path, response_data)
    database = tmp_path/'snapshots.sqlite'
    assert store_capture(database, run_id='one', page_number=1, raw_file=source) == 3
    before = database.read_bytes()
    assert store_capture(database, run_id='one', page_number=1, raw_file=source) == 3
    assert database.read_bytes() == before
    capture = json.loads(source.read_text())
    capture['captured_at_utc'] = '2026-09-09T12:00:00Z'
    later = retain_capture(capture, source.parent)
    store_capture(database, run_id='two', page_number=1, raw_file=later)
    captures, rows = read_snapshots(database)
    assert len(captures) == 2 and len(rows) == 6
    assert captures.source_sha256.is_unique
    assert rows.groupby('listing_id').observed_at_utc.nunique().eq(2).all()


@pytest.mark.parametrize('mutation', ['price', 'missing_row', 'capture_metadata'])
def test_identical_page_import_rejects_modified_database(tmp_path, response_data, mutation):
    _, source = legacy_report(tmp_path, response_data)
    database = tmp_path/'snapshots.sqlite'
    store_capture(database, run_id='one', page_number=1, raw_file=source)
    statements = dict(price='UPDATE vehicle_observations SET asking_price_usd=1',
        missing_row="DELETE FROM vehicle_observations WHERE listing_id='4474057'",
        capture_metadata="UPDATE vehicle_captures SET coverage='complete'")
    with sqlite3.connect(database) as connection:
        connection.execute(statements[mutation])
    before = database.read_bytes()
    with pytest.raises(sqlite3.IntegrityError, match='conflicts'):
        store_capture(database, run_id='one', page_number=1, raw_file=source)
    assert database.read_bytes() == before


def test_page_observation_collision_rolls_back_entire_page(tmp_path, response_data):
    _, source = legacy_report(tmp_path, response_data)
    database = tmp_path/'snapshots.sqlite'
    store_capture(database, run_id='one', page_number=1, raw_file=source)
    before = database.read_bytes()
    with pytest.raises(sqlite3.IntegrityError):
        store_capture(database, run_id='one', page_number=2, raw_file=source)
    assert database.read_bytes() == before
    captures, rows = read_snapshots(database)
    assert len(captures) == 1 and len(rows) == 3


@pytest.mark.parametrize('mutation', ['price', 'missing_capture', 'run_metadata'])
def test_repeat_history_import_checks_stored_evidence(tmp_path, response_data, mutation):
    report, _ = legacy_report(tmp_path, response_data)
    database = tmp_path/'history.sqlite'
    import_reports([report], database)
    statements = dict(price='UPDATE observations SET asking_price_usd=1',
        missing_capture='DELETE FROM captures', run_metadata='UPDATE query_runs SET stored_rows=99')
    with sqlite3.connect(database) as connection:
        connection.execute(statements[mutation])
    before = database.read_bytes()
    with pytest.raises(ValueError, match='differ from retained evidence'):
        import_reports([report], database)
    assert database.read_bytes() == before


def test_import_validates_entire_batch_before_creating_database(tmp_path, response_data):
    first, _ = legacy_report(tmp_path, response_data)
    second, source = legacy_report(tmp_path, response_data, 'two')
    source.write_text('{}', encoding='utf-8')
    database = tmp_path/'analysis'/'history.sqlite'
    with pytest.raises(ValueError, match='hash'):
        import_reports([first, second], database)
    assert not database.parent.exists()


def test_import_conflict_rolls_back_earlier_new_run_in_same_batch(tmp_path, response_data):
    first, _ = legacy_report(tmp_path, response_data)
    second, _ = legacy_report(tmp_path, response_data, 'two', observed_at='2026-09-09T12:00:00Z')
    database = tmp_path/'history.sqlite'
    import_reports([first], database)
    report = json.loads(first.read_text())
    report['reason'] = 'Changed after original import'
    first.write_text(json.dumps(report), encoding='utf-8')
    before = database.read_bytes()
    with pytest.raises(ValueError, match='report changed'):
        import_reports([second, first], database)
    assert database.read_bytes() == before
    assert read_history(database)[0].run_id.tolist() == ['one']


def test_old_capture_and_new_import_clocks_stay_separate(tmp_path, response_data):
    report, _ = legacy_report(tmp_path, response_data)
    database = tmp_path/'history.sqlite'
    import_reports([report], database)
    before = database.read_bytes()
    import_reports([report], database)
    runs, captures, observations = read_history(database)
    assert database.read_bytes() == before
    assert pd.Timestamp(runs.imported_at_utc.iloc[0]) > pd.Timestamp(runs.observation_end.iloc[0])
    assert captures.evidence_available_at_utc.isna().all()
    assert observations.observed_at_utc.eq('2026-09-08T12:00:00+00:00').all()


def test_legacy_database_read_supplies_unknown_clocks_without_migration(tmp_path, response_data):
    report, _ = legacy_report(tmp_path, response_data)
    database = tmp_path/'history.sqlite'
    import_reports([report], database)
    with sqlite3.connect(database) as connection:
        connection.execute('ALTER TABLE query_runs DROP COLUMN imported_at_utc')
        connection.execute('ALTER TABLE captures DROP COLUMN evidence_available_at_utc')
    before = database.read_bytes()
    runs, captures, _ = read_history(database)
    assert runs.imported_at_utc.isna().all() and captures.evidence_available_at_utc.isna().all()
    assert database.read_bytes() == before
    # An explicitly requested later import can add nullable columns, never backfill.
    import_reports([report], database)
    assert read_history(database)[0].imported_at_utc.isna().all()


def test_capture_availability_cannot_precede_observation(tmp_path, response_data):
    report, _ = legacy_report(tmp_path, response_data)
    value = json.loads(report.read_text())
    value['pages'][0]['evidence_available_at_utc'] = '2026-09-08T11:59:00Z'
    report.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='availability'):
        import_reports([report], tmp_path/'history.sqlite')


def test_failed_transport_keeps_unknown_observation_and_known_retention_clock(tmp_path):
    from vehicle_tracker.search import collect_search
    destination = tmp_path/'failed'
    report = collect_search(filters={}, zip_code='08542', destination=destination,
                            post=Mock(side_effect=TimeoutError()))
    assert report['pages'][0]['status'] == 'failed'
    database = tmp_path/'history.sqlite'
    import_reports([destination/'run_report.json'], database)
    runs, captures, observations = read_history(database)
    assert observations.empty and not runs.query_complete.any()
    assert runs.observation_start.isna().all() and runs.observation_end.isna().all()
    assert captures.observed_at_utc.isna().all()
    assert captures.evidence_available_at_utc.notna().all()
    assert runs.imported_at_utc.notna().all()


@pytest.mark.parametrize('mutation', [None, 'projection', 'response'])
def test_new_history_import_replays_response_binding(tmp_path, response_data, mutation):
    from vehicle_tracker.search_evidence import retain_response_evidence
    report, old_source = legacy_report(tmp_path, response_data)
    body = json.dumps(response_data).encode()
    evidence = retain_response_evidence(Mock(status_code=200, headers={'content-type': 'application/json'},
        content=body, json=Mock(return_value=response_data)), tmp_path/'response')
    capture = json.loads(old_source.read_text())
    capture['response_evidence'] = evidence
    capture['attempt_id'] = 'physical-request-one'
    if mutation == 'projection':
        capture['vehicles'][0]['price']['total'] = 1
    source = retain_capture(capture, old_source.parent)
    value = json.loads(report.read_text())
    value['pages'][0].update(retained_source=str(source), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                            evidence_available_at_utc='2026-09-08T12:00:01Z')
    report.write_text(json.dumps(value), encoding='utf-8')
    if mutation == 'response':
        from pathlib import Path
        Path(evidence['source_path']).write_text('{}', encoding='utf-8')
    database = tmp_path/'history.sqlite'
    if mutation:
        with pytest.raises(ValueError, match='projection differs|source hash changed'):
            import_reports([report], database)
        assert not database.exists()
    else:
        import_reports([report], database)
        runs, captures, _ = read_history(database)
        assert captures.evidence_available_at_utc.tolist() == ['2026-09-08T12:00:01Z']
        assert pd.Timestamp(runs.imported_at_utc.iloc[0]) > pd.Timestamp(captures.evidence_available_at_utc.iloc[0])


@pytest.mark.parametrize('mutation', [None, 'attempt_id', 'response_evidence', 'response_sha256',
    'response_bytes', 'response_hash_scope', 'response_received_at_utc', 'missing_response_clock'])
def test_new_contract_requires_matching_response_metadata(tmp_path, response_data, mutation):
    from vehicle_tracker.search import collect_search
    destination = tmp_path/'source'
    body = json.dumps(response_data).encode()
    report = collect_search(filters={}, zip_code='08542', destination=destination,
        post=Mock(return_value=Mock(status_code=200, headers={'content-type': 'application/json'},
                                   content=body, json=Mock(return_value=response_data))))
    assert report['query_complete']
    page = report['pages'][0]
    if mutation == 'response_evidence':
        page['response_evidence'] = dict(page['response_evidence'], response_content_bytes=1)
    elif mutation == 'response_bytes':
        page[mutation] += 1
    elif mutation == 'response_received_at_utc':
        page[mutation] = '2026-01-01T00:00:00Z'
    elif mutation == 'missing_response_clock':
        page.pop('response_received_at_utc')
    elif mutation is not None:
        page[mutation] = 'different'
    path = destination/'run_report.json'
    path.write_text(json.dumps(report), encoding='utf-8')
    database = tmp_path/'history.sqlite'
    if mutation is None:
        import_reports([path], database)
        assert len(read_history(database)[2]) == 3
    else:
        with pytest.raises(ValueError, match='differs|differ'):
            import_reports([path], database)
        assert not database.exists()


@pytest.mark.parametrize('status', ['pending', 'retained'])
def test_incomplete_checkpoint_without_retained_source_requires_review(tmp_path, response_data, status):
    report, _ = legacy_report(tmp_path, response_data)
    value = json.loads(report.read_text())
    value['evidence_contract'] = 'carvana-search-source-v1'
    value['query_complete'] = False
    value['pages'][0].update(status=status, outcome_kind='storage_failure')
    value['pages'][0].pop('retained_source')
    report.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='Incomplete query checkpoint.*review or recover'):
        import_reports([report], tmp_path/'history.sqlite')
