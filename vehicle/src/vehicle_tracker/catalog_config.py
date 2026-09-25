"""Full-inventory catalog helpers. See catalog.py for the public entry points."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from vehicle_tracker.cycles import CycleBudget, aware, cycle_config, cycle_lock
from vehicle_tracker.carvana import NATIVE_FIELDS
from vehicle_tracker.history import OBSERVATION_COLUMNS, import_reports, read_query_evidence
from vehicle_tracker.search import ENDPOINT, collect_search, search_transport
from vehicle_tracker.search_context import empty_context_status, validate_first_page
from vehicle_tracker.search_plan import verify_isolated_leaf_failure, verify_isolated_pagination
from vehicle_tracker.storage import read_snapshots, write_json_atomic
from vehicle_tracker.catalog_partitions import plan_year_partitions, year_make_candidates

_DIGESTS = {}


def _strategy(config):
    strategy = config.get('partition_strategy', 'all_year_models')
    if ((config['format'], strategy) not in {
            ('carvana-full-inventory-v1', 'all_year_models'),
            ('carvana-full-inventory-v2', 'year_then_make_model'),
            ('carvana-full-inventory-v3', 'year_make_adaptive')}):
        raise ValueError('Require v1/all_year_models, v2/year_then_make_model, or v3/year_make_adaptive')
    return strategy


def utcnow():
    return datetime.now(timezone.utc)


def digest(path):
    """Hash a file once per size and modification time.

    A start checks the same retained reports twice. Unchanged evidence is not
    read again; a rewritten file has a new size or modification time.
    """
    path = Path(path)
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    cached = _DIGESTS.get(key)
    if cached is not None:
        return cached
    value = hashlib.sha256(path.read_bytes()).hexdigest()
    _DIGESTS[key] = value
    return value


def query(name, zip_code, filters):
    return dict(query_id=name, zip_code=zip_code, filters=filters, location_filter=False)


def _unique_roots(paths):
    """Use the same normalized path identity for deduplication and lock ordering."""
    roots = {}
    for path in paths:
        resolved = Path(path).resolve()
        roots.setdefault(os.path.normcase(str(resolved)), resolved)
    return [roots[key] for key in sorted(roots)]


def settings(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding='utf-8'))
    _strategy(config)
    spacing = config['minimum_spacing_seconds']
    if (config['endpoint'] != ENDPOINT or config['location_filter'] is not False
            or config['page_size'] != 24 or config['sort'] != 'MostPopular'
            or type(spacing) not in (int, float) or isinstance(spacing, bool) or spacing < 3
            or type(config['max_requests']) is not int or not 1 <= config['max_requests'] <= 7000
            or type(config['max_seconds']) is not int or not 1 <= config['max_seconds'] <= 21600):
        raise ValueError('Require the public protocol, <=7000 requests, <=21600 seconds and at least 3-second spacing')
    from vehicle_tracker.catalog_schedule import retry_policy
    config['retry_policy'] = retry_policy(config)
    retries = config.get('page_retries', 2)
    if type(retries) is not int or not 0 <= retries <= 5:
        raise ValueError('page_retries must be an integer from 0 through 5')
    config['page_retries'] = retries
    zips = [config['primary_zip'], *config['validation_zips']]
    if (len(zips) != len(set(zips)) or not 1 <= len(zips) <= 4
            or any(not isinstance(z, str) or not re.fullmatch(r'\d{5}', z) for z in zips)):
        raise ValueError('Require distinct five-digit primary and validation ZIPs')
    for field, upper in [('validation_queries_per_zip', 8), ('validation_max_native_count', 240)]:
        if type(config[field]) is not int or not 1 <= config[field] <= upper:
            raise ValueError('Invalid bounded geographic validation setting')
    ZoneInfo(config['timezone'])
    if _strategy(config) == 'year_make_adaptive':
        threshold = config.get('split_threshold_vehicles', 480)
        share = config.get('max_unverified_share', 0.005)
        estimate = config.get('plan_estimate_requests')
        if type(threshold) is not int or threshold < 24:
            raise ValueError('split_threshold_vehicles must be an integer of at least 24')
        if (type(share) not in (int, float) or isinstance(share, bool) or not 0 < float(share) <= 1):
            raise ValueError('max_unverified_share must be between 0 and 1')
        if estimate is not None and (type(estimate) is not int or not 1 <= estimate <= config['max_requests']):
            raise ValueError('plan_estimate_requests must be a positive integer within the request ceiling')
        config['split_threshold_vehicles'] = threshold
        config['max_unverified_share'] = float(share)
    config['capture_root'] = str((path.parent.parent/config['capture_root']).resolve())
    peers = config.get('related_capture_roots')
    if 'related_capture_roots' in config or _strategy(config) != 'all_year_models':
        if (not isinstance(peers, list) or not peers
                or any(not isinstance(peer, str) or not peer.strip() for peer in peers)):
            raise ValueError('Require an explicit nonempty related_capture_roots path list')
        config['related_capture_roots'] = [str(root) for root in
            _unique_roots(path.parent.parent/peer for peer in peers)]
    return config


def _window_fits(config, spacing, remaining, *, strict):
    """v3 uses the measured plan size. v1 and v2 keep the request-ceiling rule."""
    from vehicle_tracker.catalog_schedule import ceiling_fits
    if _strategy(config) != 'year_make_adaptive':
        return ceiling_fits(config, spacing, remaining, strict=strict)
    estimate = config.get('plan_estimate_requests') or config['max_requests']
    return remaining >= spacing and estimate * spacing <= remaining


def _discovery_text(strategy):
    if strategy == 'year_make_adaptive':
        return ('Fresh broad counts and integer-year cells, then whole make/year pages '
                'below the split threshold and model leaves above it')
    if strategy == 'year_then_make_model':
        return 'Fresh broad counts, integer-year cells and both tails, then make/model partitions'
    return 'Fresh broad make counts, then current make/model partitions; all years'


def preview(path, *, now=None, attempt=None, spacing_seconds=None):
    from vehicle_tracker import catalog as _api
    from vehicle_tracker.catalog_roots import (
        _attempt_root_state, _capture_roots, _reviewed_failures)
    from vehicle_tracker.catalog_schedule import (
        attempt_dirname, prior_spacing_index, spacing_for)
    utcnow = _api.utcnow
    config = settings(path)
    now = now or utcnow()
    zone = ZoneInfo(config['timezone'])
    local = now.astimezone(zone)
    midnight = datetime.combine(local.date()+timedelta(days=1), datetime.min.time(), zone)
    end = min(now+timedelta(seconds=config['max_seconds']), midnight-timedelta(microseconds=1))
    day = local.date().isoformat()
    root = Path(config['capture_root'])
    policy = config['retry_policy']
    if attempt is None:
        attempt = 1
    if type(attempt) is not int or attempt < 1:
        raise ValueError('Attempt numbers start at 1')
    index = min(prior_spacing_index(root, day, policy) + attempt - 1,
                len(policy['spacing_seconds_by_attempt']) - 1)
    spacing = spacing_for(policy, index) if spacing_seconds is None else spacing_seconds
    if (type(spacing) not in (int, float) or isinstance(spacing, bool)
            or spacing < config['minimum_spacing_seconds']):
        raise ValueError('Attempt spacing is below the configured minimum')
    destination = root/attempt_dirname(day, attempt)
    remaining = (end-now).total_seconds()
    strict = attempt == 1 and index == 0
    reviewed = _reviewed_failures(config)
    states = [_attempt_root_state(peer, reviewed.get(peer, ()), now=now) for peer in _capture_roots(config)]
    return dict(config=config, config_sha256=digest(path), cycle_date=day,
        window_start=now.isoformat(), window_end=end.isoformat(), destination=str(destination),
        attempt=attempt, spacing_seconds=spacing, spacing_index=index, strict_ceiling=strict,
        destination_fresh=not destination.exists(), access_stopped=any(s['access_stopped'] for s in states),
        capture_root_states=states, capture_preflight_blocked=any(s['blocked'] for s in states),
        active_locks_checked=True,
        request_ceiling=config['max_requests'], effective_seconds=remaining,
        # The first 3-second attempt must fit the whole ceiling. A later or slower
        # attempt may start and end incomplete when the local date runs out.
        pacing_seconds_floor=(config['max_requests']-1)*spacing,
        ceiling_pacing_fits_window=_window_fits(config, spacing, remaining, strict=strict),
        discovery=(_discovery_text(_strategy(config))),
        national_coverage_verified=False, writes=False, requests=0)


