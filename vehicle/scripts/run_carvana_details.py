"""Preview, prepare and record a small connected-Chrome detail batch.

Default is a read-only preview. This CLI never navigates the browser itself;
the documented browser workflow reserves a page, reads it, then records its JSON.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from vehicle_tracker.daily import tracking_settings, tracking_history
from vehicle_tracker.cycles import cycle_config
from vehicle_tracker.detail_batch import (begin_visit, browser_capacity, browser_workload, create_batch, digest, fail_visit,
    load_browser_batches, preview_plan, read_batch, record_visit, recover_visit, validate_inventory_plan)
from vehicle_tracker.research_inputs import load_detail_cohorts, load_detail_evidence
from vehicle_tracker.sales_proxy import inventory_followup_queue


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def cohort_inputs(now):
    return load_detail_cohorts(ROOT, now)


def fresh_plan(cohorts, *, as_of, limit, controls, seed, minutes, config_path=None):
    """Use the same inventory and follow-up selection functions as Notebook 23."""
    default = ROOT/'config/carvana_daily_tracking.json'
    settings = tracking_settings(config_path or default)
    days, inventory = tracking_history(settings, as_of=as_of)
    days = days.loc[pd.to_datetime(days.available_at, utc=True).le(pd.Timestamp(as_of))].sort_values('cycle_date')
    inventory = inventory.loc[inventory.cycle_id.isin(days.cycle_id)]
    scope = None
    if not days.empty:
        first = days.iloc[0]
        scope = cycle_config(settings['queries'], cycle_date=first.cycle_date, timezone_name=settings['timezone'],
            window_start=first.get('target_window_start', first.window_start),
            window_end=first.get('target_window_end', first.window_end))['scope_id']
        if not days.scope_id.eq(scope).all() or not days.timezone.eq(settings['timezone']).all():
            raise ValueError('Inventory history differs from the selected configuration scope')
    entries = json.loads(settings['register'].read_text(encoding='utf-8'))['cycles'] if settings['register'].is_file() else []
    entries = [entry for entry in entries if entry['cycle_id'] in set(days.cycle_id)]
    sources = {Path(entry['path']) for entry in entries}
    sources.update(Path(report) for entry in entries for report in entry['reports'])
    sources.update(Path(path) for path in inventory.source_path)
    context = dict(config_path=str(settings['config_path']), query_plan=str(settings['plan']),
        database=str(settings['database']), register=str(settings['register']), timezone=settings['timezone'],
        query_count=len(settings['queries']), scope_id=scope, cycle_ids=days.cycle_id.tolist(),
        cycle_dates=days.cycle_date.tolist(), source_paths=sorted(map(str, sources)))
    evidence = load_detail_evidence(ROOT, as_of, cohorts=cohorts)
    paths = set(evidence['input_paths'])
    selected_cohorts, selections = evidence['cohorts'], evidence['selection_plans']
    unavailable = None
    if settings['config_path'].resolve() != default.resolve():
        # An alternate population cannot acquire unrelated study targets. The
        # original default keeps its existing cohort-continuation sampling frame.
        members = set(zip(inventory.retailer, inventory.vin))
        selected_cohorts = [dict(c, vehicles=[v for v in c['vehicles'] if (v['retailer'], v['vin']) in members])
                            for c in selected_cohorts]
        selected_cohorts = [c for c in selected_cohorts if c['vehicles']]
        if not selections.empty:
            selections = selections.loc[[(r.retailer, r.vin) in members for r in selections.itertuples()]]
        if (len(days) < 2 or not days.tail(2).coverage_complete.all()
                or (pd.Timestamp(days.iloc[-1].cycle_date)-pd.Timestamp(days.iloc[-2].cycle_date)).days != 1):
            unavailable = 'Need two complete consecutive dates in the selected population; no new selection is available'
    queue = (pd.DataFrame(columns=['selected_for_check']) if unavailable else
        inventory_followup_queue(days, inventory, evidence['records'], selected_cohorts, as_of=as_of,
            selection_plans=selections, batch_limit=limit, control_count=controls, seed=seed,
            browser_health=evidence['browser_health']))
    chosen = queue.loc[queue.selected_for_check.eq(True)].copy()
    chosen['inventory_scope_id'] = scope
    prepared = datetime.fromisoformat(utc_now())
    expiry = (prepared+timedelta(minutes=minutes)).isoformat()
    capacity = browser_capacity(ROOT/'data/experiments/carvana_detail_batches',
        [dict(wave='detail_batch', retailer=r.retailer, vin=r.vin, start=prepared.isoformat(), end=expiry)
         for r in chosen.itertuples()], now=prepared.isoformat(),
        prior_checks=evidence['records'].to_dict('records'))
    shortfall = sum(not row['fits'] for row in capacity['checks'])
    if shortfall:
        unavailable = f'{shortfall} selected visits do not fit the shared browser budget/window; preview a smaller batch or wait'
    elif chosen.empty and unavailable is None:
        unavailable = 'No eligible targets at this cutoff'
    paths.update(sources)
    paths.update([settings['config_path'], settings['plan'], settings['register'], settings['database']])
    pages = json.loads(chosen.to_json(orient='records', date_format='iso'))
    return dict(prepared_at=prepared.isoformat(), as_of=as_of,
        expires_at=expiry, pages=pages, inventory_context=context, capacity=capacity,
        preparation_blocked_reason=unavailable,
        purpose='Bounded native website status follow-up; no transaction or national-sales labels',
        maximum_top_level_visits=limit, seed=seed,
        input_hashes={str(p): digest(p) for p in sorted(paths) if p.is_file()})


def status_code(health):
    """0 complete, 1 unfinished, 2 failed, 3 expired with unfinished work."""
    if health.empty:
        return 1
    if (~health.outcome.isin(['matched','unattempted','started_unresolved'])).any():
        return 2
    if health.outcome.eq('matched').all():
        return 0
    return 3 if health.window_expired.any() else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='preview', choices=['preview','prepare','next','record','recover','fail','status'])
    parser.add_argument('--plan', type=Path, help='Optional existing frozen plan for preview/prepare')
    parser.add_argument('--config', type=Path, help='Inventory tracking configuration; fresh planning defaults to the original Tesla population')
    parser.add_argument('--batch', type=Path, help='Fresh destination for prepare; existing batch for later steps')
    parser.add_argument('--input', type=Path, help='Selected public projection JSON for record')
    parser.add_argument('--reason', choices=['access_blocked','navigation_failed','interrupted','page_not_ready'])
    parser.add_argument('--limit', type=int, default=6)
    parser.add_argument('--controls', type=int, default=2)
    parser.add_argument('--minutes', type=int, default=45)
    parser.add_argument('--seed', default='carvana-browser-details')
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 12 or not 0 <= args.controls < args.limit or not 1 <= args.minutes <= 60:
        parser.error('Use 1-12 visits, fewer controls than total visits, and a 1-60 minute window')
    if args.action == 'next' and args.batch is not None and args.batch.resolve().parent != (
            ROOT/'data/experiments/carvana_detail_batches').resolve():
        parser.error('New starts require a batch in the shared vehicle/data/experiments/carvana_detail_batches directory')
    now = utc_now()
    cohorts = cohort_inputs(now)
    if args.action in ['preview','prepare']:
        plan = preview_plan(args.plan, now=now)[0] if args.plan else fresh_plan(cohorts,
            as_of=now, limit=args.limit, controls=args.controls, seed=args.seed, minutes=args.minutes, config_path=args.config)
        validate_inventory_plan(plan, config_path=args.config, verify_sources=True)
        pages = pd.DataFrame(plan['pages'])
        if plan.get('inventory_context'):
            context = plan['inventory_context']
            print('Inventory configuration:', context['config_path'])
            print('Population:', context['query_count'], 'queries | scope:', context['scope_id'] or 'no retained baseline')
            print('Query plan:', context['query_plan'], '| timezone:', context['timezone'])
            print('History:', context['database'], '| register:', context['register'])
            print('Inventory dates:', context['cycle_dates'], '| retained source files:', len(context['source_paths']))
        else:
            print('Legacy frozen selection: no inventory configuration binding; original targets preserved')
        print('No selected pages.' if pages.empty else pages.reindex(columns=[
            'vin','followup_listing_id','listing_id','selection_group','selection_reason']).dropna(axis=1, how='all').to_string(index=False))
        print('Evidence cutoff:', plan['as_of'], '| window expires:', plan['expires_at'], '| planned visits:', len(pages))
        print('Shared browser budget:', json.dumps(browser_workload(ROOT/'data/experiments/carvana_detail_batches', now=now)))
        if plan.get('capacity'):
            capacity = plan['capacity']
            print('Capacity within this window:', sum(row['fits'] for row in capacity['checks']), '/', len(pages),
                  '| assumed minutes/check:', capacity['minutes_per_check'],
                  '| assumed operator minutes/day:', capacity['operator_minutes_per_day'])
        if plan.get('preparation_blocked_reason'):
            print('Preparation unavailable:', plan['preparation_blocked_reason'])
        if args.action == 'preview':
            print('Preview only: no browser navigation or files. Budget counts top-level visits, not Chrome asset requests.')
            return 0
        if plan.get('preparation_blocked_reason'):
            raise ValueError(plan['preparation_blocked_reason'])
        if args.batch is None:
            parser.error('prepare requires a fresh --batch destination')
        expected_parent = (ROOT/'data/experiments/carvana_detail_batches').resolve()
        if args.batch.resolve().parent != expected_parent:
            parser.error('Batch must be a direct child of vehicle/data/experiments/carvana_detail_batches')
        with tempfile.TemporaryDirectory(prefix='carvana-detail-plan-') as temp:
            source = Path(temp)/'plan.json'
            source.write_text(json.dumps(plan, indent=2)+'\n', encoding='utf-8')
            create_batch(source, args.batch, helper_path=ROOT/'scripts/capture_carvana_page.js', cohorts=cohorts, now=utc_now())
        print('Prepared:', args.batch.resolve())
        return 0
    if args.batch is None:
        parser.error('This action requires --batch')
    if args.config is not None:
        validate_inventory_plan(read_batch(args.batch)[1], config_path=args.config)
    if args.action == 'next':
        print(json.dumps(begin_visit(args.batch, now=now), indent=2))
    elif args.action == 'record':
        if args.input is None:
            parser.error('record requires --input')
        row = record_visit(args.batch, json.loads(args.input.read_text(encoding='utf-8')), now=now)
        print(json.dumps(row, indent=2))
        return 0 if row['parse_outcome'] == 'matched' else 2
    elif args.action == 'recover':
        row = recover_visit(args.batch, now=now)
        print(json.dumps(row, indent=2))
        return 0 if row['parse_outcome'] == 'matched' else 2
    elif args.action == 'fail':
        if args.reason is None:
            parser.error('fail requires --reason')
        print(json.dumps(fail_visit(args.batch, reason=args.reason, now=now), indent=2))
        return 2
    else:
        _, _, health, _ = load_browser_batches(ROOT, cohorts, as_of=now)
        if health.empty or not health.batch.eq(args.batch.name).any():
            parser.error('Batch not found in the recorded detail batches')
        rows = health.loc[health.batch.eq(args.batch.name)]
        print(rows.to_string(index=False))
        print(rows.outcome.value_counts().to_string())
        return status_code(rows)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError) as error:
        print('BLOCKED:', error)
        raise SystemExit(1)
