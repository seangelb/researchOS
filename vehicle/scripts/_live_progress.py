"""Read-only progress summary for an in-flight catalog capture. No requests.

Prefer `python vehicle/scripts/run_carvana_full_inventory.py --status <folder>`.
"""
import json
from pathlib import Path
import sqlite3
import sys

FOLDER = Path(sys.argv[1])


def tables(connection):
    return [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]


def main():
    report = json.loads((FOLDER/'catalog_report.json').read_text(encoding='utf-8'))
    budget = json.loads((FOLDER/'catalog_budget.json').read_text(encoding='utf-8'))['budget']
    vins, rows, names = set(), 0, None
    for database in FOLDER.glob('*/vehicle.sqlite'):
        with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as connection:
            names = names or tables(connection)
            table = next((name for name in ('vehicle_observations', 'observations') if name in names), None)
            if table is None:
                continue
            try:
                for (vin,) in connection.execute(f'SELECT vin FROM {table}'):
                    rows += 1
                    vins.add(vin)
            except sqlite3.Error:
                continue
    entries = report['entries']
    attempted = [entry for entry in entries if entry.get('report')]
    print('tables            :', names)
    ceiling = (report.get('feasibility') or {}).get('request_ceiling') or 7000
    print('status            :', report['status'], '| requests', report['requests'], '/', ceiling)
    print('budget stopped    :', budget['stopped'], '| pending', budget['pending_request'])
    print('opening total     :', report.get('opening_total'), '| page floor', report.get('inventory_payload_page_floor'))
    print('year contexts     :', len(report.get('year_discoveries', [])), '/', len(report.get('planned_year_probes', [])))
    print('make probes       :', len(report.get('planned_make_probes', [])), 'planned |',
          sum(1 for e in entries if e['role'] == 'make_discovery' and e.get('report')), 'attempted')
    print('leaf queries      :', len(report['leaf_queries']), 'frozen |',
          sum(1 for e in entries if e['role'] == 'primary_inventory' and e.get('report')), 'enumerated')
    print('leaf plan frozen  :', report.get('leaf_plan_frozen'), '| feasibility blocked', report.get('feasibility_blocked'))
    print('observation rows  :', rows)
    print('distinct VINs     :', len(vins))
    statuses = {}
    for entry in attempted:
        statuses[entry['status']] = statuses.get(entry['status'], 0) + 1
    print('attempted status  :', statuses)
    contexts = {}
    for entry in attempted:
        key = entry.get('context_status', 'unrecorded')
        contexts[key] = contexts.get(key, 0) + 1
    print('context status    :', contexts)
    failures = [entry for entry in attempted if entry.get('outcome_kind') not in (None, 'success')]
    print('non-success leaves:', len(failures))
    for entry in failures[:10]:
        print('   ', entry['query']['query_id'], entry.get('outcome_kind'), entry.get('context_status'))
    if report.get('feasibility'):
        print('feasibility       :', json.dumps(report['feasibility'], indent=2))


if __name__ == '__main__':
    main()
