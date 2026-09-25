"""Export catalog-day sales tables from selected retained folders; never collect.

Reads explicitly selected catalog analysis directories and optional saved listing
checks. Writes flows, exit episodes, the follow-up queue and the sampled sold-exit
estimate into a new destination. Makes no requests and writes nothing into captures.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from vehicle_tracker.catalog_history import read_catalog_history, unassessable_cells_from_days
from vehicle_tracker.sales_proxy import (disappearance_events, inventory_exit_episodes,
                                         inventory_flows, inventory_followup_queue,
                                         sampled_exit_estimate)


def _csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows.to_csv(path, index=False)


def _load_optional(path):
    if path is None:
        return pd.DataFrame()
    return pd.read_csv(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog-export', type=Path, action='append', required=True,
                        help='Selected catalog analysis folder; repeat for each day')
    parser.add_argument('--native-records', type=Path, help='Optional saved listing-check CSV')
    parser.add_argument('--selection-plans', type=Path, help='Optional prior-selection CSV')
    parser.add_argument('--as-of', required=True, help='Timezone-aware evidence cutoff')
    parser.add_argument('--destination', required=True, type=Path, help='Must not exist')
    args = parser.parse_args(argv)
    destination = args.destination.resolve()
    if destination.exists():
        raise ValueError('Use a new export directory; existing evidence is never replaced')
    folders = [path.resolve() for path in args.catalog_export]
    days, rows = [], []
    for folder in folders:
        selected_days, selected_rows = read_catalog_history(folder, as_of=args.as_of)
        days.append(selected_days)
        rows.append(selected_rows)
    cycles = pd.concat(days, ignore_index=True) if days else pd.DataFrame()
    observations = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    cells = unassessable_cells_from_days(cycles)
    records = _load_optional(args.native_records)
    plans = _load_optional(args.selection_plans)
    flows = inventory_flows(cycles, observations, as_of=args.as_of)
    exits = inventory_exit_episodes(cycles, observations, as_of=args.as_of,
                                    unassessable_cells=cells)
    absences, _ = disappearance_events(cycles, observations, as_of=args.as_of,
                                       unassessable_cells=cells)
    queue = inventory_followup_queue(cycles, observations, records, [], as_of=args.as_of,
                                     selection_plans=None if plans.empty else plans,
                                     unassessable_cells=cells)
    estimate = sampled_exit_estimate(absences, queue, records, as_of=args.as_of)
    destination.mkdir(parents=True)
    _csv(destination / 'inventory_flows.csv', flows)
    _csv(destination / 'exit_episodes.csv', exits)
    _csv(destination / 'disappearance_events.csv', absences)
    _csv(destination / 'followup_queue.csv', queue)
    _csv(destination / 'sampled_exit_estimate.csv', estimate)
    manifest = dict(
        created_at=datetime.now(timezone.utc).isoformat(),
        as_of=args.as_of,
        catalog_exports=[str(folder) for folder in folders],
        native_records=None if args.native_records is None else str(args.native_records.resolve()),
        selection_plans=None if args.selection_plans is None else str(args.selection_plans.resolve()),
        rows=dict(inventory_flows=len(flows), exit_episodes=len(exits),
                  disappearance_events=len(absences), followup_queue=len(queue),
                  sampled_exit_estimate=len(estimate)),
        interpretation=('Sold exits among detected catalog exits; not reported '
                        'transactions. Daily estimated_sales stays empty.'))
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True),
                                               encoding='utf-8')
    return destination


if __name__ == '__main__':
    main()
