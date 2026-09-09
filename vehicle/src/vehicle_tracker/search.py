"""Carvana's observed public search request. No browser credentials or sale inference."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from uuid import uuid4

import requests
import pandas as pd

from vehicle_tracker.carvana import parse_projection
from vehicle_tracker.collect import NavigationBudget, CollectionStopped
from vehicle_tracker.storage import retain_capture, store_capture, write_json_atomic

ENDPOINT = 'https://apik.carvana.io/merch/search/api/v2/search'
FIELDS = ('vehicleId', 'vin', 'year', 'make', 'model', 'parentModel', 'mileage',
          'isPurchasePending', 'vehicleLockType', 'vehiclePurchaseType',
          'vehicleInventoryType', 'isOnDemand', 'transportCost')


def project_response(data, request, *, observed_at):
    """Retain public inventory fields only; this is not an original response body.

    price.total agrees with USD asking prices in the observed US search/DOM comparison.
    Financing, analytics IDs, cookies and unrelated response fields are not retained.
    """
    inventory = data['inventory']
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


def collect_search(*, filters, zip_code, destination, target_listings=1000, budget=None, post=None,
                   location_filter=False, known_listing_ids=None):
    """Save whole response batches until a target, complete query, or failure.

    A target-limited run is explicitly partial. Repeated IDs or changing totals stop it.
    Raw evidence and failed attempts survive. The existing SQLite tables are reused.
    """
    if not re.fullmatch(r'\d{5}', zip_code) or (target_listings is not None and (type(target_listings) is not int or target_listings < 1)) or type(location_filter) is not bool:
        raise ValueError('Require a five-digit ZIP and positive listing target')
    if set(filters) - {'makes', 'year'}:
        raise ValueError('Only the observed make/model/year filter contract is supported')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    budget = budget or NavigationBudget()
    post = post or requests.post
    run_id, seen, vins = uuid4().hex, set(), set()
    known = {identity for retailer, identity in (known_listing_ids or set()) if retailer == 'carvana'}
    start_requests, started = budget.requests, time.monotonic()
    report = dict(run_id=run_id, filters=filters, zip_code=zip_code, endpoint=ENDPOINT,
        started_utc=datetime.now(timezone.utc).isoformat(), target_listings=target_listings,
        target_definition='New listing IDs contributed beyond earlier plan queries',
        pages=[], status='partial', reason='', query_complete=False, complete_query_count=None,
        national_coverage_verified=False, location_filter=location_filter,
        population='Public search response; national retail universe unverified')
    total = pages_total = None
    number = 1
    report['normalizer_code_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    write_json_atomic(destination/'run_report.json', report)
    while True:
        request = dict(filters=filters, pagination=dict(page=number, pageSize=24), sortBy='MostPopular', zip5=zip_code)
        if location_filter:
            request['requestedFeatures'] = ['LocationBasedPrefiltering']
        stamp = datetime.now(timezone.utc).isoformat()
        capture = dict(page_url='https://www.carvana.com/cars', captured_at_utc=stamp,
                       request=request, capture_method='failed_search', records=[])
        retained = None
        entry = dict(page=number, status='failed', stored_rows=0)
        try:
            budget.before_navigation()
            tick = time.monotonic()
            response = post(ENDPOINT, json=request, timeout=budget.timeout_ms(30000)/1000,
                allow_redirects=False, headers={'User-Agent': 'researchOS personal inventory research', 'Accept': 'application/json'})
            entry.update(http_status=response.status_code, elapsed_seconds=time.monotonic()-tick,
                         response_bytes=len(response.content), response_sha256=hashlib.sha256(response.content).hexdigest())
            headers = response.headers
            if response.status_code == 429:
                budget.stop()
                retry = headers.get('retry-after', '')
                entry['retry_after_seconds'] = int(retry) if str(retry).isdigit() else None
                raise CollectionStopped('rate_limited; no automatic retries')
            if headers.get('cf-mitigated') == 'challenge':
                budget.stop()
                raise CollectionStopped('cloudflare_challenge')
            if response.status_code != 200:
                budget.stop()
                raise CollectionStopped('http_access_failure')
            if 'application/json' not in headers.get('content-type', ''):
                budget.stop()
                raise CollectionStopped('unexpected_content')
            budget.response_received()
            capture = project_response(response.json(), request, observed_at=datetime.now(timezone.utc).isoformat())
            retained = retain_capture(capture, destination/'raw')
            frame = parse_search_capture(capture)
            page_info = capture['pagination']
            if total is None:
                total, pages_total = page_info['totalMatchedInventory'], page_info['totalMatchedPages']
            if (total, pages_total) != (page_info['totalMatchedInventory'], page_info['totalMatchedPages']):
                raise CollectionStopped('Displayed totals changed during enumeration')
            current, current_vins = set(frame.listing_id), set(frame.vin.dropna())
            if current & seen or current_vins & vins or frame.vin.isna().any() or frame.vin.duplicated().any():
                raise CollectionStopped('Repeated or missing identities; query coverage unstable')
            entry['stored_rows'] = store_capture(destination/'vehicle.sqlite', run_id=run_id, page_number=number, raw_file=retained)
            entry['status'] = 'parsed'
            seen.update(current)
            vins.update(current_vins)
            if number == max(1, pages_total):
                report['query_complete'] = len(seen) == total
                report['status'] = 'complete_query' if report['query_complete'] else 'partial'
                report['reason'] = '' if report['query_complete'] else 'Unique count differs from reported total'
            elif target_listings is not None and len(seen - known) >= target_listings:
                report['reason'] = 'Target reached; remaining query pages were not collected'
        except Exception as exc:
            if 'http_status' not in entry:
                budget.stop()  # Transport/budget failure: do not try another partition.
            reason = str(exc) if isinstance(exc, CollectionStopped) else f'{type(exc).__name__}: response validation failed'
            report.update(status='blocked', reason=reason)
            retained = retained or retain_capture(capture, destination/'raw')
            store_capture(destination/'vehicle.sqlite', run_id=run_id, page_number=number, raw_file=retained, error=reason)
            entry['error'] = reason
        entry['retained_source'] = str(retained)
        entry['source_sha256'] = hashlib.sha256(Path(retained).read_bytes()).hexdigest()
        report['pages'].append(entry)
        report.update(unique_listings=len(seen), unique_vins=len(vins), reported_total=total,
            complete_query_count=len(seen) if report['query_complete'] else None,
            new_unique_listings=len(seen-known), target_reached=target_listings is not None and len(seen-known)>=target_listings,
            requests=budget.requests-start_requests,
            elapsed_seconds=time.monotonic()-started, ended_utc=datetime.now(timezone.utc).isoformat())
        write_json_atomic(destination/'run_report.json', report)
        if report['status']=='blocked' or number==max(1, pages_total or 0) or (target_listings is not None and len(seen-known)>=target_listings):
            break
        number += 1
    return report
