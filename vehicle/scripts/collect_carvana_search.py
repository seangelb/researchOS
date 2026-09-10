"""Preview public search collection; --live explicitly writes the selected experiment."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from vehicle_tracker.collect import NavigationBudget
from vehicle_tracker.cycles import collect_cycle, cycle_config
from vehicle_tracker.search_plan import collect_plan, validate_plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,default=ROOT/'config/carvana_search_queries.json')
    parser.add_argument('--experiment',default=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    parser.add_argument('--target-listings',type=int,default=1000)
    parser.add_argument('--full-plan',action='store_true',help='Finish every query rather than a sample target')
    parser.add_argument('--max-requests',type=int,default=120)
    parser.add_argument('--max-seconds',type=float,default=1200)
    parser.add_argument('--resume-from',type=Path,help='Older sample/plan recovery; not daily-cycle recovery')
    parser.add_argument('--cycle-date',help='ISO local date; enables a fresh full-plan daily cycle')
    parser.add_argument('--timezone',default='UTC')
    parser.add_argument('--window-start',help='Timezone-aware timestamp')
    parser.add_argument('--window-end',help='Timezone-aware timestamp on the same local date')
    parser.add_argument('--resume-cycle',action='store_true',help='Continue the SAME experiment/date and limits')
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.experiment) or args.target_listings < 1:
        parser.error('Use a plain experiment name and positive sample target')
    queries=json.loads(args.plan.read_text(encoding='utf-8'))['queries']
    validate_plan(queries)
    destination=ROOT/'data/experiments'/args.experiment
    if args.cycle_date:
        if not args.window_start or not args.window_end or args.resume_from:
            parser.error('Daily cycles require both window timestamps; use --resume-cycle for recovery')
        config=cycle_config(queries,cycle_date=args.cycle_date,timezone_name=args.timezone,
            window_start=args.window_start,window_end=args.window_end,
            max_requests=args.max_requests,max_seconds=args.max_seconds)
        preview={key:value for key,value in config.items() if key!='queries'}
        preview.update(mode='full_daily_cycle',resume=args.resume_cycle,target=None)
    else:
        if args.resume_cycle or args.window_start or args.window_end:
            parser.error('Daily window/recovery options require --cycle-date')
        budget=NavigationBudget(args.max_requests,args.max_seconds)
        preview=dict(mode='full_plan' if args.full_plan else 'sample',target=None if args.full_plan else args.target_listings,
            max_requests=args.max_requests,max_seconds=args.max_seconds,resume_from=str(args.resume_from))
    print(json.dumps(dict(preview,plan=str(args.plan.resolve()),query_count=len(queries),destination=str(destination)),indent=2))
    if not args.live:
        print('Preview only: no requests or writes. Inspect every partition in the plan file; national coverage is unverified.')
        return 0
    if args.cycle_date:
        result=collect_cycle(queries,destination=destination,cycle_date=args.cycle_date,timezone_name=args.timezone,
            window_start=args.window_start,window_end=args.window_end,max_requests=args.max_requests,
            max_seconds=args.max_seconds,resume=args.resume_cycle)
        success=result['coverage_complete']
    else:
        result=collect_plan(queries,destination=destination,target_listings=args.target_listings,
            budget=budget,resume_from=args.resume_from,full_plan=args.full_plan)
        success=result['all_queries_complete'] or (not args.full_plan and result['target_reached'])
    print(json.dumps({key:value for key,value in result.items() if key not in {'queries','outcomes'}},indent=2))
    return 0 if success else 1


if __name__=='__main__':
    try:
        sys.exit(main())
    except (ValueError,KeyError,FileNotFoundError) as exc:
        print('BLOCKED:',exc)
        sys.exit(1)
