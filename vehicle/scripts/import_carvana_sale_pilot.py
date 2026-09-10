"""Preview/import 1-12 browser projections into a new experimental run; no network."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from vehicle_tracker.sale_pilot import FORMAT, NATIVE, parse_capture, validate_cohort
from vehicle_tracker.events import _aware


def import_captures(paths, cohort, *, root, now, save=False):
    """Validate first, preview by default, and write only to a fresh experiment folder."""
    vehicles = {(v['retailer'], v['vin']): v for v in validate_cohort(cohort)}
    if _aware(now) < _aware(cohort['selected_at']):
        raise ValueError('The cohort must be selected before import')
    if not 1 <= len(paths) <= 12:
        raise ValueError('Import 1-12 captures per pass')
    entries, blobs, rows = [], [], []
    permitted = {'format', 'expected', 'requested_url', 'final_url', 'checked_at', 'access_outcome',
                 'contexts', 'hero_text', 'hero_badge', 'purchase_button', 'note'}
    for path in paths:
        data = Path(path).read_bytes()
        capture = json.loads(data)
        if capture.get('format') != FORMAT or set(capture) - permitted:
            raise ValueError('Import only the documented public projection format')
        expected = capture['expected']
        if (set(expected) != {'retailer', 'vin', 'listing_id'}
                or (expected['retailer'], expected['vin']) not in vehicles
                or not re.fullmatch(r'\d+', expected['listing_id'])):
            raise ValueError('Expected retailer/VIN must belong to the frozen cohort')
        for context in capture.get('contexts', []):
            if (set(context) - {'source_path', 'forVehicleContext'}
                    or set(context.get('forVehicleContext', {})) != {'vehicleDetails'}
                    or set(context['forVehicleContext']['vehicleDetails']) - set(NATIVE)
                    or any(isinstance(value, (dict, list)) for value in context['forVehicleContext']['vehicleDetails'].values())):
                raise ValueError('Capture contains fields outside the public vehicle allowlist')
        row = parse_capture(capture, expected=expected, available_at=now, source=str(path))
        rows.append(row)
        blobs.append(data)
        entries.append(dict(file=f'capture_{len(entries)+1:02d}.json', expected=expected,
            sha256=hashlib.sha256(data).hexdigest(), input_source=str(Path(path).resolve())))
    if len({(r['retailer'], r['vin']) for r in rows}) != len(rows):
        raise ValueError('One visit per cohort VIN per import pass')
    if not save:
        return None, rows
    folder = Path(root) / 'data/experiments/carvana_sale_signals' / (
        'pilot_' + datetime.fromisoformat(now.replace('Z', '+00:00')).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    for entry, data in zip(entries, blobs):
        (folder / entry['file']).write_bytes(data)
    manifest = dict(cohort_id=cohort['cohort_id'], cohort=cohort, available_at=now, captures=entries,
        method='browser-assisted public DOM/RSC projection; manual transfer/import',
        note='No inventory, canonical checks, reviews or transaction counts written.')
    # Manifest is written last; incomplete imports are not discovered by the reader.
    (folder / 'run.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return folder, rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, nargs='+', required=True)
    parser.add_argument('--cohort', type=Path, default=ROOT / 'config/carvana_sale_pilot.json')
    parser.add_argument('--save', action='store_true', help='Save a NEW experiment run after validation')
    args = parser.parse_args(argv)
    print('Experimental destination:', ROOT / 'data/experiments/carvana_sale_signals')
    cohort = json.loads(args.cohort.read_text(encoding='utf-8'))
    folder, rows = import_captures(args.input, cohort, root=ROOT,
        now=datetime.now(timezone.utc).isoformat(), save=args.save)
    for row in rows:
        print(row['listing_id'], row['vin'], row['saleStatus'], row['purchaseType'],
              row['observed_status'], row['parse_outcome'])
    print('Saved: ' + str(folder) if folder else 'Preview only. Add --save to retain this experimental evidence.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
