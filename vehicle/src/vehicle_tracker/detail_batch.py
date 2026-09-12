"""Small browser-assisted batches: frozen plans, one checkpoint per page, no DB.

Chrome navigation is performed by the connected browser. This module handles
selection, evidence and progress; it never makes a request or launches a browser.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile

import pandas as pd

from vehicle_tracker.events import _aware
from vehicle_tracker.sale_pilot import (FORMAT, NATIVE, known_disjoint_cohorts,
    _read_capture, load_research_pass, load_selection_plan, parse_capture)


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


def create_batch(plan_path, destination, *, helper_path, cohorts, now):
    plan, selected = preview_plan(plan_path, now=now)
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
    """Reserve one navigation before touching Chrome. Never repeat a started visit."""
    folder = Path(folder)
    batch, plan, selected = read_batch(folder)
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
    # A second plan must not bypass an unresolved visit or the VIN-level 24h clock.
    for path in sorted(folder.parent.glob('*/batch.json')):
        if path.parent.resolve() == folder.resolve():
            continue
        other_batch, _, other_selected = read_batch(path.parent)
        if _aware(other_batch['created_at']) > time:
            continue
        for other_attempt, started, report in checkpoints(path.parent, other_selected):
            if (started['expected']['retailer'], started['expected']['vin']) != (target.retailer, target.vin):
                continue
            if _aware(started['started_at']) > time:
                continue
            if report is None or _aware(report['available_at']) > time:
                raise ValueError('Unresolved visit in another batch; recover or explicitly fail it first')
            row = _read_capture(other_attempt/'run.json', report['captures'][0], available_at=report['available_at'])
            if time < _aware(row['checked_at']) + pd.Timedelta(hours=24):
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
                observed_status=None, window_expired=cutoff >= _aware(plan['expires_at']))
            if index < len(attempts):
                attempt, started, report = attempts[index]
                if _aware(started['started_at']) <= cutoff:
                    if not _aware(batch['created_at']) <= _aware(started['started_at']) < _aware(plan['expires_at']):
                        raise ValueError('Visit started outside its frozen window')
                    inputs.add(attempt/'started.json')
                    state.update(outcome='started_unresolved', started_at=started['started_at'])
                    if report and _aware(report['available_at']) <= cutoff:
                        _, rows = load_research_pass(attempt/'run.json', selected, cohorts, as_of=as_of)
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
                        state.update({key: row[key] for key in ['available_at','saleStatus','purchaseType','observed_status']})
                        state['outcome'] = row.parse_outcome
            health.append(state)
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(),
            pd.concat(plans, ignore_index=True) if plans else pd.DataFrame(), pd.DataFrame(health), inputs)
