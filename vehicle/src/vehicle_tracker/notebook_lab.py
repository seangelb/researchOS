"""Notebook teaching actions and read-only retained-evidence verification.

Constructing a session, previewing, and loading evidence do not collect or write.
The notebook retains one LabSession across cell reruns. A kernel reset loses this
in-memory allowance; it is a teaching guard, not a durable operating budget.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

import pandas as pd

from vehicle_tracker.collect import CollectionStopped, NavigationBudget
from vehicle_tracker.history import read_history, read_query_evidence
from vehicle_tracker.search import ENDPOINT, build_search_request, collect_search
from vehicle_tracker.search_evidence import verify_response_evidence
from vehicle_tracker.storage import read_snapshots


def search_request(*, make, model, year, zip_code, location_filter=False):
    """Build the exact first-page payload supported by the existing collector."""
    if not isinstance(make, str) or not make.strip() or not isinstance(model, str) or not model.strip():
        raise ValueError('Choose a make and parent model, for example Chevrolet / Tahoe.')
    if type(year) is not int or not 1900 <= year <= 2100:
        raise ValueError('Choose one integer model year.')
    if not isinstance(zip_code, str) or not re.fullmatch(r'[0-9]{5}', zip_code):
        raise ValueError('ZIP must be a five-digit string, including any leading zero.')
    if type(location_filter) is not bool:
        raise ValueError('location_filter must be True or False.')
    filters = {'makes': [{'name': make.strip(), 'parentModels': [{'name': model.strip()}]}],
               'year': {'min': year, 'max': year}}
    return build_search_request(filters=filters, zip_code=zip_code, location_filter=location_filter)


def _assert_retained_rows(actual, expected, keys, *, label):
    """Compare every expected field, including absent columns and missing values."""
    try:
        actual = actual[expected.columns].sort_values(keys).reset_index(drop=True)
        expected = expected.sort_values(keys).reset_index(drop=True)
        pd.testing.assert_frame_equal(
            actual.astype(object).where(actual.notna(), None),
            expected.astype(object).where(expected.notna(), None),
            check_dtype=False, check_exact=True)
    except (AssertionError, KeyError) as exc:
        raise ValueError(f'{label} differ from retained evidence; inspect before analysis.') from exc


def load_verified_history(database):
    """Reconcile the intraday history to every retained report before returning it.

    The stored parser hash and import clock describe the original import. Current
    replay must preserve its data values; this read never changes that provenance.
    """
    runs, captures, rows = read_history(database)
    if any('run_id' not in frame for frame in (runs, captures, rows)):
        raise ValueError('Stored history is missing its query-run identity column.')
    for stored in runs.itertuples():
        run, source_captures, source_rows = read_query_evidence(stored.report_path)
        expected_run = {key: value for key, value in run.items()
                        if key not in {'normalizer_sha256', 'report_path'}}
        for actual, expected, keys, label in [
                (runs, pd.DataFrame([expected_run]), ['run_id'], 'Stored query run'),
                (captures, source_captures, ['capture_id'], 'Stored history captures'),
                (rows, source_rows, ['capture_id', 'retailer', 'listing_id'], 'Stored history rows')]:
            _assert_retained_rows(actual.loc[actual.run_id.eq(stored.run_id)], expected, keys, label=label)
    if (not captures.run_id.isin(runs.run_id).all()
            or not rows.run_id.isin(runs.run_id).all()):
        raise ValueError('Stored history contains rows without a retained query run.')
    return runs, captures, rows


class _OneRequestBudget:
    """Add a one-action ceiling; all timing, counting and stops use the shared budget."""
    def __init__(self, shared):
        self.shared, self.starting_requests = shared, shared.requests

    def __getattr__(self, name):
        return getattr(self.shared, name)

    def before_navigation(self):
        if self.shared.requests != self.starting_requests:
            raise CollectionStopped('Notebook lab action permits only one attempted request')
        self.shared.before_navigation()


class LabSession:
    """Three attempted requests, shared pacing, no restart after a collection failure."""
    def __init__(self, vehicle_root):
        self.root = Path(vehicle_root).resolve()
        self.directory = self.root / 'data/experiments/carvana_notebook_lab'
        self.session_id = datetime.now(timezone.utc).strftime('%Y%m%d') + '_' + uuid4().hex[:8]
        self.budget = NavigationBudget(max_requests=3, max_seconds=3600, pause_seconds=3)
        self.last_destination = None

    @property
    def remaining(self):
        return max(0, 3 - self.budget.requests)

    def preview(self, **settings):
        """Read-only proposal. The live function shows a fresh, final preview too."""
        request = search_request(**settings)
        # Keep paths short enough for Windows when content-hash filenames are added.
        destination = self.directory / (self.session_id + '_' + uuid4().hex[:6])
        # Do not let a redirected lab directory write into an operating destination.
        if self.directory.resolve() != self.directory or not destination.resolve().is_relative_to(self.directory):
            raise ValueError('Notebook lab destination must stay inside its isolated experiment area.')
        if destination.exists():
            raise FileExistsError(destination)
        return dict(endpoint=ENDPOINT, method='POST', request=request,
                    location_filter=settings.get('location_filter', False), request_limit=1,
                    destination=str(destination), teaching_attempts_used=self.budget.requests,
                    teaching_attempts_remaining=self.remaining, teaching_session_stopped=self.budget.stopped)

    def fetch_one_page(self, *, confirm=None, post=None, **settings):
        """Prompt freshly, then use collect_search once; return its saved report path.

        ``confirm`` and ``post`` are injection points for offline tests. In Jupyter
        omit both: input() asks for a fresh token and the existing transport sends.
        No affirmative setting or saved token grants permission to later actions.
        """
        if not self.remaining or self.budget.stopped:
            raise CollectionStopped('Teaching session exhausted or stopped; inspect the previous outcome.')
        self.budget.timeout_ms()  # The existing one-hour session limit also applies.
        preview = self.preview(**settings)
        token = 'FETCH ' + uuid4().hex[:8]
        prompt = ('One live inventory request; no pagination or network retries.\n'
                  + json.dumps(preview, indent=2)
                  + f'\nType {token} to make this exact request; anything else cancels: ')
        if (confirm or input)(prompt).strip() != token:
            print('Cancelled. No request or files created; teaching budget unchanged.')
            return None
        request = preview['request']
        self.last_destination = Path(preview['destination'])
        try:
            # A target of one VIN stops after the whole first response, retaining
            # all its rows. VIN mode also rejects a short/empty nonzero-total page.
            # The budget adapter separately prevents any second transport attempt.
            report = collect_search(filters=request['filters'], zip_code=request['zip5'],
                location_filter=preview['location_filter'], destination=self.last_destination,
                target_vins=1, budget=_OneRequestBudget(self.budget), post=post)
        except BaseException:
            self.budget.stop()  # Includes a failure publishing the first checkpoint.
            raise
        print(f"{report['status']}: {report['unique_vins']} VINs; "
              f"{self.remaining} teaching attempts remain. {report['reason']}")
        print('Retained report:', self.last_destination / 'run_report.json')
        return self.last_destination / 'run_report.json'


def load_capture(report_path, *, as_of=None):
    """Replay retained evidence and reconcile admitted rows through read-only SQLite.

    A blocked action remains blocked. Its diagnostic pages are visible but its rows
    are not admitted for calculations. Missing/tampered evidence raises an error.
    """
    report_path = Path(report_path).resolve()
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if as_of is not None:
        cutoff = pd.Timestamp(as_of)
        if pd.isna(cutoff) or cutoff.tzinfo is None:
            raise ValueError('Analysis cutoff must be a timestamp with a timezone.')
        clocks = [report['ended_utc']] + [p.get('evidence_available_at_utc') for p in report['pages']]
        if any(pd.Timestamp(t) > cutoff for t in clocks if t):
            raise ValueError('Selected evidence was not yet available at the analysis cutoff.')
    run, captures, rows = read_query_evidence(report_path, diagnostic=True)
    database = report_path.parent / 'vehicle.sqlite'
    if report['status'] == 'blocked':
        rows = rows.iloc[:0].copy()
        database_status = 'Blocked action: no rows admitted for analysis'
    else:
        stored_captures, stored = read_snapshots(database)
        # The collector stores these base-parser fields; native extensions remain
        # in the retained source and history reader, not this older SQLite schema.
        observation_columns = ['run_id', 'page_number', 'retailer', 'listing_id', 'vin',
            'observed_at_utc', 'year', 'make', 'model', 'mileage_miles', 'asking_price_usd',
            'condition_native', 'availability_native', 'card_text', 'listing_url', 'source_url']
        expected_rows = rows.assign(page_number=rows.capture_id.map(captures.set_index('capture_id').page))
        # A validated empty query has a capture but no vehicle_observations table.
        if rows.empty and stored.empty and len(stored.columns) == 0:
            stored = pd.DataFrame(columns=observation_columns)
        _assert_retained_rows(stored, expected_rows[observation_columns],
                              ['run_id', 'page_number', 'retailer', 'listing_id'], label='Saved SQLite rows')
        expected_captures = []
        for capture in captures.itertuples():
            source_path = Path(capture.source_path).resolve()
            source = json.loads(source_path.read_text(encoding='utf-8'))
            expected_captures.append(dict(run_id=capture.run_id, page_number=capture.page,
                observed_at_utc=source.get('captured_at_utc'), source_url=source.get('page_url'),
                zip_code=source.get('zip_code'), reported_total_text=source.get('reported_total_text'),
                status=capture.status, row_count=capture.row_count, error=capture.error,
                raw_file=str(source_path), source_sha256=capture.capture_id, coverage='unverified'))
        _assert_retained_rows(stored_captures, pd.DataFrame(expected_captures),
                              ['run_id', 'page_number'], label='Saved SQLite captures')
        database_status = 'Retained rows and read-only SQLite agree'
    page_rows = []
    for page in report['pages']:
        evidence = page.get('response_evidence')
        source = verify_response_evidence(evidence) if evidence else None
        inventory = source.get('inventory') if isinstance(source, dict) else None
        vehicles = inventory.get('vehicles') if isinstance(inventory, dict) else None
        pagination = inventory.get('pagination') if isinstance(inventory, dict) else None
        page_rows.append(dict(page=page['page'], http_status=page.get('http_status'),
            elapsed_seconds=page.get('elapsed_seconds'), returned_rows=len(vehicles) if isinstance(vehicles, list) else None,
            stored_rows=page['stored_rows'], source_pagination=pagination,
            outcome=page['outcome_kind'], observed_at=page.get('response_received_at_utc'),
            source_kind=(evidence or {}).get('kind'), selected_source_path=(evidence or {}).get('source_path'),
            projection_path=page.get('retained_source'), error=page.get('error')))
    page_rows = pd.DataFrame(page_rows)
    summary = pd.DataFrame([dict(status=report['status'], reason=report['reason'],
        query_complete=report['query_complete'], reported_query_total=report.get('reported_total'),
        retained_pages=len(report['pages']), requests=report['requests'],
        elapsed_seconds=report['elapsed_seconds'], admitted_rows=len(rows), distinct_vins=rows.vin.nunique(),
        observation_start=run['observation_start'], observation_end=run['observation_end'],
        analysis_cutoff=as_of, report_path=str(report_path), database_path=str(database),
        database_validation=database_status)])
    return dict(report=report, run=run, captures=captures, rows=rows, pages=page_rows, summary=summary)


def load_comparison(before_path, after_path, *, as_of=None):
    """Select two separate, chronological captures of the same query; do no join."""
    before, after = load_capture(before_path, as_of=as_of), load_capture(after_path, as_of=as_of)
    if before['report']['run_id'] == after['report']['run_id']:
        raise ValueError('Choose two separately collected results, not the same saved observation twice.')
    if any(x['report']['status'] == 'blocked' for x in (before, after)):
        raise ValueError('A blocked capture cannot support this comparison.')
    if before['run']['context_json'] != after['run']['context_json']:
        raise ValueError('Comparison requires the same make/model/year, ZIP, location setting and sort.')
    if pd.Timestamp(after['run']['observation_start']) <= pd.Timestamp(before['run']['observation_end']):
        raise ValueError('Choose a later, non-overlapping observation as the second result.')
    return before, after
