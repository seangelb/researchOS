"""Small browser-assisted batches: frozen plans, one checkpoint per page, no DB.

Chrome navigation is performed by the connected browser. This module handles
selection, evidence and progress; it never makes a request or launches a browser.
"""
import hashlib
import json
import os
import math
from contextlib import contextmanager
from pathlib import Path
import tempfile

import pandas as pd

from vehicle_tracker.events import _aware
from vehicle_tracker.sale_pilot import (FORMAT, NATIVE, known_disjoint_cohorts,
    _read_capture, load_research_pass, load_selection_plan, parse_capture)


ROLLING_DAY_LIMIT = 12
# Local pilot workload policy, not a measured or published Carvana access limit.
RESERVATION_LOCK = '.reservation.lock'


@contextmanager
def _reservation_lock(root, *, now):
    """One owner reserves browser starts across all sibling batches."""
    path = Path(root)/RESERVATION_LOCK
    try:
        handle = path.open('x', encoding='utf-8')
    except FileExistsError as error:
        raise ValueError('Browser reservation lock exists; inspect its owner before recovery, never skip it') from error
    try:
        with handle:
            json.dump(dict(pid=os.getpid(), acquired_at=_aware(now).isoformat()), handle)
            handle.flush()
            os.fsync(handle.fileno())
            yield
    finally:
        path.unlink()


def _workload(root, *, now):
    """Read saved starts, including failures and unfinished reservations.

    A reservation records a browser attempt, not a native vehicle outcome.
    New-start callers hold the shared lock; read-only previews do not reserve.
    """
    cutoff = _aware(now)
    visits = []
    for path in sorted(Path(root).glob('*/batch.json')):
        batch, _, selected = read_batch(path.parent)
        if _aware(batch['created_at']) > cutoff:
            continue
        for attempt, started, report in checkpoints(path.parent, selected):
            start = _aware(started['started_at'])
            if start > cutoff:
                continue
            unresolved = report is None or _aware(report['available_at']) > cutoff
            record = None if unresolved else _read_capture(attempt/'run.json', report['captures'][0],
                                                           available_at=report['available_at'])
            visits.append(dict(**started['expected'], started_at=start, unresolved=unresolved,
                checked_at=start if unresolved else _aware(record['checked_at']),
                access_blocked=False if unresolved else record['access_outcome'] == 'access_blocked',
                available_at=None if unresolved else _aware(report['available_at'])))
    recent = sorted(v['started_at'] for v in visits if v['started_at'] > cutoff-pd.Timedelta(hours=24))
    unresolved = sum(v['unresolved'] for v in visits)
    next_start = cutoff
    if len(recent) >= ROLLING_DAY_LIMIT:
        next_start = max(next_start, recent[len(recent)-ROLLING_DAY_LIMIT]+pd.Timedelta(hours=24))
    finished = [v['available_at'] for v in visits if not v['unresolved']]
    if finished:
        next_start = max(next_start, max(finished)+pd.Timedelta(seconds=15))
    blocked = [v['checked_at'] for v in visits if v['access_blocked']
               and v['checked_at'] > cutoff-pd.Timedelta(hours=24)]
    if blocked:
        next_start = max(next_start, max(blocked)+pd.Timedelta(hours=24))
    # Explain the highest-priority restriction; preserve every lower-priority
    # clock above so resolving one restriction does not reset another.
    if unresolved:
        reason = 'Unresolved browser visit: recover or explicitly fail it first'
    elif blocked:
        reason = 'Access challenge: all browser batches pause for 24 hours after the blocked check'
    elif len(recent) >= ROLLING_DAY_LIMIT:
        reason = 'Rolling 24-hour limit of 12 browser starts reached'
    elif next_start > cutoff:
        reason = 'Wait 15 seconds after the latest saved browser visit'
    else:
        reason = ''
    return dict(as_of=cutoff.isoformat(), rolling_24h_limit=ROLLING_DAY_LIMIT,
        attempted_last_24h=len(recent), remaining_starts=max(0, ROLLING_DAY_LIMIT-len(recent)),
        unresolved_visits=unresolved, access_blocks_last_24h=len(blocked),
        next_start_at=None if unresolved else next_start.isoformat(),
        blocked_reason=reason, reservation_lock_present=False,
        limitation='Counts retained browser reservations; direct manual navigation is not enforced'), visits


def browser_workload(root, *, now):
    """Read-only budget preview; an active/crashed lock is never auto-cleared."""
    if (Path(root)/RESERVATION_LOCK).exists():
        return dict(as_of=_aware(now).isoformat(), rolling_24h_limit=ROLLING_DAY_LIMIT,
            attempted_last_24h=None, remaining_starts=None, unresolved_visits=None,
            access_blocks_last_24h=None,
            next_start_at=None, blocked_reason='Browser reservation lock exists; inspect owner before recovery',
            reservation_lock_present=True,
            limitation='Counts retained browser reservations; direct manual navigation is not enforced')
    return _workload(root, now=now)[0]


def browser_capacity(root, requests, *, now, prior_checks=(), minutes_per_check=1.5,
                     operator_minutes_per_day=20):
    """Check whether proposed visits fit the saved budget and windows; no writes.

    Each request has wave, retailer, VIN, start and end. Capacity means requests
    that fit, including assumed capture/save time and 15 seconds after saving.
    Operator time is an explicit planning assumption, including historical starts
    whose active effort was not measured. Future unrelated work is not reserved.
    An unresolved visit or lock has no assumed recovery time and blocks approval.
    Planned times are calculations, not reservations or automatic navigation.
    """
    for value in [minutes_per_check, operator_minutes_per_day]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError('Operator effort assumptions must be positive finite minutes')
    cutoff = _aware(now)
    duration = pd.Timedelta(minutes=minutes_per_check)
    daily_limit = min(ROLLING_DAY_LIMIT, math.floor(operator_minutes_per_day / minutes_per_check))
    workload = browser_workload(root, now=cutoff)
    blocked = workload['blocked_reason'] if (workload['reservation_lock_present'] or workload['unresolved_visits']) else ''
    if not daily_limit:
        blocked = blocked or 'One check exceeds the planned daily operator effort'
    visits = [] if workload['reservation_lock_present'] else _workload(root, now=cutoff)[1]
    starts = [visit['started_at'] for visit in visits]
    last_checked = {}
    for visit in [*visits, *prior_checks]:
        checked = _aware(visit['checked_at'])
        if checked <= cutoff:
            key = (visit['retailer'], visit['vin'])
            last_checked[key] = max(checked, last_checked.get(key, checked))
    next_start = max(cutoff, _aware(workload['next_start_at'])) if workload['next_start_at'] else cutoff
    scheduled = []
    ordered = sorted(requests, key=lambda r: (_aware(r['end']), _aware(r['start']), r['retailer'], r['vin']))
    keys = [(r['wave'], r['retailer'], r['vin']) for r in ordered]
    if len(keys) != len(set(keys)):
        raise ValueError('One capacity request per VIN and wave is required')
    pending = list(ordered)
    while pending:
        # Earliest deadline first; within a wave choose the VIN available first.
        deadline = min(_aware(r['end']) for r in pending)
        group = [r for r in pending if _aware(r['end']) == deadline]
        def ready(request):
            previous = last_checked.get((request['retailer'], request['vin']))
            return max(next_start, _aware(request['start']),
                       previous + pd.Timedelta(hours=24) if previous is not None else cutoff)
        request = min(group, key=lambda r: (ready(r), r['retailer'], r['vin']))
        pending.remove(request)
        start = ready(request)
        if _aware(request['start']) >= deadline:
            raise ValueError('Observation window end must follow its start')
        if not blocked:
            recent = sorted(s for s in starts if start-pd.Timedelta(hours=24) < s <= start)
            while len(recent) >= daily_limit:
                start = recent[len(recent)-daily_limit]+pd.Timedelta(hours=24)
                recent = sorted(s for s in starts if start-pd.Timedelta(hours=24) < s <= start)
        fits = not blocked and start + duration < deadline
        if blocked:
            reason = blocked
        elif fits:
            reason = ''
        else:
            reason = 'Insufficient time/capacity before the fixed deadline'
        scheduled.append(dict(**request, planned_start=start.isoformat() if fits else None,
            planned_capture_at=(start+duration).isoformat() if fits else None, fits=bool(fits),
            reason=reason))
        if fits:
            starts.append(start)
            last_checked[(request['retailer'], request['vin'])] = start+duration
            next_start = start+duration+pd.Timedelta(seconds=15)
    return dict(workload=workload, checks=scheduled, minutes_per_check=minutes_per_check,
        operator_minutes_per_day=operator_minutes_per_day, effective_starts_per_rolling_day=daily_limit,
        assumption='Sequential operator availability throughout the windows; assumed effort for every start; no future competing work or retries reserved')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    """Publish complete JSON atomically and refuse to replace an existing file."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                prefix='.'+path.name, suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)  # Atomic no-overwrite publication on the same volume.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def preview_plan(path, *, now):
    plan = json.loads(Path(path).read_text(encoding='utf-8'))
    selected = load_selection_plan(path, as_of=now)
    if not 1 <= len(selected) <= 12 or selected.listing_id.duplicated().any():
        raise ValueError('Plan requires 1-12 distinct selected VINs and listing IDs')
    if not _aware(plan['as_of']) <= _aware(plan['prepared_at']) <= _aware(now):
        raise ValueError('Plan cutoff or preparation clock is inconsistent')
    if _aware(plan['expires_at']) <= _aware(plan['prepared_at']):
        raise ValueError('Plan expiry must follow preparation')
    return plan, selected


def validate_inventory_plan(plan, *, config_path=None, verify_sources=False):
    """Bind new inventory selections without rewriting legacy studies or recovery.

    Starting/preparing checks immutable inventory sources and configuration bytes.
    Recording an already reserved visit needs only its frozen target, even if the
    live inventory configuration has since changed.
    """
    context = plan.get('inventory_context')
    if context is None:
        if config_path is not None:
            raise ValueError('Legacy plan has no inventory scope binding; --config cannot relabel its targets')
        return
    if config_path is not None and Path(config_path).resolve() != Path(context['config_path']).resolve():
        raise ValueError('Selected configuration differs from the frozen plan inventory scope')
    if any(not context['scope_id'] or page.get('inventory_scope_id') != context['scope_id'] for page in plan['pages']):
        raise ValueError('Selected page differs from the plan inventory scope')
    if verify_sources:
        for name in [context['config_path'], context['query_plan'], *context['source_paths']]:
            path = Path(name)
            if not path.is_file() or digest(path) != plan['input_hashes'].get(str(path)):
                raise ValueError('Inventory configuration or retained selection source changed: ' + str(path))


def create_batch(plan_path, destination, *, helper_path, cohorts, now):
    plan, selected = preview_plan(plan_path, now=now)
    validate_inventory_plan(plan, verify_sources=True)
    if plan.get('preparation_blocked_reason'):
        raise ValueError(plan['preparation_blocked_reason'])
    if _aware(now) >= _aware(plan['expires_at']):
        raise ValueError('Expired plan; prepare a fresh selection')
    members = {(v['retailer'], v['vin']) for c in known_disjoint_cohorts(cohorts, as_of=now) for v in c['vehicles']}
    folder = Path(destination)
    folder.mkdir(parents=True, exist_ok=False)
    (folder/'plan.json').write_bytes(Path(plan_path).read_bytes())
    (folder/'capture.js').write_bytes(Path(helper_path).read_bytes())
    write_new(folder/'batch.json', dict(format='carvana-browser-detail-batch-v1', created_at=_aware(now).isoformat(),
        plan_sha256=digest(folder/'plan.json'), helper_sha256=digest(folder/'capture.js'),
        minimum_spacing_seconds=15, max_top_level_visits=len(selected),
        frozen_members=[dict(retailer=r.retailer, vin=r.vin) for r in selected.itertuples() if (r.retailer, r.vin) in members],
        method='Connected Chrome; selected public DOM/RSC fields; HTTP status not measured'))
    return folder


def read_batch(folder):
    folder = Path(folder)
    batch = json.loads((folder/'batch.json').read_text(encoding='utf-8'))
    if (batch['format'] != 'carvana-browser-detail-batch-v1'
            or digest(folder/'plan.json') != batch['plan_sha256']
            or digest(folder/'capture.js') != batch['helper_sha256']):
        raise ValueError('Frozen browser batch plan or extractor changed')
    plan, selected = preview_plan(folder/'plan.json', now=batch['created_at'])
    if (not _aware(batch['created_at']) < _aware(plan['expires_at'])
            or batch['max_top_level_visits'] != len(selected) or batch['minimum_spacing_seconds'] != 15):
        raise ValueError('Browser batch bounds changed')
    return batch, plan, selected


def checkpoints(folder, selected):
    """A started visit without a completed manifest is uncertain, never unattempted."""
    folder = Path(folder)
    attempts = sorted(folder.glob('visit_*'))
    if len(attempts) > len(selected):
        raise ValueError('Browser batch exceeds its visit budget')
    result = []
    for index, attempt in enumerate(attempts):
        if attempt.name != f'visit_{index+1:02d}' or not (attempt/'started.json').is_file():
            raise ValueError('Incomplete or nonconsecutive visit checkpoint; inspect before continuing')
        started = json.loads((attempt/'started.json').read_text())
        target = selected.iloc[index]
        expected = {key: target[key] for key in ['retailer', 'vin', 'listing_id']}
        if started['expected'] != expected or started['url'] != target.url:
            raise ValueError('Visit differs from frozen target order')
        report = json.loads((attempt/'run.json').read_text()) if (attempt/'run.json').is_file() else None
        if report and (report['started_sha256'] != digest(attempt/'started.json')
                       or _aware(report['available_at']) < _aware(started['started_at'])):
            raise ValueError('Visit checkpoint changed or its clock is invalid')
        result.append((attempt, started, report))
    return result


def begin_visit(folder, *, now):
    """Atomically enforce the shared budget before reserving one Chrome visit."""
    with _reservation_lock(Path(folder).parent, now=now):
        return _begin_visit(folder, now=now)


def _begin_visit(folder, *, now):
    folder = Path(folder)
    batch, plan, selected = read_batch(folder)
    validate_inventory_plan(plan, verify_sources=True)
    time = _aware(now)
    if not _aware(batch['created_at']) <= time < _aware(plan['expires_at']):
        raise ValueError('Browser batch window has not started or expired')
    previous = checkpoints(folder, selected)
    if previous:
        last = previous[-1]
        if last[2] is None:
            raise ValueError('A visit is already started; record its result or explicit failure before continuing')
        if last[2]['outcome'] != 'matched':
            raise ValueError('Batch stopped after an unsuccessful visit; no retry or transport fallback')
        if time < _aware(last[2]['available_at']) + pd.Timedelta(seconds=15):
            raise ValueError('Wait at least 15 seconds after the previous saved visit')
    if len(previous) == len(selected):
        raise ValueError('Batch is complete; no more visits')
    target = selected.iloc[len(previous)]
    workload, visits = _workload(folder.parent, now=time)
    if workload['blocked_reason']:
        raise ValueError(workload['blocked_reason'])
    if any((v['retailer'], v['vin']) == (target.retailer, target.vin)
           and time < v['checked_at']+pd.Timedelta(hours=24) for v in visits):
        raise ValueError('Wait 24 hours after the previous VIN visit in another batch')
    attempt = folder / f'visit_{len(previous)+1:02d}'
    attempt.mkdir(exist_ok=False)
    started = dict(started_at=time.isoformat(), expected={key: target[key] for key in ['retailer','vin','listing_id']},
        url=target.url, selection_reason=target.selection_reason)
    write_new(attempt/'started.json', started)
    return dict(visit_directory=str(attempt), **started, extractor_path=str(folder/'capture.js'))


def validate_projection(capture, expected):
    permitted = {'format','expected','requested_url','final_url','checked_at','access_outcome',
        'contexts','hero_text','hero_badge','purchase_button','note'}
    if (capture.get('format') != FORMAT or set(capture)-permitted or capture.get('expected') != expected
            or not isinstance(capture.get('contexts'), list)):
        raise ValueError('Expected the documented public projection for the reserved target')
    for context in capture['contexts']:
        if (not isinstance(context, dict) or set(context)-{'source_path','forVehicleContext'}
                or set(context.get('forVehicleContext', {})) != {'vehicleDetails'}):
            raise ValueError('Capture contains an undocumented context')
        details = context['forVehicleContext']['vehicleDetails']
        if not isinstance(details, dict) or set(details)-set(NATIVE) or any(isinstance(v,(dict,list)) for v in details.values()):
            raise ValueError('Capture contains fields outside the native vehicle allowlist')


def record_visit(folder, capture, *, now):
    """Save each observation immediately. A failed parse stops subsequent visits."""
    return _record_result(folder, capture, now=now, filename='capture.json')


def _record_result(folder, capture, *, now, filename):
    folder = Path(folder)
    batch, plan, selected = read_batch(folder)
    attempts = checkpoints(folder, selected)
    if not attempts or attempts[-1][2] is not None:
        raise ValueError('No unfinished reserved visit to record')
    attempt, started, _ = attempts[-1]
    if filename == 'capture.json' and (attempt/'failure.json').exists():
        raise ValueError('A failure was already retained; recover that failure instead of recording a success')
    validate_projection(capture, started['expected'])
    if not _aware(batch['created_at']) <= _aware(started['started_at']) <= _aware(capture['checked_at']) <= _aware(now):
        raise ValueError('Capture clock precedes its reserved visit or local availability')
    if _aware(started['started_at']) >= _aware(plan['expires_at']):
        raise ValueError('Visit started after expiry')
    source = attempt/filename
    row = parse_capture(capture, expected=started['expected'], available_at=now, source=str(source))
    if source.exists():
        try:
            retained = json.loads(source.read_text(encoding='utf-8'))
        except (ValueError, UnicodeError) as error:
            raise ValueError('Retained capture is incomplete; explicitly fail the visit to preserve it') from error
        if retained != capture:
            raise ValueError('Retained capture differs; never replace saved evidence')
    else:
        write_new(source, capture)
    member = {key: started['expected'][key] for key in ['retailer','vin']} in batch['frozen_members']
    report = dict(available_at=_aware(now).isoformat(), selection_as_of=plan['prepared_at'],
        outcome=row['parse_outcome'], started_sha256=digest(attempt/'started.json'),
        method=batch['method'], captures=[dict(file=filename, sha256=digest(source),
            expected=started['expected'], selection_reason=started['selection_reason'], frozen_cohort_member=member)])
    if filename == 'failure.json' and (attempt/'capture.json').exists():
        report['abandoned_capture'] = dict(file='capture.json', sha256=digest(attempt/'capture.json'))
    # Completion is published last. A partial write remains visible as an unfinished visit.
    write_new(attempt/'run.json', report)
    return row


def recover_visit(folder, *, now):
    """Finish an interrupted local save; no page revisit and no timestamp backdating."""
    _, _, selected = read_batch(folder)
    attempts = checkpoints(folder, selected)
    if not attempts or attempts[-1][2] is not None:
        raise ValueError('No unfinished reserved visit to recover')
    attempt = attempts[-1][0]
    # An explicit failure can itself have been interrupted before its manifest.
    filename = 'failure.json' if (attempt/'failure.json').exists() else 'capture.json'
    try:
        capture = json.loads((attempt/filename).read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError) as error:
        raise ValueError('No complete retained capture; explicitly fail the visit to preserve partial evidence') from error
    return _record_result(folder, capture, now=now, filename=filename)


def fail_visit(folder, *, reason, now):
    if reason not in ['access_blocked', 'navigation_failed', 'interrupted', 'page_not_ready']:
        raise ValueError('Use a documented failure reason')
    _, _, selected = read_batch(folder)
    attempts = checkpoints(folder, selected)
    if not attempts or attempts[-1][2] is not None:
        raise ValueError('No unfinished reserved visit')
    started = attempts[-1][1]
    capture = dict(format=FORMAT, expected=started['expected'], requested_url=started['url'], final_url=None,
        checked_at=_aware(now).isoformat(), access_outcome=reason, contexts=[], hero_text=None,
        hero_badge=None, purchase_button=None, note='Recorded browser failure: '+reason+'; no native status or HTTP status inferred.')
    return _record_result(folder, capture, now=now, filename='failure.json')


def load_browser_batches(root, cohorts, *, as_of):
    """Read every completed visit at its own availability clock, plus open work."""
    root, cutoff = Path(root), _aware(as_of)
    frames, plans, health, inputs = [], [], [], set()
    for path in sorted((root/'data/experiments/carvana_detail_batches').glob('*/batch.json')):
        folder = path.parent
        batch, plan, selected = read_batch(folder)
        if _aware(batch['created_at']) > cutoff:
            continue
        plans.append(selected)
        inputs.update([path, folder/'plan.json', folder/'capture.js'])
        attempts = checkpoints(folder, selected)
        for index, target in enumerate(selected.itertuples()):
            state = dict(batch=folder.name, retailer=target.retailer, vin=target.vin, listing_id=target.listing_id,
                outcome='unattempted', started_at=None, available_at=None, saleStatus=None, purchaseType=None,
                observed_status=None, checked_at=None, source=None,
                window_expired=cutoff >= _aware(plan['expires_at']))
            if index < len(attempts):
                attempt, started, report = attempts[index]
                if _aware(started['started_at']) <= cutoff:
                    if not _aware(batch['created_at']) <= _aware(started['started_at']) < _aware(plan['expires_at']):
                        raise ValueError('Visit started outside its frozen window')
                    inputs.add(attempt/'started.json')
                    state.update(outcome='started_unresolved', started_at=started['started_at'])
                    if report and _aware(report['available_at']) <= cutoff:
                        _, rows = load_research_pass(attempt/'run.json', selected, cohorts, as_of=as_of,
                                                     membership_as_of=batch['created_at'])
                        if len(rows) != 1 or rows.iloc[0].listing_id != target.listing_id:
                            raise ValueError('Completed visit does not match its reserved target')
                        row = rows.iloc[0]
                        if _aware(row.checked_at) < _aware(started['started_at']) or row.parse_outcome != report['outcome']:
                            raise ValueError('Visit result differs from its checkpoint')
                        frames.append(rows)
                        inputs.update([attempt/'run.json', Path(row.source)])
                        if report.get('abandoned_capture'):
                            abandoned = report['abandoned_capture']
                            if abandoned['file'] != 'capture.json' or digest(attempt/'capture.json') != abandoned['sha256']:
                                raise ValueError('Abandoned capture changed')
                            inputs.add(attempt/'capture.json')
                        state.update({key: row[key] for key in ['available_at','saleStatus','purchaseType','observed_status',
                                                              'checked_at', 'source']})
                        state['outcome'] = row.parse_outcome
            health.append(state)
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(),
            pd.concat(plans, ignore_index=True) if plans else pd.DataFrame(), pd.DataFrame(health), inputs)
