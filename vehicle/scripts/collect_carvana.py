"""Preview or run an explicit, capped Carvana query plan in a NEW experiment folder."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from vehicle_tracker.collect import NavigationBudget, collect_pages


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=ROOT / 'config/carvana_pilot_queries.json')
    parser.add_argument('--experiment', default=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    parser.add_argument('--max-requests', type=int, default=30)
    parser.add_argument('--max-seconds', type=float, default=600)
    parser.add_argument('--pause-seconds', type=float, default=3)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.experiment):
        parser.error('Use a plain, new experiment folder name')
    destination = ROOT / 'data/experiments' / args.experiment
    plan = json.loads(args.plan.read_text(encoding='utf-8'))
    queries = plan['queries']
    names = [q['query_id'] for q in queries]
    if not queries or len(names) != len(set(names)):
        parser.error('Plan requires unique, explicit query IDs')
    # Validate all controls before any browser or directory is created.
    NavigationBudget(args.max_requests, args.max_seconds, args.pause_seconds)
    for query in queries:
        if not re.fullmatch(r'\d{5}', query['zip_code']) or not 1 <= query['max_pages'] <= 20:
            parser.error('Each query needs a five-digit ZIP and 1-20 page cap')
        if not query['url'].startswith('https://www.carvana.com/cars/'):
            parser.error('Use a verified public filtered Carvana URL')
    print(pd.DataFrame(queries).to_string(index=False))
    print(f'Limits: {args.max_requests} navigations; {args.max_seconds:g}s; spacing {args.pause_seconds:g}s')
    print('New destination:', destination)
    if not args.live:
        print('Dry run: no browser, directory or database opened. National coverage remains unverified.')
        return 0
    if destination.exists():
        parser.error('Experiment already exists; choose a new name to preserve all retained work')
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        parser.error('Missing optional dependency: playwright>=1.50,<2. Installed Chrome is also required.')
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'query_plan.json').write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')
    reports = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=False)
        budget = NavigationBudget(args.max_requests, args.max_seconds, args.pause_seconds)
        try:
            for query in queries:
                if budget.stopped or budget.requests >= budget.max_requests:
                    reports.append(dict(query_id=query['query_id'], query_complete=False,
                                        reason='Not attempted: shared access/request budget stopped'))
                    continue
                # Each query gets an isolated ZIP/session context; one SQLite writer.
                context = browser.new_context()
                try:
                    attempts = collect_pages(context.new_page(), source_url=query['url'],
                        zip_code=query['zip_code'], max_pages=query['max_pages'], budget=budget,
                        database=destination / 'vehicle.sqlite', raw_directory=destination / 'raw')
                    report = dict(query_id=query['query_id'], **attempts.attrs['coverage'],
                                  pages=attempts.to_dict('records'))
                    reports.append(report)
                finally:
                    context.close()
                (destination / 'run_report.json').write_text(json.dumps(reports, indent=2) + '\n', encoding='utf-8')
        finally:
            browser.close()
    (destination / 'run_report.json').write_text(json.dumps(reports, indent=2) + '\n', encoding='utf-8')
    print(pd.DataFrame(reports).drop(columns='pages', errors='ignore').to_string(index=False))
    print('Top-level navigations:', budget.requests, '; no paid solver calls')
    return 0 if all(r['query_complete'] for r in reports) else 1


if __name__ == '__main__':
    sys.exit(main())
