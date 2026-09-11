"""Experimental public-page evidence. Parsing is pure; only load_pilot reads files."""
import hashlib
import json
from pathlib import Path
import re
from numbers import Real
from urllib.parse import urlsplit

import pandas as pd

from vehicle_tracker.events import _aware

FORMAT = 'carvana-public-detail-projection-v1'
NATIVE = ['vehicleId', 'vin', 'saleStatus', 'purchaseType', 'inventoryType']
MAX_COHORT_VINS = 50  # Membership limit; the importer still accepts only 12 captures per batch.


def validate_cohort(cohort):
    """A frozen, explicitly selected set of VINs; new listing IDs do not add VINs."""
    _aware(cohort['selected_at'])
    vehicles = cohort['vehicles']
    keys = [(v['retailer'], v['vin']) for v in vehicles]
    if not 1 <= len(keys) <= MAX_COHORT_VINS or len(set(keys)) != len(keys):
        raise ValueError(f'Select 1-{MAX_COHORT_VINS} distinct retailer/VINs')
    for v in vehicles:
        if (v['retailer'] != 'carvana' or not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', v['vin'])
                or not re.fullmatch(r'\d+', v['listing_id'])
                or v['role'] not in ['prospective_inventory', 'historical_control']
                or not v['selection_reason'].strip()
                or _url_id(v['url']) != v['listing_id']):
            raise ValueError('Invalid cohort identity, role, reason or URL')
    return vehicles


def select_extension(snapshot, identity_history, original, *, seed, pending_count=18, nonpending_count=8):
    """Pure deterministic sampling of a supplied snapshot; return the visible candidate audit.

    SQLite retains the native boolean as 0/1. Nulls and strings are not false.
    Ambiguous listing/VIN bindings and conflicting snapshot rows are excluded.
    """
    if any(type(n) is not int or n < 0 for n in [pending_count, nonpending_count]):
        raise ValueError('Sampling counts must be nonnegative integers')
    if pending_count + nonpending_count > MAX_COHORT_VINS:
        raise ValueError('Requested sample exceeds the cohort limit')
    candidates = snapshot.copy().reset_index(drop=True)
    key = ['retailer', 'vin']
    excluded = {(v['retailer'], v['vin']) for v in validate_cohort(original)}
    bindings = identity_history.groupby(['retailer', 'listing_id'], dropna=False).vin.nunique(dropna=False)
    ambiguous = set(bindings[bindings.ne(1)].index)
    conflicts = candidates.groupby(key, dropna=False)[['listing_id', 'purchase_pending', 'listing_url']].nunique(dropna=False)
    conflicting_vins = set(conflicts.loc[conflicts.gt(1).any(axis=1)].index)
    candidates['pending_group'] = candidates.purchase_pending.map(lambda v:
        ('true' if v == 1 else 'false') if isinstance(v, (bool, Real)) and v in [0, 1] else 'unknown')
    def reason(row):
        if (row.retailer, row.vin) in excluded:
            return 'original_cohort'
        if (row.retailer, row.listing_id) in ambiguous or (row.retailer, row.vin) in conflicting_vins:
            return 'ambiguous_identity_or_snapshot'
        if (row.retailer != 'carvana' or not isinstance(row.vin, str)
                or not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', row.vin)
                or not isinstance(row.listing_id, str) or _url_id(row.listing_url) != row.listing_id):
            return 'invalid_identity_or_url'
        return 'unknown_pending' if row.pending_group == 'unknown' else ''
    candidates['exclusion_reason'] = candidates.apply(reason, axis=1)
    candidates = candidates.sort_values(['vin', 'listing_id', 'observed_at_utc', 'capture_id'], kind='stable')
    # Equivalent duplicate source rows describe one selectable VIN, not extra sampling weight.
    candidates = candidates.drop_duplicates(key, keep='last').reset_index(drop=True)
    pool = candidates.loc[candidates.exclusion_reason.eq('')]
    chosen = []
    for group, requested in [('true', pending_count), ('false', nonpending_count)]:
        part = pool.loc[pool.pending_group.eq(group)]
        chosen.extend(part.sample(n=min(requested, len(part)), random_state=seed).index)
    remaining = pool.loc[~pool.index.isin(chosen)]
    shortfall = pending_count + nonpending_count - len(chosen)
    chosen.extend(remaining.sample(n=min(shortfall, len(remaining)), random_state=seed + 1).index)
    candidates['selected'] = candidates.index.isin(chosen)
    counts = pd.DataFrame([dict(pending_group=group, requested=requested,
        eligible=int(pool.pending_group.eq(group).sum()),
        selected=int((candidates.selected & candidates.pending_group.eq(group)).sum()))
        for group, requested in [('true', pending_count), ('false', nonpending_count)]])
    return candidates, counts


def known_disjoint_cohorts(cohorts, *, as_of):
    """Validate the explicit cohort list at a cutoff before combining any totals."""
    known = [c for c in cohorts if _aware(c['selected_at']) <= _aware(as_of)]
    seen, ids = set(), set()
    for cohort in known:
        keys = {(v['retailer'], v['vin']) for v in validate_cohort(cohort)}
        if cohort['cohort_id'] in ids or seen & keys:
            raise ValueError('Pilot cohorts overlap or repeat a cohort_id')
        seen.update(keys)
        ids.add(cohort['cohort_id'])
    return known


def _url_id(url):
    parts = urlsplit(url or '')
    match = re.fullmatch(r'/vehicle/(\d+)/?', parts.path)
    return match[1] if parts.scheme == 'https' and parts.netloc == 'www.carvana.com' and match else None


def parse_capture(capture, *, expected, available_at, source):
    """Validate a selected DOM/RSC projection, not HTML and not a transaction.

    Expected identity is supplied independently by the cohort/import command.
    Do not select the first arbitrary vehicle: recommendations can be present.
    """
    checked, available = _aware(capture['checked_at']), _aware(available_at)
    if available < checked:
        raise ValueError('available_at cannot precede the physical page check')
    item = dict(retailer=expected['retailer'], vin=expected['vin'], listing_id=expected['listing_id'],
        checked_at=checked.isoformat(), available_at=available.isoformat(), source=source,
        source_format=capture.get('format'), requested_url=capture.get('requested_url'),
        final_url=capture.get('final_url'), access_outcome=capture.get('access_outcome'),
        observed_listing_id=None, observed_vin=None, saleStatus=None, purchaseType=None,
        inventoryType=None, hero_text=capture.get('hero_text'), hero_badge=capture.get('hero_badge'),
        purchase_button=capture.get('purchase_button'), observed_status='unknown',
        parse_outcome='unresolved', note=capture.get('note', ''))
    if capture.get('format') != FORMAT:
        item.update(parse_outcome='unsupported_format')
        return item
    if capture.get('access_outcome') != 'ok':
        item.update(parse_outcome='access_failed', observed_status=(
            'access_blocked' if capture.get('access_outcome') == 'access_blocked' else 'unknown'))
        return item
    # Only explicitly named vehicle contexts count; numeric React paths are metadata.
    candidates = []
    contexts = capture.get('contexts')
    if not isinstance(contexts, list):
        item.update(parse_outcome='malformed_contexts')
        return item
    for context in contexts:
        parent = context.get('forVehicleContext') if isinstance(context, dict) else None
        details = parent.get('vehicleDetails') if isinstance(parent, dict) else None
        if isinstance(details, dict) and str(details.get('vehicleId')) == expected['listing_id']:
            candidates.append({k: details.get(k) for k in NATIVE})
    unique = {json.dumps(d, sort_keys=True) for d in candidates}
    if len(unique) != 1:
        item.update(parse_outcome='conflicting_records' if unique else 'target_not_found')
        return item
    detail = candidates[0]
    item.update(observed_listing_id=str(detail['vehicleId']), observed_vin=detail['vin'],
        **{k: detail[k] for k in ['saleStatus', 'purchaseType', 'inventoryType']})
    if (detail['vin'] != expected['vin'] or capture.get('expected') != expected
            or _url_id(item['requested_url']) != expected['listing_id']
            or _url_id(item['final_url']) != expected['listing_id']):
        item.update(parse_outcome='identity_mismatch')
        return item
    sale, purchase = detail['saleStatus'], detail['purchaseType']
    badge, button = item['hero_badge'], item['purchase_button']
    if any(item[k] is not None and not isinstance(item[k], str)
           for k in ['hero_text', 'hero_badge', 'purchase_button']):
        item.update(parse_outcome='malformed_fields')
        return item
    lines = (item['hero_text'] or '').splitlines()
    ui_sold = badge == 'Sold' or 'Sold' in lines
    # Retained target badges: "On Hold\n00:00" and "On Hold\n19:08".
    # Match only the complete badge with an MM:SS countdown, never page prose.
    # Even 00:00 is an observed hold badge, not evidence of expiry or a sale.
    ui_hold = bool(re.fullmatch(r'On Hold\n[0-9]{2}:[0-5][0-9]', badge or ''))
    ui_pending = ui_hold or badge == 'Purchase in progress' or 'Purchase in progress' in lines
    ui_ready = button == 'Get Started'
    ui_preorder = badge == 'Pre-order now' or button == 'Pre-Order Now'
    ui_unavailable = 'This vehicle is no longer available' in lines
    if (sum([ui_sold, ui_pending, ui_ready, ui_preorder]) > 1
            or (sale == 'Sold' and (purchase in ['Purchasable', 'Reservable'] or ui_pending or ui_ready or ui_preorder))
            or (ui_sold and sale != 'Sold')
            or ((ui_ready or ui_pending) and (purchase != 'Purchasable' or ui_unavailable))
            or (ui_preorder and purchase != 'Reservable')):
        item.update(parse_outcome='conflicting_status')
        return item
    item['parse_outcome'] = 'matched'
    if sale == 'Sold':
        item['observed_status'] = 'sold_label'
    elif sale == 'Available' and purchase == 'NotPurchasable':
        item['observed_status'] = 'unavailable'
    elif sale == 'Available' and purchase == 'Purchasable':
        if ui_pending:
            item['observed_status'] = 'pending'
        elif button == 'Get Started':
            item['observed_status'] = 'available'
    if sale not in ['Sold', 'Available'] or purchase not in [None, 'NotPurchasable', 'Purchasable', 'Reservable']:
        item.update(parse_outcome='unrecognized_status', observed_status='unknown')
    return item


def baseline_records(document, cohort, *, source):
    """Adapt the earlier manually retained study honestly; no fabricated HTML."""
    selected = {(v['retailer'], v['vin']): v for v in validate_cohort(cohort)}
    rows = []
    for old in document['observations']:
        key = old['retailer'], old['vehicle']['vin']
        if key not in selected:
            continue
        expected = dict(retailer=key[0], vin=key[1], listing_id=old['listing_id'])
        wording = old['hero_status_native']
        capture = dict(format=FORMAT, expected=expected, requested_url=old['url'], final_url=old['url'],
            checked_at=old['observed_at'], access_outcome='ok', hero_text=wording,
            hero_badge=wording if wording in ['Sold', 'Purchase in progress', 'Pre-order now'] else None,
            purchase_button='Get Started' if wording == 'Get Started' else None,
            contexts=[{'forVehicleContext': {'vehicleDetails': old['vehicle']}}])
        row = parse_capture(capture, expected=expected, available_at=old['available_at'], source=source)
        row['source_format'] = old['source_kind']
        rows.append(row)
    return rows


def load_pilot(root, cohort, *, as_of):
    """Read-only replay of optional evidence; verify retained capture hashes."""
    root, cutoff = Path(root), _aware(as_of)
    validate_cohort(cohort)
    if _aware(cohort['selected_at']) > cutoff:
        return pd.DataFrame()
    rows = []
    baseline = root / cohort['baseline_source'] if cohort.get('baseline_source') else None
    if baseline is not None and baseline.is_file():
        rows.extend(baseline_records(json.loads(baseline.read_text(encoding='utf-8')), cohort, source=str(baseline)))
    for manifest in sorted((root / 'data/experiments/carvana_sale_signals').glob('*/run.json')):
        run = json.loads(manifest.read_text(encoding='utf-8'))
        if run['cohort_id'] != cohort['cohort_id'] or _aware(run['available_at']) > cutoff:
            continue
        if run['cohort'] != cohort:
            raise ValueError('Frozen cohort differs from the retained run')
        for entry in run['captures']:
            if (entry['expected']['retailer'], entry['expected']['vin']) not in {
                    (v['retailer'], v['vin']) for v in cohort['vehicles']}:
                raise ValueError('Retained run contains a VIN outside its frozen cohort')
            path = manifest.parent / entry['file']
            if path.resolve().parent != manifest.parent.resolve():
                raise ValueError('Capture must be inside its run directory')
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError('Retained capture changed: ' + str(path))
            rows.append(parse_capture(json.loads(data), expected=entry['expected'],
                available_at=run['available_at'], source=str(path)))
    if not rows:
        return pd.DataFrame()
    result = pd.DataFrame(rows)
    for name in ['checked_at', 'available_at']:
        result[name] = result[name].map(_aware)
    result = result.loc[result.checked_at.le(cutoff) & result.available_at.le(cutoff)]
    # Reject conflicting interpretations of the same physical visit/version.
    key = ['retailer', 'vin', 'listing_id', 'checked_at', 'available_at']
    meaning = [*key, 'saleStatus', 'purchaseType', 'observed_status', 'parse_outcome']
    if result[meaning].drop_duplicates().duplicated(key).any():
        raise ValueError('Conflicting pilot records at the same visit and availability')
    return result.drop_duplicates(key).sort_values(['checked_at', 'available_at']).reset_index(drop=True)


def summarize_pilot(records, cohort, *, as_of, recheck_hours=24):
    """One row per cohort VIN; first Sold transition only, plus later reappearance.

    A historical control or an initially Sold VIN cannot produce a prospective
    first-Sold transition. Only matched native Available evidence establishes a
    non-Sold boundary; purchase availability can remain unresolved (e.g. pre-order).
    """
    cutoff = _aware(as_of)
    if (isinstance(recheck_hours, bool) or not isinstance(recheck_hours, Real)
            or not 0 < recheck_hours < float('inf')):
        raise ValueError('recheck_hours must be a finite positive number')
    if _aware(cohort['selected_at']) > cutoff:
        return pd.DataFrame()
    rows = records.copy()
    if not rows.empty:
        for field in ['checked_at', 'available_at']:
            rows[field] = rows[field].map(_aware)
        rows = rows.loc[rows.checked_at.le(cutoff) & rows.available_at.le(cutoff)]
        rows = rows.sort_values(['checked_at', 'available_at']).drop_duplicates(
            ['retailer', 'vin', 'listing_id', 'checked_at'], keep='last')
    output = []
    for vehicle in validate_cohort(cohort):
        history = rows.loc[rows.retailer.eq(vehicle['retailer']) & rows.vin.eq(vehicle['vin'])] if not rows.empty else rows
        item = dict(vehicle, checks=len(history), previous_status=None, latest_status=None,
            latest_listing_id=None, checked_at=None, available_at=None, first_sold_at=None,
            last_non_sold_at=None, newly_observed_sold=False, first_encountered_sold=False,
            reappeared_at=None, reappeared_listing_id=None, first_sold_listing_id=None,
            last_non_sold_listing_id=None, usable_status_checks=0, access_failures=0,
            unresolved_checks=0, usable_native_checks=0, prospective_repeat_observed=False,
            last_attempt_at=None, last_usable_native_at=None, last_usable_native_listing_id=None,
            hours_since_attempt=None, hours_since_usable_native=None,
            no_usable_native_observation=True, overdue=True, transition_interval_hours=None)
        for prefix in ['previous', 'latest']:
            for field in ['saleStatus', 'purchaseType', 'inventoryType', 'parse_outcome',
                          'hero_badge', 'purchase_button']:
                item[prefix + '_' + field] = None
        item.update(previous_checked_at=None, previous_available_at=None, previous_listing_id=None)
        if not history.empty:
            latest = history.iloc[-1]
            item.update(latest_status=latest.observed_status, latest_listing_id=latest.listing_id,
                checked_at=latest.checked_at, available_at=latest.available_at,
                last_attempt_at=latest.checked_at,
                hours_since_attempt=(cutoff - latest.checked_at).total_seconds() / 3600)
            for prefix, record in [('latest', latest),
                                   ('previous', history.iloc[-2] if len(history) > 1 else None)]:
                if record is not None:
                    for field in ['saleStatus', 'purchaseType', 'inventoryType', 'parse_outcome',
                                  'hero_badge', 'purchase_button']:
                        item[prefix + '_' + field] = record.get(field)
            if len(history) > 1:
                previous = history.iloc[-2]
                item.update(previous_status=previous.observed_status,
                    previous_checked_at=previous.checked_at, previous_available_at=previous.available_at,
                    previous_listing_id=previous.listing_id)
            access = history.parse_outcome.eq('access_failed')
            usable = history.parse_outcome.eq('matched') & history.observed_status.isin(
                ['available', 'pending', 'unavailable', 'sold_label'])
            item.update(access_failures=int(access.sum()), usable_status_checks=int(usable.sum()),
                unresolved_checks=int((~access & ~usable).sum()))
            valid = history.loc[history.parse_outcome.eq('matched') & history.saleStatus.isin(['Available', 'Sold'])]
            item['usable_native_checks'] = len(valid)
            if not valid.empty:
                last_native = valid.iloc[-1]
                age_hours = (cutoff - last_native.checked_at).total_seconds() / 3600
                item.update(last_usable_native_at=last_native.checked_at,
                    last_usable_native_listing_id=last_native.listing_id,
                    hours_since_usable_native=age_hours, no_usable_native_observation=False,
                    overdue=age_hours >= recheck_hours)
            if not valid.empty and vehicle['role'] == 'prospective_inventory':
                first_valid = valid.iloc[0]
                item['prospective_repeat_observed'] = bool(first_valid.saleStatus == 'Available' and
                    ((valid.checked_at > first_valid.checked_at) &
                     (valid.checked_at >= _aware(cohort['selected_at']))).any())
            sold = valid.loc[valid.observed_status.eq('sold_label')]
            if not sold.empty:
                first = sold.iloc[0]
                prior = valid.loc[valid.checked_at.lt(first.checked_at) & valid.saleStatus.eq('Available')]
                item.update(first_sold_at=first.checked_at, first_sold_listing_id=first.listing_id,
                    first_encountered_sold=prior.empty)
                if not prior.empty:
                    item.update(last_non_sold_at=prior.iloc[-1].checked_at,
                        last_non_sold_listing_id=prior.iloc[-1].listing_id,
                        newly_observed_sold=(vehicle['role'] == 'prospective_inventory'
                            and first.checked_at >= _aware(cohort['selected_at'])))
                    if item['newly_observed_sold']:
                        item['transition_interval_hours'] = (first.checked_at - prior.iloc[-1].checked_at).total_seconds() / 3600
                returned = valid.loc[valid.checked_at.gt(first.checked_at) & valid.saleStatus.eq('Available')
                                    & valid.purchaseType.isin(['Purchasable', 'Reservable'])]
                if not returned.empty:
                    item.update(reappeared_at=returned.iloc[0].checked_at,
                        reappeared_listing_id=returned.iloc[0].listing_id)
        output.append(item)
    return pd.DataFrame(output)
