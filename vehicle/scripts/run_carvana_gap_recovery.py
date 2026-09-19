"""Preview, explicitly collect, or independently replay the fixed gap recovery."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from vehicle_tracker.gap_recovery import collect_recovery, export_recovery, preview


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT/'config/carvana_gap_recovery_20260919.json')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--live', action='store_true')
    action.add_argument('--replay', type=Path)
    parser.add_argument('--config-sha256')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.replay:
        if not args.output:
            parser.error('--replay requires a fresh --output')
        print(export_recovery(args.replay, output=args.output))
        return 0
    if args.output:
        parser.error('--output requires --replay')
    if not args.live:
        print(json.dumps(preview(args.config), indent=2))
        return 0
    if not args.config_sha256:
        parser.error('--live requires the exact reviewed --config-sha256')
    report = collect_recovery(args.config, expected_sha256=args.config_sha256)
    print('Retained recovery:', report['capture_directory'], report['status'])
    output = export_recovery(report['capture_directory'], output=Path(report['capture_directory'])/'analysis')
    print('Reconciled export:', output)
    return 0 if json.loads((output/'summary.json').read_text())['recovery_contexts_complete'] else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('BLOCKED:', error)
        sys.exit(1)
