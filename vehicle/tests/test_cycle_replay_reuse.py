"""Invocation-local replay reuse retains cutoff, source and SQLite validation."""
import copy
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pandas as pd
import pytest

from test_daily_cycles import clock, options, plan, reply
from test_daily_tracking import settings
from test_search import response_data
from vehicle_tracker import cycles
from vehicle_tracker.research_inputs import load_operating_runs
from vehicle_tracker.search import collect_search


@pytest.fixture
def retained_cycle(tmp_path, response_data, clock):
    cycles.collect_cycle(plan(), **options(tmp_path), post=Mock(return_value=reply(response_data)))
    return tmp_path / 'cycle/cycle.json'


@pytest.mark.parametrize('use_database', [False, True])
def test_history_replays_each_report_once_even_for_repeated_cycle_path(
        retained_cycle, tmp_path, monkeypatch, use_database):
    database = tmp_path / 'history.sqlite' if use_database else None
    if database:
        cycles.import_cycle(retained_cycle, database)
    expected = cycles.read_cycle_history([retained_cycle], database)
    replay = Mock(wraps=cycles.read_query_evidence)
    monkeypatch.setattr(cycles, 'read_query_evidence', replay)
    paths = [retained_cycle, retained_cycle.parent / '.' / retained_cycle.name]
    actual = cycles.read_cycle_history(paths, database)
    assert replay.call_count == 1
    for result, reference in zip(actual, expected):
        pd.testing.assert_frame_equal(result, pd.concat([reference, reference], ignore_index=True))
    cycles.read_cycle_history([retained_cycle], database)
    assert replay.call_count == 2  # No verified payload survives an independent read.


@pytest.mark.parametrize('kind', ['projection', 'response'])
def test_independent_history_read_rejects_changed_source(retained_cycle, monkeypatch, kind):
    replay = Mock(wraps=cycles.read_query_evidence)
    monkeypatch.setattr(cycles, 'read_query_evidence', replay)
    cycles.read_cycle_history([retained_cycle])
    report = json.loads((retained_cycle.parent / 'attempt_0001/all/run_report.json').read_text())
    page = report['pages'][0]
    source = Path(page['retained_source'] if kind == 'projection' else page['response_evidence']['source_path'])
    source.write_bytes(source.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='[Hh]ash'):
        cycles.read_cycle_history([retained_cycle])
    assert replay.call_count == 2


def test_database_rows_still_require_exact_source_reconciliation(retained_cycle, tmp_path, monkeypatch):
    database = tmp_path / 'history.sqlite'
    cycles.import_cycle(retained_cycle, database)
    assert cycles.read_cycle_history([retained_cycle], database)[0].coverage_complete.all()
    with sqlite3.connect(database) as connection:
        connection.execute('UPDATE observations SET asking_price_usd = asking_price_usd + 1')
    replay = Mock(wraps=cycles.read_query_evidence)
    monkeypatch.setattr(cycles, 'read_query_evidence', replay)
    days, rows = cycles.read_cycle_history([retained_cycle], database)
    assert replay.call_count == 1 and not days.coverage_complete.any() and rows.empty
    assert 'differ from imported evidence' in days.coverage_reason.iloc[0]


def test_operating_review_replays_once_and_rechecks_next_invocation(settings, response_data, clock, monkeypatch):
    args = options(settings['config_path'].parent.parent)
    args.update(destination=settings['capture_root'] / '2026-09-08', timezone_name=settings['timezone'])
    cycles.collect_cycle(settings['queries'], **args, post=Mock(return_value=reply(response_data)))
    root = settings['config_path'].parent.parent
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    replay = Mock(wraps=cycles.read_query_evidence)
    monkeypatch.setattr(cycles, 'read_query_evidence', replay)
    rows, _ = load_operating_runs(settings, as_of='2026-09-08T23:00:00Z')
    assert replay.call_count == 1
    assert rows.iloc[0].collection_status == 'complete' and rows.iloc[0].observed_vins == 3
    assert {p: p.read_bytes() for p in root.rglob('*') if p.is_file()} == before
    report = json.loads((args['destination'] / 'attempt_0001/all/run_report.json').read_text())
    source = Path(report['pages'][0]['retained_source'])
    source.write_bytes(source.read_bytes() + b'\n')
    changed, _ = load_operating_runs(settings, as_of='2026-09-08T23:00:00Z')
    assert replay.call_count == 2
    assert changed.iloc[0].collection_status == 'invalid retained evidence'
    assert pd.isna(changed.iloc[0].observed_vins)


def test_reuse_preserves_attempt_selection_and_cutoff(retained_cycle, response_data, clock, monkeypatch):
    first_report = retained_cycle.parent / 'attempt_0001/all/run_report.json'
    first = json.loads(first_report.read_text())
    first.update(query_complete=False, status='partial', reason='Fixture incomplete attempt')
    first_report.write_text(json.dumps(first))
    later = clock + timedelta(minutes=5)
    class Later:
        @staticmethod
        def now(tz=None): return later
    monkeypatch.setattr('vehicle_tracker.search.datetime', Later)
    revised = copy.deepcopy(response_data)
    revised['inventory']['vehicles'][0]['price']['total'] += 100
    collect_search(filters={}, zip_code='08542', destination=retained_cycle.parent / 'attempt_0002/all',
                   post=Mock(return_value=reply(revised)))
    state = json.loads(retained_cycle.read_text())
    state['attempts'].append('attempt_0002')
    state['budget'].update(requests=2, last_request_utc=later.isoformat())
    retained_cycle.write_text(json.dumps(state))
    replay = Mock(wraps=cycles.read_query_evidence)
    monkeypatch.setattr(cycles, 'read_query_evidence', replay)
    early_days, early_rows = cycles.read_cycle_history([retained_cycle], as_of=(clock+timedelta(minutes=1)).isoformat())
    assert replay.call_count == 1 and not early_days.coverage_complete.any()
    assert early_rows.asking_price_usd.iloc[0] == response_data['inventory']['vehicles'][0]['price']['total']
    replay.reset_mock()
    late_days, late_rows = cycles.read_cycle_history([retained_cycle], as_of=later.isoformat())
    assert replay.call_count == 2 and late_days.coverage_complete.all()
    assert late_rows.asking_price_usd.iloc[0] == revised['inventory']['vehicles'][0]['price']['total']
    assert late_rows.source_path.str.contains('attempt_0002', regex=False).all()
