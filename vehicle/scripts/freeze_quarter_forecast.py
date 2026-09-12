"""Preview an analyst forecast; --save records a new timestamped local vintage.

This does not build a national-sales model or fetch company results. Retain every
version, including misses, and evaluate separately in Notebook 30 after reporting.
Prospective scoring requires a save strictly before the first public quarterly
result; a later reported-result revision never extends that forecast deadline.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from vehicle_tracker.expectations import _validate_forecast, freeze_forecast


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Explicit analyst forecast JSON')
    parser.add_argument('--sources', type=Path, nargs='+', required=True, help='Retained evidence and model/assumption files')
    parser.add_argument('--destination', type=Path, required=True, help='New forecast JSON file; never overwritten')
    parser.add_argument('--save', action='store_true', help='Explicit local save with the current clock')
    args = parser.parse_args()
    record = json.loads(args.input.read_text(encoding='utf-8'))
    _validate_forecast(record, saved_at=datetime.now(timezone.utc).isoformat())
    if args.destination.exists() or any(not path.is_file() for path in args.sources):
        parser.error('Use a new destination and existing source files')
    print(json.dumps(record, indent=2))
    if args.save:
        saved = freeze_forecast(record, destination=args.destination, source_paths=[args.input, *args.sources])
        print('Saved forecast:', args.destination, '| current clock:', saved['saved_at'])
    else:
        print('Preview only. No files, estimates or company results created.')


if __name__ == '__main__':
    main()
