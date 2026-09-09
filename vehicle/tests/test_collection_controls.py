import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from vehicle_tracker.collect import collect_pages


def test_no_navigation_below_three_second_spacing(tmp_path):
    with pytest.raises(ValueError, match='three'):
        collect_pages(Mock(), source_url='https://www.carvana.com/cars', raw_directory=tmp_path,
                      database=tmp_path / 'new.sqlite', pause_seconds=2)


@pytest.mark.parametrize('status,headers,expected', [
    (403, {'cf-mitigated': 'challenge'}, 'cloudflare_challenge'),
    (403, {}, 'access_denied'), (429, {'retry-after': '60'}, 'rate_limited'),
    (503, {}, 'server_failure'), (200, {'cf-mitigated': 'challenge'}, 'cloudflare_challenge')])
def test_response_classification_and_stop(tmp_path, status, headers, expected):
    page = Mock()
    page.goto.return_value.status = status
    page.goto.return_value.headers = headers
    result = collect_pages(page, source_url='https://www.carvana.com/cars',
                           raw_directory=tmp_path, database=tmp_path / 'new.sqlite')
    assert expected in result.reason.iloc[-1]
    page.reload.assert_not_called()
    page.evaluate.assert_not_called()


def test_foreign_exception_text_is_not_retained(tmp_path):
    page = Mock()
    page.goto.side_effect = RuntimeError('secret-token-THIS-MUST-NOT-LEAK')
    result = collect_pages(page, source_url='https://www.carvana.com/cars',
                           raw_directory=tmp_path, database=tmp_path / 'new.sqlite')
    assert 'THIS-MUST-NOT-LEAK' not in result.to_string()
    assert all(b'THIS-MUST-NOT-LEAK' not in p.read_bytes() for p in tmp_path.glob('*') if p.is_file())


def test_budget_counts_every_top_level_operation_and_is_shared(monkeypatch, tmp_path):
    from vehicle_tracker.collect import NavigationBudget
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', lambda _: None)
    budget = NavigationBudget(max_requests=2)
    page = Mock()
    page.goto.return_value.status = 200
    page.goto.return_value.headers = {}
    first = collect_pages(page, source_url='https://www.carvana.com/cars',
        raw_directory=tmp_path / 'raw', database=tmp_path / 'new.sqlite', budget=budget)
    assert budget.requests == 2 and 'budget' in first.reason.iloc[-1]
    page.reload.assert_not_called()
    page.goto.reset_mock()
    second = collect_pages(page, source_url='https://www.carvana.com/cars',
        raw_directory=tmp_path / 'raw', database=tmp_path / 'new.sqlite', budget=budget)
    page.goto.assert_not_called()
    assert second.status.eq('failed').all()


def test_budget_stops_after_elapsed_deadline(monkeypatch):
    from vehicle_tracker.collect import NavigationBudget
    clock = [0.0]
    monkeypatch.setattr('vehicle_tracker.collect.time.monotonic', lambda: clock[0])
    budget = NavigationBudget(max_seconds=10)
    budget.before_navigation()
    clock[0] = 11
    with pytest.raises(ValueError, match='budget'):
        budget.before_navigation()


def test_rate_limit_stops_other_queries_in_same_budget(tmp_path):
    from vehicle_tracker.collect import NavigationBudget
    budget = NavigationBudget()
    page = Mock()
    page.goto.return_value.status, page.goto.return_value.headers = 429, {}
    collect_pages(page, source_url='https://www.carvana.com/cars', raw_directory=tmp_path,
                  database=tmp_path / 'new.sqlite', budget=budget)
    assert budget.stopped
    with pytest.raises(ValueError, match='stopped'):
        budget.before_navigation()


def test_redirect_or_zip_update_cannot_drop_requested_filters(monkeypatch, tmp_path):
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', lambda _: None)
    fixture = Path(__file__).parent / 'fixtures/carvana_browser_sample_20260907.json'
    capture = json.loads(fixture.read_text())
    capture.update(sample_only=False, visible_listing_count=3, reported_total_text='3 cars',
        results_container_present=True, zip_code='08542', has_next_page=False, sort='Recommended',
        page_url='https://www.carvana.com/cars/filters?filter=ALL_CARS')
    capture['cards'] = [{'url': r['offers']['url'], 'text': 'Used'} for r in capture['records']]
    page = Mock()
    page.goto.return_value.status = page.reload.return_value.status = 200
    page.goto.return_value.headers = page.reload.return_value.headers = {}
    page.evaluate.return_value = capture
    result = collect_pages(page, source_url='https://www.carvana.com/cars/filters?filter=REQUESTED_MODEL',
        raw_directory=tmp_path, database=tmp_path / 'new.sqlite')
    assert result.status.eq('failed').all() and not result.query_complete.any()
    assert result.stored_rows.sum() == 0 and 'query' in result.reason.iloc[-1]
