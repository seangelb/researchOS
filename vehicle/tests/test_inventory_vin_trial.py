"""A single bounded inventory trial; every response here is synthetic and offline."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from test_search_evidence import response
from vehicle_tracker.collect import NavigationBudget
from vehicle_tracker.search_plan import collect_plan
from vehicle_tracker.storage import read_snapshots


def data(start=0, count=24, *, page=1, total=None):
    total = count if total is None else total
    vehicles = [dict(vehicleId=10000+i, vin=f'1HGCM82633A{i:06d}', year=2023,
        make='Honda', model='Accord', parentModel='Accord', mileage=12000,
        price={'total': 22000}, isPurchasePending=False, vehicleLockType=0)
        for i in range(start, start+count)]
    return dict(userDeliveryInfo={'zip5': '08542'}, inventory=dict(vehicles=vehicles,
        pagination=dict(currentPage=page, pageSize=24, totalMatchedInventory=total,
                        totalMatchedPages=(total+23)//24)))


def plan(count=3):
    return [dict(query_id=f'query_{i}', zip_code='08542', filters={}) for i in range(count)]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', lambda _: None)


def test_vin_target_deduplicates_overlap_and_preserves_whole_final_page(tmp_path):
    first, overlap, last = data(), data(total=60), data(24, page=2, total=60)
    post = Mock(side_effect=[response(first), response(overlap), response(last)])
    result = collect_plan(plan(), destination=tmp_path/'trial', target_vins=40, post=post)
    assert post.call_count == result['requests'] == 3
    assert result['unique_vins'] == result['unique_listings'] == 48
    assert result['duplicate_memberships'] == 24 and result['target_reached']
    assert not result['all_queries_complete'] and result['outcomes'][2]['status'] == 'unattempted'
    child = json.loads(Path(result['outcomes'][1]['report']).read_text())
    assert not child['query_complete'] and child['outcome_kind'] == 'sample_limit'
    final_source = Path(child['pages'][-1]['response_evidence']['source_path'])
    assert json.loads(final_source.read_text()) == last
    assert result['checkpoints'][0]['unique_vins'] == 48


@pytest.mark.parametrize('conflict', ['listing_changes_vin', 'vin_changes_listing'])
def test_identity_conflict_stops_all_remaining_queries(tmp_path, conflict):
    second = data()
    field = 'vin' if conflict == 'listing_changes_vin' else 'vehicleId'
    second['inventory']['vehicles'][0][field] = '1HGCM82633A999999' if field == 'vin' else 999999
    post = Mock(side_effect=[response(data()), response(second), response(data(100))])
    result = collect_plan(plan(), destination=tmp_path/'trial', target_vins=100, post=post)
    assert post.call_count == result['requests'] == 2
    assert result['stopped'] and not result['target_reached']
    assert result['unique_vins'] == 24 and len(result['identity_conflicts']) == 1
    assert result['outcomes'][1]['outcome_kind'] == 'identity_failure'
    assert result['outcomes'][2]['status'] == 'unattempted'
    child = json.loads(Path(result['outcomes'][1]['report']).read_text())
    assert Path(child['pages'][0]['response_evidence']['source_path']).is_file()


@pytest.mark.parametrize('failure', ['missing_vin', 'duplicate_vin', 'short_page', 'changed_total',
                                     'wrong_page', 'schema', '403', '429', 'timeout'])
def test_failure_ends_original_trial_without_next_query(tmp_path, failure):
    first, second = data(total=48), data(24, page=2, total=48)
    if failure == 'missing_vin': second['inventory']['vehicles'][0]['vin'] = None
    if failure == 'duplicate_vin': second['inventory']['vehicles'][1]['vin'] = second['inventory']['vehicles'][0]['vin']
    if failure == 'short_page': second['inventory']['vehicles'].pop()
    if failure == 'changed_total': second['inventory']['pagination']['totalMatchedInventory'] = 47
    if failure == 'wrong_page': second['inventory']['pagination']['currentPage'] = 1
    if failure == 'schema': second['inventory'] = None
    replies = [response(first), TimeoutError() if failure == 'timeout' else
               response(second, status=int(failure) if failure in {'403', '429'} else 200), response(data(100))]
    post = Mock(side_effect=replies)
    result = collect_plan(plan(), destination=tmp_path/'trial', target_vins=100, post=post)
    assert post.call_count == result['requests'] == 2
    assert result['stopped'] and result['unique_vins'] == 24
    assert all(o['status'] == 'unattempted' for o in result['outcomes'][1:])
    attempts = sorted((tmp_path/'trial/query_0/attempts').glob('*.json'))
    assert len(attempts) == 2
    assert all(json.loads(p.read_text())['request_started_at_utc'] for p in attempts)


def test_uncertain_committed_write_preserves_rows_and_stops(tmp_path, monkeypatch):
    import vehicle_tracker.search as search
    original = search.store_capture
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('simulated failure after database commit')
    monkeypatch.setattr(search, 'store_capture', interrupted)
    post = Mock(return_value=response(data()))
    result = collect_plan(plan(), destination=tmp_path/'trial', target_vins=100, post=post)
    assert result['stopped'] and post.call_count == 1
    child = json.loads(Path(result['outcomes'][0]['report']).read_text())
    assert child['pages'][0]['database_outcome'] == 'unconfirmed'
    assert child['pages'][0]['status'] == 'retained'
    assert result['unique_vins'] == 0  # An unconfirmed write is not admitted progress.
    captures, rows = read_snapshots(tmp_path/'trial/query_0/vehicle.sqlite')
    assert len(rows) == 24 and len(captures) == 1


def test_progress_reports_same_request_budget_at_1000_and_5000(tmp_path, capsys):
    def post(url, **kwargs):
        page = kwargs['json']['pagination']['page']
        return response(data((page-1)*24, page=page, total=6000))
    budget = NavigationBudget(max_requests=250, max_seconds=3600)
    result = collect_plan(plan(1), destination=tmp_path/'trial', target_vins=5000, budget=budget, post=post)
    assert [(r['target_vins'], r['unique_vins'], r['requests']) for r in result['checkpoints']] == [
        (1000, 1008, 42), (5000, 5016, 209)]
    assert result['requests'] == budget.requests == 209
    assert result['max_requests'] == 250 and result['pause_seconds'] == 3
    assert result['checkpoints'][1]['elapsed_seconds'] >= result['checkpoints'][0]['elapsed_seconds']
    output = capsys.readouterr().out
    assert '1,008 distinct VINs' in output and '5,016 distinct VINs' in output


@pytest.mark.parametrize('kwargs', [dict(resume_from='previous'), dict(full_plan=True), dict(target_vins=0)])
def test_vin_trial_cannot_restart_or_reset_budget(tmp_path, kwargs):
    post = Mock()
    with pytest.raises(ValueError, match='Distinct VIN'):
        collect_plan(plan(), destination=tmp_path/'trial', post=post, **({'target_vins': 100} | kwargs))
    assert not (tmp_path/'trial').exists() and post.call_count == 0


def test_request_budget_includes_failed_attempt_and_no_replacement_query(tmp_path):
    budget = NavigationBudget(max_requests=2, max_seconds=3600)
    post = Mock(side_effect=[response(data(total=72)), response(data(24, page=2, total=72))])
    result = collect_plan(plan(), destination=tmp_path/'trial', target_vins=100, budget=budget, post=post)
    assert result['requests'] == budget.requests == post.call_count == 2
    assert result['unique_vins'] == 48 and not result['target_reached']
    assert result['outcomes'][1]['status'] == 'unattempted'


def test_target_page_progress_write_failure_remains_the_stop_cause(tmp_path, monkeypatch):
    import vehicle_tracker.search_plan as search_plan
    original = search_plan.write_json_atomic
    failed = False
    def interrupted(path, value):
        nonlocal failed
        if Path(path).name == 'run_report.json' and value.get('active_query') and not failed:
            failed = True
            raise OSError('simulated parent progress publication failure')
        return original(path, value)
    monkeypatch.setattr(search_plan, 'write_json_atomic', interrupted)
    post = Mock(return_value=response(data(total=48)))
    result = collect_plan(plan(), destination=tmp_path/'trial', target_vins=20, post=post)
    assert result['target_reached'] and result['unique_vins'] == 24
    assert result['stopped'] and result['stop_reason'] == 'OSError: storage_failure'
    assert post.call_count == 1 and result['outcomes'][1]['status'] == 'unattempted'
    child = json.loads(Path(result['outcomes'][0]['report']).read_text())
    assert not child['query_complete'] and child['pages'][0]['database_outcome'] == 'unconfirmed'
