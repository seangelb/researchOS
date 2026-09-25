"""Small, read-only sales indicators. Website events are not completed transactions.

Inputs come from the existing hash-checked readers. All functions return pandas
tables and preserve both observation intervals and evidence-availability clocks.
No collector, database writer, fitted probability, or national scaling lives here.
"""
import hashlib
import json
from math import sqrt

import pandas as pd

from vehicle_tracker.events import _aware, vin_events
from vehicle_tracker.sale_pilot import _url_id, known_disjoint_cohorts, summarize_pilot
from vehicle_tracker.sales import sale_candidates
from vehicle_tracker.vehicle_history import followup_identities


def wilson_resolved_interval(sold, resolved):
    """Same 95% Wilson interval as status_experiment.arm_outcomes."""
    if not resolved:
        return None, None
    fraction = sold / resolved
    z = 1.959963984540054
    center = (fraction + z * z / (2 * resolved)) / (1 + z * z / resolved)
    half = z * sqrt(fraction * (1 - fraction) / resolved + z * z / (4 * resolved ** 2)) / (
        1 + z * z / resolved)
    return max(0.0, center - half), min(1.0, center + half)


EVENT_COLUMNS = [
    'event_id', 'retailer', 'vin', 'listing_id', 'interval_start', 'interval_end',
    'event_available_at', 'evidence_available_at', 'source_references',
    'repeat_sold_checks', 'persistence_hours', 'contrary_native_at',
    'inventory_reappearance_at', 'central_eligible', 'conservative_eligible',
]
LEDGER_COLUMNS = ['event_id', 'retailer', 'vin', 'listing_id', 'interval_start',
    'interval_end', 'event_available_at', 'evidence_available_at', 'source_references',
    'estimated_units', 'basis', 'method', 'population', 'prospective_vins',
    'initially_non_sold_vins', 'as_of']


def reappearance_summary(departures, reappearances, *, latest_snapshot):
    """Report return fractions with matching episode and retailer/VIN populations.

    An episode is (retailer, VIN, first absent snapshot), even if its listing ID
    is reused. Only departures with a later comparable snapshot enter either
    denominator. A VIN returning twice contributes two episodes but one VIN.
    These are chronological website observations, not transaction error rates.
    """
    key = ['retailer', 'vin', 'first_absent_snapshot']
    if departures.empty:
        if not reappearances.empty:
            raise ValueError('A reappearance must belong to a departure episode')
        return pd.DataFrame()
    if departures.duplicated(key).any() or (not reappearances.empty and reappearances.duplicated(key).any()):
        raise ValueError('Each departure/reappearance episode must be unique')
    mature = departures.loc[departures.first_absent_snapshot.ne(latest_snapshot)]
    returned = reappearances
    if not returned.empty:
        joined = returned.merge(mature[key], on=key, how='left', indicator=True, validate='one_to_one')
        if joined._merge.ne('both').any():
            raise ValueError('A reappearance must match an earlier departure episode')
    mature_vins = len(mature[['retailer', 'vin']].drop_duplicates())
    returned_vins = len(returned[['retailer', 'vin']].drop_duplicates()) if not returned.empty else 0
    return pd.DataFrame([dict(
        earlier_departure_episodes=len(mature), later_reappeared_episodes=len(returned),
        earlier_departed_vins=mature_vins, later_reappeared_vins=returned_vins,
        latest_departures_right_censored=int(departures.first_absent_snapshot.eq(latest_snapshot).sum()),
        observed_return_fraction=returned_vins / mature_vins if mature_vins else pd.NA,
        episode_return_fraction=len(returned) / len(mature) if len(mature) else pd.NA,
        interpretation='Separate episode and unique retailer/VIN fractions; NOT transaction error rates')])


def _rows_by_vin(rows):
    if rows is None or rows.empty:
        return {}
    return {key: part for key, part in rows.groupby(['retailer', 'vin'], sort=False)}


def inventory_exit_episodes(cycles, observations, *, as_of, events=None, unassessable_cells=None):
    """Retain each complete-sweep exit and any later observed reappearance.

    These are one-day absence candidates from the existing reader, not sales.
    Incomplete/missing dates preserve an open episode without proving absence.
    """
    episodes, _ = sale_candidates(cycles, observations, as_of=as_of, absence_days=1,
                                  events=events, unassessable_cells=unassessable_cells)
    episodes = episodes.rename(columns={'candidate_id': 'exit_episode_id'})
    known = cycles.loc[cycles.available_at.map(_aware).le(_aware(as_of))]
    complete_rows = observations.loc[observations.cycle_id.isin(known.loc[known.coverage_complete, 'cycle_id'])]
    first_complete = complete_rows.assign(observed_at_utc=complete_rows.observed_at_utc.map(_aware)).groupby(
        ['retailer', 'vin']).observed_at_utc.min()
    baseline_times = [first_complete.get((row.retailer, row.vin)) for row in episodes.itertuples()]
    comparable = [pd.notna(baseline) and _aware(baseline) <= _aware(row.last_observed_at)
                  for baseline, row in zip(baseline_times, episodes.itertuples())]
    episodes = episodes.loc[comparable].copy()
    by_date = known.set_index('cycle_date').window_end
    episodes['first_disappearance_at'] = episodes.first_absent_date.map(by_date)
    episodes['reappeared_at'] = pd.Series(pd.NaT, index=episodes.index, dtype='datetime64[ns, UTC]')
    episodes['reappeared_listing_id'] = None
    rows = observations.merge(known[['cycle_id', 'cycle_date']], on='cycle_id', validate='many_to_one')
    by_vin = _rows_by_vin(rows)
    for index, episode in episodes.loc[episodes.reappeared_date.notna()].iterrows():
        own = by_vin.get((episode.retailer, episode.vin))
        returned = own.loc[own.cycle_date.eq(episode.reappeared_date)].iloc[0]
        episodes.loc[index, 'reappeared_at'] = _aware(returned.observed_at_utc)
        episodes.loc[index, 'reappeared_listing_id'] = returned.listing_id
    return episodes


def _followup_progress(target_checks, attempts, *, trigger, last_selected, cutoff, cohort_member):
    """Remind at 24h; a matched repeat >=7d after baseline completes coverage.

    A late first repeat can complete this minimum without a separate 24h visit;
    that missed intermediate observation remains unobserved.
    """
    checks = target_checks.loc[target_checks.checked_at.ge(trigger)] if not target_checks.empty else target_checks
    baseline = checks.checked_at.min() if not checks.empty else pd.NaT
    last_attempt = attempts.checked_at.max() if not attempts.empty else pd.NaT
    if cohort_member:
        complete, due, reason = False, cutoff, 'Frozen cohort continues on its 24-hour reminder'
    elif pd.isna(baseline):
        complete, due, reason = False, cutoff, 'Needs a matched native baseline on this target'
    else:
        day_one, day_seven = baseline + pd.Timedelta(days=1), baseline + pd.Timedelta(days=7)
        repeated = checks.checked_at.ge(day_one).any()
        complete = bool(checks.checked_at.ge(day_seven).any())
        plan_checked = pd.isna(last_selected) or checks.checked_at.ge(last_selected).any()
        complete = complete and plan_checked
        due = day_seven if repeated else day_one
        if not plan_checked:
            due = max(due, last_selected)
        reason = 'Seven-day native follow-up observed' if complete else (
            'Needs the planned matched check' if not plan_checked else
            'Needs seven-day native follow-up' if repeated else 'Needs 24-hour native follow-up')
    if pd.notna(last_attempt):
        due = max(due, last_attempt + pd.Timedelta(hours=24))
    return dict(followup_outstanding=not complete, completion_reason=reason,
                baseline_checked_at=baseline, next_check_at=pd.NaT if complete else due,
                check_due=bool(not complete and due <= cutoff))


def _queue_identities(membership, visits, rows, *, as_of):
    """Reuse verified identities; keep unresolved targets visible but unvisitable."""
    conflicting = membership.groupby(['retailer', 'listing_id']).vin.transform('nunique').gt(1)
    url_ids = membership.url.map(lambda url: _url_id(url) if isinstance(url, str) else None)
    valid_reference = url_ids.eq(membership.listing_id) & ~conflicting
    resolved, deferred = [], []
    for index in membership.index[~valid_reference]:
        deferred.append((index, 'Unresolved original URL or conflicting planned listing identity'))
    safe_members = membership.loc[valid_reference]
    if not safe_members.empty:
        try:
            resolved.append(followup_identities(safe_members, visits, rows, as_of=as_of))
        except ValueError:
            # Isolate an ambiguous VIN without hiding it or blocking other targets.
            for index in safe_members.index:
                member = safe_members.loc[index]
                linked_ids = {member.listing_id}
                for evidence in [visits, rows]:
                    if not evidence.empty:
                        own = evidence.retailer.eq(member.retailer) & evidence.vin.eq(member.vin)
                        linked_ids.update(evidence.loc[own, 'listing_id'])
                related = []
                for evidence in [visits, rows]:
                    if evidence.empty:
                        related.append(evidence)
                    else:
                        # Include other VINs sharing this VIN's listing IDs so
                        # the actual collision remains visible to validation.
                        linked = evidence.retailer.eq(member.retailer) & evidence.listing_id.isin(linked_ids)
                        related.append(evidence.loc[linked])
                try:
                    resolved.append(followup_identities(safe_members.loc[[index]],
                        related[0], related[1], as_of=as_of))
                except ValueError as error:
                    deferred.append((index, str(error)))
    for index, reason in deferred:
        original = membership.loc[index]
        resolved.append(pd.DataFrame([dict(retailer=original.retailer, vin=original.vin,
            original_listing_id=original.listing_id, original_url=original.url,
            followup_listing_id=None, followup_url=None, followup_reason=reason, deferred_reason=reason)]))
    identities = pd.concat(resolved, ignore_index=True)
    if 'deferred_reason' not in identities:
        identities['deferred_reason'] = None
    return identities.sort_values(['retailer', 'vin']).reset_index(drop=True)


def inventory_followup_queue(cycles, observations, records, cohorts, *, as_of,
                             selection_plans=None, batch_limit=12, control_count=2,
                             seed='carvana-followup-v1', browser_health=None,
                             events=None, unassessable_cells=None):
    """Offline, one-VIN preview; preserve exits, selected studies and their clocks.

    Priority conflicts precede due persistence, random unselected exits, other
    event changes and random controls. Persistence and new exits share available
    candidate slots when both exist. Only actual random frames receive k/N.
    A seven-day matched check completes study coverage, never an economic sale.
    """
    if (type(batch_limit) is not int or not 1 <= batch_limit <= 12
            or type(control_count) is not int or not 0 <= control_count < batch_limit):
        raise ValueError('Use a batch of 1 to 12 and fewer control than total slots')
    cutoff = _aware(as_of)
    visits = native_visits(records, as_of=as_of)
    valid = visits.loc[visits.parse_outcome.eq('matched') & visits.saleStatus.isin(['Available', 'Sold'])] if not visits.empty else visits
    known = cycles.loc[cycles.available_at.map(_aware).le(cutoff)].sort_values('cycle_date')
    rows = observations.loc[observations.cycle_id.isin(known.cycle_id)].copy()
    rows = rows.merge(known[['cycle_id', 'available_at']], on='cycle_id', validate='many_to_one')
    inventory = rows.drop(columns='available_at')
    if events is None:
        events = vin_events(known, inventory, absence_days=1, unassessable_cells=unassessable_cells
                            ) if not known.empty else pd.DataFrame()
    complete = known.loc[known.coverage_complete.eq(True)]
    complete_rows = rows.loc[rows.cycle_id.isin(complete.cycle_id)]
    episodes = inventory_exit_episodes(known, inventory, as_of=as_of, events=events,
                                       unassessable_cells=unassessable_cells)
    latest_exits = episodes.sort_values('detected_date').drop_duplicates(['retailer', 'vin'], keep='last')
    exit_by_vin = {(r.retailer, r.vin): r for r in latest_exits.itertuples()}
    latest = {(r.retailer, r.vin): r for r in complete_rows.loc[
        complete_rows.cycle_id.eq(complete.iloc[-1].cycle_id)].itertuples()} if not complete.empty else {}
    latest_presence = {(r.retailer, r.vin): r for r in rows.sort_values('observed_at_utc').drop_duplicates(
        ['retailer', 'vin'], keep='last').itertuples()}

    plans = pd.DataFrame() if selection_plans is None else selection_plans.copy()
    if not plans.empty:
        for field in ['selected_at', 'available_at']:
            plans[field] = plans[field].map(_aware)
        if plans.available_at.lt(plans.selected_at).any():
            raise ValueError('Selection-plan availability precedes selection')
        plans = plans.loc[plans.selected_at.le(cutoff) & plans.available_at.le(cutoff)]
        plans = plans.sort_values(['selected_at', 'available_at', 'retailer', 'vin', 'listing_id'], kind='stable')
    plans_by_vin = {key: part for key, part in plans.groupby(['retailer', 'vin'])} if not plans.empty else {}
    members = {}
    for cohort in known_disjoint_cohorts(cohorts, as_of=as_of):
        for vehicle in cohort['vehicles']:
            members[(vehicle['retailer'], vehicle['vin'])] = dict(vehicle,
                cohort_id=cohort['cohort_id'], selected_at=cohort['selected_at'])

    event_keys = set()
    if not events.empty:
        changed = events.loc[events.observed_in_cycle
            & (events.reappeared_after_absence.eq(True) | events.event_type.eq('relisted'))]
        event_keys.update(zip(changed.retailer, changed.vin))
        if not complete.empty:
            pending = events.loc[events.cycle_id.eq(complete.iloc[-1].cycle_id)
                                 & events.pending_changed.eq(True)]
            event_keys.update(zip(pending.retailer, pending.vin))
    if not valid.empty:
        sold = valid.loc[valid.saleStatus.eq('Sold')]
        event_keys.update(zip(sold.retailer, sold.vin))
    real_keys = set(members) | set(exit_by_vin) | set(plans_by_vin) | event_keys
    control_pool = []
    for key, row in latest.items():
        if key in real_keys:
            continue
        url, listing = getattr(row, 'listing_url', None), getattr(row, 'listing_id', None)
        if isinstance(url, str) and listing is not None and _url_id(url) == listing:
            rank = hashlib.sha256(json.dumps([str(seed), 'control', key[0], key[1]]).encode()).hexdigest()
            control_pool.append((rank, key))
    control_frame_size = len(control_pool)
    if control_frame_size <= 256:
        control_keys = {key for _, key in control_pool}
    else:
        control_keys = {key for _, key in sorted(control_pool)[:max(control_count * 20, 32)]}
    # Candidates plus a seeded control sample. The latest day is not iterated in full.
    keys = real_keys | control_keys
    first_by_vin = {} if rows.empty else {
        (r.retailer, r.vin): r for r in rows.sort_values('observed_at_utc').drop_duplicates(
            ['retailer', 'vin']).itertuples()}
    originals = []
    for key in sorted(keys):
        if key in members:
            original = members[key]
        elif key in plans_by_vin:
            original = plans_by_vin[key].iloc[0].to_dict()
        else:
            first = first_by_vin[key]
            original = dict(retailer=key[0], vin=key[1], listing_id=first.listing_id, url=first.listing_url)
        originals.append({name: original.get(name) for name in ['retailer', 'vin', 'listing_id', 'url', 'role', 'cohort_id']})
    if not originals:
        return pd.DataFrame(columns=['retailer', 'vin', 'selected_for_check', 'selection_reason', 'selection_group'])
    membership = pd.DataFrame(originals)
    identities = _queue_identities(membership, visits, rows, as_of=as_of)
    identities['conflicting_page_listing_id'] = None
    visits_by_vin = _rows_by_vin(visits)
    valid_by_vin = _rows_by_vin(valid)
    events_by_vin = _rows_by_vin(events)
    rows_by_vin = _rows_by_vin(rows)
    empty_visits = visits.iloc[0:0]
    empty_events = events.iloc[0:0] if not events.empty else pd.DataFrame()
    latest_cycle = complete.iloc[-1].cycle_id if not complete.empty else None
    output_rows = []
    for identity in identities.to_dict('records'):
        key = (identity['retailer'], identity['vin'])
        history = visits_by_vin.get(key, empty_visits)
        native = valid_by_vin.get(key, empty_visits)
        own_rows = rows_by_vin.get(key, rows.iloc[0:0])
        own_events = events_by_vin.get(key, empty_events)
        episode, selected = exit_by_vin.get(key), plans_by_vin.get(key, pd.DataFrame())
        unresolved = pd.notna(identity.get('deferred_reason'))
        reasons, conflict = ([identity['deferred_reason']], True) if unresolved else ([], False)
        presence = latest_presence.get(key)
        if not unresolved and presence is not None and presence.listing_id != identity['followup_listing_id']:
            target_id = identity['followup_listing_id']
            earlier = own_rows.loc[own_rows.listing_id.eq(target_id)]
            target_times = list(earlier.observed_at_utc.map(_aware))
            if not native.empty:
                target_times.extend(native.loc[native.listing_id.eq(target_id), 'checked_at'])
            if not selected.empty:
                target_times.extend(selected.loc[selected.listing_id.eq(target_id), 'selected_at'])
            if key in members and members[key]['listing_id'] == target_id:
                target_times.append(_aware(members[key]['selected_at']))
            if target_times and min(target_times) < _aware(presence.observed_at_utc):
                identity['conflicting_page_listing_id'] = target_id
                identity.update(followup_listing_id=presence.listing_id, followup_url=presence.listing_url,
                    followup_observed_at=_aware(presence.observed_at_utc), followup_available_at=_aware(presence.available_at),
                    followup_source=presence.source_path, followup_reason='Latest inventory identity; obsolete page retained separately')
                reasons.append('page listing conflicts with latest inventory')
                conflict = True
        first_selected = selected.selected_at.min() if not selected.empty else pd.NaT
        last_selected = selected.selected_at.max() if not selected.empty else pd.NaT
        trigger = _aware(episode.first_disappearance_at) if episode is not None else first_selected
        if pd.isna(trigger):
            trigger = native.checked_at.min() if not native.empty else cutoff
        changes = own_events.loc[own_events.observed_in_cycle
            & (own_events.reappeared_after_absence | own_events.event_type.eq('relisted'))] if not own_events.empty else own_events
        if not changes.empty:
            changed = changes.sort_values('observed_at_utc').iloc[-1]
            trigger = max(trigger, _aware(changed.observed_at_utc))
            reasons.append('inventory reappearance or listing ID changed')
        if episode is not None:
            reasons.append('outstanding inventory exit episode' if pd.isna(episode.reappeared_date) else 'exit episode reappeared')
        if not native.empty:
            latest_native = native.iloc[-1]
            simultaneous = native.loc[native.checked_at.eq(latest_native.checked_at)].saleStatus.nunique() > 1
            contrary = (latest_native.saleStatus == 'Sold' and presence is not None
                        and _aware(presence.observed_at_utc) > latest_native.checked_at)
            conflict = conflict or simultaneous or contrary
            if simultaneous or contrary:
                reasons.append('conflicting native status or later inventory presence')
                contradiction_at = latest_native.checked_at
                if contrary:
                    later_presence = own_rows.loc[own_rows.observed_at_utc.map(_aware).gt(latest_native.checked_at)]
                    contradiction_at = later_presence.observed_at_utc.map(_aware).min()
                trigger = max(trigger, contradiction_at)
            if native.saleStatus.eq('Sold').any():
                reasons.append('known native Sold; research follow-up')
            returned_native = native.loc[native.saleStatus.eq('Available') & native.saleStatus.shift().eq('Sold')]
            if not returned_native.empty:
                trigger = max(trigger, returned_native.checked_at.max())
                reasons.append('native Available after Sold; fresh follow-up episode')
        recent_events = own_events.loc[own_events.cycle_id.eq(latest_cycle)] if (
            not own_events.empty and latest_cycle is not None) else pd.DataFrame()
        pending_changed = not recent_events.empty and recent_events.pending_changed.eq(True).any()
        if pending_changed:
            reasons.append('pending started or pending cleared')
        target = native.loc[native.listing_id.eq(identity['followup_listing_id'])] if not native.empty else native
        if not target.empty and presence is not None and presence.listing_id != identity['followup_listing_id']:
            trigger = max(trigger, target.checked_at.min())
            reasons.append('new native listing after older inventory')
        progress = _followup_progress(target, history, trigger=trigger, last_selected=last_selected,
            cutoff=cutoff, cohort_member=key in members)
        studied = not selected.empty or (not target.empty and target.checked_at.ge(trigger).any())
        candidate = bool(episode is not None or reasons)
        group = ('priority_conflict' if conflict else 'persistence' if key in members or studied else
            'new_exit' if episode is not None and pd.isna(episode.reappeared_date) else
            'event_change' if candidate else 'control')
        if group == 'control':
            progress['followup_outstanding'] = False
            progress['completion_reason'] = 'Unselected noncandidate; no study follow-up started'
        source_refs = sorted(set(history.source.dropna())) if not history.empty else []
        if not selected.empty:
            source_refs.extend(selected.get('plan_source', pd.Series(dtype='object')).dropna().astype(str))
        last_attempt = history.iloc[-1] if not history.empty else None
        last_native = target.iloc[-1] if not target.empty else None
        cohort_metadata = {name: value for name, value in members.get(key, {}).items() if name not in {
            'retailer', 'vin', 'listing_id', 'url', 'role', 'cohort_id', 'selected_at', 'selection_reason'}}
        output_rows.append(dict(cohort_metadata, **identity, **progress,
            role=members[key]['role'] if key in members else 'validation_only',
            cohort_id=members[key]['cohort_id'] if key in members else None,
            cohort_selected_at=members[key]['selected_at'] if key in members else None,
            cohort_selection_reason=members[key]['selection_reason'] if key in members else None,
            frozen_cohort_member=key in members, previously_selected=not selected.empty, candidate_event=candidate,
            exit_episode_id=episode.exit_episode_id if episode is not None else None,
            episode_listing_id=episode.listing_id if episode is not None else None,
            first_disappearance_at=episode.first_disappearance_at if episode is not None else None,
            reappeared_at=episode.reappeared_at if episode is not None else pd.NaT,
            followup_trigger_at=trigger, first_selected_at=first_selected, last_selected_at=last_selected,
            followup_episode_id=hashlib.sha256(json.dumps([*key, identity['followup_listing_id'], trigger.isoformat()]).encode()).hexdigest()[:20],
            last_attempt_at=last_attempt.checked_at if last_attempt is not None else pd.NaT,
            last_attempt_listing_id=last_attempt.listing_id if last_attempt is not None else None,
            last_usable_native_at=last_native.checked_at if last_native is not None else pd.NaT,
            hours_since_usable_native=(cutoff-last_native.checked_at).total_seconds()/3600 if last_native is not None else float('nan'),
            latest_native_status=last_native.saleStatus if last_native is not None else None,
            latest_parse_outcome=last_attempt.parse_outcome if last_attempt is not None else None,
            latest_observed_status=last_attempt.observed_status if last_attempt is not None else None,
            inventory_present_in_latest_complete=key in latest, selection_group=group,
            selection_reason='; '.join(reasons) or ('cohort continuity' if key in members else 'noncandidate inventory control'),
            source_references=json.dumps(sorted(set(source_refs)))))
    output = pd.DataFrame(output_rows)
    url_ids = output.followup_url.map(lambda url: _url_id(url) if isinstance(url, str) else None)
    valid_url = url_ids.eq(output.followup_listing_id) & output.followup_listing_id.notna()
    output['eligible_for_selection'] = (output.check_due & valid_url & output.deferred_reason.isna()
        & (output.followup_outstanding | output.selection_group.eq('control')))
    if browser_health is not None and not browser_health.empty:
        # Reservations are not native observations. Keep them separate and block
        # reselection until the original visit has been recovered or closed.
        unresolved = browser_health.loc[browser_health.outcome.eq('started_unresolved')
            & pd.to_datetime(browser_health.started_at, utc=True).le(cutoff)]
        keys = set(zip(unresolved.retailer, unresolved.vin))
        blocked = pd.Series([(r.retailer, r.vin) in keys for r in output.itertuples()], index=output.index)
        output.loc[blocked, 'eligible_for_selection'] = False
        output.loc[blocked, 'deferred_reason'] = 'Unresolved browser visit; recover or explicitly fail it first'
    group_order = ['priority_conflict', 'persistence', 'new_exit', 'event_change', 'control']
    output['priority'] = output.selection_group.map({group: rank for rank, group in enumerate(group_order)})
    output['random_rank'] = [hashlib.sha256(json.dumps([str(seed), row.selection_group, row.retailer, row.vin]).encode()).hexdigest()
                             for row in output.itertuples()]
    output = output.sort_values(['priority', 'next_check_at', 'retailer', 'vin'], na_position='last').reset_index(drop=True)
    frames = {group: output.loc[output.selection_group.eq(group) & output.eligible_for_selection]
              for group in group_order}
    picked = list(frames['priority_conflict'].head(batch_limit).index)
    remaining = batch_limit - len(picked)
    controls_reserved = min(control_count, len(frames['control']), remaining)
    candidate_slots = remaining - controls_reserved
    persistence_slots = min(len(frames['persistence']), (candidate_slots + 1)//2 if len(frames['new_exit']) else candidate_slots)
    picked.extend(frames['persistence'].head(persistence_slots).index)
    exit_slots = min(len(frames['new_exit']), candidate_slots - persistence_slots)
    picked.extend(frames['new_exit'].sort_values('random_rank').head(exit_slots).index)
    extra_slots = candidate_slots - persistence_slots - exit_slots
    extra = pd.concat([frames['persistence'].iloc[persistence_slots:], frames['event_change']])
    picked.extend(extra.head(extra_slots).index)
    picked.extend(frames['control'].sort_values('random_rank').head(controls_reserved).index)
    output['selected_for_check'] = output.index.isin(picked)
    output['random_control'] = output.selected_for_check & output.selection_group.eq('control')
    output['frame_size'] = output.selection_group.map({group: len(frame) for group, frame in frames.items()})
    output.loc[output.selection_group.eq('control'), 'frame_size'] = control_frame_size
    selected_counts = output.loc[output.selected_for_check].groupby('selection_group').size()
    output['frame_selected'] = output.selection_group.map(selected_counts).fillna(0).astype(int)
    random_frame = output.selection_group.isin(['new_exit', 'control']) & output.eligible_for_selection
    output['selection_probability'] = (output.frame_selected / output.frame_size.where(output.frame_size.gt(0))).where(random_frame)
    output['selection_seed'] = str(seed)
    output['eligibility_rule'] = 'Known evidence; 24h since any VIN visit; study outstanding or unselected control'
    output['queue_rank'] = range(1, len(output)+1)
    output['control_frame_vins'] = control_frame_size
    output['candidate_frame_vins'] = int(output.candidate_event.sum())
    output['latest_complete_cycle'] = complete.iloc[-1].cycle_id if not complete.empty else None
    output['latest_complete_date'] = complete.iloc[-1].cycle_date if not complete.empty else None
    output['latest_calendar_date_at_cutoff'] = str(cutoff.tz_convert(known.iloc[-1].timezone).date()) if not known.empty else None
    output['comparison_start'] = complete.iloc[-2].window_start if len(complete)>=2 else None
    output['comparison_end'] = complete.iloc[-1].window_end if len(complete)>=2 else None
    output['intervening_calendar_dates'] = (pd.Timestamp(complete.iloc[-1].cycle_date)-pd.Timestamp(complete.iloc[-2].cycle_date)).days-1 if len(complete)>=2 else None
    output['selection_as_of'] = cutoff.isoformat()
    output['sample_interpretation'] = 'Validation frames in this inventory population; never company sales scaling'
    return output

def native_visits(records, *, as_of):
    """Select the latest known version per physical visit, including failed checks."""
    cutoff = _aware(as_of)
    rows = records.copy()
    if rows.empty:
        return rows
    for field in ['checked_at', 'available_at']:
        rows[field] = rows[field].map(_aware)
    if rows.available_at.lt(rows.checked_at).any():
        raise ValueError('Native evidence availability precedes observation')
    rows = rows.loc[rows.checked_at.le(cutoff) & rows.available_at.le(cutoff)]
    if rows.groupby(['retailer', 'listing_id']).vin.nunique().gt(1).any():
        raise ValueError('One native listing is bound to conflicting VIN identities')
    key = ['retailer', 'vin', 'listing_id', 'checked_at']
    meaning = [*key, 'available_at', 'saleStatus', 'purchaseType', 'parse_outcome', 'observed_status']
    if rows[meaning].drop_duplicates().duplicated([*key, 'available_at']).any():
        raise ValueError('Conflicting versions of one native visit')
    return rows.sort_values(['checked_at', 'available_at', 'source'], kind='stable').drop_duplicates(key, keep='last')


def native_transition_events(records, cohorts, *, as_of, cycles=None, observations=None,
                             persistence_hours=24):
    """One first Available -> exact native Sold event per prospective VIN.

    Initially Sold vehicles and historical controls never enter the event table.
    A later Available native status or inventory presence is contrary evidence,
    not a proved cancellation/return. It suppresses the current proxy but leaves
    the observed event intact. Replay an earlier as_of to see the earlier vintage.
    Conservative eligibility requires a later Sold check at least 24 hours away;
    this is an assumption-based filter, not a transaction lower bound.
    """
    if isinstance(persistence_hours, bool) or not 0 < persistence_hours < float('inf'):
        raise ValueError('persistence_hours must be finite and positive')
    visits = native_visits(records, as_of=as_of)
    summaries = [summarize_pilot(visits, c, as_of=as_of)
                 for c in known_disjoint_cohorts(cohorts, as_of=as_of)]
    summary = pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame()
    if summary.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS), summary
    visible_inventory = pd.DataFrame()
    if cycles is not None and observations is not None and not cycles.empty:
        known = cycles.loc[cycles.available_at.map(_aware).le(_aware(as_of))]
        visible_inventory = observations.loc[observations.cycle_id.isin(known.cycle_id)].copy()
        visible_inventory['observed_at_utc'] = visible_inventory.observed_at_utc.map(_aware)
        visible_inventory = visible_inventory.loc[visible_inventory.observed_at_utc.le(_aware(as_of))]
        visible_inventory = visible_inventory.merge(known[['cycle_id', 'available_at']], on='cycle_id', validate='many_to_one')
    events = []
    for item in summary.loc[summary.newly_observed_sold].itertuples():
        history = visits.loc[visits.retailer.eq(item.retailer) & visits.vin.eq(item.vin)]
        valid = history.loc[history.parse_outcome.eq('matched') & history.saleStatus.isin(['Available', 'Sold'])]
        before = valid.loc[valid.checked_at.eq(item.last_non_sold_at) & valid.saleStatus.eq('Available')].iloc[-1]
        first = valid.loc[valid.checked_at.eq(item.first_sold_at) & valid.saleStatus.eq('Sold')].iloc[0]
        after = valid.loc[valid.checked_at.ge(first.checked_at)]
        repeats = after.loc[after.saleStatus.eq('Sold') & after.checked_at.gt(first.checked_at)]
        contrary = after.loc[after.saleStatus.eq('Available')]
        # A simultaneous old-Sold/new-Available listing is not an unambiguous transition.
        simultaneous = valid.groupby('checked_at').saleStatus.nunique().gt(1).any()
        returned = visible_inventory
        if not returned.empty:
            returned = returned.loc[returned.retailer.eq(item.retailer) & returned.vin.eq(item.vin)
                                    & returned.observed_at_utc.gt(first.checked_at)]
        eligible = contrary.empty and returned.empty and not simultaneous
        span = 0.0 if repeats.empty else (repeats.checked_at.max() - first.checked_at).total_seconds() / 3600
        references = sorted(set(history.source.dropna()) | (set(returned.source_path.dropna()) if not returned.empty else set()))
        known_times = [before.available_at, first.available_at, history.available_at.max()]
        if not returned.empty:
            known_times.extend(returned.available_at.map(_aware))
        event_id = hashlib.sha256(json.dumps([item.retailer, item.vin, first.checked_at.isoformat()]).encode()).hexdigest()[:20]
        events.append(dict(event_id=event_id, retailer=item.retailer, vin=item.vin,
            listing_id=first.listing_id, interval_start=before.checked_at, interval_end=first.checked_at,
            event_available_at=max(before.available_at, first.available_at), evidence_available_at=max(known_times),
            source_references=json.dumps(references), repeat_sold_checks=len(repeats), persistence_hours=span,
            contrary_native_at=contrary.checked_at.min() if not contrary.empty else pd.NaT,
            inventory_reappearance_at=returned.observed_at_utc.min() if not returned.empty else pd.NaT,
            central_eligible=bool(eligible), conservative_eligible=bool(eligible and span >= persistence_hours)))
    return pd.DataFrame(events, columns=EVENT_COLUMNS), summary


def native_estimate_revisions(records, cohorts, *, as_of, cycles, observations):
    """Replay first-publication native vintages, including subsequently removed events.

    Each event's first known contribution is compared with the current eligible
    contribution. A correction can remove an event entirely; its earlier estimate
    remains here with a current zero. These are gross website-event assumptions,
    not revisions to independently verified retail transactions.
    """
    columns = ['event_id', 'retailer', 'vin', 'listing_id', 'provisional_available_at',
        'provisional_event_units', 'current_event_units', 'revised_as_of', 'unit_revision',
        'reason', 'contrary_native_at', 'inventory_reappearance_at', 'repeat_sold_checks', 'repeat_hours']
    cutoff = _aware(as_of)
    if records.empty:
        return pd.DataFrame(columns=columns)
    available = records.available_at.map(_aware)
    vintages = sorted(set(available.loc[available.le(cutoff)]))
    originals = {}
    for vintage in vintages:
        events, _ = native_transition_events(records, cohorts, as_of=vintage,
            cycles=cycles, observations=observations)
        for event in events.to_dict('records'):
            originals.setdefault(event['event_id'], event)
    current, _ = native_transition_events(records, cohorts, as_of=as_of,
        cycles=cycles, observations=observations)
    current_by_id = {event['event_id']: event for event in current.to_dict('records')}
    output = []
    for event_id, original in originals.items():
        now = current_by_id.get(event_id)
        initial_units = float(original['central_eligible'])
        current_units = float(now['central_eligible']) if now is not None else 0.0
        reason = ('Originally detected event removed by later source correction' if now is None else
            'Current native/inventory evidence suppresses this event' if not current_units else
            'First native Sold event remains eligible')
        output.append(dict(event_id=event_id, retailer=original['retailer'], vin=original['vin'],
            listing_id=original['listing_id'], provisional_available_at=original['event_available_at'],
            provisional_event_units=initial_units, current_event_units=current_units,
            revised_as_of=cutoff.isoformat(), unit_revision=current_units - initial_units, reason=reason,
            contrary_native_at=now['contrary_native_at'] if now is not None else pd.NaT,
            inventory_reappearance_at=now['inventory_reappearance_at'] if now is not None else pd.NaT,
            repeat_sold_checks=now['repeat_sold_checks'] if now is not None else 0,
            repeat_hours=now['persistence_hours'] if now is not None else 0.0))
    return pd.DataFrame(output, columns=columns)


def inventory_flows(cycles, observations, *, as_of):
    """Reconcile adjacent COMPLETE comparable sweeps, retaining irregular intervals.

    B + additions - departures = E. Departures are a naive sales-proxy baseline,
    not explained sales. VIN identity absorbs a listing-ID change. Gap counts and
    actual sweep bounds prevent interpretation as a measured 24-hour flow.
    """
    cutoff = _aware(as_of)
    days = cycles.loc[cycles.available_at.map(_aware).le(cutoff)].sort_values('cycle_date')
    rows = observations.loc[observations.cycle_id.isin(days.cycle_id)].copy()
    rows = rows.loc[rows.observed_at_utc.map(_aware).le(cutoff)]
    # Existing validation rejects scope changes, conflicting identities and invalid clocks.
    if not days.empty:
        vin_events(days, rows)
    complete = days.loc[days.coverage_complete.eq(True)]
    result = []
    for previous, current in zip(complete.to_dict('records'), complete.to_dict('records')[1:]):
        before = rows.loc[rows.cycle_id.eq(previous['cycle_id'])].set_index(['retailer', 'vin'])
        after = rows.loc[rows.cycle_id.eq(current['cycle_id'])].set_index(['retailer', 'vin'])
        if before.index.has_duplicates or after.index.has_duplicates:
            raise ValueError('Duplicate VIN within a comparable inventory sweep')
        common = before.index.intersection(after.index)
        additions, departures = after.index.difference(before.index), before.index.difference(after.index)
        matched_change = after.loc[common, 'asking_price_usd'] - before.loc[common, 'asking_price_usd']
        pending_before, pending_after = before.loc[common, 'purchase_pending'], after.loc[common, 'purchase_pending']
        gap = (pd.Timestamp(current['cycle_date']) - pd.Timestamp(previous['cycle_date'])).days - 1
        result.append(dict(interval_start=previous['window_start'], interval_end=current['window_end'],
            beginning_sweep_end=previous['window_end'], ending_sweep_start=current['window_start'],
            population=previous['scope_id'], beginning_vins=len(before), additions=len(additions),
            departures=len(departures), ending_vins=len(after), matched_vins=len(common),
            flow_residual=len(after) - len(before) - len(additions) + len(departures),
            hours_between_sweep_starts=(_aware(current['window_start']) - _aware(previous['window_start'])).total_seconds() / 3600,
            intervening_calendar_dates=gap, complete_consecutive_dates=gap == 0,
            listing_id_changes=int(before.loc[common, 'listing_id'].ne(after.loc[common, 'listing_id']).sum()),
            pending_started=int((pending_before.eq(0) & pending_after.eq(1)).sum()),
            pending_cleared=int((pending_before.eq(1) & pending_after.eq(0)).sum()),
            matched_price_pairs=int(matched_change.notna().sum()),
            matched_mean_price_change_usd=matched_change.mean(),
            repriced_vins=int(matched_change.dropna().ne(0).sum()),
            beginning_mean_ask_usd=before.asking_price_usd.mean(), ending_mean_ask_usd=after.asking_price_usd.mean(),
            available_at=max(_aware(previous['available_at']), _aware(current['available_at'])),
            source_references=json.dumps(sorted(set(before.source_path) | set(after.source_path)))))
    return pd.DataFrame(result)


def disappearance_events(cycles, observations, *, as_of, absence_days=3,
                         events=None, unassessable_cells=None):
    """Reuse the existing consecutive-complete-calendar-date rule without weakening it.

    The disappearance interval ends at the first complete absent sweep. Evidence
    only becomes available at the later qualifying sweep. Gaps reset the streak.
    Returned and currently gapped episodes remain visible but are not eligible.
    """
    candidates, calendar = sale_candidates(cycles, observations, as_of=as_of, absence_days=absence_days,
                                          events=events, unassessable_cells=unassessable_cells)
    result = candidates.copy()
    result['interval_start'] = result.last_observed_at
    first_absent = cycles.set_index('cycle_date').window_end.to_dict()
    result['interval_end'] = result.first_absent_date.map(first_absent)
    result['event_available_at'] = result.detected_available_at
    result['evidence_available_at'] = result.latest_evidence_available_at
    # The legacy candidate state intentionally propagates timing uncertainty.
    # Eligibility instead needs the CURRENT complete absence streak. An old gap
    # widens timing; it must not cancel a later independently established streak.
    result['current_absence_streak'] = 0
    result['eligible'] = False
    if not result.empty:
        known = cycles.loc[cycles.available_at.map(_aware).le(_aware(as_of))]
        selected = observations.loc[observations.cycle_id.isin(known.cycle_id)]
        if events is None:
            events = vin_events(known, selected, absence_days=absence_days,
                                unassessable_cells=unassessable_cells)
        latest = events.sort_values('cycle_date').drop_duplicates(['retailer', 'vin'], keep='last').set_index(['retailer', 'vin'])
        for index, candidate in result.iterrows():
            event = latest.loc[(candidate.retailer, candidate.vin)]
            result.loc[index, 'current_absence_streak'] = event.absence_streak
            result.loc[index, 'eligible'] = bool(
                candidate.followup_state != 'reappeared' and not event.observed_in_cycle
                and event.last_observed_cycle_id == candidate.last_observed_cycle_id
                and event.coverage_complete and event.absence_streak >= absence_days
                and event.cycle_date == str(_aware(as_of).tz_convert(event.timezone).date()))
    return result, calendar


def sampled_exit_estimate(absences, queue, records, *, as_of):
    """Sold exits among detected catalog exits, with a weighted Wilson range.

    Population: three-day exit episodes by detection date. Sample: exits the
    queue randomly selected from new_exit that have a matched listing check
    after the car left the catalog. Inverse-probability weights reuse the
    recorded selection probability. This is not reported transactions; it
    misses cars that list and sell between sweeps and lags three days.
    """
    cutoff = _aware(as_of)
    interpretation = ('Sold exits among detected catalog exits; not reported '
                      'transactions. Misses cars that list and sell between sweeps '
                      'and lags three days.')
    columns = ['detection_date', 'exits', 'checked', 'sold', 'available', 'unresolved',
               'sold_share', 'sold_share_wilson95_low', 'sold_share_wilson95_high',
               'sold_share_full_sample_low', 'sold_share_full_sample_high',
               'estimated_sold_exits', 'estimated_sold_exits_low', 'estimated_sold_exits_high',
               'interpretation', 'as_of']
    empty = pd.DataFrame(columns=columns)
    if absences is None or absences.empty:
        return empty
    visits = native_visits(records, as_of=as_of)
    matched = visits.loc[visits.parse_outcome.eq('matched')
                         & visits.saleStatus.isin(['Available', 'Sold'])] if not visits.empty else visits
    selected = pd.DataFrame() if queue is None or queue.empty else queue
    if not selected.empty:
        selected = selected.loc[selected.selection_group.eq('new_exit') & selected.selected_for_check
                                & selected.selection_probability.notna()]
    selected_by_vin = {}
    for row in selected.itertuples() if not selected.empty else []:
        key = (row.retailer, row.vin)
        selected_by_vin.setdefault(key, row)
    visits_by_vin = _rows_by_vin(matched)

    def left_at(episode):
        for name in ['interval_end', 'first_disappearance_at']:
            value = episode.get(name)
            if value is not None and not (isinstance(value, float) and pd.isna(value)) and pd.notna(value):
                return _aware(value)
        first_absent = episode.get('first_absent_date')
        return None if first_absent is None or pd.isna(first_absent) else None

    def summarize(rows, date):
        exits = len(rows)
        checked = sold = available = unresolved = 0
        sold_w = available_w = unresolved_w = 0.0
        for episode in rows:
            key = (episode['retailer'], episode['vin'])
            pick = selected_by_vin.get(key)
            if pick is None:
                continue
            weight = float(pick.selection_probability)
            if weight <= 0:
                continue
            weight = 1.0 / weight
            depart = left_at(episode)
            history = visits_by_vin.get(key)
            if history is None or history.empty:
                checked += 1
                unresolved += 1
                unresolved_w += weight
                continue
            after = history if depart is None else history.loc[history.checked_at.gt(depart)]
            usable = after.loc[after.checked_at.le(cutoff)] if not after.empty else after
            if usable.empty:
                checked += 1
                unresolved += 1
                unresolved_w += weight
                continue
            status = usable.sort_values('checked_at').iloc[-1].saleStatus
            checked += 1
            if status == 'Sold':
                sold += 1
                sold_w += weight
            elif status == 'Available':
                available += 1
                available_w += weight
            else:
                unresolved += 1
                unresolved_w += weight
        resolved_w = sold_w + available_w
        share = sold_w / resolved_w if resolved_w else None
        if share is None:
            low = high = None
        else:
            low, high = wilson_resolved_interval(share * resolved_w, resolved_w)
        total_w = sold_w + available_w + unresolved_w
        full_low = sold_w / total_w if total_w else None
        full_high = (sold_w + unresolved_w) / total_w if total_w else None
        estimate = None if share is None else exits * share
        return dict(detection_date=date, exits=exits, checked=checked, sold=sold,
                    available=available, unresolved=unresolved, sold_share=share,
                    sold_share_wilson95_low=low, sold_share_wilson95_high=high,
                    sold_share_full_sample_low=full_low, sold_share_full_sample_high=full_high,
                    estimated_sold_exits=estimate,
                    estimated_sold_exits_low=None if low is None else exits * low,
                    estimated_sold_exits_high=None if high is None else exits * high,
                    interpretation=interpretation, as_of=cutoff.isoformat())

    episodes = absences.to_dict('records')
    by_date = {}
    for episode in episodes:
        by_date.setdefault(episode['detected_date'], []).append(episode)
    rows = [summarize(group, date) for date, group in sorted(by_date.items())]
    rows.append(summarize(episodes, 'pooled'))
    return pd.DataFrame(rows, columns=columns)


def cohort_estimates(native_events, absences, records, cohorts, *, as_of):
    """Compare scenarios on the SAME frozen prospective population, once per VIN.

    Central = first native Sold. Conservative adds a >=24h repeat requirement.
    Combined adds pending-at-last-inventory + 3-day absence; expansive adds any
    3-day absence. Absence branches are experimental assumptions, not calibrated.
    Later native Available evidence vetoes an absence proxy. An initially Sold
    vehicle remains excluded from every branch. Return a vehicle-level ledger.
    """
    known = known_disjoint_cohorts(cohorts, as_of=as_of)
    visits = native_visits(records, as_of=as_of)
    summaries = [summarize_pilot(visits, c, as_of=as_of) for c in known]
    panel = pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame()
    if panel.empty:
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    eligible = panel.loc[panel.role.eq('prospective_inventory') & ~panel.first_encountered_sold
                         & panel.usable_native_checks.gt(0)]
    keys = set(zip(eligible.retailer, eligible.vin))
    output = []
    for method, flag in [('conservative', 'conservative_eligible'), ('central_native', 'central_eligible'),
                         ('combined_pending_3d', 'central_eligible'), ('expansive_all_3d', 'central_eligible')]:
        selected = {}
        for event in native_events.to_dict('records'):
            if max(_aware(event['event_available_at']), _aware(event['evidence_available_at']),
                   _aware(event['interval_end'])) > _aware(as_of):
                continue
            key = (event['retailer'], event['vin'])
            if key in keys:
                selected[key] = dict(event, estimated_units=float(event[flag]), basis='native Sold transition')
        if method in ['combined_pending_3d', 'expansive_all_3d']:
            if not absences.empty and not absences.absence_days.eq(3).all():
                raise ValueError('Combined rules require the explicitly named 3-day absence input')
            for event in absences.to_dict('records'):
                if max(_aware(event['event_available_at']), _aware(event['evidence_available_at']),
                       _aware(event['interval_end'])) > _aware(as_of):
                    continue
                key = (event['retailer'], event['vin'])
                if key not in keys or key in selected or not event['eligible']:
                    continue
                if method == 'combined_pending_3d' and event['last_purchase_pending'] != 1:
                    continue
                later = visits.loc[visits.retailer.eq(key[0]) & visits.vin.eq(key[1])
                    & visits.checked_at.ge(_aware(event['interval_end']))
                    & visits.parse_outcome.eq('matched') & visits.saleStatus.eq('Available')]
                if not later.empty:
                    continue
                selected[key] = dict(event_id=event['candidate_id'], retailer=key[0], vin=key[1],
                    listing_id=event['listing_id'], interval_start=event['interval_start'], interval_end=event['interval_end'],
                    event_available_at=event['event_available_at'], evidence_available_at=event['evidence_available_at'],
                    source_references=json.dumps([event['source_url'], event['capture_id'], event['last_observed_cycle_id']]),
                    estimated_units=1.0, basis='persistent absence assumption')
        for event in selected.values():
            output.append({k: event[k] for k in ['event_id', 'retailer', 'vin', 'listing_id', 'interval_start',
                'interval_end', 'event_available_at', 'evidence_available_at', 'source_references', 'estimated_units', 'basis']}
                | dict(method=method, population='Frozen prospective cohort', prospective_vins=int(panel.role.eq('prospective_inventory').sum()),
                       initially_non_sold_vins=len(keys), as_of=_aware(as_of).isoformat()))
    return pd.DataFrame(output, columns=LEDGER_COLUMNS)


def allocate_intervals(ledger, *, as_of, timezone_name='America/New_York', allocation='uniform'):
    """Daily *allocation of detected events*, NOT complete daily population sales.

    Uniform: event weight times seconds of overlap / total interval seconds.
    first_observed: allocate all weight to the first-Sold/first-absence local date.
    Neither identifies a transaction date. Do not fill uncovered dates with zero.
    Every row retains when the estimate's underlying evidence became available.
    """
    if allocation not in ['uniform', 'first_observed']:
        raise ValueError('Unknown interval allocation')
    cutoff, output = _aware(as_of), []
    for event in ledger.to_dict('records'):
        start, end = _aware(event['interval_start']), _aware(event['interval_end'])
        available = _aware(event['evidence_available_at'])
        if end <= start or available < end:
            raise ValueError('Invalid interval or evidence-availability clock')
        if max(end, available) > cutoff:
            continue
        if allocation == 'first_observed':
            weights = [(end.tz_convert(timezone_name).date().isoformat(), 1.0)]
        else:
            weights = []
            left = start.tz_convert(timezone_name).normalize()
            while left < end:
                right = left + pd.DateOffset(days=1)  # Calendar midnight, including DST.
                overlap = (min(end, right) - max(start, left)).total_seconds()
                if overlap > 0:
                    weights.append((left.date().isoformat(), overlap / (end - start).total_seconds()))
                left = right
        for date, fraction in weights:
            output.append(dict(event, date=date, allocation=allocation, allocation_fraction=fraction,
                allocated_units=event['estimated_units'] * fraction,
                coverage='Only detected-event intervals; outside intervals unmeasured'))
    return pd.DataFrame(output, columns=[*ledger.columns, 'date', 'allocation',
        'allocation_fraction', 'allocated_units', 'coverage'])
