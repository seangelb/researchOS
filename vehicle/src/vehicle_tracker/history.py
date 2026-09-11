"""Retained search evidence -> a separate SQLite history and explicit comparisons."""
import hashlib
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pandas as pd

from vehicle_tracker.carvana import NATIVE_FIELDS, native_values, parse_capture

OBSERVATION_COLUMNS = ['retailer', 'listing_id', 'vin', 'observed_at_utc', 'year', 'make', 'model',
    'mileage_miles', 'asking_price_usd', 'condition_native', 'availability_native', 'card_text',
    'listing_url', 'source_url', *NATIVE_FIELDS, 'capture_id', 'run_id']
SCHEMA = '''
CREATE TABLE IF NOT EXISTS query_runs (
 run_id TEXT PRIMARY KEY, report_path TEXT, report_sha256 TEXT, context_json TEXT,
 query_complete INTEGER, coverage_reason TEXT, reported_total INTEGER, stored_rows INTEGER,
 observation_start TEXT, observation_end TEXT, invocation_start TEXT, invocation_end TEXT,
 normalizer_sha256 TEXT, original_normalizer_sha256 TEXT, imported_at_utc TEXT);
CREATE TABLE IF NOT EXISTS captures (
 capture_id TEXT PRIMARY KEY, run_id TEXT REFERENCES query_runs(run_id), page INTEGER,
 observed_at_utc TEXT, status TEXT, row_count INTEGER, error TEXT, source_path TEXT,
 evidence_available_at_utc TEXT);
CREATE TABLE IF NOT EXISTS observations (
 retailer TEXT, listing_id TEXT, vin TEXT, observed_at_utc TEXT, year INTEGER, make TEXT, model TEXT,
 mileage_miles INTEGER, asking_price_usd REAL, condition_native TEXT, availability_native TEXT,
 card_text TEXT, listing_url TEXT, source_url TEXT, parent_model TEXT, purchase_pending INTEGER,
 vehicle_lock_type INTEGER, purchase_type TEXT, inventory_type INTEGER, on_demand INTEGER,
 transport_cost_usd REAL, capture_id TEXT REFERENCES captures(capture_id), run_id TEXT REFERENCES query_runs(run_id),
 PRIMARY KEY (capture_id, retailer, listing_id));
CREATE INDEX IF NOT EXISTS observations_identity ON observations(retailer, listing_id, observed_at_utc);
CREATE INDEX IF NOT EXISTS observations_run ON observations(run_id);
CREATE INDEX IF NOT EXISTS captures_run ON captures(run_id);
'''


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _verify_capture_metadata(page, capture):
    """A new attempt's report and retained capture must describe the same response."""
    if (not isinstance(page.get('attempt_id'), str) or not page['attempt_id']
            or page['attempt_id'] != capture.get('attempt_id')):
        raise ValueError('Attempt identity differs between query report and retained capture')
    evidence = capture.get('response_evidence')
    if page.get('response_evidence') != evidence:
        raise ValueError('Response evidence differs between query report and retained capture')
    if evidence is None:
        if (page['status'] == 'parsed' or page.get('response_received_at_utc') is not None
                or capture.get('captured_at_utc') is not None):
            raise ValueError('Recorded response lacks its response evidence binding')
        return  # Failed transport: no source observation clock can be inferred.
    for page_key, evidence_key in [('response_sha256', 'response_content_sha256'),
                                  ('response_bytes', 'response_content_bytes'),
                                  ('response_hash_scope', 'response_hash_scope')]:
        if page.get(page_key) != evidence.get(evidence_key):
            raise ValueError('Response metadata differs between query report and retained capture')
    try:
        received = pd.Timestamp(page.get('response_received_at_utc'))
        observed = pd.Timestamp(capture.get('captured_at_utc'))
        valid = (not pd.isna(received) and received.tzinfo is not None
                 and not pd.isna(observed) and observed.tzinfo is not None and received == observed)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError('Response clocks differ between query report and retained capture')


def _source_less_checkpoint(report_path, report, page):
    """Validate a journal checkpoint for diagnosis only; it admits no observations."""
    if (report.get('evidence_contract') != 'carvana-search-source-v1' or report['query_complete']
            or page is not report['pages'][-1]
            or page.get('page') != len(report['pages'])
            or page.get('status') != 'pending' or page.get('stored_rows') != 0
            or page.get('outcome_kind') not in {'unattempted', 'request_reserved'}
            or not isinstance(page.get('attempt_id'), str) or not page['attempt_id']
            or any(page.get(key) is not None for key in (
                'retained_source', 'source_sha256', 'http_status', 'response_received_at_utc',
                'response_evidence', 'response_sha256', 'response_bytes', 'evidence_available_at_utc'))):
        raise ValueError('Missing successful-response source or unsupported incomplete checkpoint')
    reserved = page.get('request_reserved_at_utc')
    started = page.get('request_started_at_utc')
    if page['outcome_kind'] == 'unattempted' and (reserved is not None or started is not None):
        raise ValueError('Unattempted checkpoint contains request clocks')
    if page['outcome_kind'] == 'request_reserved':
        clocks = [pd.Timestamp(value) for value in [reserved, started] if value is not None]
        if not reserved or any(pd.isna(value) or value.tzinfo is None for value in clocks) or clocks != sorted(clocks):
            raise ValueError('Reserved checkpoint lacks valid request clocks')
    journal = json.loads((report_path.parent / 'attempts' / f"{page['page']:04d}.json").read_text(encoding='utf-8'))
    request = dict(filters=report['filters'], pagination=dict(page=page['page'], pageSize=24),
                   sortBy='MostPopular', zip5=report['zip_code'])
    if report.get('location_filter', False):
        request['requestedFeatures'] = ['LocationBasedPrefiltering']
    if journal != dict(run_id=report['run_id'], request=request, **page):
        raise ValueError('Checkpoint journal differs from query run, attempt, page or context')
    return page['outcome_kind']


def read_query_evidence(report_path, *, diagnostic=False):
    """Verify source hashes and context; normalize only pages admitted by the collector.

    Failed/partial attempts remain in coverage. A claimed complete query must also
    reconcile to its retained pages, native totals, identities and actual timestamps.
    Diagnostic mode can inspect verified source-less checkpoints, with zero rows;
    the default import/recovery contract still rejects those incomplete checkpoints.
    """
    report_path = Path(report_path).resolve()
    report = json.loads(report_path.read_text(encoding='utf-8'))
    frames, captures, contexts, totals, page_ends, checkpoints = [], [], [], [], [], []
    for page in report['pages']:
        if not page.get('retained_source'):
            if diagnostic:
                checkpoints.append(_source_less_checkpoint(report_path, report, page))
                continue
            raise ValueError('Incomplete query checkpoint lacks retained source; review or recover before importing')
        source = Path(page['retained_source'])
        source_hash = file_hash(source)
        if source_hash != page['source_sha256']:
            raise ValueError('Retained source hash differs from the query report')
        capture = json.loads(source.read_text(encoding='utf-8'))
        if report.get('evidence_contract') == 'carvana-search-source-v1':
            _verify_capture_metadata(page, capture)
        if 'response_evidence' in capture:
            from vehicle_tracker.search_evidence import verify_response_evidence
            retained_response = verify_response_evidence(capture['response_evidence'])
            if page['status'] == 'parsed':
                from vehicle_tracker.search import project_response
                if (retained_response is None or capture['response_evidence'].get('redacted_values', 0)
                        or capture['response_evidence'].get('ambiguous_json', False)):
                    raise ValueError('Parsed page lacks replayable response evidence')
                replayed = project_response(retained_response, capture['request'],
                                            observed_at=capture['captured_at_utc'])
                if any(capture.get(key) != value for key, value in replayed.items()):
                    raise ValueError('Retained projection differs from response evidence replay')
        available_at = page.get('evidence_available_at_utc')
        if available_at is not None:
            available, observed = pd.Timestamp(available_at), pd.Timestamp(capture.get('captured_at_utc'))
            if (pd.isna(available) or available.tzinfo is None
                    or (pd.isna(observed) and page['status'] == 'parsed')
                    or (not pd.isna(observed) and (observed.tzinfo is None or available < observed))):
                raise ValueError('Evidence availability must follow its observation clock')
        captures.append(dict(capture_id=source_hash, run_id=report['run_id'], page=page['page'],
            observed_at_utc=capture.get('captured_at_utc'), status=page['status'],
            row_count=page['stored_rows'], error=page.get('error'), source_path=str(source),
            evidence_available_at_utc=available_at))
        if page['status'] != 'parsed':
            continue
        frame = parse_capture(capture)
        request = capture['request']
        location_filter = request.get('requestedFeatures', []) == ['LocationBasedPrefiltering']
        if request.get('requestedFeatures', []) not in ([], ['LocationBasedPrefiltering']):
            raise ValueError('Unsupported source features require review')
        if (request['filters'] != report['filters'] or request['zip5'] != report['zip_code']
                or location_filter != report.get('location_filter', False)):
            raise ValueError('Retained query context differs from its report')
        contexts.append(json.dumps(dict(endpoint=capture['endpoint'], filters=request['filters'],
            zip_code=request['zip5'], location_filter=location_filter, sort=request['sortBy']), sort_keys=True))
        if len(frame) != page['stored_rows'] or capture['pagination']['currentPage'] != page['page']:
            raise ValueError('Stored page count/index differs from retained evidence')
        totals.append(capture['pagination']['totalMatchedInventory'])
        page_ends.append(capture['pagination']['totalMatchedPages'])
        native = pd.DataFrame([native_values(vehicle) for vehicle in capture['vehicles']], columns=NATIVE_FIELDS)
        frames.append(pd.concat([frame.reset_index(drop=True), native], axis=1)
                      .assign(capture_id=source_hash, run_id=report['run_id']))
    observations = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=OBSERVATION_COLUMNS)
    capture_rows = pd.DataFrame(captures, columns=['capture_id','run_id','page','observed_at_utc','status','row_count','error','source_path','evidence_available_at_utc'])
    if len(set(contexts)) > 1 or len(observations) != report.get('unique_listings', len(observations)):
        raise ValueError('Query context or admitted row count changed')
    parsed = capture_rows[capture_rows.status.eq('parsed')]
    clocks = pd.to_datetime(parsed.observed_at_utc, utc=True, errors='coerce', format='ISO8601')
    identity_ok = (not observations[['retailer','listing_id']].duplicated().any()
                   and not observations.vin.isna().any() and not observations.vin.duplicated().any())
    complete = bool(report['query_complete'])
    if complete and (parsed.empty or len(parsed) != len(capture_rows) or not identity_ok
            or clocks.isna().any() or not clocks.is_monotonic_increasing or clocks.duplicated().any()
            or parsed.page.tolist() != list(range(1,len(parsed)+1))
            or len(set(totals)) != 1 or len(observations) != totals[0]
            or report['reported_total'] != totals[0] or len(parsed) != max(1,page_ends[-1])):
        raise ValueError('Claimed complete query does not reconcile to retained evidence')
    code = ''.join(file_hash(Path(__file__).parent/name)
                   for name in ('history.py', 'search.py', 'search_evidence.py', 'carvana.py'))
    run = dict(run_id=report['run_id'], report_path=str(report_path), report_sha256=file_hash(report_path),
        context_json=contexts[0] if contexts else None, query_complete=int(complete),
        coverage_reason=report.get('reason') or ('Complete retained query' if complete else 'Incomplete attempt'),
        reported_total=report.get('reported_total'), stored_rows=len(observations),
        observation_start=clocks.min().isoformat() if len(clocks) and clocks.notna().all() else None,
        observation_end=clocks.max().isoformat() if len(clocks) and clocks.notna().all() else None,
        invocation_start=report['started_utc'], invocation_end=report.get('ended_utc'),
        normalizer_sha256=hashlib.sha256(code.encode()).hexdigest(),
        original_normalizer_sha256=report.get('normalizer_code_sha256'))
    if diagnostic:
        run.update(unattempted_pages=checkpoints.count('unattempted'),
                   uncertain_pages=checkpoints.count('request_reserved'))
    return run, capture_rows, observations.reindex(columns=OBSERVATION_COLUMNS)


def import_reports(report_paths, database):
    """Replay first, then import one atomic batch; an identical import changes no rows.

    Retrying after an interrupted import checks the stored rows, not only report
    hashes. Conflicting evidence requires a separate reviewed analysis build.
    """
    evidence = [(Path(path), read_query_evidence(path)) for path in report_paths]
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with closing(sqlite3.connect(database)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables - {'query_runs','captures','observations'}:
            raise ValueError('Refusing an unrelated database')
        connection.execute('PRAGMA foreign_keys=ON')
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            for statement in SCHEMA.split(';'):
                if statement.strip():
                    connection.execute(statement)
            # Add only provenance fields on an explicit import. Historical values
            # remain NULL; neither observation nor prior import clocks are invented.
            for table, column in [('query_runs', 'imported_at_utc'), ('captures', 'evidence_available_at_utc')]:
                columns = {row[1] for row in connection.execute('PRAGMA table_info('+table+')')}
                if column not in columns:
                    connection.execute('ALTER TABLE '+table+' ADD COLUMN '+column+' TEXT')
            for report_path, (run, captures, observations) in evidence:
                prior = connection.execute('SELECT report_sha256 FROM query_runs WHERE run_id=?', (run['run_id'],)).fetchone()
                if prior:
                    if prior[0] != run['report_sha256']:
                        raise ValueError('Previously imported query report changed; use a new reviewed analysis build')
                    # Parser-code provenance records the original import. Replaying
                    # with today's parser must agree with those stored observations.
                    expected_run = {key: value for key, value in run.items()
                                    if key not in {'normalizer_sha256', 'report_path'}}
                    _verify_imported_rows(connection, 'query_runs', pd.DataFrame([expected_run]),
                                          run['run_id'], ['run_id'])
                    _verify_imported_rows(connection, 'captures', captures, run['run_id'], ['capture_id'])
                    _verify_imported_rows(connection, 'observations', observations,
                                          run['run_id'], ['capture_id', 'retailer', 'listing_id'])
                else:
                    imported_run = dict(run, imported_at_utc=datetime.now(timezone.utc).isoformat())
                    connection.execute('INSERT INTO query_runs ('+','.join(imported_run)+') VALUES ('+
                                       ','.join('?' for _ in imported_run)+')', tuple(imported_run.values()))
                    for table, frame in [('captures',captures),('observations',observations)]:
                        values = frame.astype(object).where(frame.notna(),None)
                        connection.executemany('INSERT INTO '+table+' ('+','.join(frame.columns)+') VALUES ('+','.join('?' for _ in frame.columns)+')',
                                               values.itertuples(index=False,name=None))
                results.append(dict(run_id=run['run_id'], imported_runs=int(not prior),
                                    imported_rows=0 if prior else len(observations), report_path=str(report_path)))
    return pd.DataFrame(results)


def _verify_imported_rows(connection, table, expected, run_id, keys):
    actual = pd.read_sql_query('SELECT * FROM '+table+' WHERE run_id=?', connection, params=[run_id])
    try:
        actual = actual[expected.columns].sort_values(keys).reset_index(drop=True)
        expected = expected.sort_values(keys).reset_index(drop=True)
        pd.testing.assert_frame_equal(actual.astype(object).where(actual.notna(), None),
            expected.astype(object).where(expected.notna(), None), check_dtype=False, check_exact=True)
    except (AssertionError, KeyError) as exc:
        raise ValueError('Previously imported '+table+' differ from retained evidence') from exc


def read_history(database, *, run_ids=None):
    """Return query attempts, captures and observations using a read-only connection."""
    connection = sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro', uri=True)
    try:
        selections = {}
        for table in ('query_runs', 'captures', 'observations'):
            columns = {row[1] for row in connection.execute('PRAGMA table_info('+table+')')}
            optional = {'query_runs': 'imported_at_utc', 'captures': 'evidence_available_at_utc'}.get(table)
            selections[table] = '*'+(', NULL AS '+optional if optional and optional not in columns else '')
        if run_ids is None:
            return tuple(pd.read_sql_query('SELECT '+selections[table]+' FROM '+table,connection)
                         for table in ('query_runs','captures','observations'))
        ids = list(dict.fromkeys(run_ids))
        chunks = [ids[start:start+500] for start in range(0,len(ids),500)] or [[]]
        return tuple(pd.concat([pd.read_sql_query('SELECT '+selections[table]+' FROM '+table+
            ' WHERE run_id IN ('+','.join('?' for _ in chunk)+')',connection,params=chunk)
            for chunk in chunks],ignore_index=True)
            for table in ('query_runs','captures','observations'))
    finally:
        connection.close()


def comparison_checks(runs, previous_ids, current_ids):
    """One visible row per coverage rule. False means absence comparisons are blocked."""
    previous = runs[runs.run_id.isin(previous_ids)]
    current = runs[runs.run_id.isin(current_ids)]
    known = (len(previous_ids)==len(set(previous_ids))==len(previous)>0
             and len(current_ids)==len(set(current_ids))==len(current)>0)
    start = pd.to_datetime(current.observation_start,utc=True,errors='coerce')
    end = pd.to_datetime(previous.observation_end,utc=True,errors='coerce')
    previous_start = pd.to_datetime(previous.observation_start,utc=True,errors='coerce')
    current_end = pd.to_datetime(current.observation_end,utc=True,errors='coerce')
    checks = [
        ('selection',known,'Choose nonempty, known, unique query-run IDs'),
        ('complete',known and previous.query_complete.eq(1).all() and current.query_complete.eq(1).all(), 'All requested queries must be complete'),
        ('context',known and previous.context_json.notna().all() and current.context_json.notna().all()
            and previous.context_json.is_unique and current.context_json.is_unique
            and set(previous.context_json)==set(current.context_json),'Filters, ZIP, location setting and sort must match without missing partitions'),
        ('fresh_intervals',known and not set(previous_ids)&set(current_ids) and start.notna().all()
            and end.notna().all() and previous_start.notna().all() and current_end.notna().all()
            and previous_start.le(end).all() and start.le(current_end).all()
            and start.min()>end.max(),'Actual observation windows must be valid, ordered and non-overlapping; reused captures are not fresh')]
    return pd.DataFrame([dict(check=name,passed=bool(ok),reason='OK' if ok else reason) for name,ok,reason in checks])


def classify_changes(previous_observations, current_observations, *, seen_before=()):
    """Outer join unique listing observations after the caller's coverage gate.

    Duplicate ZIP/query memberships must be resolved by explicit scope selection,
    never by choosing a newest conflicting price. Absence is not a sale.
    """
    keys = ['retailer','listing_id']
    if any(frame.duplicated(keys).any() for frame in (previous_observations,current_observations)):
        raise ValueError('Duplicate period identities: select a non-overlapping scope before comparison')
    if any(frame[keys+['vin']].isna().any().any() or frame.duplicated(['retailer','vin']).any()
           for frame in (previous_observations,current_observations)):
        raise ValueError('Missing identities or VIN aliases require review before absence classification')
    changes = previous_observations.merge(current_observations,on=keys,how='outer',
        suffixes=('_before','_after'),indicator=True,validate='one_to_one')
    changes['observation_change'] = changes['_merge'].map({'both':'observed_both','left_only':'not_observed','right_only':'first_observed'}).astype(object)
    changes.loc[changes['_merge'].eq('right_only') & changes.listing_id.isin(seen_before),'observation_change']='reappearing'
    both = changes['_merge'].eq('both')
    changes['identity_conflict'] = both & (changes.vin_before.isna() | changes.vin_after.isna() | changes.vin_before.ne(changes.vin_after))
    changes['asking_price_change_usd'] = (changes.asking_price_usd_after-changes.asking_price_usd_before).where(both & ~changes.identity_conflict)
    statuses = ['purchase_pending','vehicle_lock_type']
    changed = pd.Series(False, index=changes.index, dtype='boolean')
    for name in statuses:
        left, right = changes[name+'_before'], changes[name+'_after']
        field_changed = left.ne(right).astype('boolean').where(left.notna() & right.notna())
        changed |= field_changed  # Nullable OR: a known true survives an unknown field.
    changes['native_status_changed'] = changed.where(both & ~changes.identity_conflict)
    return changes
