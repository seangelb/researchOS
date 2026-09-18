"""Preview a frozen facet-only experiment; --live requires its exact selected hash."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from vehicle_tracker.facets import collect_facets, digest, preview_facets, replay_facets


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=ROOT/'config/carvana_facets_proposed_20260918.json')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--plan-sha256', help='Exact previously reviewed plan hash; does not grant user approval')
    parser.add_argument('--replay', type=Path, help='Read one existing facet experiment, without collecting')
    parser.add_argument('--as-of', help='Required timezone-aware cutoff for replay')
    parser.add_argument('--export', type=Path, help='Replay only: explicitly save tables into a fresh directory')
    args = parser.parse_args(argv)
    if args.replay:
        if args.live or args.plan_sha256 or not args.as_of:
            parser.error('Replay requires --as-of and cannot collect')
        inputs = {str(p.resolve()):digest(p) for p in args.replay.rglob('*.json')}
        code = {str(p.resolve()):digest(p) for p in [Path(__file__), *sorted((ROOT/'src/vehicle_tracker').glob('*.py'))]}
        table = replay_facets(args.replay, as_of=args.as_of)
        if any(digest(Path(p)) != h for p,h in inputs.items()):
            raise ValueError('Facet inputs changed during replay')
        summary = dict(planned_requests=len(table), statuses=table.status.value_counts().to_dict(),
                       inventory_complete=False, national_coverage_verified=False)
        if args.export:
            destination = args.export.resolve()
            # Every retained input is fingerprinted; no database or inventory import.
            destination.mkdir(parents=True, exist_ok=False)
            table.to_csv(destination/'facet_coverage.csv', index=False, lineterminator='\n')
            manifest = dict(as_of=args.as_of, generated_at=datetime.now(timezone.utc).isoformat(),
                input_hashes=inputs, source_hashes=code, summary=summary,
                output_hashes={'facet_coverage.csv':digest(destination/'facet_coverage.csv')})
            if any(digest(Path(p)) != h for p,h in {**inputs,**code}.items()):
                raise ValueError('Facet inputs or code changed during export; do not use this export')
            (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n', encoding='utf-8')
            summary['export'] = str(destination)
        print(json.dumps(summary, indent=2))
        return 0
    if args.export or args.as_of:
        parser.error('--export/--as-of require --replay')
    preview, _ = preview_facets(args.plan)
    print(json.dumps({k:v for k,v in preview.items() if k!='config'}, indent=2))
    if not args.live:
        print('Preview only: no requests or writes. Daily limits and scheduled work are unchanged.')
        return 0
    if not args.plan_sha256:
        parser.error('--live requires --plan-sha256 and prior user authorization for that exact scope')
    report = collect_facets(args.plan, expected_sha256=args.plan_sha256)
    print(json.dumps({k:report[k] for k in ['status','requests','inventory_complete']}, indent=2))
    return 0 if report['status']=='retained_all_facets' else 1


if __name__ == '__main__':
    sys.exit(main())
