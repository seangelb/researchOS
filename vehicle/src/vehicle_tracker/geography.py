"""Offline ZIP comparisons: complete query sets, observed prices, and explicit gaps."""
import json
from pathlib import Path
import sqlite3

import pandas as pd

from vehicle_tracker.history import OBSERVATION_COLUMNS, read_query_evidence
from vehicle_tracker.search_plan import validate_plan, query_outcome


DESIGN = ['query_id', 'pass', 'cohort', 'zip_code', 'context_role', 'anchor_repeat', 'context_order']
COVERAGE = DESIGN + ['status', 'complete', 'observed_vins', 'requests', 'reported_total',
    'observation_start', 'observation_end', 'available_at', 'reason', 'report_path', 'storage_check']


def _clock(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError('Geography evidence clocks must be known and timezone-aware')
    return stamp


def _identity_conflicts(rows):
    return (rows.groupby(['retailer','vin']).listing_id.nunique().gt(1).any()
            or rows.groupby(['retailer','listing_id']).vin.nunique().gt(1).any())


def load_geography(plan_path, capture_root, *, as_of):
    """Replay retained query sources. Never import, collect, or manufacture a zero."""
    plan_path, capture_root = Path(plan_path), Path(capture_root)
    cutoff = _clock(as_of)
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    inputs = {plan_path}
    empty = pd.DataFrame(columns=OBSERVATION_COLUMNS + DESIGN + ['source_path'])
    if _clock(plan['prepared_at']) > cutoff:
        return pd.DataFrame(columns=COVERAGE), empty, inputs
    validate_plan(plan['queries'])
    contexts = {c['zip_code']: c for c in plan['contexts']}
    cohorts = {c['id']: c for c in plan['cohorts']}
    if len(contexts) != len(plan['contexts']) or len(cohorts) != len(plan['cohorts']):
        raise ValueError('Duplicate geography context or cohort')
    keys = [(q['pass'],q['cohort'],q['zip_code'],q['anchor_repeat']) for q in plan['queries']]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate planned pass/cohort/ZIP/anchor observation')
    expected = {(p,c,z) for p in {q['pass'] for q in plan['queries']} for c in cohorts for z in contexts}
    if ({(q['pass'],q['cohort'],q['zip_code']) for q in plan['queries'] if not q['anchor_repeat']} != expected
            or any(c['role'] not in {'discovery','held_out'} for c in contexts.values())):
        raise ValueError('Geography design must retain every planned cohort/context in each pass')
    start,end = _clock(plan['proposed_start']),_clock(plan['proposed_end'])
    if start >= end:
        raise ValueError('Invalid proposed geography window')
    manifest = capture_root/'query_plan.json'
    if manifest.is_file():
        inputs.add(manifest)
        if json.loads(manifest.read_text(encoding='utf-8'))['queries'] != plan['queries']:
            raise ValueError('Retained geography query plan differs from selected design')
    records, frames = [], []
    for query in plan['queries']:
        if (query['zip_code'] not in contexts or query['cohort'] not in cohorts
                or query['context_role'] != contexts[query['zip_code']]['role']
                or query['filters'] != cohorts[query['cohort']]['filters']
                or type(query['anchor_repeat']) is not bool):
            raise ValueError('Query metadata differs from geography design')
        row = {k:query[k] for k in DESIGN if k != 'context_order'}
        row['context_order'] = list(contexts).index(query['zip_code'])
        row.update(status='planned; not yet due' if cutoff < start
                   else 'missing at cutoff', complete=False)
        report_path = capture_root/query['query_id']/'run_report.json'
        row['report_path'] = str(report_path)
        if report_path.is_file():
            inputs.add(report_path)
            try:
                if not manifest.is_file():
                    raise ValueError('Retained query lacks its experiment manifest')
                report = json.loads(report_path.read_text(encoding='utf-8'))
                if not report.get('ended_utc') or _clock(report['ended_utc']) > cutoff:
                    row['status'] = 'query unavailable at cutoff'
                    records.append(row)
                    continue
                availability = [p.get('evidence_available_at_utc') for p in report['pages']]
                if not availability or any(v is None for v in availability):
                    raise ValueError('Query evidence availability is incomplete')
                available = max(_clock(v) for v in availability)
                if available > cutoff:
                    row['status'] = 'query unavailable at cutoff'
                    records.append(row)
                    continue
                if (report['filters'] != query['filters'] or report['zip_code'] != query['zip_code']
                        or report.get('location_filter',False) != query.get('location_filter',False)):
                    raise ValueError('Retained query has a different geographic context')
                run, captures, observations = read_query_evidence(report_path)
                in_window = start <= _clock(run['invocation_start']) <= _clock(run['invocation_end']) <= end
                if run['query_complete']:
                    verified = query_outcome(query['query_id'],report_path.parent,recover=True)
                    inputs.update(map(Path,verified['artifact_hashes']))
                row.update(status=('complete' if run['query_complete'] else 'partial retained query')
                           if in_window else 'outside proposed window',
                    complete=bool(run['query_complete']) and in_window, observed_vins=observations.vin.nunique(),
                    requests=report['requests'], reported_total=run['reported_total'],
                    observation_start=run['observation_start'], observation_end=run['observation_end'],
                    available_at=available.isoformat(), reason=run['coverage_reason'],
                    storage_check='complete source/SQLite parity verified' if run['query_complete'] else 'partial source replay only')
                inputs.update(map(Path,captures.source_path))
                for page in report['pages']:
                    source = page.get('response_evidence',{}).get('source_path')
                    if source:
                        inputs.add(Path(source))
                if not observations.empty:
                    observations = observations.merge(captures[['capture_id','source_path']],
                        on='capture_id',validate='many_to_one')
                    frames.append(observations.assign(**{k:row[k] for k in DESIGN}))
            except (ValueError,KeyError,TypeError,OSError,sqlite3.Error) as exc:
                row.update(status='invalid retained evidence',complete=False,reason=str(exc))
        records.append(row)
    return pd.DataFrame(records,columns=COVERAGE), pd.concat(frames,ignore_index=True) if frames else empty, inputs


def geography_pairs(coverage, observations, *, anchor_zip='08542'):
    """Compare complete sets only. Price deltas remain confounded by capture time."""
    comparisons, membership, prices = [], [], []
    if coverage.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    queries = coverage.set_index('query_id')
    primary = coverage.loc[~coverage.anchor_repeat]
    pairs = []
    for (wave,cohort), group in primary.groupby(['pass','cohort'],sort=False):
        anchor = group.loc[group.zip_code.eq(anchor_zip)]
        if len(anchor) == 1:
            left = anchor.iloc[0].query_id
            pairs.extend(('between_zip',left,q) for q in group.loc[~group.zip_code.eq(anchor_zip),'query_id'])
            repeats = coverage.loc[coverage['pass'].eq(wave) & coverage.cohort.eq(cohort)
                                   & coverage.zip_code.eq(anchor_zip) & coverage.anchor_repeat]
            pairs.extend(('anchor_repeat',left,q) for q in repeats.query_id)
    for _,group in primary.groupby(['cohort','zip_code'],sort=False):
        group = group.sort_values('pass')
        pairs.extend(('between_passes',a,b) for a,b in zip(group.query_id,group.query_id.iloc[1:]))
    for kind,left_id,right_id in pairs:
        left,right = queries.loc[left_id],queries.loc[right_id]
        result = dict(comparison_kind=kind,left_query=left_id,right_query=right_id,cohort=left.cohort,
            left_zip=left.zip_code,right_zip=right.zip_code,left_pass=left['pass'],right_pass=right['pass'],
            status='unavailable; both queries must be complete',left_status=left.status,right_status=right.status)
        if bool(left.complete) and bool(right.complete):
            a = observations.loc[observations.query_id.eq(left_id)].copy()
            b = observations.loc[observations.query_id.eq(right_id)].copy()
            combined = pd.concat([a,b],ignore_index=True)
            if _identity_conflicts(combined):
                result['status'] = 'identity conflict; comparison withheld'
                comparisons.append(result)
                continue
            ids_a = set(zip(a.retailer,a.vin));ids_b = set(zip(b.retailer,b.vin))
            union = ids_a | ids_b
            result.update(status='complete observed-set comparison',left_vins=len(ids_a),right_vins=len(ids_b),
                shared_vins=len(ids_a & ids_b),left_only=len(ids_a-ids_b),right_only=len(ids_b-ids_a),
                union_vins=len(union),jaccard=len(ids_a & ids_b)/len(union) if union else None)
            starts = pd.to_datetime([left.observation_start,right.observation_start],utc=True,format='ISO8601')
            ends = pd.to_datetime([left.observation_end,right.observation_end],utc=True,format='ISO8601')
            result['capture_span_seconds'] = (ends.max()-starts.min()).total_seconds()
            for retailer,vin in sorted(union):
                membership.append(dict(comparison_kind=kind,left_query=left_id,right_query=right_id,
                    retailer=retailer,vin=vin,seen_left=(retailer,vin) in ids_a,seen_right=(retailer,vin) in ids_b))
            columns = ['retailer','vin','listing_id','asking_price_usd','transport_cost_usd','observed_at_utc','source_path']
            matched = a[columns].merge(b[columns],on=['retailer','vin','listing_id'],suffixes=('_left','_right'),validate='one_to_one')
            if not matched.empty:
                for field in ['asking_price_usd','transport_cost_usd']:
                    matched[field+'_difference'] = pd.to_numeric(matched[field+'_right'])-pd.to_numeric(matched[field+'_left'])
                matched['observation_gap_seconds'] = (pd.to_datetime(matched.observed_at_utc_right,utc=True,format='ISO8601')
                    -pd.to_datetime(matched.observed_at_utc_left,utc=True,format='ISO8601')).dt.total_seconds()
                prices.append(matched.assign(comparison_kind=kind,left_query=left_id,right_query=right_id))
        comparisons.append(result)
    return pd.DataFrame(comparisons), pd.DataFrame(membership), pd.concat(prices,ignore_index=True) if prices else pd.DataFrame()


def geography_discovery(coverage, observations):
    """Order-dependent additions from discovery ZIPs; held-out ZIPs never select them."""
    records = []
    if coverage.empty:
        return pd.DataFrame()
    primary = coverage.loc[~coverage.anchor_repeat]
    for (wave,cohort),group in primary.groupby(['pass','cohort'],sort=False):
        discovery = group.loc[group.context_role.eq('discovery')].sort_values('context_order')
        complete_rows = observations.loc[observations.query_id.isin(group.loc[group.complete,'query_id'])]
        if _identity_conflicts(complete_rows):
            records.extend(dict(pass_number=wave,cohort=cohort,zip_code=q.zip_code,role=q.context_role,
                status='identity conflict; contribution withheld') for q in group.itertuples())
            continue
        seen, earlier_complete = set(), True
        for query in discovery.itertuples():
            row = dict(pass_number=wave,cohort=cohort,zip_code=query.zip_code,role='discovery',
                status='unavailable; current and earlier discovery queries must be complete')
            if earlier_complete and query.complete:
                rows = observations.loc[observations.query_id.eq(query.query_id)]
                current = set(zip(rows.retailer,rows.vin))
                row.update(status='complete observed-set contribution',additional_vins=len(current-seen),
                    previous_union_vins=len(seen),union_vins=len(seen|current))
                seen |= current
            earlier_complete = earlier_complete and query.complete
            records.append(row)
        for query in group.loc[group.context_role.eq('held_out')].itertuples():
            row = dict(pass_number=wave,cohort=cohort,zip_code=query.zip_code,role='held_out',
                status='unavailable; all discovery queries and this held-out query must be complete')
            if len(discovery) and earlier_complete and query.complete:
                rows = observations.loc[observations.query_id.eq(query.query_id)]
                current = set(zip(rows.retailer,rows.vin))
                row.update(status='held-out validation against full discovery union',additional_vins=len(current-seen),
                    previous_union_vins=len(seen),union_vins=len(seen|current))
            records.append(row)
    return pd.DataFrame(records)
