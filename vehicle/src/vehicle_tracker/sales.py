"""Read-only sale-candidate episodes and explicit analyst outcomes; no conversion model."""
import hashlib
import json

import pandas as pd

from vehicle_tracker.events import CYCLE_COLUMNS, _aware, _cycles, vin_events

RULE_VERSION = 'sale-candidates-v1'
CANDIDATE_COLUMNS = ['candidate_id', 'scope_id', 'retailer', 'vin', 'listing_id',
    'last_observed_cycle_id', 'last_observed_at', 'capture_id', 'source_url', 'listing_url',
    'last_asking_price_usd', 'last_purchase_pending', 'first_absent_date',
    'detected_date', 'detected_available_at', 'absence_days', 'timing_uncertain',
    'followup_state', 'reappeared_date', 'latest_evidence_available_at', 'rule_version',
    'review_outcome', 'sale_date', 'reviewer', 'review_source', 'review_available_at',
    'review_note', 'review_needs_followup']
REVIEW_COLUMNS = ['candidate_id', 'outcome', 'sale_date', 'reviewer', 'source', 'available_at', 'note']


def sale_candidates(cycles, observations, *, as_of, absence_days=3, reviews=None):
    """Return one row per qualifying absence episode and a daily diagnostic calendar.

    Candidates qualify on the persistent-absence detection day, never an inferred
    transaction date. Known later presence closes the episode even with a new listing
    ID. Reviews are explicitly selected analyst labels, not automatically verified facts.
    One version per candidate is allowed. Sale dates must come from the cited evidence;
    an undated confirmed outcome is retained without assignment to a daily sales count.
    All outputs use only evidence available at as_of. No inputs are changed or written.
    """
    if type(absence_days) is not int or absence_days < 1:
        raise ValueError('absence_days must be a positive integer')
    cutoff = _aware(as_of)
    if cycles.empty:
        cycles = cycles.reindex(columns=CYCLE_COLUMNS)
    known = cycles.loc[pd.to_datetime(cycles.available_at.map(_aware), utc=True).le(cutoff)].copy()
    schedule = _cycles(known)
    events = pd.DataFrame()
    if not schedule.empty:
        selected = observations.loc[observations.cycle_id.isin(schedule.cycle_id)].copy()
        events = vin_events(known, selected, absence_days=absence_days)
    episodes, active, onset = [], {}, {}
    for event in events.to_dict('records'):
        key = (event['retailer'], event['vin'])
        if event['observed_in_cycle']:
            onset.pop(key, None)
            if key in active:
                episode = episodes[active.pop(key)]
                episode.update(followup_state='reappeared', reappeared_date=event['cycle_date'],
                    latest_evidence_available_at=event['available_at'])
            continue
        if event['absence_started']:
            onset[key] = event['cycle_date']
        if event['event_type'] == 'persistent_absence':
            identity = [event['scope_id'], *key, event['last_observed_cycle_id'], absence_days, RULE_VERSION]
            episode = {name: event.get(name) for name in
                ['scope_id', 'retailer', 'vin', 'listing_id', 'last_observed_cycle_id',
                 'capture_id', 'source_url', 'listing_url', 'timing_uncertain']}
            episode.update(candidate_id=hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
                last_observed_at=event['observed_at_utc'], last_asking_price_usd=event['asking_price_usd'],
                last_purchase_pending=event['purchase_pending'], first_absent_date=onset.get(key),
                detected_date=event['cycle_date'], detected_available_at=event['available_at'],
                absence_days=absence_days, followup_state='still_absent', reappeared_date=None,
                latest_evidence_available_at=event['available_at'], rule_version=RULE_VERSION,
                review_outcome='unreviewed', sale_date=None, reviewer=None, review_source=None,
                review_available_at=None, review_note=None, review_needs_followup=False)
            active[key] = len(episodes)
            episodes.append(episode)
        elif key in active:
            episode = episodes[active[key]]
            episode['latest_evidence_available_at'] = event['available_at']
            if not event['coverage_complete'] or event['timing_uncertain']:
                episode.update(followup_state='coverage_gap', timing_uncertain=True)
    candidates = pd.DataFrame(episodes, columns=CANDIDATE_COLUMNS)
    # A missing date after the final selected cycle also prevents a current presence claim.
    if len(schedule) and schedule.iloc[-1].cycle_date < str(cutoff.tz_convert(schedule.iloc[-1].timezone).date()):
        for index in active.values():
            candidates.loc[index, ['followup_state', 'timing_uncertain']] = ['coverage_gap', True]
    review_rows = pd.DataFrame(columns=REVIEW_COLUMNS) if reviews is None else reviews.copy()
    if set(REVIEW_COLUMNS) - set(review_rows.columns):
        raise ValueError('Reviews require candidate, outcome, date, reviewer, source, availability and note columns')
    review_rows = review_rows.loc[pd.to_datetime(review_rows.available_at.map(_aware), utc=True).le(cutoff)]
    if review_rows.candidate_id.duplicated().any():
        raise ValueError('Select exactly one review version per candidate')
    for review in review_rows.to_dict('records'):
        for field in ['candidate_id', 'reviewer', 'source', 'note']:
            if not isinstance(review[field], str) or not review[field].strip():
                raise ValueError('Review provenance must be nonempty: ' + field)
        match = candidates.index[candidates.candidate_id.eq(review['candidate_id'])]
        if len(match) != 1 or review['outcome'] not in {'confirmed_sale', 'not_sale', 'unresolved'}:
            raise ValueError('Unknown candidate or review outcome')
        index = match[0]
        available = _aware(review['available_at'])
        if available < _aware(candidates.loc[index, 'detected_available_at']):
            raise ValueError('Review predates candidate evidence')
        sale_date = review['sale_date']
        if pd.notna(sale_date):
            stamp = pd.Timestamp(sale_date)
            timezone = schedule.iloc[0].timezone
            if (review['outcome'] != 'confirmed_sale' or not isinstance(sale_date, str)
                    or stamp.strftime('%Y-%m-%d') != sale_date
                    or stamp.date() > available.tz_convert(timezone).date()):
                raise ValueError('Sale date requires a confirmed outcome and an evidenced nonfuture ISO date')
        candidates.loc[index, ['review_outcome', 'sale_date', 'reviewer', 'review_source',
            'review_available_at', 'review_note']] = [review['outcome'], sale_date, review['reviewer'],
                review['source'], available.isoformat(), review['note']]
        # Keep the original label; later contrary/uncertain evidence asks for a new explicit review.
        candidates.loc[index, 'review_needs_followup'] = bool(review['outcome'] == 'confirmed_sale'
            and candidates.loc[index, 'followup_state'] != 'still_absent')
    if schedule.empty:
        return candidates, pd.DataFrame(columns=['cycle_date', 'coverage_complete', 'new_candidates',
            'candidate_reappearances', 'reviewed_sales_with_known_date', 'estimated_sales', 'as_of'])
    dates = pd.date_range(schedule.iloc[0].cycle_date, cutoff.tz_convert(schedule.iloc[0].timezone).date())
    daily = pd.DataFrame({'cycle_date': dates.strftime('%Y-%m-%d')}).merge(
        schedule[CYCLE_COLUMNS], on='cycle_date', how='left', validate='one_to_one')
    new_counts = candidates.groupby('detected_date').size()
    returns = candidates.groupby('reappeared_date').size()
    confirmed = candidates[candidates.review_outcome.eq('confirmed_sale')]
    dated_counts = confirmed.dropna(subset=['sale_date']).groupby('sale_date').size()
    daily['new_candidates'] = daily.cycle_date.map(new_counts).fillna(0).astype('Int64').where(daily.coverage_complete.eq(True))
    daily['candidate_reappearances'] = daily.cycle_date.map(returns).fillna(0).astype('Int64').where(daily.cycle_id.notna())
    daily['reviewed_sales_with_known_date'] = daily.cycle_date.map(dated_counts).fillna(0).astype('Int64')
    daily['estimated_sales'] = pd.NA
    daily['as_of'] = cutoff.isoformat()
    return candidates, daily
