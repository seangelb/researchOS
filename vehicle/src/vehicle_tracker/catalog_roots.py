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
from vehicle_tracker.catalog_config import _unique_roots, digest


def _clock():
    """Use the facade clock so tests that replace catalog.utcnow stay in force."""
    from vehicle_tracker import catalog as api
    return api.utcnow()

REVIEW_FATAL_OUTCOMES = frozenset({'access_failure', 'transport_failure', 'schema_failure',
                                   'identity_failure', 'storage_failure'})


def _capture_roots(config):
    return _unique_roots([config['capture_root'], *config.get('related_capture_roots', [])])


def _reviewed_failures(config):
    """Bind previously inspected terminal client failures, keeping their evidence.

    A reviewed disposition never edits or deletes a stop marker. It records that
    an exact, hash-bound failure was inspected, so a closed investigation cannot
    block every later collection in an unrelated root. Any changed hash, added
    attempt, or a different fatal shape blocks collection again. Discovery
    ValueError stops that follow ordinary sample probes are eligible when every
    charged outcome stayed non-fatal and the failure reason is retained.
    Transport-uncertain peer stops keep pending_request=true on the failed date;
    hash-binding them only clears the peer preflight block for a fresh root.
    """
    reviewed = config.get('reviewed_peer_failures') or []
    if not isinstance(reviewed, list):
        raise ValueError('Reviewed peer failures must be an explicit list')
    roots, own = _capture_roots(config), Path(config['capture_root']).resolve()
    # Ordinary probe/leaf outcomes that may appear beside a single transport stop.
    transport_review_outcomes = frozenset({None, 'sample_limit', 'pagination_unstable', 'transport_failure'})
    accepted = {}
    for item in reviewed:
        folder = Path(item['capture_directory']).resolve()
        if folder.parent not in roots or folder.parent == own:
            raise ValueError('A reviewed failure must be a locked peer, never this destination')
        report_path, budget_path = folder/'catalog_report.json', folder/'catalog_budget.json'
        stop_path = folder.parent/'access_stop.json'
        for path, sha in [(report_path, item['catalog_report_sha256']),
                          (budget_path, item['catalog_budget_sha256']),
                          (stop_path, item['access_stop_sha256'])]:
            if not Path(path).is_file() or digest(path) != sha:
                raise ValueError('Reviewed failure evidence changed: ' + str(path))
        report = json.loads(report_path.read_text(encoding='utf-8'))
        budget = json.loads(budget_path.read_text(encoding='utf-8'))['budget']
        stop = json.loads(stop_path.read_text(encoding='utf-8'))
        outcomes = ([entry.get('outcome_kind') for entry in report['entries'] if entry.get('report')]
                    if 'entries' in report else [report.get('query_outcome')])
        fatal_non_schema = any(kind in (REVIEW_FATAL_OUTCOMES - {'schema_failure'}) for kind in outcomes if kind)
        schema_only = bool(outcomes) and all(kind in (None, 'schema_failure') for kind in outcomes)
        # A discovery ValueError stop can follow ordinary sample_limit probes; retain it
        # when every charged outcome stayed non-fatal and the failure reason is explicit.
        value_error_stop = (report.get('failure_type') == 'ValueError'
                            and bool(report.get('failure_reason')) and not fatal_non_schema)
        # An older invocation could wrap the same HTTP-200 schema ValueError as
        # CollectionStopped after isolating ordinary pagination leaves.
        collection_schema_stop = (report.get('failure_type') == 'CollectionStopped'
            and str(report.get('failure_reason') or '').startswith('ValueError')
            and 'schema_failure' in outcomes and not fatal_non_schema)
        # VPN/network drop before an HTTP response: pending stays true on that date.
        transport_uncertain_stop = (bool(outcomes) and bool(report.get('failure_reason'))
            and budget.get('pending_request') is True
            and 'transport_failure' in outcomes
            and all(kind in transport_review_outcomes for kind in outcomes)
            and not any(kind in (REVIEW_FATAL_OUTCOMES - {'transport_failure'}) for kind in outcomes if kind))
        schema_or_value = ((schema_only or value_error_stop or collection_schema_stop)
                           and budget.get('pending_request') is False)
        if (report.get('status') != 'stopped' or not report.get('ended_at')
                or stop.get('run') != str(report_path)
                or budget.get('stopped') is not True
                or budget.get('requests') != report.get('requests')
                or not (schema_or_value or transport_uncertain_stop)):
            raise ValueError('A reviewed failure must be a terminal client schema failure '
                             'or transport-uncertain stop: ' + str(folder))
        aware(report['ended_at'])
        accepted.setdefault(folder.parent, []).append(folder)
    return accepted


def _lock_held(root):
    """True when another process holds this root. Never creates a lock file."""
    root = Path(root)
    if not (root/'cycle.lock').is_file():
        return False
    from vehicle_tracker import cycles as cycle_module
    try:
        with cycle_module.cycle_lock(root):
            return False
    except (ValueError, OSError):
        return True


def _active_cooldown(root, now):
    """An access stop blocks only until its recorded cooldown expires."""
    from vehicle_tracker.catalog_schedule import read_attempt_outcome
    root = Path(root)
    if not root.is_dir():
        return None
    latest = None

    def consider(value):
        nonlocal latest
        if not value:
            return
        try:
            stamp = aware(value)
        except (ValueError, TypeError):
            return
        if stamp > now and (latest is None or stamp > latest):
            latest = stamp

    stop_path = root/'access_stop.json'
    if stop_path.is_file():
        try:
            consider(json.loads(stop_path.read_text(encoding='utf-8')).get('cooldown_until'))
        except (OSError, ValueError):
            pass
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        outcome = read_attempt_outcome(folder)
        if outcome and outcome.get('kind') == 'access_stop':
            consider(outcome.get('cooldown_until'))
    return latest


def _capture_root_state(root, reviewed=()):
    """Read prior evidence without opening a lock or creating any directories.

    Gap recovery still uses this stricter view. Full-inventory attempts use
    ``_attempt_root_state``, which blocks only on an active cooldown or lock.
    """
    unresolved = []
    if root.exists() and not root.is_dir():
        unresolved.append(dict(capture_directory=str(root), reason='Capture root is not a directory'))
    else:
        prior = [path for path in root.glob('*') if path.is_dir()]
        for folder in sorted(prior):
            try:
                budget = json.loads((folder/'catalog_budget.json').read_text(encoding='utf-8'))['budget']
                report = json.loads((folder/'catalog_report.json').read_text(encoding='utf-8'))
                if any(type(budget[key]) is not bool for key in ['pending_request', 'stopped']):
                    raise ValueError('Invalid pending/stopped request state')
                if budget['pending_request']:
                    reason = 'Pending request outcome remains uncertain'
                elif budget['stopped']:
                    reason = 'Durable catalog budget remains stopped'
                elif report.get('status') not in {'collection_finished', 'stopped'}:
                    reason = 'Catalog report is running or lacks terminal status'
                else:
                    aware(report['ended_at'])
                    continue
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                reason = 'Missing or invalid catalog budget/report; inspect original evidence'
            if folder in reviewed:
                continue
            unresolved.append(dict(capture_directory=str(folder), reason=reason))
    stop_path = root/'access_stop.json'
    stopped = stop_path.exists()
    retained = False
    if stopped and reviewed:
        try:
            run = json.loads(stop_path.read_text(encoding='utf-8')).get('run')
        except (OSError, ValueError):
            run = None
        retained = any(run == str(folder/'catalog_report.json') for folder in reviewed)
    return dict(capture_root=str(root), access_stopped=stopped and not retained,
                retained_access_stopped=retained,
                reviewed_client_failures=sorted(str(folder) for folder in reviewed),
                unresolved_invocations=unresolved,
                blocked=(stopped and not retained) or bool(unresolved))


def _attempt_root_state(root, reviewed=(), now=None, check_lock=True):
    """Read prior evidence without creating directories.

    A finished or failed attempt does not block a later date. Only an unexpired
    access-stop cooldown or a held root lock does. Reviewed failure hashes stay
    attached to their evidence and are not required to clear that cooldown.
    """
    now = now or _clock()
    unresolved = []
    if root.exists() and not root.is_dir():
        unresolved.append(dict(capture_directory=str(root), reason='Capture root is not a directory'))
    stop_path = root/'access_stop.json'
    stopped = stop_path.exists()
    retained = False
    if stopped and reviewed:
        try:
            run = json.loads(stop_path.read_text(encoding='utf-8')).get('run')
        except (OSError, ValueError):
            run = None
        retained = any(run == str(folder/'catalog_report.json') for folder in reviewed)
    cooldown = None if unresolved else _active_cooldown(root, now)
    lock_held = _lock_held(root) if check_lock and not unresolved else False
    return dict(capture_root=str(root), access_stopped=cooldown is not None,
                cooldown_until=None if cooldown is None else cooldown.isoformat(),
                lock_held=lock_held,
                retained_access_stopped=retained,
                reviewed_client_failures=sorted(str(folder) for folder in reviewed),
                unresolved_invocations=unresolved,
                blocked=bool(unresolved) or cooldown is not None or lock_held)


def _require_clear_roots(states):
    if any(state['access_stopped'] for state in states):
        raise ValueError('Unresolved access stop in a related capture root; inspect original evidence')
    if any(state['unresolved_invocations'] for state in states):
        raise ValueError('Unresolved prior catalog invocation in a related capture root; inspect original evidence')


