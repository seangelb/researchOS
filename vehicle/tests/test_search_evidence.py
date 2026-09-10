"""Offline evidence/recovery contracts, including real serialized response bodies."""
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from test_search import response_data
from vehicle_tracker.search import collect_search, search_transport
from vehicle_tracker.search_evidence import replay_response, verify_response_evidence
from vehicle_tracker.search_plan import collect_plan
from vehicle_tracker.history import import_reports, read_history, read_query_evidence


def response(data=None, *, content=None, status=200, content_type='application/json'):
    result = requests.Response()
    result.status_code = status
    result.headers['content-type'] = content_type
    result._content = json.dumps(data).encode() if content is None else content
    return result


def collect(tmp_path, data=None, **kwargs):
    return collect_search(filters={}, zip_code='08542', destination=tmp_path/'query',
                          post=Mock(return_value=response(data, **kwargs)))


@pytest.mark.parametrize('mutation', ['missing_inventory', 'bad_inventory', 'bad_pagination', 'bad_price', 'bad_native'])
def test_schema_failure_retains_replayable_source_before_projection(tmp_path, response_data, mutation):
    data = copy.deepcopy(response_data)
    if mutation == 'missing_inventory': data.pop('inventory')
    if mutation == 'bad_inventory': data['inventory'] = None
    if mutation == 'bad_pagination': data['inventory']['pagination']['totalMatchedPages'] = 9
    if mutation == 'bad_price': data['inventory']['vehicles'][0]['price'] = None
    if mutation == 'bad_native': data['inventory']['vehicles'][0]['isPurchasePending'] = 7
    result = collect(tmp_path, data)
    page = result['pages'][0]
    assert not result['query_complete'] and result['outcome_kind'] == 'schema_failure'
    evidence = page['response_evidence']
    assert verify_response_evidence(evidence) == data
    assert evidence['kind'] == 'original_response_content'
    assert Path(evidence['source_path']).read_bytes() == json.dumps(data).encode()
    with pytest.raises((ValueError, KeyError)):
        replay_response(evidence, json.loads(Path(page['retained_source']).read_text())['request'],
                        observed_at=page['response_received_at_utc'])
    run, captures, rows = read_query_evidence(tmp_path/'query/run_report.json')
    assert run['query_complete'] == 0 and len(captures) == 1 and rows.empty


def test_selected_source_excludes_credentials_and_replays_rows(tmp_path, response_data):
    data = copy.deepcopy(response_data)
    data['account'] = {'email': 'private@example.com', 'token': 'SENSITIVE-TOKEN'}
    data['inventory']['vehicles'][0]['secretToken'] = 'SENSITIVE-TOKEN'
    result = collect(tmp_path, data)
    page = result['pages'][0]
    evidence = page['response_evidence']
    assert result['query_complete'] and evidence['kind'] == 'selected_source'
    assert verify_response_evidence(evidence) == response_data
    assert evidence['response_content_sha256'] != evidence['source_sha256']
    assert evidence['limitation']
    for path in (tmp_path/'query').rglob('*.json'):
        assert 'SENSITIVE-TOKEN' not in path.read_text() and 'private@example.com' not in path.read_text()
    projection, rows = replay_response(evidence, {'filters': {}, 'pagination': {'page': 1, 'pageSize': 24},
        'sortBy': 'MostPopular', 'zip5': '08542'}, observed_at=page['response_received_at_utc'])
    assert len(rows) == 3 and rows.asking_price_usd.iloc[0] == 14590
    assert page['request_started_at_utc'] <= page['response_received_at_utc'] <= page['evidence_available_at_utc']


@pytest.mark.parametrize('body,ctype,status', [
    (b'{"private":"SECRET",', 'application/json', 200),
    (b'<html>SECRET private account</html>', 'text/html', 200),
    (b'{"private":"SECRET"}', 'application/json', 403)])
def test_unretainable_body_has_explicit_replay_limit(tmp_path, body, ctype, status):
    result = collect(tmp_path, content=body, status=status, content_type=ctype)
    evidence = result['pages'][0]['response_evidence']
    assert evidence['kind'] == 'not_retained' and evidence['limitation']
    assert evidence['response_content_sha256'] == hashlib.sha256(body).hexdigest()
    for path in (tmp_path/'query').rglob('*.json'):
        assert 'SECRET' not in path.read_text()
    with pytest.raises(ValueError, match='Body unavailable'):
        replay_response(evidence, {}, observed_at='2026-09-08T12:00:00Z')


def test_unsafe_value_is_retained_as_redacted_not_admitted(tmp_path, response_data):
    response_data['inventory']['vehicles'][0]['vin'] = 'private@example.com'
    result = collect(tmp_path, response_data)
    evidence = result['pages'][0]['response_evidence']
    assert evidence['kind'] == 'selected_source' and evidence['redacted_values'] == 1
    assert not result['query_complete'] and result['unique_listings'] == 0
    assert 'private@example.com' not in Path(evidence['source_path']).read_text()


def test_duplicate_json_key_cannot_hide_secret_in_original_bytes(tmp_path, response_data):
    body = json.dumps(response_data).replace('"make":', '"make":"Bearer SECRET", "make":', 1).encode()
    result = collect(tmp_path, content=body)
    evidence = result['pages'][0]['response_evidence']
    assert evidence['kind'] == 'selected_source' and evidence['ambiguous_json']
    assert not result['query_complete']
    assert all('SECRET' not in p.read_text() for p in (tmp_path/'query').rglob('*.json'))


@pytest.mark.parametrize('key,value', [('source_hash_scope', 'original wire bytes'),
                                    ('response_hash_scope', 'projection hash'), ('redacted_values', -1)])
def test_hash_scope_is_validated(tmp_path, response_data, key, value):
    result = collect(tmp_path, response_data)
    evidence = result['pages'][0]['response_evidence']
    evidence[key] = value
    with pytest.raises(ValueError, match='scope'):
        verify_response_evidence(evidence)


def test_connection_reuse_does_not_send_cookie_netrc_or_proxy(monkeypatch, response_data):
    import vehicle_tracker.search as search
    original = requests.Session
    sessions, sent = [], []
    def session():
        current = original()
        sessions.append(current)
        def send(prepared, **kwargs):
            sent.append((dict(prepared.headers), kwargs, current.trust_env))
            current.cookies.set('personal', 'SECRET')  # Simulate a Set-Cookie response.
            return response(response_data)
        current.send = send
        current.close = Mock(wraps=current.close)
        return current
    monkeypatch.setattr(search.requests, 'Session', session)
    monkeypatch.setattr(requests.sessions, 'get_netrc_auth', Mock(side_effect=AssertionError('netrc forbidden')))
    monkeypatch.setenv('HTTPS_PROXY', 'http://private-proxy.invalid')
    with search_transport() as send:
        for _ in range(2): send('https://apik.carvana.io/merch/search/api/v2/search', json={}, allow_redirects=False)
    assert len(sessions) == 1 and len(sent) == 2
    assert all('Cookie' not in h and 'Authorization' not in h and not kw['proxies'] and not trust for h, kw, trust in sent)
    assert not sessions[0].cookies
    sessions[0].close.assert_called_once()


def test_slow_checkpoint_cannot_shorten_actual_post_spacing(tmp_path, response_data, monkeypatch):
    import vehicle_tracker.search as search
    moment, starts = [0.0], []
    monkeypatch.setattr('vehicle_tracker.collect.time.monotonic', lambda: moment[0])
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', lambda seconds: moment.__setitem__(0, moment[0]+seconds))
    original = search.write_json_atomic
    delayed = [False]
    def checkpoint(path, data):
        if data.get('outcome_kind') == 'request_reserved' and not delayed[0]:
            moment[0] += 5.0
            delayed[0] = True
        original(path, data)
    monkeypatch.setattr(search, 'write_json_atomic', checkpoint)
    first = copy.deepcopy(response_data)
    first['inventory']['pagination'].update(totalMatchedInventory=27, totalMatchedPages=2)
    def post(*args, **kwargs):
        starts.append(moment[0])
        if len(starts) == 1:
            return response(first)
        changed = copy.deepcopy(first)
        changed['inventory']['pagination'].update(currentPage=2, totalMatchedInventory=28)
        return response(changed)
    collect_search(filters={}, zip_code='08542', destination=tmp_path/'query', post=post)
    assert starts == [5.0, 8.0]


@pytest.mark.parametrize('failure', ['access', 'transport'])
def test_generic_resume_cannot_reset_access_stop(tmp_path, response_data, failure):
    plan = [dict(query_id='all', zip_code='08542', filters={})]
    post = Mock(return_value=response(status=403)) if failure == 'access' else Mock(side_effect=TimeoutError('SECRET'))
    collect_plan(plan, destination=tmp_path/'old', post=post)
    future = Mock(return_value=response(response_data))
    with pytest.raises(ValueError, match='blocks resume'):
        collect_plan(plan, destination=tmp_path/'new', resume_from=tmp_path/'old/run_report.json', post=future)
    future.assert_not_called()
    assert not (tmp_path/'new').exists()


def test_two_observations_are_distinct_but_imports_are_idempotent(tmp_path, response_data):
    reports = []
    for name in ('one', 'two'):
        folder = tmp_path/name
        result = collect_search(filters={}, zip_code='08542', destination=folder,
            post=Mock(return_value=response(response_data)))
        assert result['query_complete']
        reports.append(folder/'run_report.json')
    database = tmp_path/'history.sqlite'
    assert import_reports(reports, database).imported_rows.sum() == 6
    original = database.read_bytes()
    assert import_reports(reports, database).imported_rows.sum() == 0
    assert database.read_bytes() == original
    runs, captures, rows = read_history(database)
    assert len(runs) == 2 and len(rows) == 6 and captures.capture_id.nunique() == 2
    assert rows.vin.nunique() == 3
    assert runs.imported_at_utc.notna().all() and captures.evidence_available_at_utc.notna().all()


def test_changed_total_and_membership_never_complete(tmp_path, response_data, monkeypatch):
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', lambda _: None)
    first = copy.deepcopy(response_data)
    first['inventory']['pagination'].update(totalMatchedInventory=27, totalMatchedPages=2)
    second = copy.deepcopy(first)
    second['inventory']['pagination'].update(currentPage=2, totalMatchedInventory=28)
    for v in second['inventory']['vehicles']:
        v['vehicleId'] += 100
        v['vin'] = v['vin'][:-1] + ('1' if v['vin'][-1] != '1' else '2')
    result = collect_search(filters={}, zip_code='08542', destination=tmp_path/'query',
        post=Mock(side_effect=[response(first), response(second)]))
    assert not result['query_complete'] and result['outcome_kind'] == 'pagination_unstable'
    assert result['unique_listings'] == 3
    assert result['pages'][1]['response_evidence']['source_path']


def test_postcommit_failure_does_not_overwrite_successful_sqlite_page(tmp_path, response_data, monkeypatch):
    import vehicle_tracker.search as search
    from vehicle_tracker.storage import read_snapshots, store_capture
    def fail_after_commit(*args, **kwargs):
        store_capture(*args, **kwargs)
        raise OSError('SECRET filesystem interruption')
    monkeypatch.setattr(search, 'store_capture', fail_after_commit)
    result = collect(tmp_path, response_data)
    assert not result['query_complete'] and result['outcome_kind'] == 'storage_failure'
    captures, rows = read_snapshots(tmp_path/'query/vehicle.sqlite')
    assert len(rows) == 3 and captures.status.tolist() == ['parsed']
    page = result['pages'][0]
    assert page['status'] == 'retained' and page['database_outcome'] == 'unconfirmed'
    # Explicit replay to a new database is safe; the old incomplete query is not
    # promoted or spliced into a later query. Replaying twice is the same import.
    args = dict(run_id=result['run_id'], page_number=1, raw_file=Path(page['retained_source']))
    assert store_capture(tmp_path/'recovered.sqlite', **args) == 3
    assert store_capture(tmp_path/'recovered.sqlite', **args) == 3
    assert 'SECRET' not in (tmp_path/'query/run_report.json').read_text()
