"""Decide whether today's full-inventory attempt should start. No requests or writes."""
from datetime import datetime, timedelta, timezone
import json
import re
from pathlib import Path

DATE_NAME = re.compile(r'^(\d{4}-\d{2}-\d{2})(?:_a(\d{2}))?$')
DEFAULT_RETRY_POLICY = dict(
    attempts_per_day=3,
    spacing_seconds_by_attempt=[3, 5, 8],
    cooldown_minutes_after_access_stop=[45, 120, 240],
    cooldown_minutes_after_degraded=[20, 60, 120],
    retry_backoff_seconds=[10, 30],
    consecutive_failure_breaker=5,
)
FINISHED_KINDS = frozenset({'complete', 'complete_with_gaps', 'finished_incomplete', 'infeasible'})
RETRY_KINDS = frozenset({'access_stop', 'degraded', 'client_failure', 'incomplete'})


def retry_policy(config):
    """Fill and check the attempt policy. Missing policy keeps these defaults."""
    raw = config.get('retry_policy') or {}
    if not isinstance(raw, dict):
        raise ValueError('retry_policy must be an object')
    policy = dict(DEFAULT_RETRY_POLICY)
    policy.update(raw)
    attempts = policy['attempts_per_day']
    if type(attempts) is not int or not 1 <= attempts <= 6:
        raise ValueError('attempts_per_day must be an integer from 1 through 6')
    spacings = policy['spacing_seconds_by_attempt']
    if (not isinstance(spacings, list) or not spacings
            or any(type(item) not in (int, float) or isinstance(item, bool) or item < 3 for item in spacings)):
        raise ValueError('spacing_seconds_by_attempt must be at least three seconds')
    for key in ('cooldown_minutes_after_access_stop', 'cooldown_minutes_after_degraded'):
        minutes = policy[key]
        if (not isinstance(minutes, list) or not minutes
                or any(type(item) is not int or item < 0 for item in minutes)):
            raise ValueError(key + ' must be nonnegative integer minutes')
    backoff = policy['retry_backoff_seconds']
    if (not isinstance(backoff, list) or not backoff
            or any(type(item) not in (int, float) or isinstance(item, bool) or item < 0 for item in backoff)):
        raise ValueError('retry_backoff_seconds must be nonnegative waits')
    breaker = policy['consecutive_failure_breaker']
    if type(breaker) is not int or breaker < 1:
        raise ValueError('consecutive_failure_breaker must be a positive integer')
    return policy


def attempt_dirname(day, attempt):
    if type(attempt) is not int or attempt < 1:
        raise ValueError('Attempt numbers start at 1')
    return day if attempt == 1 else f'{day}_a{attempt:02d}'


def spacing_for(policy, index):
    spacings = policy['spacing_seconds_by_attempt']
    return spacings[min(max(0, index), len(spacings) - 1)]


def cooldown_minutes(policy, kind, spacing_index):
    key = ('cooldown_minutes_after_access_stop' if kind == 'access_stop'
           else 'cooldown_minutes_after_degraded')
    minutes = policy[key]
    return minutes[min(max(0, spacing_index), len(minutes) - 1)]


def _aware(value):
    stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if stamp.utcoffset() is None:
        raise ValueError('Use timezone-aware attempt clocks')
    return stamp.astimezone(timezone.utc)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        return None


def attempt_directories(root, day):
    """Return (attempt number, folder) pairs already present for one local date."""
    root = Path(root)
    if not root.is_dir():
        return []
    found = []
    for path in root.iterdir():
        if not path.is_dir():
            continue
        match = DATE_NAME.fullmatch(path.name)
        if not match or match.group(1) != day:
            continue
        number = 1 if match.group(2) is None else int(match.group(2))
        if number >= 1:
            found.append((number, path))
    return sorted(found, key=lambda item: item[0])


def entry_http_status(entry):
    """HTTP status of the last page that recorded one, if the child report is readable."""
    path = (entry or {}).get('report')
    if not path:
        return None
    child = _read_json(path)
    if not isinstance(child, dict):
        return None
    for page in reversed(child.get('pages') or []):
        status = page.get('http_status')
        if type(status) is int:
            return status
    return None


def effective_outcome(entry):
    """Treat a recorded access failure as a server failure when the status is 408 or 5xx.

    Older runs labeled Cloudflare HTTP 520 as an access stop. 401, 403, 429 and a
    missing status stay access failures. A missing status keeps the historical
    label because there is no page to reclassify.
    """
    kind = (entry or {}).get('outcome_kind')
    status = entry_http_status(entry)
    if kind == 'access_failure' and type(status) is int and (status == 408 or 500 <= status <= 599):
        return 'server_failure'
    return kind


def classify_terminal(report):
    """Return access_stop or degraded, or None when success rules still apply."""
    if not isinstance(report, dict):
        return None
    entries = report.get('entries') or []
    kinds = [effective_outcome(entry) for entry in entries]
    if report.get('failure_type') == 'degraded':
        return 'degraded'
    if any(kind == 'access_failure' for kind in kinds):
        return 'access_stop'
    reason = str(report.get('failure_reason') or '')
    if any(token in reason for token in ('rate_limited', 'cloudflare_challenge', 'unexpected_content')):
        return 'access_stop'
    if 'http_access_failure' in reason and 'server_failure' not in kinds:
        return 'access_stop'
    if report.get('status') != 'collection_finished' and 'server_failure' in kinds:
        return 'degraded'
    return None


def _legacy_kind(folder):
    """Classify a retained attempt that predates attempt_outcome.json."""
    report = _read_json(Path(folder) / 'catalog_report.json')
    if not isinstance(report, dict):
        return None
    status = report.get('status')
    if status not in {'collection_finished', 'stopped'}:
        return None
    terminal = classify_terminal(report)
    if terminal:
        return terminal
    if report.get('declared_collection_complete'):
        return 'complete'
    if status == 'collection_finished':
        return 'finished_incomplete'
    return 'client_failure'


def read_attempt_outcome(folder):
    """Prefer the attempt file. Fall back to a terminal catalog report."""
    folder = Path(folder)
    outcome = _read_json(folder / 'attempt_outcome.json')
    if isinstance(outcome, dict) and outcome.get('kind') in FINISHED_KINDS | RETRY_KINDS:
        return outcome
    kind = _legacy_kind(folder)
    if kind is None:
        return None
    report = _read_json(folder / 'catalog_report.json') or {}
    return dict(kind=kind, ended_at=report.get('ended_at'), spacing_index=0,
                spacing_seconds=None, requests=report.get('requests'), legacy=True)


def prior_spacing_index(root, day, policy):
    """After an access stop, the next local date starts one spacing step slower."""
    root = Path(root)
    if not root.is_dir():
        return 0
    latest = None
    for path in root.iterdir():
        if not path.is_dir():
            continue
        match = DATE_NAME.fullmatch(path.name)
        if not match or match.group(1) >= day:
            continue
        number = 1 if match.group(2) is None else int(match.group(2))
        outcome = read_attempt_outcome(path)
        if outcome is None:
            continue
        if latest is None or (match.group(1), number) > latest[0]:
            latest = ((match.group(1), number), outcome)
    if latest is None or latest[1].get('kind') != 'access_stop':
        return 0
    previous = latest[1].get('spacing_index')
    if type(previous) is not int or previous < 0:
        previous = 0
    return min(previous + 1, len(policy['spacing_seconds_by_attempt']) - 1)


def ceiling_fits(config, spacing, seconds_remaining, *, strict):
    if seconds_remaining < spacing:
        return False
    if not strict:
        return True
    return (config['max_requests'] - 1) * spacing <= seconds_remaining


def latest_plan_estimate(root):
    """The newest retained feasibility estimate, if a previous attempt recorded one."""
    root = Path(root)
    if not root.is_dir():
        return None
    latest = None
    chosen = None
    for path in root.iterdir():
        if not path.is_dir():
            continue
        match = DATE_NAME.fullmatch(path.name)
        if not match:
            continue
        number = 1 if match.group(2) is None else int(match.group(2))
        report = _read_json(path / 'catalog_report.json')
        estimate = (report or {}).get('feasibility', {}) if isinstance(report, dict) else {}
        estimate = estimate.get('estimated_minimum_requests') if isinstance(estimate, dict) else None
        if type(estimate) is not int:
            continue
        key = (match.group(1), number)
        if latest is None or key > latest:
            latest, chosen = key, estimate
    return chosen


def _decision(**fields):
    base = dict(action='start', attempt=None, spacing_seconds=None, spacing_index=None,
                destination=None, cycle_date=None, config_sha256=None, cooldown_until=None,
                reason='')
    base.update(fields)
    return base


def daily_decision(config_path, now=None):
    """Return done, running, cooling_until, exhausted, or start. No requests or writes."""
    from vehicle_tracker.catalog import digest, settings, utcnow
    from vehicle_tracker.cycles import cycle_lock
    path = Path(config_path)
    config = settings(path)
    now = now or utcnow()
    policy = config['retry_policy']
    zone_name = config['timezone']
    from zoneinfo import ZoneInfo
    local = now.astimezone(ZoneInfo(zone_name))
    day = local.date().isoformat()
    root = Path(config['capture_root'])
    sha = digest(path)
    base_index = prior_spacing_index(root, day, policy)
    present = attempt_directories(root, day)
    outcomes = [(number, folder, read_attempt_outcome(folder)) for number, folder in present]
    if any(item[2] and item[2].get('kind') in FINISHED_KINDS for item in outcomes):
        return _decision(action='done', cycle_date=day, config_sha256=sha,
                         reason='A finished attempt already covers this local date')
    lock_held = False
    if root.is_dir() and (root / 'cycle.lock').is_file():
        try:
            with cycle_lock(root):
                lock_held = False
        except (ValueError, OSError):
            lock_held = True
    if lock_held:
        return _decision(action='running', cycle_date=day, config_sha256=sha,
                         reason='Capture root lock is held')
    consumed = []
    for number, folder, outcome in outcomes:
        if outcome is None:
            consumed.append((number, folder, dict(kind='client_failure', spacing_index=base_index,
                                                  ended_at=None)))
        else:
            consumed.append((number, folder, outcome))
    next_attempt = (max(number for number, _, _ in consumed) + 1) if consumed else 1
    if next_attempt > policy['attempts_per_day']:
        return _decision(action='exhausted', attempt=next_attempt - 1, cycle_date=day,
                         config_sha256=sha, reason='Attempt allowance for this local date is consumed')
    cooldown_until = None
    if consumed:
        _, _, last = max(consumed, key=lambda item: item[0])
        if last.get('kind') in RETRY_KINDS:
            index = last.get('spacing_index')
            if type(index) is not int or index < 0:
                index = base_index + max(0, next_attempt - 2)
            if last.get('cooldown_until'):
                try:
                    cooldown_until = _aware(last['cooldown_until'])
                except (ValueError, TypeError):
                    cooldown_until = None
            elif last.get('ended_at'):
                try:
                    cooldown_until = _aware(last['ended_at']) + timedelta(
                        minutes=cooldown_minutes(policy, last['kind'], index))
                except (ValueError, TypeError):
                    cooldown_until = None
            if cooldown_until is not None and now < cooldown_until:
                return _decision(action='cooling_until', attempt=next_attempt, cycle_date=day,
                                 config_sha256=sha, cooldown_until=cooldown_until.isoformat(),
                                 reason='Cooldown after ' + last['kind'])
    index = min(base_index + next_attempt - 1, len(policy['spacing_seconds_by_attempt']) - 1)
    spacing = spacing_for(policy, index)
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), local.tzinfo)
    end = min(now + timedelta(seconds=config['max_seconds']), midnight - timedelta(microseconds=1))
    remaining = (end - now).total_seconds()
    strict = next_attempt == 1 and index == 0
    estimate = latest_plan_estimate(root) or config.get('plan_estimate_requests')
    if estimate:
        if estimate * spacing > remaining:
            return _decision(action='skip', attempt=next_attempt, spacing_seconds=spacing,
                             spacing_index=index, cycle_date=day, config_sha256=sha,
                             reason='Estimated plan does not fit the remaining window at this spacing')
    elif not ceiling_fits(config, spacing, remaining, strict=strict):
        return _decision(action='exhausted', attempt=next_attempt, spacing_seconds=spacing,
                         spacing_index=index, cycle_date=day, config_sha256=sha,
                         reason='Remaining local-date window cannot pace this attempt')
    destination = root / attempt_dirname(day, next_attempt)
    return _decision(action='start', attempt=next_attempt, spacing_seconds=spacing,
                     spacing_index=index, destination=str(destination), cycle_date=day,
                     config_sha256=sha, cooldown_until=None, reason='')
