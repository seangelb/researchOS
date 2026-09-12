"""Preview/freeze a status study, prepare bounded Chrome batches, or report offline.

This command does not navigate Chrome or collect inventory. Use the existing
collectors; one study can span multiple batches without replacing sampled VINs.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from vehicle_tracker.cycles import read_cycle_history
from vehicle_tracker.detail_batch import browser_capacity, create_batch, digest, write_new
from vehicle_tracker.events import _aware
from vehicle_tracker.research_inputs import load_detail_evidence
from vehicle_tracker.status_experiment import (arm_outcomes, freeze_plan, hypothesis_tests, inventory_frame,
    read_experiment, sample_frame, score_plan, study_feasibility)


def show_feasibility(plan, records, *, now, minutes_per_check=1.5, operator_minutes_per_day=20):
    feasibility = study_feasibility(plan, records, browser_root=ROOT/'data/experiments/carvana_detail_batches',
        now=now, minutes_per_check=minutes_per_check, operator_minutes_per_day=operator_minutes_per_day)
    print('Shared browser budget:', json.dumps(feasibility['workload']))
    print('Required checks and capacity for those checks (fixed windows):')
    print(pd.DataFrame(feasibility['table']).to_string(index=False))
    print(f"Planning assumptions: {minutes_per_check:g} active minutes/check; {operator_minutes_per_day:g} minutes/rolling day.")
    print('Local pilot setting: 12 starts/rolling day; this is not an established Carvana limit.')
    print(feasibility['assumption'])
    return feasibility


def study_batch(plan, records, health, *, now, wave='primary', limit=12):
    """Keep frozen membership; defer recently checked/reserved VINs without replacement."""
    now = _aware(now)
    if wave not in ['primary', 'repeat'] or not 1 <= limit <= 12:
        raise ValueError('Use primary/repeat and a batch limit of 1-12')
    start, end = _aware(plan[wave+'_start']), _aware(plan[wave+'_end'])
    if not start <= now < end:
        raise ValueError('Outside the frozen observation window; do not move its deadline')
    from vehicle_tracker.sales_proxy import native_visits
    visits = native_visits(records, as_of=now)
    pages, deferred = [], []
    for page in plan['pages']:
        own = visits.loc[visits.retailer.eq(page['retailer']) & visits.vin.eq(page['vin'])] if not visits.empty else visits
        matched = own.loc[own.listing_id.eq(page['listing_id']) & own.parse_outcome.eq('matched')
            & own.checked_at.ge(start) & own.checked_at.lt(end)] if not own.empty else own
        if len(matched):
            continue
        reserved = health.loc[health.retailer.eq(page['retailer']) & health.vin.eq(page['vin'])
            & health.outcome.eq('started_unresolved')
            & pd.to_datetime(health.started_at, utc=True).le(now)] if not health.empty else health
        reason = ('unresolved browser reservation' if len(reserved) else
                  '24-hour physical-check spacing' if len(own) and now < own.checked_at.max()+pd.Timedelta(hours=24) else '')
        if reason:
            deferred.append(dict(vin=page['vin'], reason=reason))
        else:
            pages.append(page)
    chosen = pd.DataFrame(pages)
    if not chosen.empty:
        # Interleave arms to reduce systematic differences in observation times.
        chosen['arm_position'] = chosen.groupby('arm').cumcount()
        chosen = chosen.sort_values(['arm_position', 'arm', 'vin']).head(limit).drop(columns='arm_position')
    return dict(prepared_at=now.isoformat(), as_of=now.isoformat(),
        expires_at=min(end, now+pd.Timedelta(minutes=45)).isoformat(),
        pages=[] if chosen.empty else json.loads(chosen.to_json(orient='records')),
        purpose='Frozen prospective website-status experiment: '+wave,
        study_prepared_at=plan['prepared_at'], maximum_top_level_visits=limit), deferred


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='preview', choices=['preview', 'freeze', 'batch', 'report'])
    parser.add_argument('--cycles', type=Path, nargs='+', help='Explicit retained cycle.json paths; no scope mixing')
    parser.add_argument('--study', type=Path, help='Existing frozen study plan.json for batch/report')
    parser.add_argument('--destination', type=Path, help='New study directory for freeze; new detail batch for batch')
    parser.add_argument('--per-arm', type=int, default=3)
    parser.add_argument('--seed', default='carvana-status-v1')
    parser.add_argument('--wave', choices=['primary', 'repeat'], default='primary')
    parser.add_argument('--limit', type=int, default=12)
    parser.add_argument('--minutes-per-check', type=float, default=1.5, help='Explicit assumed operator effort per attempted check')
    parser.add_argument('--operator-minutes-per-day', type=float, default=20, help='Planned operator minutes in a rolling day')
    parser.add_argument('--as-of', help='Read-only replay cutoff for preview/report; writes use current UTC time')
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc).isoformat()
    if args.as_of and args.action in ['freeze', 'batch']:
        parser.error('Freeze/batch use the current clock; --as-of is for offline preview/report')
    cutoff = args.as_of or now
    if args.action in ['preview', 'freeze']:
        if not args.cycles:
            parser.error('Provide --cycles with the retained inventory history to inspect')
        days, inventory = read_cycle_history(args.cycles, as_of=cutoff)
        frame = inventory_frame(days, inventory, as_of=cutoff)
        sampled = sample_frame(frame, per_arm=args.per_arm, seed=args.seed)
        print(sampled.groupby(['arm', 'exclusion_reason'], dropna=False).agg(
            population=('vin', 'size'), selected=('selected_for_check', 'sum')).to_string())
        plan = freeze_plan(frame, as_of=cutoff, prepared_at=cutoff, seed=args.seed, per_arm=args.per_arm)
        evidence = load_detail_evidence(ROOT, as_of=cutoff)
        feasibility = show_feasibility(plan, evidence['records'], now=cutoff,
            minutes_per_check=args.minutes_per_check, operator_minutes_per_day=args.operator_minutes_per_day)
        if args.action == 'preview':
            print('Preview only: no requests, files, imports or frozen selection.')
            return 0
        if args.destination is None:
            parser.error('freeze needs a fresh --destination')
        if not feasibility['feasible']:
            raise ValueError('Study is infeasible under its fixed windows and workload; no plan was frozen. Preview a smaller future study or an explicit effort plan.')
        if _aware(now)-frame.decision_at.map(_aware).max() > pd.Timedelta(hours=36):
            raise ValueError('Latest inventory is older than 36 hours; collect a fresh comparable date')
        paths = {p.resolve() for cycle in args.cycles for p in cycle.parent.rglob('*')
                 if p.is_file() and p.suffix in {'.json', '.bin'}}
        before = {str(p): digest(p) for p in sorted(paths)}
        # Re-read while binding bytes, so a changing collector cannot publish a mixed selection.
        bound_days, bound_rows = read_cycle_history(args.cycles, as_of=cutoff)
        pd.testing.assert_frame_equal(frame, inventory_frame(bound_days, bound_rows, as_of=cutoff))
        freeze_time = datetime.now(timezone.utc).isoformat()
        plan = freeze_plan(frame, as_of=cutoff, prepared_at=freeze_time, seed=args.seed, per_arm=args.per_arm)
        # Recheck after binding inventory; never publish a stale capacity warning.
        feasibility = show_feasibility(plan, load_detail_evidence(ROOT, as_of=freeze_time)['records'], now=freeze_time,
            minutes_per_check=args.minutes_per_check, operator_minutes_per_day=args.operator_minutes_per_day)
        if not feasibility['feasible']:
            raise ValueError('Study capacity changed or is insufficient; no plan was frozen')
        plan['feasibility_at_freeze'] = feasibility
        if before != {str(p): digest(p) for p in sorted(paths)}:
            raise ValueError('Inventory evidence changed during preparation')
        args.destination.mkdir(parents=True, exist_ok=False)
        write_new(args.destination/'plan.json', plan)
        write_new(args.destination/'manifest.json', dict(plan_sha256=digest(args.destination/'plan.json'),
            cycles=[str(p.resolve()) for p in args.cycles], input_hashes=before))
        print('Frozen:', (args.destination/'plan.json').resolve(), '| VINs:', len(plan['pages']))
        print('Primary deadline:', plan['primary_end'], '| repeat starts:', plan['repeat_start'])
        return 0
    if args.study is None:
        parser.error('batch/report needs --study plan.json')
    plan = read_experiment(args.study, as_of=cutoff)
    if plan is None:
        print('Study not yet available at this cutoff')
        return 0
    if args.action == 'batch':
        if args.destination is None or args.destination.resolve().parent != (ROOT/'data/experiments/carvana_detail_batches').resolve():
            parser.error('batch needs a fresh direct child of vehicle/data/experiments/carvana_detail_batches')
        now = datetime.now(timezone.utc).isoformat()
        evidence = load_detail_evidence(ROOT, as_of=now)
        batch, deferred = study_batch(plan, evidence['records'], evidence['browser_health'], now=now, wave=args.wave, limit=args.limit)
        print('Deferred without replacement:', json.dumps(deferred))
        if not batch['pages']:
            print('No eligible visits now; selected VINs and study deadlines are unchanged')
            return 1 if deferred else 0
        admission_at = datetime.now(timezone.utc).isoformat()
        capacity = browser_capacity(ROOT/'data/experiments/carvana_detail_batches',
            [dict(wave=args.wave, retailer=p['retailer'], vin=p['vin'],
                  start=batch['prepared_at'], end=batch['expires_at']) for p in batch['pages']],
            now=admission_at, prior_checks=evidence['records'].to_dict('records'),
            minutes_per_check=args.minutes_per_check, operator_minutes_per_day=args.operator_minutes_per_day)
        print('Shared browser budget:', json.dumps(capacity['workload']))
        shortfall = sum(not check['fits'] for check in capacity['checks'])
        if shortfall:
            raise ValueError(f'{shortfall} selected visits do not fit the current browser budget/window; no batch was prepared. Selected VINs and deadlines are unchanged.')
        batch['capacity'] = capacity
        batch['input_hashes'] = {str(args.study.resolve()): digest(args.study)}
        with tempfile.TemporaryDirectory(prefix='carvana-study-') as folder:
            source = Path(folder)/'plan.json'
            source.write_text(json.dumps(batch, indent=2)+'\n', encoding='utf-8')
            create_batch(source, args.destination, helper_path=ROOT/'scripts/capture_carvana_page.js',
                         cohorts=evidence['cohorts'], now=admission_at)
        print('Prepared browser batch:', args.destination, '| visits:', len(batch['pages']))
        print('Use run_carvana_details.py next/record/fail/status with this --batch. Chrome connection required.')
        return 0
    evidence = load_detail_evidence(ROOT, as_of=cutoff)
    manifest = json.loads((args.study.parent/'manifest.json').read_text())
    cycles = list(dict.fromkeys([*map(Path, manifest['cycles']), *(args.cycles or [])]))
    days, inventory = read_cycle_history(cycles, as_of=cutoff)
    outcomes = score_plan(plan, evidence['records'], days, inventory, as_of=cutoff,
                          browser_health=evidence['browser_health'])
    print(outcomes[['vin', 'arm', 'primary_outcome', 'primary_status', 'repeat_status']].to_string(index=False))
    print(pd.concat([arm_outcomes(outcomes, plan, as_of=cutoff, wave=wave)
                     for wave in ['primary', 'repeat']], ignore_index=True).to_string(index=False))
    print('Wilson intervals assume independent outcomes within each arm; resolved-only proportions can be biased by missing outcomes.')
    print('Selected-sample bounds include every unresolved binary endpoint. Neither is transaction accuracy or a national estimate.')
    print(hypothesis_tests(outcomes, plan, as_of=cutoff).to_string(index=False))
    show_feasibility(plan, evidence['records'], now=cutoff,
        minutes_per_check=args.minutes_per_check, operator_minutes_per_day=args.operator_minutes_per_day)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError) as error:
        print('BLOCKED:', error)
        raise SystemExit(1)
