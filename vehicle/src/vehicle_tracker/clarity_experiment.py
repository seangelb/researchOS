"""Clarity-method hypotheses over existing evidence, never confirmed transactions.

The FAQ discloses a rolling-week pending rule, not the exact Sold classifier.
These plain functions expose that ambiguity and keep observation clocks visible.
"""
import json
import re
from pathlib import Path

import pandas as pd

from vehicle_tracker.events import _aware, vin_events
from vehicle_tracker.sale_pilot import FORMAT, NATIVE, _read_capture, _url_id, parse_capture
from vehicle_tracker.sales import sale_candidates
from vehicle_tracker.sales_proxy import inventory_exit_episodes, native_visits


def pending_entry_experiment(cycles, observations, *, as_of):
    """Observe false-to-true entries; test a seven-calendar-day entry cooldown.

    Daily snapshots miss short holds. A missing lookback, new VIN, relisting or
    uncertain before/after interval cannot establish a Clarity-equivalent order.
    The cooldown is measured from every observed entry, including suppressed ones.
    It is an explicit interpretation of the FAQ, not a recovered vendor algorithm.
    """
    cutoff = _aware(as_of)
    days = cycles.loc[cycles.available_at.map(_aware).le(cutoff)].sort_values('cycle_date')
    rows = observations.loc[observations.cycle_id.isin(days.cycle_id)]
    events = vin_events(days, rows)
    columns = ['retailer', 'vin', 'listing_id', 'cycle_date', 'checked_at', 'available_at',
               'entry_observed', 'continuous_pair', 'complete_prior_week',
               'previous_entry_date', 'repeat_within_week', 'order_proxy_eligible', 'reason']
    complete_dates = set(days.loc[days.coverage_complete, 'cycle_date'])
    dated = rows.merge(days[['cycle_id', 'cycle_date']], on='cycle_id', validate='many_to_one')
    observation_dates = {(r.retailer, r.vin, r.capture_id): r.cycle_date for r in dated.itertuples()}
    result, previous_entries = [], {}
    for row in events.loc[events.observed_in_cycle & events.purchase_pending.eq(True)].itertuples():
        if pd.notna(row.previous_purchase_pending) and row.previous_purchase_pending == 1:
            continue  # Remaining pending never becomes another entry when a week passes.
        date = pd.Timestamp(row.cycle_date)
        previous_date = observation_dates.get((row.retailer, row.vin, row.previous_capture_id))
        entry = bool(pd.notna(row.previous_purchase_pending) and row.previous_purchase_pending == 0)
        pair = bool(entry and previous_date == (date - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
                    and previous_date in complete_dates and row.cycle_date in complete_dates
                    and row.listing_id == row.previous_listing_id and not row.timing_uncertain)
        prior_week = {(date - pd.Timedelta(days=n)).strftime('%Y-%m-%d') for n in range(1, 8)}
        full_week = prior_week.issubset(complete_dates)
        key = (row.retailer, row.vin)
        previous = previous_entries.get(key)
        repeat = previous is not None and (date - previous).days < 7
        eligible = pair and full_week and not repeat
        reason = ('first/unknown pending baseline' if not entry else
                  'incomplete interval or changed listing' if not pair else
                  'missing complete prior week' if not full_week else
                  'repeat entry within seven days' if repeat else 'observed rolling-week order proxy')
        result.append(dict(retailer=row.retailer, vin=row.vin, listing_id=row.listing_id,
            cycle_date=row.cycle_date, checked_at=row.observed_at_utc, available_at=row.available_at,
            entry_observed=entry, continuous_pair=pair, complete_prior_week=full_week,
            previous_entry_date=None if previous is None else previous.strftime('%Y-%m-%d'),
            repeat_within_week=repeat, order_proxy_eligible=eligible, reason=reason))
        # Unknown first-pending observations also prevent claiming a new order soon afterward.
        previous_entries[key] = date
    return pd.DataFrame(result, columns=columns)


def exit_rule_experiment(cycles, observations, records, *, as_of):
    """Compare rules on the same exit episodes, using only later matched checks.

    Three/seven-day rules receive their own decision clocks. Native Sold already
    known at that decision is excluded from the later-outcome evaluation. This
    avoids validating a rule with evidence already used/known when it qualified.
    Unchecked and unfinished episodes remain in the denominator as unresolved.
    """
    cutoff = _aware(as_of)
    episodes = inventory_exit_episodes(cycles, observations, as_of=as_of)
    visits = native_visits(records, as_of=as_of)
    valid = visits.loc[visits.parse_outcome.eq('matched') & visits.saleStatus.isin(['Available', 'Sold'])] if not visits.empty else visits
    key = ['retailer', 'vin', 'last_observed_cycle_id']
    qualified = {}
    for days in [3, 7]:
        frame, _ = sale_candidates(cycles, observations, as_of=as_of, absence_days=days)
        qualified[days] = {tuple(row[k] for k in key): row for row in frame.to_dict('records')}
    output = []
    for episode in episodes.to_dict('records'):
        own = valid.loc[valid.retailer.eq(episode['retailer']) & valid.vin.eq(episode['vin'])
                        & valid.listing_id.eq(episode['listing_id'])] if not valid.empty else valid
        for rule in ['all_exits', 'pending_before_exit', 'absence_3d', 'absence_7d']:
            candidate = episode if rule in ['all_exits', 'pending_before_exit'] else qualified[int(rule[-2])].get(tuple(episode[k] for k in key))
            pending = episode['last_purchase_pending']
            chosen = candidate is not None and (rule != 'pending_before_exit' or (pd.notna(pending) and pending == 1))
            decision = _aware(candidate['detected_available_at']) if chosen else pd.NaT
            # Replay the decision vintage before selecting the latest record version.
            # A later correction/re-export must not erase an earlier known status.
            before = native_visits(records, as_of=decision) if chosen else own.iloc[:0]
            if not before.empty:
                before = before.loc[before.retailer.eq(episode['retailer']) & before.vin.eq(episode['vin'])
                    & before.listing_id.eq(episode['listing_id']) & before.parse_outcome.eq('matched')
                    & before.saleStatus.isin(['Available', 'Sold'])]
            known_sold = bool(not before.empty and before.iloc[-1].saleStatus == 'Sold')
            later = own.loc[own.checked_at.gt(decision)] if chosen and not own.empty else own.iloc[:0]
            censor = _aware(episode['reappeared_at']) if pd.notna(episode['reappeared_at']) else cutoff
            if pd.notna(episode['reappeared_at']) and not later.empty:
                later = later.loc[later.checked_at.lt(censor)]
            sold = later.loc[later.saleStatus.eq('Sold')] if not later.empty else later
            returned = pd.notna(episode['reappeared_date'])
            evaluable = bool(chosen and not known_sold)
            attempts = visits.loc[visits.retailer.eq(episode['retailer']) & visits.vin.eq(episode['vin'])
                & visits.listing_id.eq(episode['listing_id']) & visits.checked_at.gt(decision)] if chosen and not visits.empty else visits.iloc[:0]
            if returned and not attempts.empty:
                attempts = attempts.loc[attempts.checked_at.lt(censor)]
            output.append(dict(rule=rule, exit_episode_id=episode['exit_episode_id'],
                retailer=episode['retailer'], vin=episode['vin'], listing_id=episode['listing_id'],
                last_seen_at=episode['last_observed_at'], first_absent_date=episode['first_absent_date'],
                last_purchase_pending=pending, last_asking_price_usd=episode['last_asking_price_usd'],
                timing_uncertain=episode['timing_uncertain'], selected=bool(chosen), decision_at=decision,
                sold_known_at_decision=known_sold, evaluable=evaluable,
                followup_attempts=len(attempts), followup_censored_at=censor,
                matched_later_checks=len(later), first_later_native=None if later.empty else later.iloc[0].saleStatus,
                later_sold_seen=bool(len(sold)) if evaluable and not later.empty else pd.NA,
                first_later_sold_at=pd.NaT if sold.empty else sold.checked_at.min(),
                latest_later_native=None if later.empty else later.iloc[-1].saleStatus,
                inventory_reappeared=returned, reappeared_date=episode['reappeared_date'],
                hours_of_followup=max(0, (censor-decision).total_seconds()/3600) if chosen else pd.NA,
                source_references=json.dumps(sorted(set(own.source))) if not own.empty else '[]',
                interpretation='Website corroboration in checked subset; not transaction precision'))
    return pd.DataFrame(output, columns=[
        'rule', 'exit_episode_id', 'retailer', 'vin', 'listing_id', 'last_seen_at', 'first_absent_date',
        'last_purchase_pending', 'last_asking_price_usd', 'timing_uncertain', 'selected', 'decision_at',
        'sold_known_at_decision', 'evaluable', 'followup_attempts', 'followup_censored_at', 'matched_later_checks', 'first_later_native',
        'later_sold_seen', 'first_later_sold_at', 'latest_later_native', 'inventory_reappeared',
        'reappeared_date', 'hours_of_followup', 'source_references', 'interpretation'])


def capture_public_html(html, expected, *, checked_at, final_url):
    """Extract only the same public RSC vehicle fields as the Chrome helper.

    Never execute JavaScript, follow a discovered endpoint, or infer Sold from
    arbitrary body text. Existing parse_capture validates VIN, URL and listing.
    Hero fields stay unknown because this transport does not render a browser.
    """
    if _url_id(final_url) != expected['listing_id']:
        raise ValueError('Detail URL does not match the planned listing')
    chunks = []
    decoder = json.JSONDecoder()
    for script in re.findall(r'<script\b[^>]*>(.*?)</script\s*>', html, flags=re.S | re.I):
        for match in re.finditer(r'self\.__next_f\.push\s*\(', script):
            try:
                value, _ = decoder.raw_decode(script[match.end():].lstrip())
                if isinstance(value, list) and len(value) >= 2 and value[0] == 1 and isinstance(value[1], str):
                    chunks.append(value[1])
            except (ValueError, TypeError):
                continue
    contexts = []
    def visit(value, path):
        if not isinstance(value, (dict, list)):
            return
        if isinstance(value, dict):
            parent = value.get('forVehicleContext')
            detail = parent.get('vehicleDetails') if isinstance(parent, dict) else None
            if isinstance(detail, dict) and str(detail.get('vehicleId')) == expected['listing_id']:
                contexts.append({'source_path': path + '.forVehicleContext.vehicleDetails',
                    'forVehicleContext': {'vehicleDetails': {k: detail[k] for k in NATIVE if k in detail}}})
            children = ((k, v) for k, v in value.items() if not re.search(r'cookie|token|session|user|account|auth|customer|financing', k, re.I))
        else:
            children = enumerate(value)
        for key, child in children:
            visit(child, path + '.' + str(key))
    for line in ''.join(chunks).splitlines():
        prefix, separator, record = line.partition(':')
        if separator:
            try:
                visit(json.loads(record), prefix)
            except ValueError:
                continue
    capture = dict(format=FORMAT, expected=expected,
        requested_url='https://www.carvana.com/vehicle/' + expected['listing_id'], final_url=final_url,
        checked_at=_aware(checked_at).isoformat(), access_outcome='ok' if contexts else 'unknown',
        contexts=contexts, hero_text=None, hero_badge=None, purchase_button=None,
        capture_method='anonymous_http_html_rsc_projection',
        note='Selected public HTTP HTML/RSC fields; no rendered DOM or transaction evidence.')
    result = parse_capture(capture, expected=expected, available_at=checked_at, source='in-memory HTTP projection')
    if result['parse_outcome'] != 'matched':
        raise ValueError('No unique matched native record: ' + result['parse_outcome'])
    return capture


def load_detail_probe(report_path, plan_path, *, as_of):
    """Read a completed/stopped experimental run against its separate frozen plan."""
    report_path = Path(report_path)
    report = json.loads(report_path.read_text(encoding='utf-8'))
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8'))
    cutoff, available = _aware(as_of), _aware(report['available_at'])
    if available > cutoff:
        return None, pd.DataFrame()
    if report['plan'] != plan or report['status'] not in ['complete', 'stopped']:
        raise ValueError('Probe report differs from plan or is incomplete')
    if (not _aware(plan['prepared_at']) <= _aware(report['started_at']) <= available
            or not 0 <= report['requests'] == len(report['attempts']) <= len(plan['pages']) <= 6):
        raise ValueError('Probe request counts or clocks are inconsistent')
    rows = []
    for attempt, selected in zip(report['attempts'], plan['pages']):
        expected = {k: selected[k] for k in ['retailer', 'vin', 'listing_id']}
        if attempt['expected'] != expected or attempt['url'] != selected['url']:
            raise ValueError('Probe target differs from selected identity')
        if not _aware(plan['prepared_at']) <= _aware(attempt['started_at']) < _aware(plan['expires_at']):
            raise ValueError('Probe request started outside its plan window')
        if attempt['outcome'] != 'matched':
            continue
        entry = dict(file=attempt['capture_file'], sha256=attempt['capture_sha256'], expected=expected)
        row = _read_capture(report_path, entry, available_at=available)
        if not _aware(attempt['started_at']) <= _aware(row['checked_at']) <= available or row['parse_outcome'] != 'matched':
            raise ValueError('Probe capture clock or identity is invalid')
        row['transport'] = 'anonymous HTTP HTML/RSC'
        rows.append(row)
    return report, pd.DataFrame(rows)
