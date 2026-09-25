"""Synthetic HTTP observations only; these tests never contact Carvana."""
import hashlib
import json
from unittest.mock import Mock

import pytest
import requests

from test_search import response_data
from vehicle_tracker.collect import NavigationBudget
from vehicle_tracker.search import collect_search
from vehicle_tracker.search_evidence import (
    response_diagnostics, retain_response_evidence, replay_response, verify_response_evidence,
)


def reply(status=403, body=b'Access denied', headers=None):
    response = requests.Response()
    response.status_code = status
    response._content = body
    response.headers.update(headers or {})
    return response


@pytest.mark.parametrize('status,headers,expected', [
    (403, {'Content-Type': 'Text/HTML; charset=UTF-8', 'Server': 'cloudflare',
           'CF-Ray': '0123456789abcdef-IAD'},
     {'http_status': 403, 'media_type': 'text/html', 'server_family': 'cloudflare',
      'cf_ray': '0123456789abcdef-IAD', 'cf_mitigated': None, 'retry_after_seconds': None}),
    (429, {'Content-Type': 'application/json', 'Server': 'awselb/2.0', 'Retry-After': '120'},
     {'http_status': 429, 'media_type': 'application/json', 'server_family': 'awselb',
      'cf_ray': None, 'cf_mitigated': None, 'retry_after_seconds': 120}),
    (200, {'Content-Type': 'text/html', 'CF-Mitigated': 'challenge'},
     {'http_status': 200, 'media_type': 'text/html', 'server_family': None,
      'cf_ray': None, 'cf_mitigated': 'challenge', 'retry_after_seconds': None}),
])
def test_selected_http_observations_remain_distinct(status, headers, expected):
    diagnostic = response_diagnostics(reply(status, headers=headers))
    assert {key: diagnostic[key] for key in expected} == expected
    assert diagnostic['body_markers']['access_denied'] is True
    assert diagnostic['body_markers']['challenge_markup'] is False
    assert 'not proof' in diagnostic['marker_scope']


def test_no_arbitrary_header_or_body_text_is_retained(tmp_path):
    secret = 'SENSITIVE-ACCOUNT-TOKEN'
    body = (f'<html><title>{secret}</title> Cloudflare Access denied '
            f'<script src="/cdn-cgi/challenge-platform/{secret}"></script> '
            f'private@example.com https://private.invalid/{secret} 192.0.2.4</html>').encode()
    headers = {
        'Content-Type': f'text/html; private={secret}',
        'Server': f'cloudflare {secret}', 'CF-Ray': f'0123456789abcdef-IAD {secret}',
        'CF-Mitigated': f'challenge {secret}', 'Retry-After': secret,
        'Cookie': secret, 'Set-Cookie': secret, 'Authorization': f'Bearer {secret}',
        'Proxy-Authenticate': secret, 'Location': f'https://private.invalid/{secret}',
        'X-Request-ID': secret, 'X-Forwarded-For': '192.0.2.4',
    }
    result = collect_search(filters={}, zip_code='08542', destination=tmp_path/'query',
                            post=Mock(return_value=reply(body=body, headers=headers)))
    evidence = result['pages'][0]['response_evidence']
    diagnostic = evidence['http_diagnostics']
    assert evidence['kind'] == 'not_retained'
    assert evidence['response_content_sha256'] == hashlib.sha256(body).hexdigest()
    assert diagnostic['media_type'] == 'text/html'
    assert all(diagnostic[key] is None for key in
               ('server_family', 'cf_ray', 'cf_mitigated', 'retry_after_seconds'))
    assert diagnostic['body_markers'] == {
        'cloudflare': True, 'cloudfront': False, 'access_denied': True, 'challenge_markup': True}
    for path in (tmp_path/'query').rglob('*.json'):
        retained = path.read_text()
        assert all(value not in retained for value in
                   (secret, 'private@example.com', 'private.invalid', '192.0.2.4', 'Set-Cookie', 'Authorization'))
    assert not (tmp_path/'query/response_sources').exists()


def test_body_scan_is_bounded_and_does_not_claim_unseen_markers():
    body = b'x' * 65536 + b' CloudFront Forbidden cf-chl-private'
    diagnostic = response_diagnostics(reply(body=body))
    assert diagnostic['body_inspected_bytes'] == 65536
    assert diagnostic['body_truncated'] is True
    assert not any(diagnostic['body_markers'].values())
    within = response_diagnostics(reply(body=b'CloudFront: The request could not be satisfied'))
    assert within['body_truncated'] is False
    assert within['body_markers']['cloudfront'] is True
    assert within['body_markers']['access_denied'] is True


@pytest.mark.parametrize('headers', [
    {},
    {'Content-Type': 'private/type', 'Server': 'private-host', 'CF-Ray': 'not-a-ray',
     'CF-Mitigated': 'other', 'Retry-After': 'Wed, 21 Oct 2015 07:28:00 GMT'},
    {'CF-Ray': 'a' * 2000, 'Retry-After': '9' * 2000},
])
def test_missing_or_unrecognized_header_values_stay_unknown(headers):
    diagnostic = response_diagnostics(reply(headers=headers))
    assert all(diagnostic[key] is None for key in
               ('media_type', 'server_family', 'cf_ray', 'cf_mitigated', 'retry_after_seconds'))


def test_unavailable_body_and_invalid_status_are_unknown():
    diagnostic = response_diagnostics(Mock(status_code=True, content=None, headers={}))
    assert diagnostic['http_status'] is None
    assert diagnostic['body_inspected_bytes'] is None and diagnostic['body_truncated'] is None
    assert all(value is None for value in diagnostic['body_markers'].values())


@pytest.mark.parametrize('status,headers,reason', [
    (403, {'Content-Type': 'text/html'}, 'http_access_failure'),
    (429, {'Content-Type': 'text/html', 'Retry-After': '120'}, 'rate_limited; no automatic retries'),
    (200, {'Content-Type': 'text/html', 'CF-Mitigated': 'challenge'}, 'cloudflare_challenge'),
])
def test_diagnostics_preserve_first_page_stop_and_zero_observations(tmp_path, status, headers, reason):
    post = Mock(return_value=reply(status, headers=headers))
    budget = NavigationBudget()
    result = collect_search(filters={}, zip_code='08542', destination=tmp_path/'query',
                            budget=budget, post=post)
    assert post.call_count == budget.requests == result['requests'] == 1
    assert budget.stopped and result['status'] == 'blocked'
    assert result['reason'] == reason and result['outcome_kind'] == 'access_failure'
    assert not result['query_complete'] and result['complete_query_count'] is None
    assert result['unique_listings'] == result['unique_vins'] == 0
    assert len(result['pages']) == 1
    assert result['pages'][0]['response_evidence']['http_diagnostics']['http_status'] == status


def test_server_failure_retries_after_backoff(tmp_path, response_data, monkeypatch):
    sleeps = []
    monkeypatch.setattr('vehicle_tracker.search.time.sleep', lambda seconds: sleeps.append(seconds))
    body = json.dumps(response_data).encode()
    calls = {'n': 0}

    def post(url, **kwargs):
        calls['n'] += 1
        if calls['n'] == 1:
            return reply(520, b'origin', {'Content-Type': 'text/plain'})
        return reply(200, body, {'Content-Type': 'application/json'})

    budget = NavigationBudget(max_requests=4, max_seconds=60)
    result = collect_search(filters={}, zip_code='08542', destination=tmp_path/'query',
                            budget=budget, post=post, page_retries=2, target_listings=1)
    assert calls['n'] == 2 and result['query_complete']
    assert result['pages'][0]['outcome_kind'] == 'server_failure'
    assert 10 in sleeps


def test_cloudflare_challenge_does_not_retry(tmp_path):
    post = Mock(return_value=reply(520, b'challenge', {
        'Content-Type': 'text/html', 'CF-Mitigated': 'challenge'}))
    result = collect_search(filters={}, zip_code='08542', destination=tmp_path/'query',
                            post=post, page_retries=2)
    assert post.call_count == 1
    assert result['outcome_kind'] == 'access_failure'
    assert result['reason'] == 'cloudflare_challenge'


def test_persistent_server_failure_stops_after_the_retry_allowance(tmp_path):
    post = Mock(return_value=reply(520, b'origin', {'Content-Type': 'text/plain'}))
    result = collect_search(filters={}, zip_code='08542', destination=tmp_path/'query',
                            post=post, page_retries=2, retry_backoff_seconds=[0, 0])
    assert post.call_count == 3
    assert result['outcome_kind'] == 'server_failure' and result['status'] == 'blocked'


def test_additive_diagnostics_leave_legacy_source_and_replay_compatible(tmp_path, response_data):
    content = json.dumps(response_data).encode()
    evidence = retain_response_evidence(
        reply(200, content, {'Content-Type': 'application/json'}), tmp_path/'sources')
    legacy = {key: value for key, value in evidence.items() if key != 'http_diagnostics'}
    assert verify_response_evidence(evidence) == verify_response_evidence(legacy) == response_data
    request = {'filters': {}, 'pagination': {'page': 1, 'pageSize': 24},
               'sortBy': 'MostPopular', 'zip5': '08542'}
    current_projection, current_rows = replay_response(evidence, request, observed_at='2026-09-19T12:00:00Z')
    old_projection, old_rows = replay_response(legacy, request, observed_at='2026-09-19T12:00:00Z')
    assert current_projection == old_projection and current_rows.equals(old_rows)
    assert evidence['contract'] == legacy['contract'] == 'carvana-search-source-v1'
    assert evidence['response_content_sha256'] == evidence['source_sha256'] == hashlib.sha256(content).hexdigest()
    unavailable = retain_response_evidence(reply(), tmp_path/'absent')
    unavailable.pop('http_diagnostics')
    assert verify_response_evidence(unavailable) is None
