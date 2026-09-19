"""Small public search-source contract, retained before projection/normalization.

Original means requests.Response.content, possibly decompressed, never wire bytes.
Unknown fields are omitted. Unsafe/malformed bodies have an explicit replay limit.
"""
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
import re

from vehicle_tracker.storage import retain_bytes, retain_capture


CONTRACT = 'carvana-search-source-v1'
RESPONSE_SCOPE = 'SHA-256 of requests.Response.content; not wire bytes'
SOURCE_SCOPES = {'original_response_content': 'SHA-256 of retained Response.content bytes',
                 'selected_source': 'SHA-256 of canonical selected public JSON bytes'}
VEHICLE = {key: None for key in (
    'vehicleId', 'vin', 'year', 'make', 'model', 'parentModel', 'mileage',
    'isPurchasePending', 'vehicleLockType', 'vehiclePurchaseType',
    'vehicleInventoryType', 'isOnDemand', 'transportCost',
    'previousPrice', 'priceUpdateDate')}
VEHICLE['price'] = {'total': None}
SOURCE_FIELDS = {
    'inventory': {'pagination': {key: None for key in (
        'currentPage', 'pageSize', 'totalMatchedInventory', 'totalMatchedPages')},
        'vehicles': [VEHICLE]},
    'userDeliveryInfo': {'zip5': None},
}


def response_diagnostics(response):
    """Select bounded HTTP observations without retaining arbitrary server text.

    Missing or unrecognized header values remain unknown. Body markers describe
    literal matches in a prefix, not proof of a provider, block rule or root cause.
    This metadata does not change access decisions or make the body replayable.
    """
    headers = {key.lower(): value for key, value in response.headers.items()
               if isinstance(key, str)}

    def header(name):
        value = headers.get(name)
        return value.strip() if isinstance(value, str) else ''

    media_type = header('content-type').split(';', 1)[0].strip().lower()
    if media_type not in {'application/json', 'application/problem+json', 'text/html',
                          'text/plain', 'application/xml', 'text/xml', 'application/octet-stream'}:
        media_type = None
    server = re.fullmatch(
        r'(cloudflare|cloudfront|awselb|nginx|apache|envoy|microsoft-iis|varnish)(?:/[0-9.]{1,20})?',
        header('server').lower())
    ray = re.fullmatch(r'([0-9a-f]{16})-([a-z]{3})', header('cf-ray').lower())
    retry = header('retry-after')
    content = response.content
    prefix = content[:65536].lower() if isinstance(content, bytes) else None
    marker_patterns = {
        'cloudflare': (b'cloudflare', b'cf-ray'),
        'cloudfront': (b'cloudfront',),
        'access_denied': (b'access denied', b'access-denied', b'access_denied',
                          b'request blocked', b'the request could not be satisfied', b'forbidden'),
        'challenge_markup': (b'/cdn-cgi/challenge-platform/', b'cf-chl-', b'cf_chl_'),
    }
    status = response.status_code
    return dict(
        http_status=status if type(status) is int and 100 <= status <= 599 else None,
        media_type=media_type,
        server_family=server.group(1) if server else None,
        cf_ray=f'{ray.group(1)}-{ray.group(2).upper()}' if ray else None,
        cf_mitigated='challenge' if header('cf-mitigated').lower() == 'challenge' else None,
        retry_after_seconds=int(retry) if re.fullmatch(r'[0-9]{1,10}', retry) else None,
        body_inspected_bytes=len(prefix) if prefix is not None else None,
        body_truncated=len(content) > 65536 if prefix is not None else None,
        body_markers={name: any(value in prefix for value in values) if prefix is not None else None
                      for name, values in marker_patterns.items()},
        marker_scope='Literal matches in inspected body prefix; not proof of provider, rule or root cause.')


def unique_object(pairs):
    """Duplicate keys must not hide an earlier unsafe value in original JSON."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key')
        result[key] = value
    return result


def public_source(value, fields=SOURCE_FIELDS, *, key='', changes=None):
    """Select known public fields without first requiring a valid source schema.

    Missing keys, nulls, booleans, numbers and wrong container types survive. Unknown
    keys and unsafe strings do not. A redacted value is never admitted as inventory.
    Projection and parsing validate inventory shape later; this step only decides
    which source information can be retained safely, including malformed shapes.
    """
    changes = changes if changes is not None else []
    if key == 'priceUpdateDate' and value is not None:
        # Only the observed UTC timestamp form is public; validate the actual
        # calendar/time too, without converting this native clock to our clock.
        valid = False
        if isinstance(value, str) and re.fullmatch(
                r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z', value):
            try:
                datetime.fromisoformat(value[:-1] + '+00:00')
                valid = True
            except ValueError:
                pass
        if not valid:
            changes.append('redacted_value')
            return '[redacted string]' if isinstance(value, str) else None
        return value
    if isinstance(value, dict):
        allowed = fields if isinstance(fields, dict) else {}
        if set(value) - set(allowed):
            changes.append('omitted_fields')
        return {k: public_source(v, allowed[k], key=k, changes=changes)
                for k, v in value.items() if k in allowed}
    if isinstance(value, list):
        child = fields[0] if isinstance(fields, list) else None
        return [public_source(v, child, key=key, changes=changes) for v in value]
    if isinstance(value, str):
        patterns = {'vin': r'[A-HJ-NPR-Z0-9]{17}', 'zip5': r'\d{5}',
                    'vehicleId': r'\d{1,12}'}
        text_fields = {'make', 'model', 'parentModel', 'vehiclePurchaseType'}
        if key in patterns:
            pattern = patterns[key]
        elif key in text_fields:
            pattern = r"[A-Za-z0-9 .()/'&+\-]{1,100}"
        else:
            pattern = r'-?\d+(\.\d+)?'
        if fields is None and re.fullmatch(pattern, value):
            return value
        changes.append('redacted_value')
        return '[redacted string]'
    if value is None or type(value) in (bool, int) or (type(value) is float and math.isfinite(value)):
        return value
    changes.append('redacted_value')
    return None


def retain_response_evidence(response, directory):
    """Retain replayable safe JSON even when project_response would raise.

    Original response bytes are retained only when every decoded field/value is
    allowed and duplicate JSON keys are absent. Their hash matches response_content_sha256.
    Other JSON becomes a selected source. HTML/invalid JSON is not retained because
    this contract cannot establish that its text is public vehicle information.
    source_sha256 hashes the retained file; a later projection has its own hash.
    """
    content = response.content
    evidence = dict(contract=CONTRACT, kind='not_retained',
        response_content_sha256=hashlib.sha256(content).hexdigest(),
        response_content_bytes=len(content),
        response_hash_scope=RESPONSE_SCOPE,
        http_diagnostics=response_diagnostics(response),
        source_path=None, source_sha256=None, source_hash_scope=None,
        limitation='Non-JSON or access-failure body not retained; body replay unavailable.')
    if response.status_code != 200 or 'application/json' not in response.headers.get('content-type', ''):
        return evidence
    try:
        data = response.json()
    except (ValueError, TypeError):
        evidence['limitation'] = 'Invalid JSON body not retained; JSON decoding cannot be replayed from a hash.'
        return evidence
    changes = []
    selected = public_source(data, changes=changes)
    # Checking the decoded content also prevents a mocked .json() result being
    # described as the literal bytes supplied by an offline test fixture.
    try:
        original = json.loads(content, object_pairs_hook=unique_object)
        original_is_safe = not changes and original == selected
    except (ValueError, TypeError, UnicodeError) as exc:
        original_is_safe = False
        if str(exc) == 'Duplicate JSON object key':
            evidence['ambiguous_json'] = True
    if original_is_safe:
        path = retain_bytes(content, directory, suffix='.json')
        evidence.update(kind='original_response_content', limitation=None,
                        source_hash_scope='SHA-256 of retained Response.content bytes')
    else:
        path = retain_capture(selected, directory)
        evidence.update(kind='selected_source',
            source_hash_scope='SHA-256 of canonical selected public JSON bytes',
            limitation='Omitted fields/original serialization are unavailable; selected fields and shapes can be replayed.')
    evidence.update(source_path=str(path.resolve()), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    redacted_values=changes.count('redacted_value'))
    return evidence


def verify_response_evidence(evidence):
    """Read-only validation; return retained source JSON or None when unavailable."""
    if evidence.get('contract') != CONTRACT:
        raise ValueError('Unknown search response evidence contract')
    if (evidence.get('response_hash_scope') != RESPONSE_SCOPE
            or not re.fullmatch(r'[0-9a-f]{64}', str(evidence.get('response_content_sha256')))
            or type(evidence.get('response_content_bytes')) is not int or evidence['response_content_bytes'] < 0):
        raise ValueError('Invalid response content/hash scope')
    kind = evidence.get('kind')
    if kind == 'not_retained':
        if evidence.get('source_path') or evidence.get('source_sha256') or not evidence.get('limitation'):
            raise ValueError('Invalid withheld-body evidence')
        return None
    if kind not in {'original_response_content', 'selected_source'}:
        raise ValueError('Unknown search source kind')
    if (evidence.get('source_hash_scope') != SOURCE_SCOPES[kind]
            or type(evidence.get('redacted_values')) is not int or evidence['redacted_values'] < 0):
        raise ValueError('Invalid retained source/hash scope')
    if kind == 'selected_source' and not evidence.get('limitation'):
        raise ValueError('Selected source must disclose omitted original content')
    content = Path(evidence['source_path']).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != evidence['source_sha256']:
        raise ValueError('Retained response source hash changed')
    if kind == 'original_response_content' and (
            digest != evidence['response_content_sha256'] or len(content) != evidence['response_content_bytes']):
        raise ValueError('Original response content binding changed')
    return json.loads(content, object_pairs_hook=unique_object)


def replay_response(evidence, request, *, observed_at):
    """Verify retained source bytes, then rebuild the projection and parsed rows.

    Replay uses the supplied observation clock; reading a source does not make it
    a new observation. This function makes no requests or writes.
    """
    from vehicle_tracker.search import project_response, parse_search_capture
    source = verify_response_evidence(evidence)
    if evidence['kind'] == 'not_retained':
        raise ValueError('Body unavailable: ' + evidence['limitation'])
    if evidence.get('redacted_values', 0) or evidence.get('ambiguous_json', False):
        raise ValueError('Unsafe source values were redacted; do not normalize them')
    projection = project_response(source, request, observed_at=observed_at)
    return projection, parse_search_capture(projection)
