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


def build_search_request(*, filters, zip_code, page=1, location_filter=False):
    """Use one public payload for collection, retained replay and notebook preview."""
    request = dict(filters=filters, pagination=dict(page=page, pageSize=24),
                   sortBy='MostPopular', zip5=zip_code)
    if location_filter:
        request['requestedFeatures'] = ['LocationBasedPrefiltering']
    return request


def _validate_pagination(pagination, request, vehicles):
    for key in ('currentPage', 'pageSize', 'totalMatchedInventory', 'totalMatchedPages'):
        if type(pagination[key]) is not int or pagination[key] < 0:
            raise ValueError('Invalid pagination count')
    if (any(type(request['pagination'][key]) is not int for key in ('page', 'pageSize'))
            or pagination['currentPage'] != request['pagination']['page']
            or pagination['pageSize'] != request['pagination']['pageSize']):
        raise ValueError('Returned pagination differs from the requested page')
    total, pages = pagination['totalMatchedInventory'], pagination['totalMatchedPages']
    # Retain either native empty-first-page convention; never rewrite its count.
    empty_first_page = (total == 0 and vehicles == [] and pagination['currentPage'] == 1
                        and pagination['pageSize'] == 24 and pages in (0, 1))
    if (pagination['pageSize'] != 24 or not isinstance(vehicles, list)
            or (total == 0 and not empty_first_page)
            or (total > 0 and pages != (total + 23) // 24)):
        raise ValueError('Inconsistent search pagination; coverage cannot be established')
    if len(vehicles) > min(24, total):
        raise ValueError('More vehicles than the declared batch or total')


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
    _validate_pagination(pagination, request, inventory['vehicles'])
    safe_request = {k: request[k] for k in ('filters', 'pagination', 'sortBy', 'zip5')}
    if 'requestedFeatures' in request:
        safe_request['requestedFeatures'] = request['requestedFeatures']
    # Preserve optional native asking-price history without changing older projections.
    vehicles = [{**{k: v.get(k) for k in FIELDS},
                 **{k: v[k] for k in ('previousPrice', 'priceUpdateDate') if k in v},
                 'price': {'total': v.get('price', {}).get('total')}}
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
    _validate_pagination(capture['pagination'], capture['request'], capture['vehicles'])
    if capture['pagination']['totalMatchedInventory'] == 0:
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
                   location_filter=False, known_listing_ids=None, target_vins=None,
                   known_listing_vins=None, page_progress=None, retain_facets=False,
                   first_page_validator=None):
    """Collect one query into retained files, a page journal and a query database.

    A supplied budget is shared across queries; this function does not reset it.
    Earlier-query identities measure overlap without discarding this query's rows.
    """
    if first_page_validator is not None and (not callable(first_page_validator) or not retain_facets):
        raise ValueError('First-page validation requires retained facets and a callable')
    with search_transport(post) as send:
        return _collect_search(filters=filters, zip_code=zip_code, destination=destination,
            target_listings=target_listings, budget=budget, post=send,
            location_filter=location_filter, known_listing_ids=known_listing_ids,
            target_vins=target_vins, known_listing_vins=known_listing_vins,
            page_progress=page_progress, retain_facets=retain_facets,
            first_page_validator=first_page_validator)


def _collect_search(*, filters, zip_code, destination, target_listings, budget, post,
                    location_filter, known_listing_ids, target_vins, known_listing_vins,
                    page_progress, retain_facets, first_page_validator):
    """Advance each page from reservation through retention to parsed storage.

    ``stage`` identifies the operation whose failure stopped collection. Keep the
    checkpoints in order: a response can be received before its source is durable,
    and a retained source can still fail projection, identity or database checks.
    """
    if not re.fullmatch(r'\d{5}', zip_code) or (target_listings is not None and
            (type(target_listings) is not int or target_listings < 1)) or type(location_filter) is not bool:
        raise ValueError('Require a five-digit ZIP and positive listing target')
    if set(filters) - {'makes', 'year'}:
        raise ValueError('Only the observed make/model/year filter contract is supported')
    if target_vins is not None:
        if type(target_vins) is not int or target_vins < 1:
            raise ValueError('Require a positive distinct VIN target')
        target_listings = None
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    (destination/'attempts').mkdir()
    budget = budget or NavigationBudget()
    run_id, query_listing_ids, query_vins = uuid4().hex, set(), set()
    earlier_listing_ids = {identity for retailer, identity in (known_listing_ids or set()) if retailer == 'carvana'}
    earlier_listing_vins = dict(known_listing_vins or {})
    earlier_vins = set(earlier_listing_vins.values())
    earlier_vin_listings = {vin: listing for listing, vin in earlier_listing_vins.items()}
    if len(earlier_vin_listings) != len(earlier_listing_vins):
        raise ValueError('Known VINs have conflicting listing identities')
    start_requests, started = budget.requests, time.monotonic()
    report = dict(run_id=run_id, filters=filters, zip_code=zip_code, endpoint=ENDPOINT,
        started_utc=datetime.now(timezone.utc).isoformat(), target_listings=target_listings,
        target_vins=target_vins,
        target_definition=('Distinct VINs contributed beyond earlier plan queries' if target_vins is not None
                           else 'New listing IDs contributed beyond earlier plan queries'),
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
        report.update(unique_listings=len(query_listing_ids), unique_vins=len(query_vins), reported_total=total,
            complete_query_count=len(query_listing_ids) if report['query_complete'] else None,
            new_unique_listings=len(query_listing_ids - earlier_listing_ids),
            target_reached=target_listings is not None and len(query_listing_ids - earlier_listing_ids) >= target_listings,
            new_unique_vins=len(query_vins - earlier_vins),
            requests=budget.requests-start_requests, elapsed_seconds=time.monotonic()-started,
            ended_utc=datetime.now(timezone.utc).isoformat())
        if target_vins is not None:
            report['target_reached'] = len(query_vins - earlier_vins) >= target_vins
        if entry is not None:
            write_json_atomic(destination/'attempts'/f"{entry['page']:04d}.json",
                dict(run_id=run_id, request=request, **entry))
        write_json_atomic(destination/'run_report.json', report)

    checkpoint()
    while True:
        request = build_search_request(filters=filters, zip_code=zip_code, page=number,
                                       location_filter=location_filter)
        entry = dict(page=number, attempt_id=uuid4().hex, status='pending', stored_rows=0,
            outcome_kind='unattempted', request_started_at_utc=None, response_received_at_utc=None,
            evidence_available_at_utc=None)
        capture = dict(page_url='https://www.carvana.com/cars', captured_at_utc=None,
            request=request, capture_method='failed_search', records=[], attempt_id=entry['attempt_id'])
        report['pages'].append(entry)
        capture_path, response, interrupted = None, None, None
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
            if retain_facets and number == 1:
                # Discovery uses this same charged response, never a second probe.
                from vehicle_tracker.facets import select_facets
                from vehicle_tracker.search_evidence import unique_object
                data = json.loads(response.content, object_pairs_hook=unique_object)
                selected = dict(request=request, captured_at_utc=stamp,
                    zip_code=capture['zip_code'], pagination=capture['pagination'],
                    facet_data=select_facets(data))
                stage = 'storage_failure'
                facet_path = retain_capture(selected, destination/'facets')
                entry.update(facet_source=str(facet_path),
                    facet_sha256=hashlib.sha256(facet_path.read_bytes()).hexdigest())
            stage = 'storage_failure'
            capture_path = retain_capture(capture, destination/'raw')
            entry.update(retained_source=str(capture_path), source_sha256=hashlib.sha256(capture_path.read_bytes()).hexdigest(),
                source_hash_scope='SHA-256 of selected projection JSON bytes',
                evidence_available_at_utc=datetime.now(timezone.utc).isoformat(), status='retained')
            checkpoint(entry)  # Retention and the SQLite commit are separate steps.
            if number == 1 and first_page_validator is not None:
                stage = 'schema_failure'
                first_page_validator(capture, selected)
                report['first_page_context_validated'] = True
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
            page_listing_ids, page_vins = set(frame.listing_id), set(frame.vin.dropna())
            if page_listing_ids & query_listing_ids or page_vins & query_vins or frame.vin.isna().any():
                raise CollectionStopped('Repeated or missing identities; query coverage unstable')
            if target_vins is not None:
                expected_rows = min(24, max(0, total - 24 * (number - 1)))
                if len(frame) != expected_rows:
                    raise CollectionStopped('Page row count differs from declared pagination')
            stage = 'identity_failure'
            page_listing_vins = dict(zip(frame.listing_id, frame.vin))
            conflicts = [dict(listing_id=listing, vin=vin,
                              earlier_vin=earlier_listing_vins.get(listing),
                              earlier_listing_id=earlier_vin_listings.get(vin))
                         for listing, vin in page_listing_vins.items()
                         if ((listing in earlier_listing_vins and earlier_listing_vins[listing] != vin)
                             or (vin in earlier_vin_listings and earlier_vin_listings[vin] != listing))]
            entry['identity_conflicts'] = conflicts
            entry['overlapping_rows'] = sum(earlier_listing_vins.get(listing) == vin
                                            for listing, vin in page_listing_vins.items())
            if conflicts:
                raise CollectionStopped('Conflicting VIN/listing identity across plan queries')
            stage = 'storage_failure'
            entry['stored_rows'] = store_capture(destination/'vehicle.sqlite', run_id=run_id, page_number=number, raw_file=capture_path)
            entry.update(status='parsed', outcome_kind='success')
            query_listing_ids.update(page_listing_ids)
            query_vins.update(page_vins)
            if number == max(1, pages_total):
                report['query_complete'] = len(query_listing_ids) == total
                report['status'] = 'complete_query' if report['query_complete'] else 'partial'
                report['reason'] = '' if report['query_complete'] else 'Unique count differs from reported total'
                if not report['query_complete']:
                    report['outcome_kind'] = 'pagination_unstable'
                    budget.stop()
            elif ((target_listings is not None and len(query_listing_ids - earlier_listing_ids) >= target_listings)
                  or (target_vins is not None and len(query_vins - earlier_vins) >= target_vins)):
                report.update(reason='Target reached; remaining query pages were not collected', outcome_kind='sample_limit')
            checkpoint(entry)
            if page_progress is not None:
                page_progress(report, frame)
        except BaseException as exc:
            if not isinstance(exc, Exception):
                interrupted = exc
            # Every unresolved source, identity, pagination or write failure ends
            # this shared invocation; another query must not hide the failure.
            budget.stop()
            reason = str(exc) if isinstance(exc, CollectionStopped) else f'{type(exc).__name__}: {stage}'
            report.update(status='blocked', reason=reason, outcome_kind=stage, query_complete=False)
            entry.update(error=reason, outcome_kind=stage)
            if isinstance(exc, OSError):
                # Local filesystem diagnostics survive the safe, short reason.
                # Do not record arbitrary transport exception messages or headers.
                entry['failure_details'] = dict(error_type=type(exc).__name__,
                    errno=exc.errno, winerror=getattr(exc, 'winerror', None),
                    filename=str(exc.filename) if exc.filename is not None else None,
                    filename2=str(exc.filename2) if exc.filename2 is not None else None,
                    operation=getattr(exc, 'storage_operation', stage),
                    target=str(getattr(exc, 'storage_target', '')) or None,
                    publication_attempts=getattr(exc, 'storage_publication_attempts', None),
                    confirmed_sharing_winerror=getattr(exc, 'storage_sharing_violation', None),
                    cleanup_notes=getattr(exc, '__notes__', []))
            if stage == 'storage_failure':
                # A commit may already have happened. Never overwrite it as a failed
                # capture; replay/idempotence checks or a new query attempt resolve it.
                entry['database_outcome'] = 'unconfirmed'
            else:
                entry['status'] = 'failed'
                try:
                    capture_path = capture_path or retain_capture(capture, destination/'raw')
                    entry.update(retained_source=str(capture_path), source_sha256=hashlib.sha256(capture_path.read_bytes()).hexdigest(),
                        source_hash_scope='SHA-256 of selected projection/failure JSON bytes',
                        evidence_available_at_utc=datetime.now(timezone.utc).isoformat())
                    store_capture(destination/'vehicle.sqlite', run_id=run_id, page_number=number, raw_file=capture_path, error=reason)
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
        if (report['status']=='blocked' or number==max(1, pages_total or 0)
                or (target_listings is not None and len(query_listing_ids - earlier_listing_ids) >= target_listings)
                or (target_vins is not None and len(query_vins - earlier_vins) >= target_vins)):
            break
        number += 1
    return report
