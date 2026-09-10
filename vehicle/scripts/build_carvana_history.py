"""Explicit import/export commands; default invocation previews without writes or network."""
import argparse
import json
from pathlib import Path
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from vehicle_tracker.history import import_reports, read_history, comparison_checks, classify_changes


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'config/carvana_history_example.json')
    parser.add_argument('--cycle-report',type=Path,action='append',help='Explicit cycle.json; repeat for daily comparisons')
    parser.add_argument('--database',type=Path,help='Explicit cycle-analysis database')
    parser.add_argument('--absence-days',type=int,default=3,help='Diagnostic threshold; not a sales definition')
    parser.add_argument('--write',action='store_true',help='Import into the explicitly configured analysis database')
    parser.add_argument('--export',type=Path,help='Write derived CSV tables to this NEW directory')
    args=parser.parse_args(argv)
    if args.cycle_report:
        from vehicle_tracker.cycles import cycle_evidence, import_cycle, read_cycle_history
        from vehicle_tracker.events import vin_events, daily_counts
        if args.database is None:
            parser.error('--cycle-report requires an explicit --database')
        if args.absence_days < 1:
            parser.error('--absence-days must be positive')
        for path in args.cycle_report:
            state, coverage, reports = cycle_evidence(path)
            print('Cycle:',state['cycle_id'],state['coverage_reason'],'Attempt reports:',len(reports))
            print(coverage[['query_id','coverage_complete','reason']].to_string(index=False))
        if not args.write and args.export is None:
            print('Preview only: no imports, exports or requests. Database:',args.database)
            return 0
        if args.write:
            for path in args.cycle_report:
                result=import_cycle(path,args.database)
                print('Imported rows:',int(result.imported_rows.sum()) if not result.empty else 0)
        if args.export is not None:
            days,rows=read_cycle_history(args.cycle_report,args.database)
            args.export.mkdir(parents=True,exist_ok=False)
            days.to_csv(args.export/'daily_coverage.csv',index=False)
            rows.to_csv(args.export/'daily_observations.csv',index=False)
            try:
                events=vin_events(days,rows,absence_days=args.absence_days)
            except ValueError as exc:
                print('Event comparison blocked:',exc)
                return 1
            events.to_csv(args.export/'vin_events.csv',index=False)
            daily_counts(days,events).to_csv(args.export/'daily_counts.csv',index=False)
            print('Saved diagnostics; estimated_sales remains missing:',args.export)
        return 0
    if args.database is not None:
        parser.error('--database is reserved for --cycle-report; existing examples use --config')
    config=json.loads(args.config.read_text(encoding='utf-8'))
    database=ROOT/config['database']
    print('Analysis database:',database)
    print('Retained query reports:',len(config['reports']))
    if not args.write and args.export is None:
        print('Preview: no requests, imports or exports. Use --write or --export explicitly.')
        return 0
    if args.write:
        result=import_reports([ROOT/p for p in config['reports']],database)
        print(result[['imported_runs','imported_rows']].sum().to_string())
    if args.export is not None:
        runs,captures,observations=read_history(database)
        args.export.mkdir(parents=True,exist_ok=False)
        runs.assign(reconciliation_difference=runs.stored_rows-runs.reported_total).to_csv(args.export/'query_coverage.csv',index=False)
        counts=observations.groupby('run_id').agg(observed_rows=('listing_id','size'),
            unique_listings=('listing_id','nunique'),unique_vins=('vin','nunique'))
        inventory=runs[['run_id','query_complete','reported_total','stored_rows']].merge(counts,on='run_id',how='left')
        inventory[counts.columns]=inventory[counts.columns].fillna(0).astype(int)
        inventory.to_csv(args.export/'inventory_by_query.csv',index=False)
        plans=[]
        for path in config.get('collection_reports',[]):
            plan=json.loads((ROOT/path).read_text(encoding='utf-8'))
            outcomes=pd.DataFrame(plan.get('outcomes',[]),columns=['query_id','status','query_complete','reason','report'])
            coverage=pd.DataFrame(plan['queries']).merge(outcomes,on='query_id',how='left',validate='one_to_one')
            plans.append(coverage.assign(collection_report=str(ROOT/path)))
        if plans:
            pd.concat(plans,ignore_index=True).to_csv(args.export/'collection_coverage.csv',index=False)
        lookup=runs.set_index('report_path').run_id.to_dict()
        exploratory_ids=[lookup.get(str((ROOT/p).resolve())) for p in config.get('exploratory_reports',[])]
        exploratory=observations[observations.run_id.isin(exploratory_ids)]
        if not exploratory.empty:
            exploratory.to_csv(args.export/'broader_observed_listings.csv',index=False)
            exploratory.groupby(['make','year'],dropna=False).agg(observed_listings=('listing_id','nunique'),
                median_asking_price_usd=('asking_price_usd','median')).to_csv(args.export/'broader_observed_mix.csv')
        previous_ids=[lookup.get(str((ROOT/p).resolve()),'missing:'+p) for p in config['previous_reports']]
        current_ids=[lookup.get(str((ROOT/p).resolve()),'missing:'+p) for p in config['current_reports']]
        checks=comparison_checks(runs,previous_ids,current_ids)
        checks.to_csv(args.export/'comparison_checks.csv',index=False)
        if checks.passed.all():
            previous=observations[observations.run_id.isin(previous_ids)]
            current=observations[observations.run_id.isin(current_ids)]
            contexts=set(runs.loc[runs.run_id.isin(previous_ids),'context_json'])
            previous_start=runs.loc[runs.run_id.isin(previous_ids),'observation_start'].min()
            earlier_ids=runs.loc[runs.context_json.isin(contexts) & runs.observation_end.lt(previous_start),'run_id']
            seen_before=set(observations.loc[observations.run_id.isin(earlier_ids),'listing_id'])
            try:
                changes=classify_changes(previous,current,seen_before=seen_before)
            except ValueError as exc:
                print('Comparison blocked:',exc)
                checks.loc[len(checks)]=['period_identities',False,str(exc)]
                checks.to_csv(args.export/'comparison_checks.csv',index=False)
                return 1
            changes.to_csv(args.export/'listing_changes.csv',index=False)
            changes.groupby('observation_change').listing_id.nunique().rename('listings').to_csv(args.export/'change_summary.csv')
            current.groupby(['make','model','year'],dropna=False).agg(listings=('listing_id','nunique'),
                median_asking_price_usd=('asking_price_usd','median'),missing_prices=('asking_price_usd',lambda x:x.isna().sum())).to_csv(args.export/'current_inventory_mix.csv')
        else:
            print('Comparison blocked; exported coverage diagnostics only.')
        print('Derived tables:',args.export.resolve())
    return 0


if __name__=='__main__':
    sys.exit(main())
