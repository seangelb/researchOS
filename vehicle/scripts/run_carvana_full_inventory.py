"""Preview the full Carvana catalog, explicitly collect, or replay retained evidence."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from vehicle_tracker.catalog import collect_catalog, export_catalog, preview
from vehicle_tracker.catalog_schedule import daily_decision

SUCCESS_KINDS = {'complete', 'complete_with_gaps'}
INCOMPLETE_KINDS = {'incomplete', 'infeasible', 'finished_incomplete'}


def exit_code(report):
    """0 when the run is usable, 2 when it finished short, 1 when it stopped hard."""
    kind = report.get('attempt_outcome')
    if kind in SUCCESS_KINDS:
        return 0
    if kind in INCOMPLETE_KINDS or report.get('status') == 'collection_finished':
        return 2
    return 1


def print_status(folder):
    """One-screen read of a capture folder. No requests."""
    folder = Path(folder)
    report = json.loads((folder/'catalog_report.json').read_text(encoding='utf-8'))
    budget = json.loads((folder/'catalog_budget.json').read_text(encoding='utf-8'))['budget']
    feasibility = report.get('feasibility') or {}
    print('status            :', report.get('status'), '| outcome', report.get('attempt_outcome'))
    print('requests          :', report.get('requests'), '/', feasibility.get('request_ceiling') or budget.get('requests'))
    print('budget stopped    :', budget.get('stopped'), '| pending', budget.get('pending_request'))
    print('phase             :', report.get('phase'))
    print('opening total     :', report.get('opening_total'))
    print('leaf queries      :', len(report.get('leaf_queries') or []))
    print('observed VINs     :', report.get('primary_observed_vins'))
    print('unverified share  :', report.get('unverified_share'))
    print('failure           :', report.get('failure_type'), report.get('failure_reason'))
    if feasibility:
        print('estimate          :', feasibility.get('estimated_minimum_requests'),
              '| fits', feasibility.get('fits_remaining_allowance'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT/'config/carvana_full_inventory_adaptive.json')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--live', action='store_true')
    action.add_argument('--replay', type=Path)
    action.add_argument('--decide', action='store_true',
                        help='Print whether a daily attempt should start. No requests or writes.')
    action.add_argument('--status', type=Path, help='Print a capture folder summary. No requests.')
    parser.add_argument('--config-sha256', help='Exact configuration hash printed by preview')
    parser.add_argument('--attempt', type=int, default=1)
    parser.add_argument('--spacing-seconds', type=float)
    parser.add_argument('--force', action='store_true',
                        help='Start even when the remaining window cannot pace the estimated plan.')
    parser.add_argument('--output', type=Path, help='Fresh offline replay destination')
    args = parser.parse_args(argv)
    if args.decide:
        print(json.dumps(daily_decision(args.config)))
        return 0
    if args.status:
        print_status(args.status)
        return 0
    if args.replay:
        if not args.output:
            parser.error('--replay requires a fresh --output')
        print(export_catalog(args.replay, output=args.output))
        return 0
    if args.output:
        parser.error('--output is only for --replay')
    if not args.live:
        result = preview(args.config)
        print(json.dumps(result, indent=2))
        if not result['ceiling_pacing_fits_window']:
            print('BLOCKED: Remaining local-date window is shorter than three-second pacing of the request ceiling.')
        print('Preview only: no requests or writes. Full-inventory collection is a separate scope from the old panel.')
        return 0
    if not args.config_sha256:
        parser.error('--live requires --config-sha256 from the reviewed preview')
    report = collect_catalog(args.config, expected_sha256=args.config_sha256,
                             attempt=args.attempt, spacing_seconds=args.spacing_seconds,
                             force=args.force)
    print(json.dumps({key:report.get(key) for key in ['capture_directory','partition_strategy','status','requests',
        'discovery_complete','primary_queries_complete','declared_collection_complete','primary_observed_vins','primary_scope_reconciled',
        'opening_count_residual','closing_count_residual','geographic_membership_stable','failure_type']}, indent=2))
    print('Reconciled export:', export_catalog(report['capture_directory'],
        output=Path(report['capture_directory'])/'analysis'))
    code = exit_code(report)
    if code == 2 and report.get('attempt_outcome') == 'infeasible':
        print('INFEASIBLE: the frozen plan does not fit the remaining request or time allowance.')
    elif code == 2:
        print('FINISHED_INCOMPLETE: collection ended without a reconciled full catalog.')
    return code


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('BLOCKED:', error)
        sys.exit(1)
