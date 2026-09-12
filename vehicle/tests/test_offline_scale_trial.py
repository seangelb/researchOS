"""Invented search responses only. These tests never contact Carvana."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from vehicle_tracker.collect import NavigationBudget
from vehicle_tracker.history import import_reports, read_history
from vehicle_tracker.search import collect_search


def synthetic_reply(ids, *, page, total):
    """Construct an explicitly invented public-response shape, with valid identities."""
    data = dict(userDeliveryInfo=dict(zip5='08542'), inventory=dict(
        pagination=dict(currentPage=page, pageSize=24, totalMatchedInventory=total,
                        totalMatchedPages=(total + 23) // 24),
        vehicles=[dict(vehicleId=i + 1, vin=f'{i:017}', year=2024,
            make='Synthetic', model='Example', parentModel='Example', mileage=100,
            price=dict(total=20000), isPurchasePending=False, vehicleLockType=0)
            for i in ids]))
    response = requests.Response()
    response.status_code = 200
    response.headers['content-type'] = 'application/json'
    response._content = json.dumps(data).encode()
    return response


def test_thousand_vin_pagination_import_and_repeat_keep_exact_counts(tmp_path, monkeypatch):
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', lambda _: None)
    total = 1000
    def reply(url, **kwargs):
        page = kwargs['json']['pagination']['page']
        return synthetic_reply(range((page - 1) * 24, min(page * 24, total)), page=page, total=total)
    post = Mock(side_effect=reply)
    report = collect_search(filters={}, zip_code='08542', destination=tmp_path/'SYNTHETIC',
        target_listings=None, budget=NavigationBudget(max_requests=42), post=post)
    assert report['query_complete'] and report['unique_vins'] == report['unique_listings'] == total
    assert report['requests'] == post.call_count == 42
    assert sum(page['stored_rows'] for page in report['pages']) == total
    assert [page['page'] for page in report['pages']] == list(range(1, 43))
    report_path = tmp_path/'SYNTHETIC/run_report.json'
    database = tmp_path/'SYNTHETIC_history.sqlite'
    assert import_reports([report_path], database).imported_rows.sum() == total
    before = database.read_bytes()
    assert import_reports([report_path], database).imported_rows.sum() == 0
    assert database.read_bytes() == before
    _, _, rows = read_history(database)
    assert len(rows) == total and not rows.duplicated(['retailer', 'vin']).any()


@pytest.mark.parametrize('fault', ['duplicate_vin', 'missing_rows'])
def test_full_page_then_fault_preserves_partial_evidence(tmp_path, monkeypatch, fault):
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', lambda _: None)
    second = synthetic_reply([0, *range(25, 48)] if fault == 'duplicate_vin' else range(24, 26),
                             page=2, total=48)
    post = Mock(side_effect=[synthetic_reply(range(24), page=1, total=48), second])
    report = collect_search(filters={}, zip_code='08542', destination=tmp_path/'SYNTHETIC',
                            target_listings=None, post=post)
    assert post.call_count == 2 and not report['query_complete']
    assert report['complete_query_count'] is None
    assert report['unique_vins'] == (24 if fault == 'duplicate_vin' else 26)
    assert report['pages'][0]['stored_rows'] == 24
    assert report['outcome_kind'] == 'pagination_unstable'


def test_constant_total_churn_can_reconcile_without_an_atomic_snapshot(tmp_path, monkeypatch):
    """Document an observability limit: totals and uniqueness cannot detect all churn.

    Initially IDs 0..47 exist. Between pages ID 0 exits and ID 48 enters. A valid
    second page can contain IDs 25..48. The saved union contains 0 and 48, even
    though they never coexisted. Its count is still 48 with no duplicates.
    """
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', lambda _: None)
    post = Mock(side_effect=[synthetic_reply(range(24), page=1, total=48),
                            synthetic_reply(range(25, 49), page=2, total=48)])
    report = collect_search(filters={}, zip_code='08542', destination=tmp_path/'SYNTHETIC',
                            target_listings=None, post=post)
    assert report['query_complete'] and report['unique_vins'] == 48
    assert not report['national_coverage_verified']
    saved_ids = set()
    for page in report['pages']:
        capture = json.loads(Path(page['retained_source']).read_text())
        saved_ids.update(row['vehicleId'] - 1 for row in capture['vehicles'])
    assert 0 in saved_ids and 48 in saved_ids and 24 not in saved_ids
