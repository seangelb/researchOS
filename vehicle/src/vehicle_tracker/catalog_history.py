"""Read-only positive observation history across explicit catalog and legacy inputs.

Different scopes can establish earlier sightings, never cross-scope absences.
Catalog availability is conservatively the saved export's publication clock.
No input is imported, overwritten or scanned for. Physical source aliases are
deduplicated only after exact value/context checks, with every alias retained.
"""
import hashlib
import json
from pathlib import Path

import pandas as pd

from vehicle_tracker.cycles import cycle_evidence, read_cycle_history
from vehicle_tracker.events import CYCLE_COLUMNS, _aware
from vehicle_tracker.history import OBSERVATION_COLUMNS, file_hash, read_history
from vehicle_tracker.search import ENDPOINT
from vehicle_tracker.timeline import observed_history, summarize_observed_memberships


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _verified_manifest(directory):
    """Verify the immutable export and retained sources, without running a collector."""
    manifest_path = directory/'manifest.json'
    manifest = _json(manifest_path)
    sources = {str(Path(path).resolve()): sha for path, sha in manifest['sources'].items()}
    for name, sha in manifest['outputs'].items():
        if Path(name).name != name:
            raise ValueError('Catalog output must be a filename inside its export')
        sources[str(directory/name)] = sha
    for path, expected in sources.items():
        if not Path(path).is_file() or file_hash(path) != expected:
            raise ValueError('Catalog manifest source/output changed or is missing: '+path)
    if not {'summary.json', 'observations.csv', 'coverage.csv'} <= set(manifest['outputs']):
        raise ValueError('Catalog manifest lacks its required primary outputs')
    return manifest, sources


def read_catalog_history(export_directory, *, as_of):
    """Adapt a verified per-run catalog export to positive-history cycle/row tables.

    Only declared primary leaves are admitted. Discovery and ZIP-validation rows
    cannot enter the primary inventory, except an explicitly reused complete make
    probe. Partial positive evidence is retained with incomplete coverage. Hashes
    bind the existing source-reconciled export; changing collector code does not
    rewrite that evidence or require its old source-code paths to remain current.
    """
    cutoff = _aware(as_of)
    directory = Path(export_directory).resolve()
    manifest, verified = _verified_manifest(directory)
    available = _aware(manifest['created_at'])
    report = _json(directory/'summary.json')
    folder = Path(report['capture_directory']).resolve()
    for name in ['catalog_report.json', 'catalog_budget.json', 'selected_config.json']:
        if str(folder/name) not in verified:
            raise ValueError('Catalog manifest lacks required collection evidence')
    if _json(folder/'catalog_report.json') != report:
        raise ValueError('Catalog summary differs from retained collection report')
    if report['status'] == 'running' or not report.get('ended_at'):
        raise ValueError('Catalog history requires a terminal attempt')
    config, budget = _json(folder/'selected_config.json'), _json(folder/'catalog_budget.json')
    if (file_hash(folder/'selected_config.json') != report['config_sha256']
            or budget['budget']['pending_request']
            or budget['budget']['requests'] != report['requests']):
        raise ValueError('Catalog history has uncertain budget or configuration evidence')
    if available < _aware(report['ended_at']):
        raise ValueError('Catalog publication predates its terminal evidence')

    empty = pd.DataFrame(columns=[*OBSERVATION_COLUMNS, 'cycle_id', 'context_json'])
    database = directory/'history.sqlite'
    leaf_ids = [q['query_id'] for q in report['leaf_queries']]
    entries = {entry['query']['query_id']: entry for entry in report['entries']}
    if len(leaf_ids) != len(set(leaf_ids)) or len(entries) != len(report['entries']):
        raise ValueError('Repeated catalog query identity')
    selected = [entries[key] for key in leaf_ids if key in entries and entries[key].get('report')]
    if report.get('format') == 'carvana-full-inventory-run-v3':
        selected.extend(entry for entry in report['entries']
                        if entry.get('retry_of') in leaf_ids and entry.get('report'))
    if not selected:
        if database.exists():
            raise ValueError('Catalog without primary reports has an unexpected history database')
        return pd.DataFrame(columns=CYCLE_COLUMNS), empty
    if 'history.sqlite' not in manifest['outputs']:
        raise ValueError('Catalog primary history is not bound to the export manifest')
    runs, captures, rows = read_history(database)
    lookup = []
    for entry in selected:
        query = entry['query']
        allowed_roles = {'primary_inventory', 'make_discovery'}
        if report.get('format') == 'carvana-full-inventory-run-v3':
            allowed_roles.add('primary_inventory_retry')
        if (entry['role'] not in allowed_roles
                or query['zip_code'] != config['primary_zip']
                or (entry['role'] == 'make_discovery' and not entry['query_complete'])):
            raise ValueError('Catalog primary selection contains discovery or geographic samples')
        path = Path(entry['report']).resolve()
        if verified.get(str(path)) != entry['report_sha256']:
            raise ValueError('Catalog primary report is not bound to its source manifest')
        child = _json(path)
        context = json.dumps(dict(endpoint=ENDPOINT, filters=query['filters'],
            zip_code=query['zip_code'], location_filter=False, sort='MostPopular'), sort_keys=True)
        lookup.append(dict(run_id=child['run_id'], query_id=query['query_id'],
            expected_context=context, expected_report=entry['report_sha256'],
            expected_complete=int(entry['query_complete'])))
    expected = pd.DataFrame(lookup)
    if (expected.run_id.duplicated().any() or set(runs.run_id) != set(expected.run_id)
            or not rows.run_id.isin(expected.run_id).all()
            or not captures.run_id.isin(expected.run_id).all()):
        raise ValueError('Catalog history contains missing or non-primary query runs')
    checked = runs.merge(expected, on='run_id', validate='one_to_one')
    populated = checked.stored_rows.gt(0)
    if (not checked.report_sha256.eq(checked.expected_report).all()
            or not checked.query_complete.eq(checked.expected_complete).all()
            or not checked.loc[populated, 'context_json'].eq(checked.loc[populated, 'expected_context']).all()):
        raise ValueError('Catalog database query provenance differs from primary reports')
    if (captures.capture_id.duplicated().any()
            or not rows.capture_id.isin(captures.loc[captures.status.eq('parsed'), 'capture_id']).all()):
        raise ValueError('Catalog observations lack matching retained captures')
    actual_counts = rows.groupby('run_id').size()
    if not checked.run_id.map(actual_counts).fillna(0).eq(checked.stored_rows).all():
        raise ValueError('Catalog history row counts differ from primary query provenance')
    for capture in captures.itertuples():
        if verified.get(str(Path(capture.source_path).resolve())) != capture.capture_id:
            raise ValueError('Catalog capture is not bound to its retained source')
    parsed = captures.loc[captures.status.eq('parsed')]
    if (parsed.evidence_available_at_utc.isna().any()
            or parsed.evidence_available_at_utc.map(_aware).gt(available).any()):
        raise ValueError('Catalog source availability is missing or later than publication')

    rows = rows.merge(checked[['run_id', 'query_id', 'context_json', 'query_complete']],
                      on='run_id', validate='many_to_one').rename(columns={'query_complete': 'source_query_complete'})
    matched = rows.merge(captures[['capture_id', 'run_id', 'source_path', 'evidence_available_at_utc']],
                         on=['capture_id', 'run_id'], validate='many_to_one')
    if len(matched) != len(rows):
        raise ValueError('Catalog observation and capture query identities differ')
    rows = matched
    rows = rows.assign(cycle_id=budget['cycle_id'], history_manifest_path=str(directory/'manifest.json'),
                       history_manifest_sha256=file_hash(directory/'manifest.json'))
    clocks = rows.observed_at_utc.map(_aware)
    if not clocks.empty and not clocks.between(_aware(report['window_start']), _aware(report['window_end'])).all():
        raise ValueError('Catalog observations fall outside the retained collection window')
    scope = dict(endpoint=ENDPOINT, primary_zip=config['primary_zip'], location_filter=False,
                 sort='MostPopular', declared_leaves=report['leaf_queries'])
    complete = report['primary_queries_complete']
    if type(complete) is not bool:
        raise ValueError('Catalog primary completeness must be an explicit boolean')
    if complete and (len(selected) != len(leaf_ids) or not checked.query_complete.eq(1).all()):
        raise ValueError('Catalog complete primary scope has missing or incomplete leaves')
    day = dict(cycle_id=budget['cycle_id'], cycle_date=report['cycle_date'], timezone=config['timezone'],
        window_start=clocks.min().isoformat() if len(clocks) else report['started_at'],
        window_end=clocks.max().isoformat() if len(clocks) else report['ended_at'],
        scope_id='catalog:'+hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest(),
        coverage_complete=complete,
        coverage_reason='Complete declared primary leaves; national coverage unverified' if complete else
                        'Incomplete primary scope; retained positive observations only',
        available_at=available.isoformat())
    if available > cutoff:
        return pd.DataFrame(columns=CYCLE_COLUMNS), empty
    return pd.DataFrame([day], columns=CYCLE_COLUMNS), rows


def _legacy_provenance(rows, source, *, as_of):
    """Retain source clocks omitted by the cycle row adapter, without inferring any."""
    if rows.empty:
        return rows
    rows = rows.copy()
    contexts, classes = {}, {}
    for path in rows.source_path.unique():
        capture = _json(path)
        request = capture['request']
        contexts[path] = json.dumps(dict(endpoint=capture['endpoint'], filters=request['filters'],
            zip_code=request['zip5'], sort=request['sortBy'],
            location_filter=request.get('requestedFeatures', []) == ['LocationBasedPrefiltering']), sort_keys=True)
        classes[path] = ('search_response_' + capture['response_evidence']['kind']
                         if capture.get('response_evidence') else 'retained_search_projection_only')
    rows['context_json'] = rows.source_path.map(contexts)
    rows['source_evidence_class'] = rows.source_path.map(classes)
    if 'evidence_available_at_utc' not in rows:
        available = {}
        if source.get('database') is not None:
            _, captures, _ = read_history(source['database'], run_ids=rows.run_id.unique())
            available = {(r.run_id, r.capture_id): r.evidence_available_at_utc for r in captures.itertuples()}
        else:
            for path in source['cycle_paths']:
                _, _, reports = cycle_evidence(path, as_of=as_of)
                for report_path in reports:
                    report = _json(report_path)
                    for page in report['pages']:
                        if page.get('source_sha256'):
                            available[(report['run_id'], page['source_sha256'])] = page.get('evidence_available_at_utc')
        rows['evidence_available_at_utc'] = [available.get((r.run_id, r.capture_id)) for r in rows.itertuples()]
    rows['original_evidence_available_at'] = rows.evidence_available_at_utc
    return rows


def _physical_key(row):
    return row['capture_id'], row['retailer'], row['listing_id']


def _deduplicate_memberships(memberships):
    """Database/report/cycle aliases witness one physical observation, not new ones."""
    if memberships.empty:
        return memberships
    rows = memberships.copy()
    value_columns = [name for name in OBSERVATION_COLUMNS if name not in {'run_id', 'capture_id'}]
    for name in value_columns:
        if name not in rows:
            rows[name] = None
    result = []
    for _, group in rows.groupby(['capture_id', 'retailer', 'listing_id'], sort=False, dropna=False):
        reference = group.iloc[0]
        for _, candidate in group.iloc[1:].iterrows():
            for name in [*value_columns, 'context_json']:
                left, right = reference[name], candidate[name]
                if pd.isna(left) and pd.isna(right):
                    continue
                if pd.isna(left) or pd.isna(right):
                    raise ValueError('Physical capture aliases differ in values or context: '+name)
                if name == 'observed_at_utc':
                    left, right = _aware(left), _aware(right)
                elif name == 'context_json':
                    left, right = json.loads(left), json.loads(right)
                if left != right:
                    raise ValueError('Physical capture aliases differ in values or context: '+name)
        real = group.loc[group.cycle_id.notna()]
        if real.cycle_id.nunique() > 1:
            raise ValueError('One physical capture has conflicting real cycle memberships')
        chosen = (real if not real.empty else group).iloc[0].to_dict()
        aliases = []
        for _, alias in group.iterrows():
            existing = alias.get('source_aliases_json')
            if isinstance(existing, str):
                aliases.extend(json.loads(existing))
            record = {}
            for name in ['source_group_kind', 'source_group_id', 'cycle_id', 'run_id',
                         'source_path', 'query_report_path', 'query_report_sha256',
                         'manifest_path', 'manifest_sha256', 'history_manifest_path',
                         'history_manifest_sha256', 'original_evidence_available_at', 'analysis_available_at']:
                value = alias.get(name)
                record[name] = (None if pd.isna(value) else value.isoformat()
                                if isinstance(value, pd.Timestamp) else value)
            aliases.append(record)
        chosen['source_aliases_json'] = json.dumps(
            list({json.dumps(alias, sort_keys=True): alias for alias in aliases}.values()), sort_keys=True)
        # A previously known cycle remains an earlier witness of this same fact;
        # adding a later publication cannot manufacture another observation.
        chosen['available_at'] = chosen['analysis_available_at'] = group.analysis_available_at.min()
        result.append(chosen)
    return pd.DataFrame(result)


def observed_catalog_history(*, catalog_exports=(), legacy_sources=(), retained_manifests=(), as_of):
    """Return (VIN history, first-observed cohorts, source memberships) read-only.

    ``catalog_exports`` contains explicitly selected catalog analysis directories.
    Each ``legacy_sources`` item supplies ``cycle_paths`` and optionally ``database``
    (None replays source-only). Distinct same-date attempts and different scopes
    remain distinct memberships. No absence, reappearance or sales estimate is
    inferred by this adapter; use separate complete comparable-scope gates.
    ``retained_manifests`` explicitly selects {path, sha256} publications of older
    query reports and genuine DOM samples. Unknown original cycle availability is
    withheld unless such a publication supplies a conservative analysis clock.
    """
    from vehicle_tracker.retained_history import read_retained_history
    cutoff = _aware(as_of)
    retained = [read_retained_history(item['path'], expected_sha256=item['sha256'], as_of=as_of)
                for item in retained_manifests]
    published = pd.concat(retained, ignore_index=True) if retained else pd.DataFrame()
    publication_clocks = {}
    for _, row in published.iterrows():
        key = _physical_key(row)
        publication_clocks[key] = min(publication_clocks.get(key, _aware(row.analysis_available_at)),
                                      _aware(row.analysis_available_at))
    days, rows = [], []
    for source in legacy_sources:
        selected_days, selected_rows = read_cycle_history(
            source['cycle_paths'], source.get('database'), as_of=as_of)
        selected_rows = _legacy_provenance(selected_rows, source, as_of=as_of)
        days.append(selected_days)
        rows.append(selected_rows)
    for directory in catalog_exports:
        selected_days, selected_rows = read_catalog_history(directory, as_of=as_of)
        days.append(selected_days)
        rows.append(selected_rows)
    combined_days = pd.concat(days, ignore_index=True) if days else pd.DataFrame(columns=CYCLE_COLUMNS)
    combined_rows = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=[*OBSERVATION_COLUMNS, 'cycle_id'])
    _, _, memberships = observed_history(combined_days, combined_rows, as_of=as_of)
    if not memberships.empty:
        original = memberships.get('original_evidence_available_at',
                                   pd.Series(None, index=memberships.index, dtype=object))
        source_clock = memberships.get('evidence_available_at_utc',
                                       pd.Series(None, index=memberships.index, dtype=object))
        memberships['original_evidence_available_at'] = original.where(original.notna(), source_clock)
        clocks = []
        for _, row in memberships.iterrows():
            original_clock = row.original_evidence_available_at
            known = (_aware(original_clock) if pd.notna(original_clock)
                     else publication_clocks.get(_physical_key(row)))
            clocks.append(max(_aware(row.available_at), known) if known is not None else pd.NaT)
        memberships['available_at'] = memberships['analysis_available_at'] = pd.to_datetime(clocks, utc=True)
    combined = pd.concat([memberships, published], ignore_index=True) if not published.empty else memberships
    if not combined.empty:
        combined['analysis_available_at'] = pd.to_datetime(combined.analysis_available_at, utc=True, format='ISO8601')
        combined['available_at'] = combined.analysis_available_at
        combined = combined.loc[combined.analysis_available_at.notna() & combined.analysis_available_at.le(cutoff)].copy()
        combined = _deduplicate_memberships(combined)
    eligible_days = combined_days.loc[combined_days.available_at.map(_aware).le(cutoff)] if not combined_days.empty else combined_days
    earliest = (set(eligible_days.loc[eligible_days.window_start.map(_aware).eq(
        eligible_days.window_start.map(_aware).min()), 'cycle_id']) if not eligible_days.empty else set())
    return summarize_observed_memberships(combined, as_of=as_of, earliest_cycle_ids=earliest)
