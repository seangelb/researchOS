"""An inventory-defined, prospective website-status experiment.

Four sampling groups, two fixed observation windows, and three small exact tests.
Native Sold is the endpoint; transactions and nationwide sales are not measured.
"""
from math import comb, sqrt
import hashlib
import json
from pathlib import Path

import pandas as pd

from vehicle_tracker.detail_batch import browser_capacity
from vehicle_tracker.events import _aware, _cycles, vin_events
from vehicle_tracker.sales_proxy import native_visits

ARMS = ['exit_pending', 'exit_nonpending', 'listed_pending', 'listed_nonpending']
COMPARISONS = [
    ('H1_pending', 'exit_pending', 'listed_pending'),
    ('H1_nonpending', 'exit_nonpending', 'listed_nonpending'),
    ('H2_pending_exits', 'exit_pending', 'exit_nonpending'),
]


def inventory_frame(cycles, observations, *, as_of):
    """Use the latest two known dates, never skip an incomplete date or gap.

    Pending means the BEFORE snapshot flag in all four arms. New entrants are
    outside this beginning-inventory population. Changed listing IDs and unknown
    pending flags stay visible as exclusions. Detail outcomes are not an input.
    """
    cutoff = _aware(as_of)
    days = cycles.loc[cycles.available_at.map(_aware).le(cutoff)].copy()
    days = _cycles(days).tail(2)
    if len(days) != 2 or not days.coverage_complete.all():
        raise ValueError('Need two latest complete inventory dates; no fallback over gaps')
    before, after = days.iloc[0], days.iloc[1]
    if (pd.Timestamp(after.cycle_date) - pd.Timestamp(before.cycle_date)).days != 1:
        raise ValueError('Inventory dates must be consecutive')
    rows = observations.loc[observations.cycle_id.isin(days.cycle_id)].copy()
    vin_events(days, rows)  # Reuse identity, duplicate and physical-clock validation.
    old = rows.loc[rows.cycle_id.eq(before.cycle_id)].copy()
    new = rows.loc[rows.cycle_id.eq(after.cycle_id)].copy()
    current = {(r.retailer, r.vin): r for r in new.itertuples()}
    output = []
    for row in old.sort_values(['retailer', 'vin']).itertuples():
        later = current.get((row.retailer, row.vin))
        exited = later is None
        pending = row.purchase_pending
        if pd.notna(pending) and pending not in (0, 1, False, True):
            raise ValueError('Pending must be true, false or unknown')
        excluded = ('unknown baseline pending' if pd.isna(pending) else
                    'changed listing identity' if later is not None and later.listing_id != row.listing_id else '')
        arm = None if excluded else ('exit_' if exited else 'listed_') + ('pending' if pending else 'nonpending')
        output.append(dict(retailer=row.retailer, vin=row.vin, listing_id=row.listing_id,
            url=row.listing_url, arm=arm, exclusion_reason=excluded,
            baseline_pending=None if pd.isna(pending) else bool(pending), exited=exited,
            before_cycle_id=before.cycle_id, after_cycle_id=after.cycle_id, scope_id=after.scope_id,
            before_date=before.cycle_date, after_date=after.cycle_date,
            before_observed_at=row.observed_at_utc,
            after_observed_at=None if exited else later.observed_at_utc,
            decision_at=after.available_at, last_asking_price_usd=row.asking_price_usd,
            parent_model=row.parent_model, model_year=row.year,
            before_source=row.source_path, after_source=None if exited else later.source_path))
    if not output:
        raise ValueError('Beginning inventory is empty; no sampling population')
    return pd.DataFrame(output)


def sample_frame(frame, *, per_arm=3, seed='carvana-status-v1'):
    """Seeded sampling within four inventory arms; order-independent hash ranking.

    k/N belongs to its arm only. Unequal arm populations are not silently pooled
    into an estimate for the inventory universe. At most 100 study VINs; the
    existing collector splits each wave into batches of at most 12 visits.
    """
    if type(per_arm) is not int or not 1 <= per_arm <= 25:
        raise ValueError('Use 1-25 VINs per arm, at most 100 study VINs')
    if frame.duplicated(['retailer', 'vin']).any():
        raise ValueError('One independent row per retailer/VIN is required')
    result = frame.sort_values(['retailer', 'vin']).reset_index(drop=True).copy()
    result['selected_for_check'] = False
    result['selection_probability'] = 0.0
    result['stratum_population'] = 0
    for arm in ARMS:
        eligible = result.loc[result.arm.eq(arm) & result.exclusion_reason.eq('')]
        count, size = min(per_arm, len(eligible)), len(eligible)
        if not size:
            continue
        ranks = eligible.apply(lambda r: hashlib.sha256(
            f'{seed}|{arm}|{r.retailer}|{r.vin}'.encode()).hexdigest(), axis=1)
        result.loc[eligible.index, 'stratum_population'] = size
        result.loc[eligible.index, 'selection_probability'] = count / size
        result.loc[ranks.sort_values().head(count).index, 'selected_for_check'] = True
    return result


def freeze_plan(frame, *, as_of, prepared_at, seed, per_arm=3):
    """Build candidate JSON; the CLI requires study_feasibility before publication."""
    cutoff, prepared = _aware(as_of), _aware(prepared_at)
    if cutoff > prepared or frame.decision_at.map(_aware).gt(cutoff).any():
        raise ValueError('Selection cannot precede its source evidence')
    selected = sample_frame(frame, per_arm=per_arm, seed=seed)
    pages = selected.loc[selected.selected_for_check].copy()
    if pages.empty:
        raise ValueError('No eligible inventory-defined targets')
    pages['selection_reason'] = 'Frozen status experiment: ' + pages.arm
    return dict(format='carvana-status-experiment-v1', as_of=cutoff.isoformat(),
        prepared_at=prepared.isoformat(), seed=seed, per_arm=per_arm,
        primary_start=prepared.isoformat(), primary_end=(prepared + pd.Timedelta(hours=48)).isoformat(),
        repeat_start=(prepared + pd.Timedelta(days=7)).isoformat(),
        repeat_end=(prepared + pd.Timedelta(days=9)).isoformat(),
        endpoint='Native saleStatus on first matched check in each fixed window; not transactions',
        tests='Three one-sided Fisher tests, positive association; Bonferroni family of three, alpha=0.05',
        frame=json.loads(selected.to_json(orient='records', date_format='iso')),
        pages=json.loads(pages.to_json(orient='records', date_format='iso')))


def study_feasibility(plan, records, *, browser_root, now, minutes_per_check=1.5,
                      operator_minutes_per_day=20):
    """Read-only remaining workload for both fixed waves; never resize a plan.

    First matched checks, including Unavailable, finish ascertainment. Failures
    and absent checks still require capacity. A feasible result is conditional
    on the stated operator effort and no new competing work, not guaranteed
    collection success or reserved future starts.
    """
    cutoff = _aware(now)
    visits = native_visits(records, as_of=cutoff)
    requests, completed = [], {}
    for wave in ['primary', 'repeat']:
        start, end = _aware(plan[wave+'_start']), _aware(plan[wave+'_end'])
        completed[wave] = 0
        for page in plan['pages']:
            own = visits.loc[visits.retailer.eq(page['retailer']) & visits.vin.eq(page['vin'])
                & visits.listing_id.eq(page['listing_id']) & visits.parse_outcome.eq('matched')
                & visits.checked_at.ge(start) & visits.checked_at.lt(end)] if not visits.empty else visits
            if not own.empty:
                completed[wave] += 1
            else:
                requests.append(dict(wave=wave, retailer=page['retailer'], vin=page['vin'],
                    start=start.isoformat(), end=end.isoformat()))
    capacity = browser_capacity(browser_root, requests, now=cutoff,
        prior_checks=[] if visits.empty else visits[['retailer', 'vin', 'checked_at']].to_dict('records'),
        minutes_per_check=minutes_per_check, operator_minutes_per_day=operator_minutes_per_day)
    rows = []
    for wave in ['primary', 'repeat']:
        checks = [check for check in capacity['checks'] if check['wave'] == wave]
        available = sum(check['fits'] for check in checks)
        start, end = _aware(plan[wave+'_start']), _aware(plan[wave+'_end'])
        rows.append(dict(wave=wave, window_start=start.isoformat(), window_end=end.isoformat(),
            window_state='not started' if cutoff < start else 'open' if cutoff < end else 'closed',
            selected=len(plan['pages']), identity_matched_completed=completed[wave], required_checks=len(checks),
            available_capacity=available, shortfall=len(checks)-available,
            required_operator_minutes=len(checks)*minutes_per_check,
            planned_operator_minutes=available*minutes_per_check))
    return dict(as_of=cutoff.isoformat(), feasible=all(r['shortfall'] == 0 for r in rows),
        table=rows, **capacity)


def _physical_attempts(visits, health, *, cutoff):
    """One physical attempt, dated by its known start rather than late completion.

    Captures without a browser reservation use their observation time. Browser
    health carries exact source/checked clocks; the interval fallback supports
    older health tables without counting a reservation and its capture twice.
    No reservation creates a native endpoint.
    """
    attempts = visits.to_dict('records') if not visits.empty else []
    for attempt in attempts:
        attempt['attempt_at'] = attempt['checked_at']
    if health.empty:
        return pd.DataFrame(attempts)
    starts = health.loc[health.started_at.notna()].copy()
    starts['started_at'] = starts.started_at.map(_aware)
    starts = starts.loc[starts.started_at.le(cutoff)].sort_values('started_at')
    identity = ['retailer', 'vin', 'listing_id']
    starts = starts.drop_duplicates([*identity, 'started_at'])
    assigned = set()
    for reservation in starts.to_dict('records'):
        same = starts.loc[(starts[identity] == pd.Series({k: reservation[k] for k in identity})).all(axis=1)
                          & starts.started_at.gt(reservation['started_at'])]
        next_start = same.started_at.min() if not same.empty else None
        candidates = [i for i, attempt in enumerate(attempts) if i not in assigned
            and all(attempt[k] == reservation[k] for k in identity)
            and pd.notna(attempt.get('checked_at'))
            and attempt['checked_at'] >= reservation['started_at']
            and (next_start is None or attempt['checked_at'] < next_start)]
        # Exact capture clocks avoid attaching a later correction to another visit.
        if pd.notna(reservation.get('checked_at')):
            candidates = [i for i in candidates if attempts[i]['checked_at'] == _aware(reservation['checked_at'])]
        elif pd.notna(reservation.get('available_at')):
            candidates = [i for i in candidates if attempts[i]['available_at'] == _aware(reservation['available_at'])]
        unresolved = (reservation['outcome'] == 'started_unresolved' or
            (pd.notna(reservation.get('available_at')) and _aware(reservation['available_at']) > cutoff))
        if candidates and not unresolved:
            index = min(candidates, key=lambda i: attempts[i]['checked_at'])
            attempts[index]['attempt_at'] = reservation['started_at']
            assigned.add(index)
        else:
            attempts.append(dict(**{k: reservation[k] for k in identity}, attempt_at=reservation['started_at'],
                checked_at=pd.NaT, parse_outcome='started_unresolved' if unresolved else 'capture_missing'))
            assigned.add(len(attempts)-1)
    return pd.DataFrame(attempts)


def score_plan(plan, records, cycles, observations, *, as_of, browser_health=None):
    """One row per selected VIN; first matched check wins within each fixed window.

    Failed, missing, late and unavailable captures never become Available labels.
    Later corrections replay through native_visits at the requested cutoff.
    Reappearance and elapsed observation intervals are descriptive, not returns
    or delivery times. A changed listing cannot supply this listing's outcome.
    """
    cutoff = _aware(as_of)
    if _aware(plan['prepared_at']) > cutoff:
        return pd.DataFrame()
    visits = native_visits(records, as_of=as_of)
    health = pd.DataFrame() if browser_health is None else browser_health
    physical = _physical_attempts(visits, health, cutoff=cutoff)
    days = cycles.loc[cycles.available_at.map(_aware).le(cutoff)]
    inventory = observations.loc[observations.cycle_id.isin(days.cycle_id)]
    output = []
    for page in plan['pages']:
        own = visits.loc[visits.retailer.eq(page['retailer']) & visits.vin.eq(page['vin'])
            & visits.listing_id.eq(page['listing_id'])] if not visits.empty else visits
        matched = own.loc[own.parse_outcome.eq('matched')] if not own.empty else own
        own_attempts = physical.loc[physical.retailer.eq(page['retailer']) & physical.vin.eq(page['vin'])
            & physical.listing_id.eq(page['listing_id'])] if not physical.empty else physical
        row = {k: page[k] for k in ['retailer', 'vin', 'listing_id', 'arm', 'selection_probability', 'stratum_population']}
        row['scored_as_of'] = cutoff.isoformat()
        for wave in ['primary', 'repeat']:
            start, end = _aware(plan[wave+'_start']), _aware(plan[wave+'_end'])
            attempts = own_attempts.loc[own_attempts.attempt_at.ge(start) & own_attempts.attempt_at.lt(end)] if not own_attempts.empty else own_attempts
            reservations = attempts.loc[attempts.parse_outcome.eq('started_unresolved')] if not attempts.empty else attempts
            late = attempts.loc[pd.to_datetime(attempts.checked_at, utc=True).ge(end)] if not attempts.empty else attempts
            valid = matched.loc[matched.checked_at.ge(start) & matched.checked_at.lt(end)] if not matched.empty else matched
            first = None if valid.empty else valid.iloc[0]
            resolved = first is not None and first.saleStatus in ['Available', 'Sold']
            row.update({wave+'_status': first.saleStatus if resolved else None,
                wave+'_native_status': None if first is None else first.saleStatus,
                wave+'_checked_at': None if first is None else first.checked_at,
                wave+'_source': None if first is None else first.source,
                wave+'_attempts': len(attempts),
                wave+'_identity_matched': first is not None,
                wave+'_failed_attempts': int((~attempts.parse_outcome.isin(['matched', 'started_unresolved', 'capture_missing'])).sum()) if not attempts.empty else 0,
                wave+'_unresolved_visits': len(reservations),
                wave+'_late_completions': len(late),
                wave+'_late_matched_captures': int(late.parse_outcome.eq('matched').sum()) if not late.empty else 0,
                wave+'_outcome': ('matched' if resolved else 'unresolved status') if first is not None else
                    'started unresolved' if len(reservations) else
                    'late completion' if len(late) else
                    'pending' if cutoff < end else 'failed' if len(attempts) else 'missing'})
        after = matched.loc[matched.checked_at.ge(_aware(plan['prepared_at']))] if not matched.empty else matched
        sold = after.loc[after.saleStatus.eq('Sold')] if not after.empty else after
        first_sold = None if sold.empty else sold.iloc[0]
        available = matched.loc[matched.saleStatus.eq('Available') & matched.checked_at.lt(first_sold.checked_at)] if first_sold is not None else matched.iloc[:0]
        returned = inventory.loc[inventory.retailer.eq(page['retailer']) & inventory.vin.eq(page['vin'])
            & inventory.observed_at_utc.map(_aware).gt(_aware(page['decision_at']))] if page['exited'] and not inventory.empty else inventory.iloc[:0]
        row.update(first_sold_at=None if first_sold is None else first_sold.checked_at,
            last_available_before_sold_at=None if available.empty else available.iloc[-1].checked_at,
            hours_exit_to_sold_observation=None if first_sold is None or not page['exited'] else
                (first_sold.checked_at - _aware(page['decision_at'])).total_seconds() / 3600,
            inventory_reappeared_at=None if returned.empty else returned.observed_at_utc.map(_aware).min(),
            sold_to_available_between_waves=row['primary_status'] == 'Sold' and row['repeat_status'] == 'Available',
            available_to_sold_between_waves=row['primary_status'] == 'Available' and row['repeat_status'] == 'Sold')
        output.append(row)
    return pd.DataFrame(output)


def _validate_outcomes(outcomes, plan, *, as_of):
    identity = ['retailer', 'vin', 'listing_id', 'arm']
    expected = pd.DataFrame(plan['pages'])[identity].sort_values(identity).reset_index(drop=True)
    actual = outcomes.reindex(columns=identity).sort_values(identity).reset_index(drop=True)
    if not expected.equals(actual) or outcomes.duplicated(['retailer', 'vin']).any():
        raise ValueError('Outcomes must contain every frozen selected VIN exactly once in its original arm')
    if 'scored_as_of' not in outcomes or not outcomes.scored_as_of.map(_aware).eq(_aware(as_of)).all():
        raise ValueError('Rescore outcomes at the requested hypothesis cutoff')


def arm_outcomes(outcomes, plan, *, as_of, wave='primary'):
    """Distinct-VIN counts, conditional Wilson intervals and full-sample bounds.

    Sold/resolved is descriptive: selective missingness can bias it. Its 95%
    Wilson interval assumes independent Bernoulli outcomes within an arm and
    does not correct missingness, shared daily conditions or selection bias.
    Bounds allow every unresolved binary endpoint, including Unavailable, to
    be Sold or non-Sold. Repeated observations never increase the VIN denominator.
    """
    if wave not in ['primary', 'repeat']:
        raise ValueError('Use primary or repeat')
    _validate_outcomes(outcomes, plan, as_of=as_of)
    rows = []
    for arm in ARMS:
        group = outcomes.loc[outcomes.arm.eq(arm)]
        selected = len(group)
        sold, available = [int(group[wave+'_status'].eq(status).sum()) for status in ['Sold', 'Available']]
        resolved, unresolved = sold+available, selected-sold-available
        fraction = sold/resolved if resolved else None
        low, high = None, None
        if resolved:
            z = 1.959963984540054
            center = (fraction+z*z/(2*resolved))/(1+z*z/resolved)
            half = z*sqrt(fraction*(1-fraction)/resolved+z*z/(4*resolved**2))/(1+z*z/resolved)
            low, high = max(0.0, center-half), min(1.0, center+half)
        rows.append(dict(wave=wave, arm=arm, selected=selected,
            attempted=int(group[wave+'_attempts'].gt(0).sum()),
            identity_matched=int(group[wave+'_identity_matched'].sum()), sold=sold, available=available,
            unavailable=int(group[wave+'_native_status'].eq('Unavailable').sum()),
            failed=int((group[wave+'_failed_attempts'].gt(0) & ~group[wave+'_identity_matched']).sum()),
            started_unresolved=int(group[wave+'_unresolved_visits'].gt(0).sum()),
            late_completion=int(group[wave+'_late_completions'].gt(0).sum()),
            late_identity_matched=int(group[wave+'_late_matched_captures'].gt(0).sum()),
            unvisited=int(group[wave+'_attempts'].eq(0).sum()), resolved=resolved, unresolved=unresolved,
            sold_among_resolved=fraction, resolved_wilson95_low=low, resolved_wilson95_high=high,
            selected_sold_lower=sold/selected if selected else None,
            selected_sold_upper=(sold+unresolved)/selected if selected else None))
    return pd.DataFrame(rows)


def fisher_greater(a, b, c, d):
    """Exact positive-association test for [[Sold A, Available A], [Sold B, Available B]].

    Sum the hypergeometric upper tail, as in SciPy's Fisher exact documentation.
    Small fixed batches make a standard-library implementation sufficient.
    """
    if any(type(n) is not int or n < 0 for n in [a, b, c, d]) or not (a+b and c+d):
        raise ValueError('Fisher test needs nonnegative integer counts and two nonempty arms')
    total, sold, first = a+b+c+d, a+c, a+b
    denominator = comb(total, first)
    return min(1.0, sum(comb(sold, x) * comb(total-sold, first-x) / denominator
        for x in range(a, min(sold, first)+1) if 0 <= first-x <= total-sold))


def hypothesis_tests(outcomes, plan, *, as_of):
    """Within-pending exit comparisons avoid pooling oversampled pending strata.

    Missing outcomes block tests, not denominators. P-values concern this sampled
    snapshot pair under an independence model; model/year confounding and day
    clustering remain. They cannot establish causation or transaction accuracy.
    """
    _validate_outcomes(outcomes, plan, as_of=as_of)
    output = []
    for hypothesis, arm_a, arm_b in COMPARISONS:
        a = outcomes.loc[outcomes.arm.eq(arm_a)] if not outcomes.empty else outcomes
        b = outcomes.loc[outcomes.arm.eq(arm_b)] if not outcomes.empty else outcomes
        counts = [int(group.primary_status.eq(status).sum()) if len(group) else 0
                  for group in [a, b] for status in ['Sold', 'Available']]
        unresolved = len(a)+len(b)-sum(counts)
        state = ('window open' if _aware(as_of) < _aware(plan['primary_end']) else
                 'empty arm' if not len(a) or not len(b) else
                 'unresolved outcomes' if unresolved else 'ready')
        probability = fisher_greater(*counts) if state == 'ready' else None
        difference = counts[0]/len(a)-counts[2]/len(b) if state == 'ready' else None
        output.append(dict(hypothesis=hypothesis, arm_a=arm_a, arm_b=arm_b,
            selected_a=len(a), selected_b=len(b), sold_a=counts[0], available_a=counts[1],
            sold_b=counts[2], available_b=counts[3], unresolved=unresolved, status=state,
            risk_difference=difference, p_value=probability,
            p_adjusted=None if probability is None else min(1.0, 3*probability),
            interpretation='Exploratory website-status association; not transaction accuracy'))
    return pd.DataFrame(output)


def read_experiment(path, *, as_of):
    """Verify frozen selection and its exact source files without any writes."""
    path = Path(path)
    manifest = json.loads((path.parent/'manifest.json').read_text(encoding='utf-8'))
    digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    if digest(path) != manifest['plan_sha256']:
        raise ValueError('Frozen experiment plan changed')
    plan = json.loads(path.read_text(encoding='utf-8'))
    if plan['format'] != 'carvana-status-experiment-v1':
        raise ValueError('Unknown status experiment format')
    if _aware(plan['prepared_at']) > _aware(as_of):
        return None
    if any(digest(p) != sha for p, sha in manifest['input_hashes'].items()):
        raise ValueError('Frozen experiment source changed')
    return plan
