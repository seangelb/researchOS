"""Carvana's observed public search request. No browser credentials or sale inference."""
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import time
from uuid import uuid4

import requests
import pandas as pd

from vehicle_tracker.carvana import native_values, parse_projection
from vehicle_tracker.collect import NavigationBudget, CollectionStopped
from vehicle_tracker.storage import retain_capture, store_capture, write_json_atomic
from vehicle_tracker.search_evidence import retain_response_evidence, verify_response_evidence

ENDPOINT = 'https://apik.carvana.io/merch/search/api/v2/search'
FIELDS = ('vehicleId', 'vin', 'year', 'make', 'model', 'parentModel', 'mileage',
          'isPurchasePending', 'vehicleLockType', 'vehiclePurchaseType',
          'vehicleInventoryType', 'isOnDemand', 'transportCost')


def project_response(data, request, *, observed_at):
    """Retain public inventory fields only; this is not an original response body.

    price.total agrees with USD asking prices in the observed US search/DOM comparison.
    Financing, analytics IDs, cookies and unrelated response fields are not retained.
    """
    if not isinstance(data, dict) or not isinstance(data.get('inventory'), dict):
        raise ValueError('Missing or invalid inventory object')
    inventory = data['inventory']
    if not isinstance(inventory.get('pagination'), dict) or not isinstance(inventory.get('vehicles'), list):
        raise ValueError('Missing or invalid pagination/vehicles shape')
    if any(not isinstance(v, dict) or not isinstance(v.get('price', {}), dict) for v in inventory['vehicles']):
        raise ValueError('Invalid vehicle/price shape')
    if not isinstance(data.get('userDeliveryInfo', {}), dict):
        raise ValueError('Invalid delivery context shape')
    pagination = inventory['pagination']
    for key in ('currentPage', 'pageSize', 'totalMatchedInventory', 'totalMatchedPages'):
        if type(pagination[key]) is not int or pagination[key] < 0:
            raise ValueError('Invalid pagination count')
    if pagination['currentPage'] != request['pagination']['page'] or pagination['pageSize'] != request['pagination']['pageSize']:
        raise ValueError('Returned pagination differs from the requested page')
    if pagination['pageSize'] != 24 or pagination['totalMatchedPages'] != (pagination['totalMatchedInventory'] + 23) // 24:
        raise ValueError('Inconsistent search pagination; coverage cannot be established')
    if len(inventory['vehicles']) > min(24, pagination['totalMatchedInventory']):
        raise ValueError('More vehicles than the declared batch or total')
    safe_request = {k: request[k] for k in ('filters', 'pagination', 'sortBy', 'zip5')}
    if 'requestedFeatures' in request:
        safe_request['requestedFeatures'] = request['requestedFeatures']
    vehicles = [{**{k: v.get(k) for k in FIELDS}, 'price': {'total': v.get('price', {}).get('total')}}
                for v in inventory['vehicles']]
    return dict(capture_method='carvana_search_projection', page_url='https://www.carvana.com/cars',
                endpoint=ENDPOINT, captured_at_utc=observed_at, requested_zip=request['zip5'],
                zip_code=data.get('userDeliveryInfo', {}).get('zip5'),
                request=safe_request, pagination={k: pagination[k] for k in
                    ('currentPage', 'pageSize', 'totalMatchedInventory', 'totalMatchedPages')},
                vehicles=vehicles, reported_total_text=str(pagination['totalMatchedInventory']) + ' cars')


def parse_search_capture(capture):
    """Normalize public inventory into the existing observation columns."""
    if capture['endpoint'] != ENDPOINT or capture['pagination']['currentPage'] != capture['request']['pagination']['page']:
        raise ValueError('Unexpected source or pagination')
    if capture.get('zip_code') != capture['requested_zip']:
        raise ValueError('Returned ZIP differs from requested context or is missing')
    if not capture['vehicles'] and capture['pagination']['totalMatchedInventory'] == capture['pagination']['totalMatchedPages'] == 0:
        return pd.DataFrame(columns=['retailer', 'listing_id', 'vin', 'observed_at_utc',
            'year', 'make', 'model', 'mileage_miles', 'asking_price_usd', 'condition_native',
            'availability_native', 'card_text', 'listing_url', 'source_url'])
    rows = []
    for v in capture['vehicles']:
        native_values(v)  # Reject native type drift before admitting query coverage.
        native = {k: v.get(k) for k in ('isPurchasePending', 'vehicleLockType', 'vehiclePurchaseType', 'isOnDemand')}
        rows.append(dict(retailer='carvana', listing_id=str(v['vehicleId']), vin=v.get('vin'),
            year=v.get('year'), make=v.get('make'), model=v.get('model'), mileage_miles=v.get('mileage'),
            asking_price_usd=v.get('price', {}).get('total'), condition_native=None,
            availability_native=None, card_status_native=json.dumps(native, sort_keys=True),
            shipping_native=None if v.get('transportCost') is None else f"transportCost USD: {v['transportCost']}"))
    frame = parse_projection(dict(capture, rows=rows, visible_listing_count=len(rows)))
    filters = capture['request']['filters']
    year = filters.get('year', {})
    if year and frame.year.isna().any():
        raise ValueError('Missing year prevents validation of requested year filters')
    if ('min' in year and (frame.year < year['min']).any()) or ('max' in year and (frame.year > year['max']).any()):
        raise ValueError('Returned model years violate requested filters')
    makes = filters.get('makes', [])
    if makes:
        for v in capture['vehicles']:
            matched = [m for m in makes if m['name'].casefold() == str(v.get('make')).casefold()]
            if not matched or not any(not m.get('parentModels') or str(v.get('parentModel')).casefold() in
                    {x['name'].casefold() for x in m['parentModels']} for m in matched):
                raise ValueError('Returned vehicle violates requested make/model filters')
    return frame


@contextmanager
def search_transport(post=None):
    """Reuse connections within an invocation, without ambient auth or cookies.

    An injected post function is for explicit offline tests. No speedup is claimed.
    """
    if post is not None:
        yield post
        return
    with requests.Session() as session:
        session.trust_env = False  # No netrc authentication or environment proxies.
        def send(url, **kwargs):
            session.cookies.clear()
            try:
                return session.post(url, **kwargs)
            finally:
                session.cookies.clear()  # Never carry Set-Cookie to the next request.
        yield send


def collect_search(*, filters, zip_code, destination, target_listings=1000, budget=None, post=None,
                   location_filter=False, known_listing_ids=None):
    """Collect one query with immutable evidence and an explicit attempt journal."""
    with search_transport(post) as send:
        return _collect_search(filters=filters, zip_code=zip_code, destination=destination,
            target_listings=target_listings, budget=budget, post=send,
            location_filter=location_filter, known_listing_ids=known_listing_ids)


def _collect_search(*, filters, zip_code, destination, target_listings, budget, post,
                    location_filter, known_listing_ids):
    if not re.fullmatch(r'\d{5}', zip_code) or (target_listings is not None and
            (type(target_listings) is not int or target_listings < 1)) or type(location_filter) is not bool:
        raise ValueError('Require a five-digit ZIP and positive listing target')
    if set(filters) - {'makes', 'year'}:
        raise ValueError('Only the observed make/model/year filter contract is supported')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    (destination/'attempts').mkdir()
    budget = budget or NavigationBudget()
    run_id, seen, vins = uuid4().hex, set(), set()
    known = {identity for retailer, identity in (known_listing_ids or set()) if retailer == 'carvana'}
    start_requests, started = budget.requests, time.monotonic()
    report = dict(run_id=run_id, filters=filters, zip_code=zip_code, endpoint=ENDPOINT,
        started_utc=datetime.now(timezone.utc).isoformat(), target_listings=target_listings,
        target_definition='New listing IDs contributed beyond earlier plan queries',
        pages=[], status='partial', reason='', query_complete=False, complete_query_count=None,
        national_coverage_verified=False, location_filter=location_filter,
        population='Public search response; national retail universe unverified',
        evidence_contract='carvana-search-source-v1', unique_listings=0, unique_vins=0,
        normalizer_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    report['normalizer_source_hashes'] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ('search.py', 'search_evidence.py', 'carvana.py', 'history.py')}
    total = pages_total = None
    number = 1

    def checkpoint(entry=None):
        report.update(unique_listings=len(seen), unique_vins=len(vins), reported_total=total,
            complete_query_count=len(seen) if report['query_complete'] else None,
            new_unique_listings=len(seen-known), target_reached=target_listings is not None and len(seen-known)>=target_listings,
            requests=budget.requests-start_requests, elapsed_seconds=time.monotonic()-started,
            ended_utc=datetime.now(timezone.utc).isoformat())
        if entry is not None:
            write_json_atomic(destination/'attempts'/f"{entry['page']:04d}.json",
                dict(run_id=run_id, request=request, **entry))
        write_json_atomic(destination/'run_report.json', report)

    checkpoint()
    while True:
        request = dict(filters=filters, pagination=dict(page=number, pageSize=24), sortBy='MostPopular', zip5=zip_code)
        if location_filter:
            request['requestedFeatures'] = ['LocationBasedPrefiltering']
        entry = dict(page=number, attempt_id=uuid4().hex, status='pending', stored_rows=0,
            outcome_kind='unattempted', request_started_at_utc=None, response_received_at_utc=None,
            evidence_available_at_utc=None)
        capture = dict(page_url='https://www.carvana.com/cars', captured_at_utc=None,
            request=request, capture_method='failed_search', records=[], attempt_id=entry['attempt_id'])
        report['pages'].append(entry)
        retained, response, interrupted = None, None, None
        stage = 'budget_exhausted'
        checkpoint(entry)  # Discoverable even if the process dies before transport.
        try:
            budget.before_navigation()
            entry.update(request_reserved_at_utc=datetime.now(timezone.utc).isoformat(), outcome_kind='request_reserved')
            checkpoint(entry)
            stage = 'budget_exhausted'
            timeout = budget.timeout_ms(30000)/1000
            stage = 'transport_failure'
            budget.request_started()  # No filesystem work between this and the POST.
            entry['request_started_at_utc'] = datetime.now(timezone.utc).isoformat()
            tick = time.monotonic()
            response = post(ENDPOINT, json=request, timeout=timeout,
                allow_redirects=False, headers={'User-Agent': 'researchOS personal inventory research', 'Accept': 'application/json'})
            stamp = datetime.now(timezone.utc).isoformat()
            entry.update(http_status=response.status_code, elapsed_seconds=time.monotonic()-tick,
                response_received_at_utc=stamp, response_bytes=len(response.content),
                response_sha256=hashlib.sha256(response.content).hexdigest(),
                response_hash_scope='SHA-256 of requests.Response.content; not wire bytes')
            capture['captured_at_utc'] = stamp
            stage = 'storage_failure'
            checkpoint(entry)
            evidence = retain_response_evidence(response, destination/'response_sources')
            capture['response_evidence'] = evidence
            entry['response_evidence'] = evidence
            checkpoint(entry)  # Source is durable before projection is attempted.
            budget.response_received()
            headers = response.headers
            stage = 'access_failure'
            if response.status_code == 429:
                retry = headers.get('retry-after', '')
                entry['retry_after_seconds'] = int(retry) if str(retry).isdigit() else None
                raise CollectionStopped('rate_limited; no automatic retries')
            if headers.get('cf-mitigated') == 'challenge':
                raise CollectionStopped('cloudflare_challenge')
            if response.status_code != 200:
                raise CollectionStopped('http_access_failure')
            if 'application/json' not in headers.get('content-type', ''):
                raise CollectionStopped('unexpected_content')
            stage = 'schema_failure'
            if evidence['kind'] == 'not_retained' or evidence.get('redacted_values', 0) or evidence.get('ambiguous_json', False):
                raise ValueError('Response cannot be safely replayed as public inventory')
            source = verify_response_evidence(evidence)
            capture = dict(project_response(source, request, observed_at=stamp),
                           attempt_id=entry['attempt_id'], response_evidence=evidence)
            stage = 'storage_failure'
            retained = retain_capture(capture, destination/'raw')
            entry.update(retained_source=str(retained), source_sha256=hashlib.sha256(retained.read_bytes()).hexdigest(),
                source_hash_scope='SHA-256 of selected projection JSON bytes',
                evidence_available_at_utc=datetime.now(timezone.utc).isoformat(), status='retained')
            checkpoint(entry)  # Retention and the SQLite commit are separate steps.
            stage = 'pagination_unstable'
            raw_ids = [v['vehicleId'] for v in capture['vehicles']]
            raw_vins = [v.get('vin') for v in capture['vehicles']]
            if len(set(raw_ids)) != len(raw_ids) or len(set(raw_vins)) != len(raw_vins):
                raise CollectionStopped('Repeated identities; query coverage unstable')
            stage = 'schema_failure'
            frame = parse_search_capture(capture)
            page_info = capture['pagination']
            if total is None:
                total, pages_total = page_info['totalMatchedInventory'], page_info['totalMatchedPages']
            stage = 'pagination_unstable'
            if (total, pages_total) != (page_info['totalMatchedInventory'], page_info['totalMatchedPages']):
                raise CollectionStopped('Displayed totals changed during enumeration')
            current, current_vins = set(frame.listing_id), set(frame.vin.dropna())
            if current & seen or current_vins & vins or frame.vin.isna().any():
                raise CollectionStopped('Repeated or missing identities; query coverage unstable')
            stage = 'storage_failure'
            entry['stored_rows'] = store_capture(destination/'vehicle.sqlite', run_id=run_id, page_number=number, raw_file=retained)
            entry.update(status='parsed', outcome_kind='success')
            seen.update(current)
            vins.update(current_vins)
            if number == max(1, pages_total):
                report['query_complete'] = len(seen) == total
                report['status'] = 'complete_query' if report['query_complete'] else 'partial'
                report['reason'] = '' if report['query_complete'] else 'Unique count differs from reported total'
                if not report['query_complete']:
                    report['outcome_kind'] = 'pagination_unstable'
            elif target_listings is not None and len(seen - known) >= target_listings:
                report.update(reason='Target reached; remaining query pages were not collected', outcome_kind='sample_limit')
        except BaseException as exc:
            if not isinstance(exc, Exception):
                interrupted = exc
            if stage in {'access_failure', 'transport_failure', 'storage_failure', 'budget_exhausted'}:
                budget.stop()
            reason = str(exc) if isinstance(exc, CollectionStopped) else f'{type(exc).__name__}: {stage}'
            report.update(status='blocked', reason=reason, outcome_kind=stage, query_complete=False)
            entry.update(error=reason, outcome_kind=stage)
            if stage == 'storage_failure':
                # A commit may already have happened. Never overwrite it as a failed
                # capture; replay/idempotence checks or a new query attempt resolve it.
                entry['database_outcome'] = 'unconfirmed'
            else:
                entry['status'] = 'failed'
                try:
                    retained = retained or retain_capture(capture, destination/'raw')
                    entry.update(retained_source=str(retained), source_sha256=hashlib.sha256(retained.read_bytes()).hexdigest(),
                        source_hash_scope='SHA-256 of selected projection/failure JSON bytes',
                        evidence_available_at_utc=datetime.now(timezone.utc).isoformat())
                    store_capture(destination/'vehicle.sqlite', run_id=run_id, page_number=number, raw_file=retained, error=reason)
                except Exception:
                    entry['database_outcome'] = 'unconfirmed'
                    report['outcome_kind'] = 'storage_failure'
            checkpoint(entry)
        finally:
            if response is not None:
                response.close()
        checkpoint(entry)
        if interrupted is not None:
            raise interrupted
        if report['status']=='blocked' or number==max(1, pages_total or 0) or (target_listings is not None and len(seen-known)>=target_listings):
            break
        number += 1
    return report
