"""Notebook teaching controls exercised with retained fixtures and mocked transport."""
import builtins
import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from vehicle_tracker.collect import CollectionStopped
from vehicle_tracker.notebook_lab import (
    LabSession, load_capture, load_comparison, load_verified_history, search_request,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_path(tmp_path_factory):
    # Retained evidence adds 64-character hash names: use a short Windows test root.
    return tmp_path_factory.mktemp('lab')


@pytest.fixture
def vehicle():
    """An actual retained browser record, mapped into the existing search fixture shape."""
    source = json.loads((ROOT / 'tests/fixtures/carvana_browser_sample_20260907.json').read_text())
    row = source['records'][0]
    return dict(vehicleId=int(row['offers']['url'].rstrip('/').split('/')[-1]),
                vin=row['vehicleIdentificationNumber'], year=int(row['modelDate']),
                make=row['brand'], model=row['model'], parentModel=row['model'],
                mileage=row['mileageFromOdometer'], price={'total': row['offers']['price']},
                isPurchasePending=None, vehicleLockType=None)


def settings(vehicle, **changes):
    return dict(make=vehicle['make'], model=vehicle['parentModel'], year=vehicle['year'],
                zip_code='08542', **changes)


def body(vehicle, count=1, total=None):
    """Additional page members are explicitly synthetic, derived only for offline tests."""
    total = count if total is None else total
    rows = [copy.deepcopy(vehicle) for _ in range(count)]
    for i, row in enumerate(rows[1:], 1):
        row.update(vehicleId=9000000+i, vin=f'1HGCM82633A{i:06d}')
    return dict(userDeliveryInfo={'zip5': '08542'}, inventory=dict(vehicles=rows,
        pagination=dict(currentPage=1, pageSize=24, totalMatchedInventory=total,
                        totalMatchedPages=(total+23)//24)))


def response(data, status=200):
    result = requests.Response()
    result.status_code = status
    result.headers['content-type'] = 'application/json'
    result._content = json.dumps(data).encode()
    return result


def accept(prompt):
    return re.search(r'Type (FETCH [0-9a-f]+) to make', prompt).group(1)


def hashes(folder):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        value = 0.0
        pauses = []
        def monotonic(self): return self.value
        def sleep(self, seconds):
            self.pauses.append(seconds)
            self.value += seconds
    clock = Clock()
    monkeypatch.setattr('vehicle_tracker.collect.time.monotonic', clock.monotonic)
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', clock.sleep)
    return clock


def test_preview_and_declined_action_make_no_request_or_files(tmp_path, vehicle):
    session = LabSession(tmp_path)
    post, prompts = Mock(), []
    preview = session.preview(**settings(vehicle))
    assert preview['request'] == search_request(**settings(vehicle))
    assert preview['request']['pagination'] == {'page': 1, 'pageSize': 24}
    assert preview['request_limit'] == 1 and preview['teaching_attempts_remaining'] == 3
    def cancel(prompt):
        prompts.append(prompt)
        return 'yes'  # A generic/saved affirmative is not the fresh confirmation.
    assert session.fetch_one_page(**settings(vehicle), confirm=cancel, post=post) is None
    assert post.call_count == session.budget.requests == 0
    assert list(tmp_path.iterdir()) == []
    assert all(text in prompts[0] for text in ['"teaching_attempts_remaining": 3', '"request_limit": 1',
                                              'carvana_notebook_lab', vehicle['make']])


def test_each_confirmation_is_fresh_and_binds_current_settings(tmp_path, vehicle, clock):
    session = LabSession(tmp_path)
    prompts, tokens = [], []
    def approve(prompt):
        prompts.append(prompt)
        tokens.append(accept(prompt))
        return tokens[-1]
    post = Mock(return_value=response(body(vehicle)))
    first = session.fetch_one_page(**settings(vehicle), confirm=approve, post=post)
    preserved = hashes(tmp_path)
    assert session.fetch_one_page(**settings(vehicle), confirm=lambda _: tokens[0], post=post) is None
    assert hashes(tmp_path) == preserved and post.call_count == 1
    second_vehicle = dict(vehicle, year=vehicle['year']+1)
    post.return_value = response(body(second_vehicle))
    second = session.fetch_one_page(**settings(second_vehicle), confirm=approve, post=post)
    assert first != second and tokens[0] != tokens[1]
    assert f'"min": {second_vehicle["year"]}' in prompts[-1]
    assert '"teaching_attempts_remaining": 2' in prompts[-1]
    assert str(second.parent).replace('\\', '\\\\') in prompts[-1]
    assert post.call_args.kwargs['json']['filters']['year']['min'] == second_vehicle['year']
    assert session.budget.requests == 2 and session.remaining == 1


def test_three_actions_share_budget_pacing_and_isolated_evidence(tmp_path, vehicle, clock):
    session = LabSession(tmp_path)
    budget, starts, reports = session.budget, [], []
    def post(*args, **kwargs):
        starts.append(clock.value)
        assert kwargs['json']['pagination']['page'] == 1 and kwargs['allow_redirects'] is False
        return response(body(vehicle, count=24, total=100))
    for _ in range(3):
        path = session.fetch_one_page(**settings(vehicle), confirm=accept, post=post)
        reports.append(path)
        report = json.loads(path.read_text())
        assert len(report['pages']) == report['requests'] == 1
        assert report['unique_vins'] == 24 and not report['query_complete']
        assert report['reported_total'] == 100 and report['status'] == 'partial'
        assert path.is_relative_to(tmp_path/'data/experiments/carvana_notebook_lab')
        assert len(list((path.parent/'attempts').glob('*.json'))) == 1
    with pytest.raises(CollectionStopped, match='exhausted'):
        session.fetch_one_page(**settings(vehicle), confirm=Mock(side_effect=AssertionError), post=Mock())
    assert budget is session.budget and budget.requests == len(starts) == 3
    assert session.remaining == 0 and len(set(reports)) == 3
    assert all(b-a >= 3 for a, b in zip(starts, starts[1:]))
    assert not (tmp_path/'data/analysis').exists()


@pytest.mark.parametrize('problem', ['empty_nonzero', 'short_page', 'duplicate_vin', 'duplicate_listing',
    'missing_vin', 'wrong_zip', 'wrong_page', 'schema', '403', '429', 'timeout', 'storage'])
def test_failure_stops_session_counts_attempt_and_never_paginates(tmp_path, vehicle, problem, monkeypatch):
    data = body(vehicle, count=24, total=100)
    if problem == 'empty_nonzero': data['inventory']['vehicles'] = []
    if problem == 'short_page': data['inventory']['vehicles'].pop()
    if problem == 'duplicate_vin': data['inventory']['vehicles'][1]['vin'] = vehicle['vin']
    if problem == 'duplicate_listing': data['inventory']['vehicles'][1]['vehicleId'] = vehicle['vehicleId']
    if problem == 'missing_vin': data['inventory']['vehicles'][0]['vin'] = None
    if problem == 'wrong_zip': data['userDeliveryInfo']['zip5'] = '90210'
    if problem == 'wrong_page': data['inventory']['pagination']['currentPage'] = 2
    if problem == 'schema': data.pop('inventory')
    if problem == 'storage':
        monkeypatch.setattr('vehicle_tracker.search.store_capture', Mock(side_effect=PermissionError('test disk failure')))
    post = Mock(side_effect=TimeoutError('test timeout')) if problem == 'timeout' else Mock(
        return_value=response(data, int(problem) if problem in ('403', '429') else 200))
    session = LabSession(tmp_path)
    path = session.fetch_one_page(**settings(vehicle), confirm=accept, post=post)
    report = json.loads(path.read_text())
    assert post.call_count == session.budget.requests == report['requests'] == len(report['pages']) == 1
    assert session.remaining == 2 and session.budget.stopped
    assert report['status'] == 'blocked' and not report['query_complete'] and report['reason']
    expected_stage = {'empty_nonzero': 'schema_failure', 'short_page': 'pagination_unstable',
        'duplicate_vin': 'pagination_unstable', 'duplicate_listing': 'pagination_unstable',
        'missing_vin': 'pagination_unstable', 'wrong_zip': 'schema_failure', 'wrong_page': 'schema_failure',
        'schema': 'schema_failure', '403': 'access_failure', '429': 'access_failure',
        'timeout': 'transport_failure', 'storage': 'storage_failure'}[problem]
    assert report['outcome_kind'] == expected_stage
    reloaded = load_capture(path)
    assert reloaded['rows'].empty and reloaded['summary'].iloc[0]['status'] == 'blocked'
    with pytest.raises(CollectionStopped):
        session.fetch_one_page(**settings(vehicle), confirm=accept, post=post)
    assert post.call_count == 1


def test_empty_zero_query_is_complete_and_still_only_one_request(tmp_path, vehicle):
    session = LabSession(tmp_path)
    post = Mock(return_value=response(body(vehicle, count=0)))
    saved = session.fetch_one_page(**settings(vehicle), confirm=accept, post=post)
    loaded = load_capture(saved)
    assert post.call_count == 1 and loaded['report']['query_complete']
    assert loaded['rows'].empty and loaded['summary'].iloc[0]['reported_query_total'] == 0
    assert not session.budget.stopped


def test_one_request_guard_enforces_action_limit_even_if_collector_asks_again(tmp_path, vehicle, monkeypatch):
    post = Mock()
    def unexpected_second_page(**kwargs):
        kwargs['budget'].before_navigation()
        kwargs['post']()
        kwargs['budget'].before_navigation()
        kwargs['post']()
    monkeypatch.setattr('vehicle_tracker.notebook_lab.collect_search', unexpected_second_page)
    session = LabSession(tmp_path)
    with pytest.raises(CollectionStopped, match='only one'):
        session.fetch_one_page(**settings(vehicle), confirm=accept, post=post)
    assert post.call_count == session.budget.requests == 1 and session.budget.stopped


def test_reload_preserves_time_bytes_and_uses_no_transport(tmp_path, vehicle, monkeypatch):
    post = Mock(return_value=response(body(vehicle)))
    path = LabSession(tmp_path).fetch_one_page(**settings(vehicle), confirm=accept, post=post)
    before = hashes(tmp_path)
    monkeypatch.setattr('requests.sessions.Session.request', Mock(side_effect=AssertionError('no network')))
    first, second = load_capture(path), load_capture(path)
    pd.testing.assert_frame_equal(first['rows'], second['rows'])
    assert first['rows'].iloc[0]['vin'] == vehicle['vin']
    assert first['rows'].iloc[0]['asking_price_usd'] == vehicle['price']['total']
    assert first['rows'].iloc[0]['observed_at_utc'] == second['rows'].iloc[0]['observed_at_utc']
    assert hashes(tmp_path) == before and post.call_count == 1
    with pytest.raises(ValueError, match='not yet available'):
        load_capture(path, as_of='2000-01-01T00:00:00Z')


@pytest.mark.parametrize('changed', ['source', 'database'])
def test_reload_fails_closed_on_changed_evidence(tmp_path, vehicle, changed):
    path = LabSession(tmp_path).fetch_one_page(**settings(vehicle), confirm=accept,
        post=Mock(return_value=response(body(vehicle))))
    if changed == 'source':
        source = Path(json.loads(path.read_text())['pages'][0]['retained_source'])
        source.write_text(source.read_text()+' ')
    else:
        with sqlite3.connect(path.parent/'vehicle.sqlite') as connection:
            connection.execute('UPDATE vehicle_observations SET asking_price_usd = 1')
    with pytest.raises(ValueError):
        load_capture(path)


def test_comparison_keeps_partial_pages_and_visible_price_join(tmp_path, vehicle, clock):
    session = LabSession(tmp_path)
    data = body(vehicle, count=24, total=100)
    before_path = session.fetch_one_page(**settings(vehicle), confirm=accept, post=Mock(return_value=response(data)))
    data['inventory']['vehicles'][0]['price']['total'] += 100
    after_path = session.fetch_one_page(**settings(vehicle), confirm=accept, post=Mock(return_value=response(data)))
    before, after = load_comparison(before_path, after_path)
    assert not before['report']['query_complete'] and not after['report']['query_complete']
    shared = before['rows'].merge(after['rows'], on=['retailer', 'vin'], suffixes=('_before', '_after'), validate='one_to_one')
    changes = shared.asking_price_usd_after - shared.asking_price_usd_before
    assert len(shared) == 24 and changes.sum() == 100
    with pytest.raises(ValueError, match='separately collected'):
        load_comparison(before_path, before_path)
    with pytest.raises(ValueError, match='later'):
        load_comparison(after_path, before_path)


@pytest.mark.parametrize('changed', ['year', 'zip_code', 'location_filter'])
def test_comparison_refuses_scope_mixing(tmp_path, vehicle, clock, changed):
    session = LabSession(tmp_path)
    before = session.fetch_one_page(**settings(vehicle), confirm=accept, post=Mock(return_value=response(body(vehicle))))
    query, data = settings(vehicle), body(vehicle)
    if changed == 'year':
        query['year'] += 1
        data['inventory']['vehicles'][0]['year'] += 1
    if changed == 'zip_code': query['zip_code'] = data['userDeliveryInfo']['zip5'] = '90210'
    if changed == 'location_filter': query['location_filter'] = True
    after = session.fetch_one_page(**query, confirm=accept, post=Mock(return_value=response(data)))
    with pytest.raises(ValueError, match='same make/model/year'):
        load_comparison(before, after)


@pytest.mark.parametrize('kind', ['missing_price', 'numeric_text_price', 'empty', 'blocked'])
def test_notebook_inspection_handles_unavailable_values(tmp_path, vehicle, kind, monkeypatch):
    data = body(vehicle, count=0 if kind == 'empty' else 1)
    if kind == 'missing_price': data['inventory']['vehicles'][0]['price']['total'] = None
    if kind == 'numeric_text_price': data['inventory']['vehicles'][0]['price']['total'] = '14590.00'
    path = LabSession(tmp_path).fetch_one_page(**settings(vehicle), confirm=accept,
        post=Mock(return_value=response(data, 403 if kind == 'blocked' else 200)))
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr('IPython.display.display', lambda *args, **kwargs: None)
    monkeypatch.setattr(builtins, 'input', Mock(side_effect=AssertionError('no new confirmation')))
    monkeypatch.setattr('requests.sessions.Session.request', Mock(side_effect=AssertionError('no network')))
    scope = {'__name__': '__main__', 'LAB_REPORT_PATH_OVERRIDE': path}
    notebook = json.loads((ROOT/'notebooks/11_carvana_live_collection_lab.ipynb').read_text(encoding='utf-8'))
    for cell in notebook['cells']:
        if cell['cell_type'] == 'code':
            exec(compile(''.join(cell['source']), cell['id'], 'exec'), scope)
    assert scope['LAB_SESSION'].budget.requests == 0
    if kind == 'missing_price':
        assert pd.isna(scope['price_minus_sample_mean']) and len(scope['priced_rows']) == 0
    elif kind == 'numeric_text_price':
        assert scope['source_price'] == '14590.00'  # Keep the native source representation visible.
        assert scope['normalized_price'] == 14590 and scope['prices_agree']
        assert scope['price_minus_sample_mean'] == 0
    else:
        assert scope['rows'].empty and scope['trace_row'] is None


@pytest.mark.parametrize('table,column', [
    ('vehicle_observations', 'asking_price_usd'),
    ('vehicle_observations', 'observed_at_utc'),
    ('vehicle_observations', 'source_url'),
    ('vehicle_captures', 'raw_file'),
    ('vehicle_captures', 'zip_code'),
    ('vehicle_captures', 'coverage'),
])
def test_reload_rejects_missing_stored_columns(tmp_path, vehicle, table, column):
    path = LabSession(tmp_path).fetch_one_page(**settings(vehicle), confirm=accept,
        post=Mock(return_value=response(body(vehicle))))
    with closing(sqlite3.connect(path.parent/'vehicle.sqlite')) as connection, connection:
        connection.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
    with pytest.raises(ValueError, match='differ from retained evidence'):
        load_capture(path)


@pytest.mark.parametrize('table,column,value', [
    ('vehicle_observations', 'page_number', 2),
    ('vehicle_observations', 'run_id', 'different-run'),
    ('vehicle_captures', 'raw_file', 'different-source.json'),
    ('vehicle_captures', 'zip_code', '90210'),
    ('vehicle_captures', 'coverage', 'complete'),
])
def test_reload_rejects_changed_stored_metadata(tmp_path, vehicle, table, column, value):
    path = LabSession(tmp_path).fetch_one_page(**settings(vehicle), confirm=accept,
        post=Mock(return_value=response(body(vehicle))))
    with closing(sqlite3.connect(path.parent/'vehicle.sqlite')) as connection, connection:
        connection.execute(f'UPDATE {table} SET {column} = ?', (value,))
    with pytest.raises(ValueError, match='differ from retained evidence'):
        load_capture(path)


@pytest.mark.parametrize('changed', ['none', 'price', 'missing_column', 'missing_run_id', 'capture', 'run', 'source'])
def test_intraday_history_reconciles_retained_evidence(tmp_path, vehicle, changed):
    from vehicle_tracker.history import import_reports, read_history
    report = LabSession(tmp_path).fetch_one_page(**settings(vehicle), confirm=accept,
        post=Mock(return_value=response(body(vehicle))))
    database = tmp_path/'intraday.sqlite'
    import_reports([report], database)  # Offline fixture only; no operating import.
    if changed == 'source':
        source = Path(json.loads(report.read_text())['pages'][0]['retained_source'])
        source.write_bytes(source.read_bytes()+b' ')
    else:
        statements = dict(
            none="UPDATE query_runs SET normalizer_sha256='original-parser-hash'",
            price='UPDATE observations SET asking_price_usd=asking_price_usd+12345',
            missing_column='ALTER TABLE observations DROP COLUMN asking_price_usd',
            missing_run_id='ALTER TABLE captures DROP COLUMN run_id',
            capture="UPDATE captures SET source_path='different-source.json'",
            run='UPDATE query_runs SET query_complete=0')
        with closing(sqlite3.connect(database)) as connection, connection:
            if changed == 'missing_run_id':
                connection.execute('DROP INDEX captures_run')
            connection.execute(statements[changed])
    before = hashes(tmp_path)
    if changed == 'none':
        verified = load_verified_history(database)
        for actual, expected in zip(verified, read_history(database)):
            pd.testing.assert_frame_equal(actual, expected)
        assert verified[0].normalizer_sha256.iloc[0] == 'original-parser-hash'
    else:
        with pytest.raises(ValueError):
            load_verified_history(database)
    assert hashes(tmp_path) == before


def test_default_notebook_run_all_and_cell_rerun_are_offline_readonly(monkeypatch):
    """Execute the actual default notebook, denying input, transport and filesystem writes."""
    import IPython.display
    import requests
    notebook = json.loads((ROOT/'notebooks/11_carvana_live_collection_lab.ipynb').read_text(encoding='utf-8'))
    original_open, path_open, connect = builtins.open, Path.open, sqlite3.connect
    def deny(*args, **kwargs): raise AssertionError('Default notebook attempted input, network or a write')
    def readonly_open(file, mode='r', *args, **kwargs):
        if any(flag in mode for flag in 'wax+'): deny()
        return original_open(file, mode, *args, **kwargs)
    def readonly_path_open(path, mode='r', *args, **kwargs):
        if any(flag in mode for flag in 'wax+'): deny()
        return path_open(path, mode, *args, **kwargs)
    def readonly_connect(database, *args, **kwargs):
        assert kwargs.get('uri') is True and str(database).endswith('?mode=ro')
        return connect(database, *args, **kwargs)
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(builtins, 'input', deny)
    monkeypatch.setattr(builtins, 'open', readonly_open)
    monkeypatch.setattr(Path, 'open', readonly_path_open)
    monkeypatch.setattr(Path, 'mkdir', deny)
    monkeypatch.setattr(Path, 'unlink', deny)
    monkeypatch.setattr(Path, 'rename', deny)
    monkeypatch.setattr(Path, 'replace', deny)
    monkeypatch.setattr(sqlite3, 'connect', readonly_connect)
    monkeypatch.setattr(requests.sessions.Session, 'request', deny)
    monkeypatch.setattr(IPython.display, 'display', lambda *args, **kwargs: None)
    scope = {'__name__': '__main__'}
    for cell in notebook['cells']:
        if cell['cell_type'] == 'code':
            exec(compile(''.join(cell['source']), cell['id'], 'exec'), scope)
    assert scope['LAB_SESSION'].budget.requests == 0
    assert scope['trace_row']['vin'] == '1GNSCNKD1PR458818'
    assert scope['normalized_price'] == 40590 and len(scope['rows']) == 52
    session = scope['LAB_SESSION']
    exec(''.join(next(c for c in notebook['cells'] if c['id'] == 'lab-settings')['source']), scope)
    assert session is scope['LAB_SESSION']
