"""Offline regressions for shared controls and verified query recovery."""
import hashlib
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

from test_search import response_data
from test_search_evidence import response
from vehicle_tracker.collect import CollectionStopped, NavigationBudget
from vehicle_tracker.search import collect_search
from vehicle_tracker.search_plan import query_outcome


@pytest.mark.parametrize('controls', [
    {'max_requests': 1.5}, {'max_requests': True}, {'max_requests': float('nan')},
    {'max_seconds': float('nan')}, {'max_seconds': float('inf')}, {'max_seconds': True},
    {'pause_seconds': float('nan')}, {'pause_seconds': float('inf')}, {'pause_seconds': True},
])
def test_invalid_budget_controls_are_rejected_before_reserving(controls):
    with pytest.raises(ValueError, match='requests.*seconds.*spacing'):
        NavigationBudget(**controls)


def test_valid_budget_keeps_defaults_and_exact_reservation_cap(monkeypatch):
    defaults = NavigationBudget()
    assert (defaults.max_requests, defaults.max_seconds, defaults.pause_seconds) == (120, 1200, 3)
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', lambda _: None)
    budget = NavigationBudget(max_requests=2, max_seconds=30.5, pause_seconds=3.5)
    budget.before_navigation()
    budget.before_navigation()
    with pytest.raises(CollectionStopped, match='Request budget'):
        budget.before_navigation()
    assert budget.requests == 2


@pytest.mark.parametrize('mutation', [
    'run', 'page', 'hash', 'status', 'count', 'clock', 'price', 'price_cents', 'missing_row',
    'complete_count', 'source_hash', 'sort',
])
def test_recovery_still_rejects_changed_database_or_context(tmp_path, response_data, mutation):
    folder = tmp_path/'query'
    if mutation == 'price_cents':
        response_data['inventory']['vehicles'][0]['price']['total'] = 14590.25
    report = collect_search(filters={}, zip_code='08542', destination=folder,
                            post=Mock(return_value=response(response_data)))
    page = report['pages'][0]
    database, report_path = folder/'vehicle.sqlite', folder/'run_report.json'
    statements = {
        'run': "UPDATE vehicle_captures SET run_id='changed'",
        'page': 'UPDATE vehicle_captures SET page_number=2',
        'hash': "UPDATE vehicle_captures SET source_sha256='changed'",
        'status': "UPDATE vehicle_captures SET status='failed'",
        'count': 'UPDATE vehicle_captures SET row_count=0',
        'clock': "UPDATE vehicle_captures SET observed_at_utc='2026-01-01T00:00:00Z'",
        'price': 'UPDATE vehicle_observations SET asking_price_usd=1',
        'price_cents': 'UPDATE vehicle_observations SET asking_price_usd=asking_price_usd+0.01 WHERE rowid=1',
        'missing_row': 'DELETE FROM vehicle_observations WHERE rowid=1',
    }
    if mutation in statements:
        with sqlite3.connect(database) as connection:
            connection.execute(statements[mutation])
    elif mutation == 'complete_count':
        report['complete_query_count'] = 0
    else:
        source = Path(page['retained_source'])
        capture = json.loads(source.read_text(encoding='utf-8'))
        capture['request']['sortBy'] = 'LowestPrice'
        source.write_text(json.dumps(capture), encoding='utf-8')
        if mutation == 'sort':
            # Hashes agree, so the independent approved-sort check must still reject.
            page['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
            with sqlite3.connect(database) as connection:
                connection.execute('UPDATE vehicle_captures SET source_sha256=?', (page['source_sha256'],))
    report_path.write_text(json.dumps(report), encoding='utf-8')
    before = {p: p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        query_outcome('query', folder, recover=True)
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('empty', [False, True])
def test_verified_recovery_preserves_complete_rows_and_empty_query(tmp_path, response_data, empty):
    if empty:
        response_data['inventory']['vehicles'] = []
        response_data['inventory']['pagination'].update(totalMatchedInventory=0, totalMatchedPages=0)
    folder = tmp_path/'query'
    collect_search(filters={}, zip_code='08542', destination=folder,
                   post=Mock(return_value=response(response_data)))
    before = {p: p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    recovered = query_outcome('query', folder, recover=True)
    assert recovered['query_complete'] and recovered['resumed']
    assert {p: p.read_bytes() for p in before} == before
