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
from vehicle_tracker.detail_batch import (begin_visit, create_batch, digest, fail_visit,
    load_browser_batches, preview_plan, record_visit, recover_visit)
from vehicle_tracker.sale_pilot import (known_disjoint_cohorts, load_pilot, load_research_pass, load_selection_plan)
from vehicle_tracker.sales_proxy import inventory_followup_queue


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def cohort_inputs(now):
    paths = [ROOT/'config/carvana_sale_pilot.json', ROOT/'config/carvana_sale_pilot_extension_20260909.json']
    return known_disjoint_cohorts([json.loads(p.read_text()) for p in paths], as_of=now)


def fresh_plan(cohorts, *, as_of, limit, controls, seed, minutes):
    """Use the same inventory and follow-up selection functions as Notebook 23."""
    settings = tracking_settings(ROOT/'config/carvana_daily_tracking.json')
    days, inventory = tracking_history(settings, as_of=as_of)
    frames = [load_pilot(ROOT, cohort, as_of=as_of) for cohort in cohorts]
    plans, paths = [], set()
    review = ROOT/'data/experiments/vendor_review/20260911T162600Z'
    for plan_name, pass_name in [('visible_check_plan.json','pass.json'), ('exit_batch_plan.json','exit_batch_pass.json')]:
        selected = load_selection_plan(review/plan_name, as_of=as_of)
        _, rows = load_research_pass(review/pass_name, selected, cohorts, as_of=as_of)
        plans.append(selected)
        frames.append(rows)
        paths.update([review/plan_name, review/pass_name])
    browser_rows, browser_plans, browser_health, browser_paths = load_browser_batches(ROOT, cohorts, as_of=as_of)
    frames.append(browser_rows)
    plans.append(browser_plans)
    paths.update(browser_paths)
    records = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)
    selections = pd.concat([frame for frame in plans if not frame.empty], ignore_index=True)
    queue = inventory_followup_queue(days, inventory, records, cohorts, as_of=as_of,
        selection_plans=selections, batch_limit=limit, control_count=controls, seed=seed, browser_health=browser_health)
    chosen = queue.loc[queue.selected_for_check]
    prepared = datetime.now(timezone.utc)
    paths.update(Path(p) for p in inventory.source_path)
    paths.update(Path(p) for p in records.source if Path(p).is_file())
    paths.update([settings['config_path'], settings['plan'], settings['register'], settings['database']])
    pages = json.loads(chosen.to_json(orient='records', date_format='iso'))
    return dict(prepared_at=prepared.isoformat(), as_of=as_of,
        expires_at=(prepared+timedelta(minutes=minutes)).isoformat(), pages=pages,
        purpose='Bounded native website status follow-up; no transaction or national-sales labels',
        maximum_top_level_visits=limit, seed=seed,
        input_hashes={str(p): digest(p) for p in sorted(paths)})


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
    now = utc_now()
    cohorts = cohort_inputs(now)
    if args.action in ['preview','prepare']:
        plan = preview_plan(args.plan, now=now)[0] if args.plan else fresh_plan(cohorts,
            as_of=now, limit=args.limit, controls=args.controls, seed=args.seed, minutes=args.minutes)
        pages = pd.DataFrame(plan['pages'])
        print(pages.reindex(columns=['vin','followup_listing_id','listing_id','selection_group','selection_reason']).dropna(axis=1, how='all').to_string(index=False))
        print('Evidence cutoff:', plan['as_of'], '| window expires:', plan['expires_at'], '| planned visits:', len(pages))
        if args.action == 'preview':
            print('Preview only: no browser navigation or files. Budget counts top-level visits, not Chrome asset requests.')
            return 0
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
    raise SystemExit(main())
