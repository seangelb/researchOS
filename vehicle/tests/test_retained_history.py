"""Retained-source admission is offline, exact and publication-cutoff safe."""
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from test_search import packet, response_data
from vehicle_tracker import retained_history
from vehicle_tracker.history import file_hash, import_reports
from vehicle_tracker.search import build_search_request
from vehicle_tracker.storage import store_capture


OBSERVED = '2026-09-08T12:00:00Z'
PUBLISHED = '2026-09-19T12:00:00Z'
LATER = '2026-09-20T12:00:00Z'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


def query(tmp_path, response_data, *, available=None):
    capture = write(tmp_path/'capture.json', packet(response_data))
    page = dict(page=1, status='parsed', stored_rows=3, error=None,
                retained_source=str(capture), source_sha256=file_hash(capture),
                evidence_available_at_utc=available)
    report = write(tmp_path/'report.json', dict(run_id='query-one', query_id='first',
        query_complete=True, pages=[page], unique_listings=3, reported_total=3,
        filters={}, zip_code='08542', location_filter=False,
        started_utc='2026-09-08T11:59:00Z', ended_utc='2026-09-08T12:01:00Z'))
    return report, capture


def dom(tmp_path):
    source = Path(__file__).parent/'fixtures/carvana_browser_sample_20260907.json'
    return write(tmp_path/'dom.json', json.loads(source.read_text()))


def publication(tmp_path, *, query_sources=(), dom_sources=(), projection_sources=(),
                published_at=PUBLISHED, extras=()):
    """Build a temporary test publication, closing explicit retained references."""
    inputs = list(extras)
    for source in query_sources:
        report = Path(source['report_path'])
        inputs.append(report)
        for page in json.loads(report.read_text())['pages']:
            if page.get('retained_source'):
                capture = Path(page['retained_source'])
                inputs.append(capture)
                evidence = json.loads(capture.read_text()).get('response_evidence') or {}
                if evidence.get('source_path'):
                    inputs.append(Path(evidence['source_path']))
            else:
                inputs.append(report.parent/'attempts'/f"{page['page']:04d}.json")
    inputs.extend(Path(source['capture_path']) for source in dom_sources)
    inputs.extend(Path(source[key]) for source in projection_sources
                  for key in ('capture_path', 'parent_report_path'))
    inputs.extend(Path(source['database_witness']['path']) for source in [*query_sources, *dom_sources, *projection_sources]
                  if source.get('database_witness'))
    value = dict(format=retained_history.FORMAT, published_at=published_at,
        timezone='America/New_York',
        code_hashes={str(Path(retained_history.__file__).parent/name):
                     file_hash(Path(retained_history.__file__).parent/name)
                     for name in retained_history.PARSER_FILES},
        input_hashes={str(path): file_hash(path) for path in inputs},
        query_sources=list(query_sources), dom_sources=list(dom_sources), projection_sources=list(projection_sources))
    path = write(tmp_path/'manifest.json', value)
    return dict(path=str(path), sha256=file_hash(path))


def manifest(tmp_path, *, report=None, capture=None, witness=None, diagnostic=False, extras=()):
    queries = [dict(report_path=str(report), report_sha256=file_hash(report),
                    diagnostic=diagnostic, database_witness=witness)] if report else []
    doms = [dict(capture_path=str(capture), capture_sha256=file_hash(capture),
                 database_witness=witness)] if capture else []
    return Path(publication(tmp_path, query_sources=queries, dom_sources=doms, extras=extras)['path'])


def read(path, *, cutoff=PUBLISHED):
    return retained_history.read_retained_history(path, expected_sha256=file_hash(path), as_of=cutoff)


def witness(path, kind):
    return dict(path=str(path), sha256=file_hash(path), kind=kind)


def test_legacy_missing_availability_appears_only_at_publication(tmp_path, response_data):
    report, capture = query(tmp_path, response_data)
    selected = manifest(tmp_path, report=report)
    before = {path: path.read_bytes() for path in (report, capture, selected)}
    assert read(selected, cutoff='2026-09-19T11:59:59Z').empty
    rows = read(selected)
    assert len(rows) == 3 and list(rows.columns) == retained_history.MEMBERSHIP_COLUMNS
    assert rows.original_evidence_available_at.isna().all()
    assert rows.evidence_available_at_utc.isna().all()
    assert rows.analysis_available_at.eq('2026-09-19T12:00:00+00:00').all()
    assert rows.cycle_id.isna().all() and rows.cycle_date.isna().all()
    assert not rows.coverage_complete.any() and rows.source_query_complete.eq(1).all()
    assert rows.source_evidence_class.eq('legacy_search_projection').all()
    assert rows.purchase_pending.eq(False).all() and rows.on_demand.isna().all()
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize('field', ['ended_utc', 'request_started_at_utc', 'evidence_available_at_utc'])
def test_later_known_source_clock_delays_analysis(tmp_path, response_data, field):
    report, _ = query(tmp_path, response_data)
    value = json.loads(report.read_text())
    (value if field == 'ended_utc' else value['pages'][0])[field] = LATER
    write(report, value)
    selected = manifest(tmp_path, report=report)
    assert read(selected).empty
    rows = read(selected, cutoff=LATER)
    assert len(rows) == 3 and rows.available_at.eq('2026-09-20T12:00:00+00:00').all()
    if field != 'evidence_available_at_utc':
        assert rows.original_evidence_available_at.isna().all()


def test_source_less_diagnostic_checkpoint_admits_only_prior_positive_rows(tmp_path, response_data):
    report, capture = query(tmp_path, response_data)
    value = json.loads(report.read_text())
    # Legacy parsed source plus a supported new-contract pending checkpoint. The
    # successful page is upgraded with coherent source metadata for replay.
    raw = json.loads(capture.read_text())
    from unittest.mock import Mock
    from vehicle_tracker.search_evidence import retain_response_evidence
    response = Mock(status_code=200, headers={'content-type':'application/json'},
                    content=json.dumps(response_data).encode(), json=lambda: response_data)
    evidence = retain_response_evidence(response, tmp_path/'responses')
    raw.update(attempt_id='first-attempt', response_evidence=evidence)
    write(capture, raw)
    value['pages'][0].update(attempt_id='first-attempt', response_evidence=evidence,
        response_sha256=evidence['response_content_sha256'], response_bytes=evidence['response_content_bytes'],
        response_hash_scope=evidence['response_hash_scope'], response_received_at_utc=OBSERVED,
        source_sha256=file_hash(capture))
    pending = dict(page=2, status='pending', stored_rows=0, outcome_kind='unattempted',
                   attempt_id='pending-second', retained_source=None)
    value.update(query_complete=False, evidence_contract='carvana-search-source-v1')
    value['pages'].append(pending)
    write(report, value)
    journal = write(tmp_path/'attempts/0002.json', dict(run_id=value['run_id'],
        request=build_search_request(filters={}, zip_code='08542', page=2), **pending))
    selected = manifest(tmp_path, report=report, diagnostic=True,
                        extras=[journal, Path(evidence['source_path'])])
    rows = read(selected)
    assert len(rows) == 3 and rows.source_query_complete.eq(0).all()
    assert rows.capture_id.nunique() == 1 and rows.source_path.eq(str(capture)).all()
    value = json.loads(selected.read_text())
    del value['input_hashes'][str(journal)]
    write(selected, value)
    with pytest.raises(ValueError, match='journal'):
        read(selected)


def test_wholly_source_less_checkpoint_is_validated_but_has_no_rows(tmp_path):
    pending = dict(page=1, status='pending', stored_rows=0, outcome_kind='unattempted',
                   attempt_id='not-attempted', retained_source=None)
    report = write(tmp_path/'report.json', dict(run_id='unattempted', query_complete=False,
        evidence_contract='carvana-search-source-v1', filters={}, zip_code='08542',
        pages=[pending], unique_listings=0, started_utc=OBSERVED))
    write(tmp_path/'attempts/0001.json', dict(run_id='unattempted',
        request=build_search_request(filters={}, zip_code='08542'), **pending))
    assert read(manifest(tmp_path, report=report, diagnostic=True)).empty


@pytest.mark.parametrize('clock', ['2026-09-08', 'bad', 'NaT'])
def test_dom_malformed_observation_clock_rejected_before_cutoff_filter(tmp_path, clock):
    capture = dom(tmp_path)
    value = json.loads(capture.read_text())
    value['captured_at_utc'] = clock
    write(capture, value)
    selected = manifest(tmp_path, capture=capture)
    with pytest.raises(ValueError):
        read(selected, cutoff='2026-01-01T00:00:00Z')


def test_dom_sample_preserves_partial_rows_unknown_fields_and_witness_aliases(tmp_path):
    capture = dom(tmp_path)
    database = tmp_path/'snapshots.sqlite'
    store_capture(database, run_id='dom-run', page_number=4, raw_file=capture)
    selected = manifest(tmp_path, capture=capture, witness=witness(database, 'snapshots'))
    before = database.read_bytes()
    rows = read(selected)
    assert len(rows) == 3 and not rows.coverage_complete.any()
    assert rows.source_query_complete.isna().all() and rows.purchase_pending.isna().all()
    assert rows.source_group_kind.eq('dom_sample').all()
    assert json.loads(rows.source_aliases_json.iloc[0])[0]['run_id'] == 'dom-run'
    assert database.read_bytes() == before


def test_dom_cannot_bypass_query_report_with_search_projection(tmp_path, response_data):
    _, capture = query(tmp_path, response_data)
    selected = manifest(tmp_path, capture=capture)
    with pytest.raises(ValueError, match='genuine browser DOM'):
        read(selected)


@pytest.mark.parametrize('kind, table, field, value', [
    ('history', 'observations', 'asking_price_usd', 1),
    ('history', 'observations', 'purchase_pending', 1),
    ('history', 'query_runs', 'context_json', '{}'),
    ('history', 'captures', 'source_path', 'C:/wrong.json'),
    ('snapshots', 'vehicle_observations', 'asking_price_usd', 1),
    ('snapshots', 'vehicle_captures', 'zip_code', '90210'),
])
def test_exact_database_witness_rejects_same_count_value_mutation(tmp_path, response_data, kind, table, field, value):
    report, capture = query(tmp_path, response_data)
    database = tmp_path/'witness.sqlite'
    if kind == 'history':
        import_reports([report], database)
        with sqlite3.connect(database) as connection:
            connection.execute('UPDATE query_runs SET imported_at_utc=NULL')
    else:
        store_capture(database, run_id='query-one', page_number=1, raw_file=capture)
    selected = manifest(tmp_path, report=report, witness=witness(database, kind))
    assert len(read(selected)) == 3
    with sqlite3.connect(database) as connection:
        connection.execute(f'UPDATE {table} SET {field}=?', [value])
    selected = manifest(tmp_path, report=report, witness=witness(database, kind))
    with pytest.raises(ValueError, match='witness'):
        read(selected)


def test_witness_import_clock_delays_analysis_without_backfilling_original(tmp_path, response_data):
    report, _ = query(tmp_path, response_data)
    database = tmp_path/'history.sqlite'
    import_reports([report], database)
    with sqlite3.connect(database) as connection:
        connection.execute('UPDATE query_runs SET imported_at_utc=?', [LATER])
        connection.execute('UPDATE observations SET observed_at_utc=?', ['2026-09-08T08:00:00-04:00'])
    selected = manifest(tmp_path, report=report, witness=witness(database, 'history'))
    assert read(selected).empty
    rows = read(selected, cutoff=LATER)
    assert len(rows) == 3 and rows.original_evidence_available_at.isna().all()


def test_snapshot_aliases_select_only_the_rows_own_capture(tmp_path, response_data):
    report, capture = query(tmp_path, response_data)
    value = json.loads(report.read_text())
    second = json.loads(capture.read_text())
    second['captured_at_utc'] = '2026-09-08T12:00:02Z'
    second['request']['pagination']['page'] = second['pagination']['currentPage'] = 2
    for index, vehicle in enumerate(second['vehicles']):
        vehicle.update(vehicleId=100+index, vin=f'{100+index:017}')
    second_path = write(tmp_path/'second.json', second)
    value['pages'].append(dict(value['pages'][0], page=2, retained_source=str(second_path),
                               source_sha256=file_hash(second_path)))
    value.update(query_complete=False, unique_listings=6)
    write(report, value)
    database = tmp_path/'snapshots.sqlite'
    for number, source in enumerate((capture, second_path), 1):
        store_capture(database, run_id='query-one', page_number=number, raw_file=source)
    rows = read(manifest(tmp_path, report=report, witness=witness(database, 'snapshots')))
    assert len(rows) == 6
    for row in rows.itertuples():
        aliases = json.loads(row.source_aliases_json)
        assert len(aliases) == 1
        assert aliases[0]['capture_id'] == row.capture_id
        assert aliases[0]['source_path'] == row.source_path


@pytest.mark.parametrize('mutation', ['manifest', 'input', 'code', 'missing_code', 'missing_capture'])
def test_manifest_input_and_current_code_bindings_fail_closed(tmp_path, response_data, mutation):
    report, capture = query(tmp_path, response_data)
    selected = manifest(tmp_path, report=report)
    digest = file_hash(selected)
    value = json.loads(selected.read_text())
    if mutation == 'manifest':
        value['published_at'] = LATER
    elif mutation == 'input':
        capture.write_text(capture.read_text() + ' ')
    elif mutation == 'code':
        value['code_hashes'][next(iter(value['code_hashes']))] = '0'*64
    elif mutation == 'missing_code':
        value['code_hashes'].pop(next(iter(value['code_hashes'])))
    else:
        value['input_hashes'].pop(str(capture))
    if mutation != 'input':
        write(selected, value)
    with pytest.raises(ValueError):
        retained_history.read_retained_history(selected,
            expected_sha256=digest if mutation == 'manifest' else file_hash(selected), as_of=PUBLISHED)


def test_missing_vin_does_not_become_vin_membership(tmp_path):
    capture = dom(tmp_path)
    value = json.loads(capture.read_text())
    value['records'][0]['vehicleIdentificationNumber'] = None
    write(capture, value)
    assert len(read(manifest(tmp_path, capture=capture))) == 2


def test_no_network_no_writes_and_unsettled_witness_rejected(tmp_path, response_data, monkeypatch):
    report, _ = query(tmp_path, response_data)
    database = tmp_path/'history.sqlite'
    import_reports([report], database)
    with sqlite3.connect(database) as connection:
        connection.execute('UPDATE query_runs SET imported_at_utc=NULL')
    selected = manifest(tmp_path, report=report, witness=witness(database, 'history'))
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    def forbidden(*args, **kwargs):
        raise AssertionError('Reader must not make requests or publish data')
    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    monkeypatch.setattr(Path, 'write_text', forbidden)
    monkeypatch.setattr(Path, 'write_bytes', forbidden)
    assert len(read(selected)) == 3
    assert all(path.read_bytes() == content for path, content in before.items())
    with open(str(database)+'-wal', 'wb') as stream:
        stream.write(b'unbound sidecar')
    with pytest.raises(ValueError, match='sidecars'):
        read(selected)


@pytest.mark.parametrize('mutation', [None, 'request', 'clock', 'pagination', 'oversized_page',
                                      'source_zip', 'response_evidence_bypass',
                                      'parent_bool_page', 'parent_bool_current_page',
                                      'matching_bool_page', 'matching_float_page_size'])
def test_standalone_projection_requires_exact_parent_binding(tmp_path, response_data, mutation):
    capture = write(tmp_path/'projection.json', packet(response_data))
    value = json.loads(capture.read_text())
    parent_entry = dict(retained_source=str(capture), http_status=200,
                        request=value['request'], pagination=value['pagination'],
                        observed_at_utc=value['captured_at_utc'])
    parent_entry = json.loads(json.dumps(parent_entry))
    if mutation == 'request':
        parent_entry['request']['requestedFeatures'] = ['LocationBasedPrefiltering']
    elif mutation == 'clock':
        parent_entry['observed_at_utc'] = LATER
    elif mutation == 'pagination':
        parent_entry['pagination']['totalMatchedInventory'] = 4
    elif mutation == 'parent_bool_page':
        parent_entry['request']['pagination']['page'] = True
    elif mutation == 'parent_bool_current_page':
        parent_entry['pagination']['currentPage'] = True
    elif mutation in {'matching_bool_page', 'matching_float_page_size'}:
        key, replacement = ('page', True) if mutation == 'matching_bool_page' else ('pageSize', 24.0)
        value['request']['pagination'][key] = replacement
        parent_entry['request']['pagination'][key] = replacement
        write(capture, value)
    elif mutation == 'source_zip':
        value['zip_code'] = value['requested_zip'] = '90210'
        write(capture, value)
    elif mutation == 'response_evidence_bypass':
        value['response_evidence'] = dict(source_path=str(tmp_path/'unbound.json'), source_sha256='0'*64)
        write(capture, value)
    elif mutation == 'oversized_page':
        value['pagination'].update(totalMatchedInventory=27, totalMatchedPages=2)
        value['vehicles'] = [dict(value['vehicles'][0], vehicleId=i, vin=f'{i:017}') for i in range(27)]
        parent_entry['pagination'] = value['pagination']
        write(capture, value)
    parent = write(tmp_path/'experiment.json', [parent_entry])
    selected = publication(tmp_path, projection_sources=[dict(capture_path=str(capture),
        capture_sha256=file_hash(capture), parent_report_path=str(parent),
        parent_report_sha256=file_hash(parent), parent_entry_index=0, database_witness=None)])
    if mutation:
        with pytest.raises(ValueError, match='projection'):
            read(Path(selected['path']))
    else:
        rows = read(Path(selected['path']))
        assert len(rows) == 3 and rows.source_group_kind.eq('search_projection').all()
        assert rows.cycle_id.isna().all() and rows.source_query_complete.isna().all()
        assert rows.original_evidence_available_at.isna().all()
        assert rows.parent_report_path.eq(str(parent)).all()
        assert rows.purchase_pending.eq(False).all()


@pytest.mark.parametrize('mutation', [None, 'undeclared', 'audit_hash', 'report_binding',
    'database_binding', 'journal_binding', 'audit_journal', 'journal_identity',
    'not_parsed', 'confirmed', 'complete', 'audit_parity', 'audit_count',
    'snapshot_error', 'snapshot_row', 'wrong_witness', 'audit_query_mapping', 'report_query_mapping',
    'null_report_query'])
@pytest.mark.parametrize('child_query_id', [None, 'first'])
def test_audited_post_storage_failure_keeps_both_error_states(tmp_path, response_data, mutation, child_query_id):
    report, capture = query(tmp_path, response_data)
    database = tmp_path/'snapshots.sqlite'
    store_capture(database, run_id='query-one', page_number=1, raw_file=capture)
    value = json.loads(report.read_text())
    if child_query_id is None:
        value.pop('query_id')  # Actual standalone child reports do not carry the plan query ID.
    if mutation == 'report_query_mapping':
        value['query_id'] = 'unrelated-query'
    elif mutation == 'null_report_query':
        value['query_id'] = None
    error = 'PermissionError: storage_failure'
    value.update(status='blocked', query_complete=False, outcome_kind='storage_failure', reason=error)
    page = value['pages'][0]
    page.update(error=error, outcome_kind='storage_failure', database_outcome='unconfirmed')
    if mutation == 'not_parsed':
        page['status'] = 'failed'
    elif mutation == 'confirmed':
        page['database_outcome'] = 'confirmed'
    elif mutation == 'complete':
        value['query_complete'] = True
    write(report, value)
    journal_value = dict(run_id=value['run_id'], request=json.loads(capture.read_text())['request'], **page)
    if mutation == 'journal_identity':
        journal_value['run_id'] = 'wrong-run'
    journal = write(tmp_path/'attempts/0001.json', journal_value)
    if mutation in {'snapshot_error', 'snapshot_row'}:
        with sqlite3.connect(database) as connection:
            if mutation == 'snapshot_error':
                connection.execute("UPDATE vehicle_captures SET error='unexpected'")
            else:
                connection.execute('UPDATE vehicle_observations SET asking_price_usd=1')
    audit_value = dict(audited_at=LATER,
        source_artifact_sha256={str(path): file_hash(path) for path in (report, database, journal)},
        failures=[dict(query_id='first', journal_path=str(journal), journal=journal_value)],
        query_reconciliation=[dict(query_id='first', original_status='blocked', query_complete=False,
            verified_rows=3, attempted_requests=1, failed_requests=1, reason=error, source_sqlite_parity=True)])
    if mutation in {'report_binding', 'database_binding', 'journal_binding'}:
        target = {'report_binding': report, 'database_binding': database, 'journal_binding': journal}[mutation]
        audit_value['source_artifact_sha256'][str(target)] = '0'*64
    elif mutation == 'audit_journal':
        audit_value['failures'][0]['journal'] = dict(journal_value, page=2)
    elif mutation == 'audit_parity':
        audit_value['query_reconciliation'][0]['source_sqlite_parity'] = False
    elif mutation == 'audit_count':
        audit_value['query_reconciliation'][0]['verified_rows'] = 4
    elif mutation == 'audit_query_mapping':
        audit_value['query_reconciliation'][0]['query_id'] = 'unrelated-query'
    audit = write(tmp_path/'audit.json', audit_value)
    declaration = dict(path=str(audit), sha256=file_hash(audit),
                       journal_path=str(journal), journal_sha256=file_hash(journal))
    if mutation == 'audit_hash':
        declaration['sha256'] = '0'*64
    source = dict(report_path=str(report), report_sha256=file_hash(report),
        database_witness=witness(database, 'history' if mutation == 'wrong_witness' else 'snapshots'))
    if mutation != 'undeclared':
        source['snapshot_reconciliation'] = declaration
    selected = Path(publication(tmp_path, query_sources=[source], extras=[audit, journal])['path'])
    if mutation:
        with pytest.raises(ValueError):
            read(selected, cutoff=LATER)
    else:
        assert read(selected).empty  # The later audit delays publication, not original evidence.
        rows = read(selected, cutoff=LATER)
        assert len(rows) == 3 and rows.source_query_complete.eq(0).all()
        assert rows.query_id.isna().all() if child_query_id is None else rows.query_id.eq(child_query_id).all()
        assert rows.original_evidence_available_at.isna().all()
        alias = json.loads(rows.source_aliases_json.iloc[0])[0]
        assert alias['original_report_error'] == error and alias['snapshot_capture_error'] is None
        assert alias['original_database_outcome'] == 'unconfirmed'
        assert alias['original_report_outcome_kind'] == 'storage_failure'
        assert alias['reconciliation_sha256'] == file_hash(audit)
        assert alias['journal_sha256'] == file_hash(journal)
