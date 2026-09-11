"""Preview, collect or rebuild the explicitly configured daily Carvana inventory."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from vehicle_tracker.daily import record_evidence, run_tracking, tracking_settings
from vehicle_tracker.search import ENDPOINT


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/carvana_daily_tracking.json')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--live', action='store_true', help='Collect a fresh daily cycle, import and save tables')
    action.add_argument('--import-cycle', type=Path, help='Register/import this retained cycle without requests')
    action.add_argument('--refresh', action='store_true', help='Write new tables from the registered evidence only')
    action.add_argument('--record-check', type=Path, help='Save one supplied listing-check JSON record; no requests')
    action.add_argument('--record-review', type=Path, help='Save one supplied candidate-review JSON record; no requests')
    args = parser.parse_args(argv)
    settings = tracking_settings(args.config)
    if args.record_check or args.record_review:
        kind = 'check' if args.record_check else 'review'
        changed = record_evidence(settings, args.record_check or args.record_review, kind=kind)
        print('Recorded' if changed else 'Already recorded (unchanged)', kind, 'in', settings['checks' if kind == 'check' else 'reviews'])
        print('Run --refresh to save new report tables, or rerun notebook 20 to read them without writing.')
        return 0
    print('Source:', ENDPOINT, '| Plan:', settings['plan'], flush=True)
    state, folder, tables = run_tracking(settings, live=args.live, import_path=args.import_cycle, refresh=args.refresh)
    if tables is None:
        print(json.dumps(dict(state, destinations={key: str(settings[key]) for key in
            ['capture_root', 'database', 'register', 'exports', 'checks', 'reviews']}), indent=2))
        print('Queries:', len(settings['queries']), 'Database:', settings['database'])
        print('Preview only: no requests or writes. --live collects/imports/exports this fixed pilot.')
        return 0
    print('Tables:', folder)
    if not tables['daily_inventory'].empty:
        print(tables['daily_inventory'][['cycle_date', 'coverage_status', 'observed_vins', 'inventory_count',
            'pending_true', 'new_since_previous_day', 'absent_since_previous_day',
            'reviewed_sales_with_known_date', 'estimated_sales']].to_string(index=False))
        errors = tables['daily_inventory'].analysis_error.dropna().unique()
        if len(errors):
            print('Analysis withheld:', '; '.join(errors))
            return 1
    return 1 if state is not None and not state['coverage_complete'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as error:
        print('BLOCKED:', error)
        sys.exit(1)
