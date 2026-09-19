"""Preview the full Carvana catalog, explicitly collect, or replay retained evidence."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from vehicle_tracker.catalog import collect_catalog, export_catalog, preview


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT/'config/carvana_full_inventory.json')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--live', action='store_true')
    action.add_argument('--replay', type=Path)
    parser.add_argument('--config-sha256', help='Exact configuration hash printed by preview')
    parser.add_argument('--output', type=Path, help='Fresh offline replay destination')
    args = parser.parse_args(argv)
    if args.replay:
        if not args.output:
            parser.error('--replay requires a fresh --output')
        print(export_catalog(args.replay, output=args.output))
        return 0
    if args.output:
        parser.error('--output is only for --replay')
    if not args.live:
        print(json.dumps(preview(args.config), indent=2))
        print('Preview only: no requests or writes. Full-inventory collection is a separate scope from the old panel.')
        return 0
    if not args.config_sha256:
        parser.error('--live requires --config-sha256 from the reviewed preview')
    report = collect_catalog(args.config, expected_sha256=args.config_sha256)
    print(json.dumps({key:report.get(key) for key in ['capture_directory','status','requests',
        'discovery_complete','primary_queries_complete','primary_observed_vins','primary_scope_reconciled',
        'opening_count_residual','closing_count_residual','geographic_membership_stable','failure_type']}, indent=2))
    print('Reconciled export:', export_catalog(report['capture_directory'],
        output=Path(report['capture_directory'])/'analysis'))
    return 0 if (report['status'] == 'collection_finished' and report.get('primary_scope_reconciled')
                 and report.get('geographic_membership_stable')) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('BLOCKED:', error)
        sys.exit(1)
