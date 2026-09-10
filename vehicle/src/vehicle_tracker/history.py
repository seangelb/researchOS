"""Retained search evidence -> a separate SQLite history and explicit comparisons."""
import hashlib
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3

import pandas as pd

from vehicle_tracker.carvana import parse_capture

NATIVE_FIELDS = {'parent_model': 'parentModel', 'purchase_pending': 'isPurchasePending',
    'vehicle_lock_type': 'vehicleLockType', 'purchase_type': 'vehiclePurchaseType',
    'inventory_type': 'vehicleInventoryType', 'on_demand': 'isOnDemand', 'transport_cost_usd': 'transportCost'}
OBSERVATION_COLUMNS = ['retailer', 'listing_id', 'vin', 'observed_at_utc', 'year', 'make', 'model',
    'mileage_miles', 'asking_price_usd', 'condition_native', 'availability_native', 'card_text',
    'listing_url', 'source_url', *NATIVE_FIELDS, 'capture_id', 'run_id']
SCHEMA = '''
CREATE TABLE IF NOT EXISTS query_runs (
 run_id TEXT PRIMARY KEY, report_path TEXT, report_sha256 TEXT, context_json TEXT,
 query_complete INTEGER, coverage_reason TEXT, reported_total INTEGER, stored_rows INTEGER,
 observation_start TEXT, observation_end TEXT, invocation_start TEXT, invocation_end TEXT,
 normalizer_sha256 TEXT, original_normalizer_sha256 TEXT);
CREATE TABLE IF NOT EXISTS captures (
 capture_id TEXT PRIMARY KEY, run_id TEXT REFERENCES query_runs(run_id), page INTEGER,
 observed_at_utc TEXT, status TEXT, row_count INTEGER, error TEXT, source_path TEXT);
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


def native_values(vehicle):
    """Keep native nulls/types; reject schema drift instead of SQLite text coercion."""
    values = {name: vehicle.get(key) for name, key in NATIVE_FIELDS.items()}
    types = {'parent_model': (str,), 'purchase_pending': (bool,), 'vehicle_lock_type': (int,),
             'purchase_type': (str,), 'inventory_type': (int,), 'on_demand': (bool,),
             'transport_cost_usd': (int, float)}
    for name, value in values.items():
        if value is not None and (type(value) not in types[name] or
                (name == 'transport_cost_usd' and not math.isfinite(value))):
            raise ValueError('Unexpected native type/value: ' + name)
    return values


def read_query_evidence(report_path):
    """Verify source hashes and context; normalize only pages admitted by the collector.

    Failed/partial attempts remain in coverage. A claimed complete query must also
    reconcile to its retained pages, native totals, identities and actual timestamps.
    """
    report_path = Path(report_path).resolve()
    report = json.loads(report_path.read_text(encoding='utf-8'))
    frames, captures, contexts, totals, page_ends = [], [], [], [], []
    for page in report['pages']:
        source = Path(page['retained_source'])
        source_hash = file_hash(source)
        if source_hash != page['source_sha256']:
            raise ValueError('Retained source hash differs from the query report')
        capture = json.loads(source.read_text(encoding='utf-8'))
        captures.append(dict(capture_id=source_hash, run_id=report['run_id'], page=page['page'],
            observed_at_utc=capture.get('captured_at_utc'), status=page['status'],
            row_count=page['stored_rows'], error=page.get('error'), source_path=str(source)))
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
    capture_rows = pd.DataFrame(captures, columns=['capture_id','run_id','page','observed_at_utc','status','row_count','error','source_path'])
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
    code = ''.join(file_hash(Path(__file__).parent/name) for name in ('history.py','search.py','carvana.py'))
    run = dict(run_id=report['run_id'], report_path=str(report_path), report_sha256=file_hash(report_path),
        context_json=contexts[0] if contexts else None, query_complete=int(complete),
        coverage_reason=report.get('reason') or ('Complete retained query' if complete else 'Incomplete attempt'),
        reported_total=report.get('reported_total'), stored_rows=len(observations),
        observation_start=clocks.min().isoformat() if len(clocks) and clocks.notna().all() else None,
        observation_end=clocks.max().isoformat() if len(clocks) and clocks.notna().all() else None,
        invocation_start=report['started_utc'], invocation_end=report.get('ended_utc'),
        normalizer_sha256=hashlib.sha256(code.encode()).hexdigest(),
        original_normalizer_sha256=report.get('normalizer_code_sha256'))
    return run, capture_rows, observations.reindex(columns=OBSERVATION_COLUMNS)


def import_reports(report_paths, database):
    """Idempotent import into the caller's explicit analysis DB; sources stay read-only."""
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with closing(sqlite3.connect(database)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables - {'query_runs','captures','observations'}:
            raise ValueError('Refusing an unrelated database')
        connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript(SCHEMA)
        for report_path in report_paths:
            run, captures, observations = read_query_evidence(report_path)
            prior = connection.execute('SELECT report_sha256 FROM query_runs WHERE run_id=?', (run['run_id'],)).fetchone()
            if prior and prior[0] != run['report_sha256']:
                raise ValueError('Previously imported query report changed; use a new reviewed analysis build')
            if not prior:
                with connection:
                    connection.execute('INSERT INTO query_runs VALUES ('+','.join('?' for _ in run)+')',tuple(run.values()))
                    for table, frame in [('captures',captures),('observations',observations)]:
                        values = frame.astype(object).where(frame.notna(),None)
                        connection.executemany('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in frame.columns)+')',
                                               values.itertuples(index=False,name=None))
            results.append(dict(run_id=run['run_id'], imported_runs=int(not prior),
                                imported_rows=0 if prior else len(observations), report_path=str(report_path)))
    return pd.DataFrame(results)


def read_history(database, *, run_ids=None):
    """Return query attempts, captures and observations using a read-only connection."""
    connection = sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro', uri=True)
    try:
        if run_ids is None:
            return tuple(pd.read_sql_query('SELECT * FROM '+table,connection)
                         for table in ('query_runs','captures','observations'))
        ids = list(dict.fromkeys(run_ids))
        chunks = [ids[start:start+500] for start in range(0,len(ids),500)] or [[]]
        return tuple(pd.concat([pd.read_sql_query('SELECT * FROM '+table+
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
