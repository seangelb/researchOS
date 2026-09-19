"""Explicit, hash-bound retained positives; no discovery, imports or collection.

Query reports and genuine DOM captures establish sightings, never a complete daily
inventory. SQLite inputs are parity witnesses, not additional observations. Original
availability stays unknown when absent; publication is a separate analysis clock.
"""
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import pandas as pd

from vehicle_tracker.carvana import NATIVE_FIELDS, native_values, parse_capture
from vehicle_tracker.history import OBSERVATION_COLUMNS, file_hash, read_history, read_query_evidence
from vehicle_tracker.storage import read_snapshots


FORMAT = 'carvana-retained-positive-history-v1'
PARSER_FILES = ('retained_history.py', 'history.py', 'carvana.py', 'search.py',
                'search_evidence.py', 'storage.py')
MEMBERSHIP_COLUMNS = [*OBSERVATION_COLUMNS, 'query_id', 'query_report_path',
    'query_report_sha256', 'parent_report_path', 'parent_report_sha256', 'parent_entry_index',
    'source_path', 'context_json', 'cycle_id', 'cycle_date',
    'source_group_kind', 'source_group_id', 'scope_id', 'timezone', 'coverage_complete',
    'coverage_reason', 'source_query_complete', 'source_evidence_class',
    'source_evidence_limitation', 'original_evidence_available_at',
    'evidence_available_at_utc', 'analysis_available_at', 'available_at',
    'manifest_path', 'manifest_sha256', 'source_aliases_json']
CLOCK_FIELDS = {'captured_at_utc', 'observed_at_utc', 'evidence_available_at_utc',
    'available_at_utc', 'available_at', 'imported_at_utc', 'published_at',
    'started_utc', 'ended_utc', 'invocation_start', 'invocation_end',
    'observation_start', 'observation_end', 'request_reserved_at_utc',
    'request_started_at_utc', 'response_received_at_utc'}
ORIGINAL_CLOCKS = ('evidence_available_at_utc', 'available_at_utc', 'available_at')
DOM_METHODS = {'browser_dom_sample', 'browser_dom', 'browser_dom_projection'}


def _aware(value):
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError
        return stamp.tz_convert('UTC')
    except (TypeError, ValueError) as exc:
        raise ValueError('Retained history requires a valid timezone-aware clock') from exc


def _clocks(metadata):
    return [_aware(value) for key, value in metadata.items()
            if key in CLOCK_FIELDS and value is not None and not pd.isna(value)]


def _original(*metadata):
    known = [_aware(item[key]) for item in metadata for key in ORIGINAL_CLOCKS
             if item.get(key) is not None]
    return max(known).isoformat() if known else None


def _json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate retained JSON key')
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('Non-finite retained JSON value: ' + value)
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique,
                      parse_constant=invalid_constant)


def _path(path):
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ValueError('Manifest references require explicit absolute paths')
    return str(Path(path).resolve())


def _hashes(bindings):
    if not isinstance(bindings, dict):
        raise ValueError('Manifest requires explicit hash mappings')
    verified = {}
    for path, digest in bindings.items():
        path = _path(path)
        if (path in verified or not re.fullmatch('[0-9a-f]{64}', str(digest))
                or not Path(path).is_file() or file_hash(path) != digest):
            raise ValueError('Manifest hash mismatch or duplicate path: ' + path)
        verified[path] = digest
    return verified


def _reference(path, digest, inputs):
    path = _path(path)
    if digest is None or inputs.get(path) != digest:
        raise ValueError('Selected reference is absent from input_hashes or differs: ' + path)
    return path


def _equal(actual, expected, keys, label):
    """Exact values with null, boolean/SQLite integer and aware-clock semantics."""
    try:
        actual, expected = actual[expected.columns].copy(), expected.copy()
        for frame in (actual, expected):
            if frame.duplicated(keys).any():
                raise ValueError('Duplicate witness identity')
            for column in frame.columns:
                if column in CLOCK_FIELDS:
                    frame[column] = frame[column].map(
                        lambda value: None if pd.isna(value) else _aware(value).isoformat())
                elif column in {'source_path', 'raw_file', 'report_path'}:
                    frame[column] = frame[column].map(
                        lambda value: None if pd.isna(value) else _path(value))
                elif column == 'context_json':
                    frame[column] = frame[column].map(lambda value: None if pd.isna(value)
                        else json.dumps(json.loads(value), sort_keys=True))
        actual = actual.sort_values(keys).reset_index(drop=True)
        expected = expected.sort_values(keys).reset_index(drop=True)
        pd.testing.assert_frame_equal(actual.astype(object).where(actual.notna(), None),
            expected.astype(object).where(expected.notna(), None), check_dtype=False, check_exact=True)
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        raise ValueError('Database witness differs from retained ' + label) from exc


def _snapshot_reconciliation(entry, inputs, report, sources):
    """Bind one previously audited post-storage failure without reclassifying it."""
    declaration = entry['snapshot_reconciliation']
    witness = entry.get('database_witness') or {}
    if witness.get('kind') != 'snapshots':
        raise ValueError('Snapshot reconciliation requires a snapshot witness')
    audit_path = _reference(declaration['path'], declaration['sha256'], inputs)
    journal_path = _reference(declaration['journal_path'], declaration['journal_sha256'], inputs)
    audit, journal = _json(audit_path), _json(journal_path)
    bindings = {_path(path): digest for path, digest in audit['source_artifact_sha256'].items()}
    for path, digest in ((entry['report_path'], entry['report_sha256']),
                         (witness['path'], witness['sha256']),
                         (journal_path, declaration['journal_sha256'])):
        if bindings.get(_path(path)) != digest:
            raise ValueError('Snapshot reconciliation audit does not bind current evidence')
    failures = [failure for failure in audit['failures']
                if _path(failure['journal_path']) == journal_path]
    if len(failures) != 1 or not isinstance(failures[0].get('query_id'), str) or not failures[0]['query_id']:
        raise ValueError('Snapshot reconciliation requires one identified failure journal')
    audit_query_id = failures[0]['query_id']
    queries = [query for query in audit['query_reconciliation']
               if query['query_id'] == audit_query_id]
    pages = [page for page in report['pages'] if page.get('error')]
    if (report.get('query_complete') is not False or report.get('status') != 'blocked'
            or report.get('outcome_kind') != 'storage_failure'
            or len(failures) != 1 or len(queries) != 1 or len(pages) != 1):
        raise ValueError('Snapshot reconciliation requires one audited blocked storage failure')
    page, query, failure = pages[0], queries[0], failures[0]
    if (page.get('status') != 'parsed' or page.get('outcome_kind') != 'storage_failure'
            or page.get('database_outcome') != 'unconfirmed' or not page.get('retained_source')
            or not isinstance(page.get('error'), str) or not page['error']
            or query.get('source_sqlite_parity') is not True
            or query.get('query_complete') is not False or query.get('original_status') != 'blocked'
            or query.get('verified_rows') != report['unique_listings']
            or query.get('attempted_requests') != len(report['pages']) or query.get('failed_requests') != 1
            or query.get('reason') != page['error'] or report.get('reason') != page['error']
            or ('query_id' in report and report['query_id'] != audit_query_id)
            or journal_path != _path(str(Path(entry['report_path']).parent/'attempts'/f"{page['page']:04d}.json"))):
        raise ValueError('Snapshot reconciliation failure state differs')
    matched = [source for source in sources if source['page'] == page['page']]
    if len(matched) != 1:
        raise ValueError('Snapshot reconciliation lacks a retained parsed page')
    source = matched[0]
    expected = dict(run_id=report['run_id'], request=source['capture']['request'], **page)
    def canonical(value):
        return json.dumps({key: _aware(item).isoformat() if key in CLOCK_FIELDS and item is not None
                           else item for key, item in value.items()}, sort_keys=True)
    if canonical(journal) != canonical(expected) or canonical(failure['journal']) != canonical(expected):
        raise ValueError('Snapshot reconciliation journal differs from current failure')
    source['snapshot_reconciliation'] = dict(
        reconciliation_path=audit_path, reconciliation_sha256=declaration['sha256'],
        journal_path=journal_path, journal_sha256=declaration['journal_sha256'],
        original_report_error=page['error'], original_database_outcome=page['database_outcome'],
        original_report_outcome_kind=page['outcome_kind'], snapshot_capture_error=None)
    return [_aware(audit['audited_at'])]


def _snapshot_witness(path, sources, *, run_id=None):
    captures, observations = read_snapshots(path)
    if run_id is not None:
        selected = captures[captures.run_id.eq(run_id)]
    else:
        hashes = {item['capture_id'] for item in sources}
        paths = {item['source_path'] for item in sources}
        selected = captures[captures.source_sha256.isin(hashes)
                            | captures.raw_file.map(_path).isin(paths)]
    if selected.empty or (run_id is not None and len(selected) != len(sources)):
        raise ValueError('Database witness has missing or extra selected captures')
    if not selected.source_sha256.isin({item['capture_id'] for item in sources}).all():
        raise ValueError('Database witness has an unexpected selected capture hash')
    known, aliases = [], []
    for source in sources:
        matches = selected[selected.source_sha256.eq(source['capture_id'])]
        if matches.empty or (run_id is not None and len(matches) != 1):
            raise ValueError('Database witness lacks selected capture identity')
        capture = source['capture']
        expected_rows = parse_capture(capture) if source['status'] == 'parsed' else pd.DataFrame()
        for _, stored in matches.iterrows():
            if pd.isna(stored.run_id) or pd.isna(stored.page_number):
                raise ValueError('Database witness lacks run/page identity')
            expected = dict(run_id=run_id or stored.run_id,
                page_number=source.get('page', stored.page_number),
                observed_at_utc=capture.get('captured_at_utc'), source_url=capture.get('page_url'),
                zip_code=capture.get('zip_code'), reported_total_text=capture.get('reported_total_text'),
                status=source['status'], row_count=len(expected_rows),
                error=None if 'snapshot_reconciliation' in source else source.get('error'),
                raw_file=source['source_path'], source_sha256=source['capture_id'], coverage='unverified')
            _equal(pd.DataFrame([stored]), pd.DataFrame([expected]), ['run_id', 'page_number'], 'capture')
            rows = (observations[observations.run_id.eq(stored.run_id)
                    & observations.page_number.eq(stored.page_number)] if not observations.empty
                    else pd.DataFrame(columns=[*expected_rows.columns, 'run_id', 'page_number']))
            expected_frame = expected_rows.assign(run_id=stored.run_id, page_number=stored.page_number)
            if expected_frame.empty:
                if not rows.empty:
                    raise ValueError('Database witness has observations for a failed capture')
            else:
                _equal(rows, expected_frame, ['run_id', 'page_number', 'retailer', 'listing_id'], 'observations')
            known.extend(_clocks(stored.to_dict()))
            known.extend(clock for row in rows.to_dict('records') for clock in _clocks(row))
            aliases.append(dict(witness_path=path, witness_kind='snapshots',
                                run_id=stored.run_id, page_number=int(stored.page_number),
                                capture_id=source['capture_id'],
                                **source.get('snapshot_reconciliation', {})))
    if run_id is not None and not observations.empty:
        selected_rows = observations[observations.run_id.eq(run_id)]
        if len(selected_rows) != sum(item['row_count'] for item in sources):
            raise ValueError('Database witness has extra selected observations')
    return known, aliases


def _witness(entry, inputs, *, run=None, captures=None, rows=None, sources):
    witness = entry.get('database_witness')
    if witness is None:
        return [], []
    path = _reference(witness['path'], witness['sha256'], inputs)
    if any(Path(path+suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('Database witness has SQLite sidecars; require a settled retained database')
    if witness['kind'] == 'snapshots':
        return _snapshot_witness(path, sources, run_id=run['run_id'] if run else None)
    if witness['kind'] != 'history' or run is None:
        raise ValueError('Unsupported database witness kind for source')
    actual_run, actual_captures, actual_rows = read_history(path, run_ids=[run['run_id']])
    expected_run = {key: value for key, value in run.items()
                    if key not in {'normalizer_sha256', 'unattempted_pages', 'uncertain_pages'}}
    _equal(actual_run, pd.DataFrame([expected_run]), ['run_id'], 'query run')
    _equal(actual_captures, captures, ['capture_id'], 'captures')
    _equal(actual_rows, rows, ['capture_id', 'retailer', 'listing_id'], 'observations')
    known = [clock for frame in (actual_run, actual_captures, actual_rows)
             for metadata in frame.to_dict('records') for clock in _clocks(metadata)]
    return known, [dict(witness_path=path, witness_kind='history', run_id=run['run_id'],
                        capture_ids=captures.capture_id.tolist())]


def _query(entry, inputs):
    path = _reference(entry['report_path'], entry['report_sha256'], inputs)
    report = _json(path)
    complete = report.get('query_complete')
    if type(complete) not in (bool, int) or complete not in (0, 1):
        raise ValueError('Source query completeness must be explicit')
    diagnostic = entry.get('diagnostic', False)
    checkpoints = [page for page in report['pages'] if not page.get('retained_source')]
    if type(diagnostic) is not bool or (diagnostic and (complete or not checkpoints)):
        raise ValueError('Diagnostic replay requires an incomplete checkpoint report')
    sources, clocks = [], _clocks(report)
    for page in report['pages']:
        clocks.extend(_clocks(page))
        if not page.get('retained_source'):
            journal = str((Path(path).parent/'attempts'/f"{page['page']:04d}.json").resolve())
            if journal not in inputs:
                raise ValueError('Checkpoint journal is absent from input_hashes')
            clocks.extend(_clocks(_json(journal)))
            continue
        source = _reference(page['retained_source'], page['source_sha256'], inputs)
        capture = _json(source)
        clocks.extend(_clocks(capture))
        evidence = capture.get('response_evidence') or {}
        clocks.extend(_clocks(evidence))
        if evidence.get('source_path'):
            _reference(evidence['source_path'], evidence.get('source_sha256'), inputs)
        sources.append(dict(capture_id=page['source_sha256'], source_path=source,
            capture=capture, page=page['page'], status=page['status'],
            row_count=page['stored_rows'], error=page.get('error'),
            original=_original(page, capture)))
    run, captures, rows = read_query_evidence(path, diagnostic=diagnostic)
    if 'snapshot_reconciliation' in entry:
        clocks.extend(_snapshot_reconciliation(entry, inputs, report, sources))
    extra, aliases = _witness(entry, inputs, run=run, captures=captures, rows=rows, sources=sources)
    clocks.extend(extra)
    lookup = {source['capture_id']: source for source in sources}
    rows = rows.copy()
    rows['source_path'] = rows.capture_id.map(lambda key: lookup[key]['source_path'])
    rows['original_evidence_available_at'] = rows.capture_id.map(lambda key: lookup[key]['original'])
    def evidence_class(key):
        return (lookup[key]['capture'].get('response_evidence') or {}).get('kind', 'legacy_search_projection')
    def limitation(key):
        return (lookup[key]['capture'].get('response_evidence') or {}).get('limitation') or (
            'Legacy selected projection; original response bytes unavailable.'
            if evidence_class(key) == 'legacy_search_projection' else None)
    rows['source_evidence_class'] = rows.capture_id.map(evidence_class)
    rows['source_evidence_limitation'] = rows.capture_id.map(limitation)
    return rows, dict(query_id=report.get('query_id'), query_report_path=path,
        query_report_sha256=entry['report_sha256'], context_json=run['context_json'],
        source_group_kind='query_report', source_group_id='query:'+run['run_id'],
        source_query_complete=run['query_complete']), clocks, aliases


def _dom(entry, inputs):
    path = _reference(entry['capture_path'], entry['capture_sha256'], inputs)
    capture = _json(path)
    if capture.get('capture_method') not in DOM_METHODS:
        raise ValueError('DOM source requires a genuine browser DOM capture method')
    rows = parse_capture(capture).reindex(columns=OBSERVATION_COLUMNS)
    digest = entry['capture_sha256']
    rows = rows.assign(capture_id=digest, run_id=capture.get('run_id'), source_path=path,
        original_evidence_available_at=_original(capture),
        source_evidence_class=capture['capture_method'],
        source_evidence_limitation='Retained browser DOM sample/projection; inventory coverage unverified.')
    source = dict(capture_id=digest, source_path=path, capture=capture,
                  status='parsed', row_count=len(rows))
    clocks, aliases = _witness(entry, inputs, sources=[source])
    clocks.extend(_clocks(capture))
    context = {key: capture.get(key) for key in ('page_url', 'zip_code', 'sort',
        'sample_only', 'visible_listing_count', 'reported_total_text', 'capture_method')}
    return rows, dict(query_id=None, query_report_path=None, query_report_sha256=None,
        context_json=json.dumps(context, sort_keys=True), source_group_kind='dom_sample',
        source_group_id='dom:'+digest, source_query_complete=None), clocks, aliases


def _projection(entry, inputs):
    """A bounded experiment entry binds one standalone selected search page."""
    path = _reference(entry['capture_path'], entry['capture_sha256'], inputs)
    parent_path = _reference(entry['parent_report_path'], entry['parent_report_sha256'], inputs)
    parent, capture = _json(parent_path), _json(path)
    index = entry['parent_entry_index']
    if not isinstance(parent, list) or type(index) is not int or not 0 <= index < len(parent):
        raise ValueError('Projection requires an explicit parent experiment entry')
    selected = parent[index]
    if (capture.get('capture_method') != 'carvana_search_projection'
            or 'response_evidence' in capture
            or type(selected.get('http_status')) is not int or selected['http_status'] != 200
            or _path(selected['retained_source']) != path
            or json.dumps(selected.get('request'), sort_keys=True) != json.dumps(capture.get('request'), sort_keys=True)
            or json.dumps(selected.get('pagination'), sort_keys=True) != json.dumps(capture.get('pagination'), sort_keys=True)
            or _aware(selected.get('observed_at_utc')) != _aware(capture.get('captured_at_utc'))):
        raise ValueError('Standalone legacy projection differs from its parent experiment binding')
    request, pagination = capture['request'], capture['pagination']
    if (set(request) - {'filters', 'pagination', 'sortBy', 'zip5', 'requestedFeatures'}
            or request.get('sortBy') != 'MostPopular'
            or request.get('requestedFeatures', []) not in ([], ['LocationBasedPrefiltering'])
            or not isinstance(request.get('zip5'), str)
            or not re.fullmatch('[0-9]{5}', request['zip5'])
            or capture.get('zip_code') != capture.get('requested_zip')
            or capture.get('requested_zip') != request['zip5']):
        raise ValueError('Unsupported standalone projection request context')
    if (set(pagination) != {'currentPage', 'pageSize', 'totalMatchedInventory', 'totalMatchedPages'}
            or any(type(value) is not int or value < 0 for value in pagination.values())
            or pagination['pageSize'] != 24
            or pagination['totalMatchedPages'] != (pagination['totalMatchedInventory']+23)//24
            or not isinstance(request.get('pagination'), dict)
            or any(type(request['pagination'].get(key)) is not int for key in ('page', 'pageSize'))
            or request['pagination'] != dict(page=pagination['currentPage'], pageSize=24)
            or not 1 <= pagination['currentPage'] <= max(1, pagination['totalMatchedPages'])
            or len(capture['vehicles']) != min(24, max(0, pagination['totalMatchedInventory']
                                                     - 24*(pagination['currentPage']-1)))):
        raise ValueError('Standalone projection has inconsistent pagination or page size')
    frame = parse_capture(capture)
    native = pd.DataFrame([native_values(vehicle) for vehicle in capture['vehicles']], columns=NATIVE_FIELDS)
    rows = pd.concat([frame.reset_index(drop=True), native], axis=1).reindex(columns=OBSERVATION_COLUMNS)
    digest = entry['capture_sha256']
    rows = rows.assign(capture_id=digest, run_id=capture.get('run_id'), source_path=path,
        original_evidence_available_at=_original(selected, capture),
        source_evidence_class='legacy_search_projection',
        source_evidence_limitation='Standalone selected search projection bound to an experiment; original response bytes unavailable.')
    source = dict(capture_id=digest, source_path=path, capture=capture,
                  status='parsed', row_count=len(rows), page=pagination['currentPage'])
    clocks, aliases = _witness(entry, inputs, sources=[source])
    clocks.extend(clock for item in parent for clock in _clocks(item))
    clocks.extend(_clocks(capture))
    context = dict(endpoint=capture['endpoint'], filters=request['filters'], zip_code=request['zip5'],
        sort=request['sortBy'], location_filter=request.get('requestedFeatures', []) == ['LocationBasedPrefiltering'])
    return rows, dict(query_id=None, query_report_path=None, query_report_sha256=None,
        parent_report_path=parent_path, parent_report_sha256=entry['parent_report_sha256'],
        parent_entry_index=index, context_json=json.dumps(context, sort_keys=True),
        source_group_kind='search_projection', source_group_id='projection:'+digest,
        source_query_complete=None), clocks, aliases


def read_retained_history(manifest_path, *, expected_sha256, as_of):
    """Validate all selected evidence before cutoff-filtering positive VIN rows.

    ``code_hashes`` binds each current PARSER_FILES path; ``input_hashes`` binds
    every selected report, capture, response, checkpoint journal and witness.
    Optional ``projection_sources`` also bind an explicit parent experiment entry;
    they remain standalone selected projections with unknown query completeness.
    Witness import times delay analysis availability but never become original
    evidence availability. This function only reads files and read-only SQLite.
    """
    path = Path(manifest_path).resolve()
    if not re.fullmatch('[0-9a-f]{64}', str(expected_sha256)) or file_hash(path) != expected_sha256:
        raise ValueError('Retained manifest hash mismatch')
    manifest = _json(path)
    if manifest.get('format') != FORMAT:
        raise ValueError('Unknown retained history manifest format')
    published, cutoff = _aware(manifest['published_at']), _aware(as_of)
    ZoneInfo(manifest['timezone'])
    code = _hashes(manifest['code_hashes'])
    required = {str((Path(__file__).parent/name).resolve()) for name in PARSER_FILES}
    if not required <= code.keys():
        raise ValueError('Manifest lacks current parser code hashes')
    inputs = _hashes(manifest['input_hashes'])
    frames, groups = [], set()
    for entries, reader in ((manifest['query_sources'], _query), (manifest['dom_sources'], _dom),
                            (manifest.get('projection_sources', []), _projection)):
        if not isinstance(entries, list):
            raise ValueError('Manifest source selections must be lists')
        for entry in entries:
            if 'snapshot_reconciliation' in entry and reader is not _query:
                raise ValueError('Snapshot reconciliation applies only to query sources')
            rows, metadata, clocks, witnesses = reader(entry, inputs)
            identity = metadata['source_group_id']
            if identity in groups:
                raise ValueError('Repeated retained source group identity')
            groups.add(identity)
            observed = rows.observed_at_utc.map(_aware)
            for observation, original in zip(observed, rows.original_evidence_available_at):
                if original is not None and _aware(original) < observation:
                    raise ValueError('Original availability predates retained observation')
            available = max([published, *clocks, *observed])
            rows = rows.assign(**metadata, cycle_id=None, cycle_date=None,
                scope_id=hashlib.sha256((metadata['context_json'] or 'null').encode()).hexdigest(),
                timezone=manifest['timezone'], coverage_complete=False,
                coverage_reason='Positive retained observations only; no cycle or absence coverage.',
                evidence_available_at_utc=rows.original_evidence_available_at,
                analysis_available_at=available.isoformat(), available_at=available.isoformat(),
                manifest_path=str(path), manifest_sha256=expected_sha256)
            rows['source_aliases_json'] = rows.apply(lambda row: json.dumps([
                dict(source_group_kind=metadata['source_group_kind'], source_group_id=identity,
                     manifest_path=str(path), manifest_sha256=expected_sha256,
                     query_report_path=metadata['query_report_path'],
                     query_report_sha256=metadata['query_report_sha256'], source_path=row.source_path,
                     **{key: metadata[key] for key in ('parent_report_path', 'parent_report_sha256',
                                                       'parent_entry_index') if key in metadata},
                     **witness) for witness in (witnesses or [{}])
                     if witness.get('capture_id', row.capture_id) == row.capture_id], sort_keys=True), axis=1
                ) if not rows.empty else pd.Series(dtype=object)
            # The selected evidence remains validated even when it is too late or
            # lacks a VIN. Unknown VINs cannot become VIN-level memberships.
            if available <= cutoff:
                frames.append(rows[rows.vin.notna() & rows.vin.astype(str).str.strip().ne('')])
    return (pd.concat(frames, ignore_index=True).reindex(columns=MEMBERSHIP_COLUMNS)
            if frames else pd.DataFrame(columns=MEMBERSHIP_COLUMNS))
