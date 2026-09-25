"""Offline positive-history adapters preserve scope, clocks and retained evidence."""
import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from test_catalog import experiment, run
from vehicle_tracker import catalog, catalog_history
from vehicle_tracker.events import CYCLE_COLUMNS
from vehicle_tracker.history import file_hash


@pytest.fixture
def exported(experiment, tmp_path):
    run(experiment)
    output = catalog.export_catalog(experiment.folder, output=tmp_path/'history-export')
    return experiment, output


def after_publication(output):
    return pd.Timestamp(json.loads((output/'manifest.json').read_text())['created_at']) + pd.Timedelta(seconds=1)


def test_catalog_adapter_keeps_only_primary_leaves_and_is_read_only(exported):
    e, output = exported
    before = {p: file_hash(p) for p in e.path.parent.rglob('*') if p.is_file()}
    history, cohorts, members = catalog_history.observed_catalog_history(
        catalog_exports=[output], as_of=after_publication(output))
    assert len(history) == len(members) == 26
    assert set(members.query_id) == {'make_000_model_000', 'make_000_model_001'}
    assert members.context_json.map(lambda value: json.loads(value)['zip_code']).eq('08542').all()
    assert members.source_query_complete.eq(1).all()
    assert members.history_manifest_sha256.eq(file_hash(output/'manifest.json')).all()
    assert history.observed_scopes.eq(1).all() and history.earlier_listing_history_unknown.all()
    assert cohorts.observed_vins.sum() == 26
    assert not {'estimated_sales', 'event_type', 'absence_streak', 'days_on_market'} & set(history.columns)
    assert before == {p: file_hash(p) for p in e.path.parent.rglob('*') if p.is_file()}


def test_catalog_cutoff_uses_publication_without_backdating_observation(exported):
    _, output = exported
    publication = pd.Timestamp(json.loads((output/'manifest.json').read_text())['created_at'])
    early = catalog_history.observed_catalog_history(catalog_exports=[output],
        as_of=publication-pd.Timedelta(microseconds=1))
    assert all(table.empty for table in early)
    history, _, members = catalog_history.observed_catalog_history(catalog_exports=[output], as_of=publication)
    assert history.available_at.eq(publication).all()
    assert history.first_observed_at.lt(publication).all()
    assert members.observed_at_utc.le(members.available_at).all()


def test_partial_rows_survive_without_absence_or_reappearance_claim(experiment, tmp_path):
    e = experiment
    for model in ['Model 3', 'Model Y']:
        for _ in range(13):
            vehicle = copy.deepcopy(e.vehicles[0])
            number = len(e.vehicles)+1
            vehicle.update(vehicleId=number, vin=f'5YJ3E1EA0MF{number:06d}', model=model, parentModel=model)
            e.vehicles.append(vehicle)

    def send(url, **kwargs):
        response = e.send(url, **kwargs)
        request = kwargs['json']
        if (request['filters'].get('makes', [{}])[0].get('parentModels') == [{'name': 'Model 3'}]
                and request['pagination']['page'] == 2):
            data = json.loads(response.content)
            data['inventory']['vehicles'][0] = copy.deepcopy(e.vehicles[0])
            response.content = json.dumps(data).encode()
        return response

    run(e, send)
    output = catalog.export_catalog(e.folder, output=tmp_path/'partial-history')
    history, _, members = catalog_history.observed_catalog_history(
        catalog_exports=[output], as_of=after_publication(output))
    assert len(history) == 50  # First 24 Model 3 rows plus all 26 Model Y rows.
    assert members.source_query_complete.eq(0).sum() == 24
    assert members.source_query_complete.eq(1).sum() == 26
    assert not members.coverage_complete.any()
    assert history.known_only_from_partial_cycles.all()
    assert 'reappeared_after_absence' not in members


def test_mixed_scope_partial_legacy_and_same_date_attempts_retain_first_sighting(exported, monkeypatch):
    _, output = exported
    _, current = catalog_history.read_catalog_history(output, as_of=after_publication(output))
    old = current.iloc[[0]].drop(columns=['context_json', 'query_id', 'source_query_complete',
        'history_manifest_path', 'history_manifest_sha256']).copy()
    old = old.assign(cycle_id='old-partial', listing_id='old-listing', capture_id='old-capture',
        observed_at_utc='2026-09-18T13:00:00Z')
    recovery = old.assign(cycle_id='old-recovery', capture_id='recovery-capture',
                          observed_at_utc='2026-09-18T15:00:00Z')
    days = pd.DataFrame([dict(cycle_id=identity, cycle_date='2026-09-18', timezone='America/New_York',
        window_start=clock, window_end=clock, available_at=clock, scope_id='legacy-panel',
        coverage_complete=False, coverage_reason='synthetic partial attempt')
        for identity, clock in [('old-partial', '2026-09-18T13:00:00Z'),
                                ('old-recovery', '2026-09-18T15:00:00Z')]], columns=CYCLE_COLUMNS)
    original = pd.concat([old, recovery], ignore_index=True)
    calls = []

    def read(paths, database, *, as_of):
        calls.append((paths, database, as_of))
        return days.copy(deep=True), original.copy(deep=True)

    monkeypatch.setattr(catalog_history, 'read_cycle_history', read)
    selected = dict(cycle_paths=['explicit/partial.json', 'explicit/recovery.json'], database='explicit/history.sqlite')
    cutoff = after_publication(output)
    history, cohorts, members = catalog_history.observed_catalog_history(
        catalog_exports=[output], legacy_sources=[selected], as_of=cutoff)
    known = history.set_index('vin').loc[old.vin.iloc[0]]
    assert known.first_observed_at == pd.Timestamp('2026-09-18T13:00:00Z')
    assert known.observed_scopes == 2 and known.observed_cycles == 3
    assert known.first_evidence_includes_partial and not known.known_only_from_partial_cycles
    assert 'old-listing' in known.listing_ids
    assert len(members) == 28 and members.context_json.notna().all()
    assert cohorts.observed_vins.sum() == 26
    assert calls == [(selected['cycle_paths'], selected['database'], cutoff)]
    assert len(original) == 2 and original.listing_id.eq('old-listing').all()


@pytest.mark.parametrize('target', ['history.sqlite', 'observations.csv', 'retained_source', 'missing_source'])
def test_source_or_export_tampering_fails_closed(exported, target):
    e, output = exported
    cutoff = after_publication(output)
    if target in {'retained_source', 'missing_source'}:
        report = json.loads((e.folder/'make_000_model_000/run_report.json').read_text())
        path = Path(report['pages'][0]['retained_source'])
    else:
        path = output/target
    if target == 'missing_source':
        path.unlink()
    else:
        path.write_bytes(path.read_bytes()+b' changed')
    with pytest.raises(ValueError, match='manifest source/output changed or is missing'):
        catalog_history.observed_catalog_history(catalog_exports=[output], as_of=cutoff)


def test_unbound_primary_database_and_repeated_inputs_are_rejected(exported):
    _, output = exported
    cutoff = after_publication(output)
    with pytest.raises(ValueError, match='duplicate or unknown observation-cycle'):
        catalog_history.observed_catalog_history(catalog_exports=[output, output], as_of=cutoff)
    path = output/'manifest.json'
    manifest = json.loads(path.read_text())
    del manifest['outputs']['history.sqlite']
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='not bound to the export manifest'):
        catalog_history.observed_catalog_history(catalog_exports=[output], as_of=cutoff)


def test_no_inputs_returns_defined_empty_tables():
    history, cohorts, members = catalog_history.observed_catalog_history(as_of='2026-09-19T13:00Z')
    assert history.empty and cohorts.empty and members.empty
    assert 'first_observed_at' in history and 'observed_vins' in cohorts
    with pytest.raises(ValueError, match='timezone aware'):
        catalog_history.observed_catalog_history(as_of='2026-09-19')


def test_v3_population_scope_ignores_daily_leaf_plans():
    config = dict(primary_zip='33130')
    first = dict(format='carvana-full-inventory-run-v3',
                 leaf_queries=[dict(query_id='year_2024_make_chevy')])
    second = dict(format='carvana-full-inventory-run-v3',
                  leaf_queries=[dict(query_id='year_2024_make_chevy_model_silverado')])
    assert catalog_history.catalog_population_scope(config, first) == (
        catalog_history.catalog_population_scope(config, second))
    v1_a = dict(leaf_queries=first['leaf_queries'])
    v1_b = dict(leaf_queries=second['leaf_queries'])
    assert catalog_history.catalog_population_scope(config, v1_a) != (
        catalog_history.catalog_population_scope(config, v1_b))
    digest_a = catalog_history.leaf_plan_digest(first['leaf_queries'])
    digest_b = catalog_history.leaf_plan_digest(second['leaf_queries'])
    assert digest_a != digest_b


def test_same_listing_overlap_collapses_and_two_listings_fail():
    shared = dict(cycle_id='c1', retailer='carvana', vin='1GC3KSE77SF161034',
                  listing_id='1001', observed_at_utc='2026-09-25T05:00:00Z')
    rows = pd.DataFrame([
        dict(shared, query_id='year_2025_make_chevy', run_id='r1'),
        dict(shared, query_id='year_2025_make_chevy_silverado', run_id='r2',
             observed_at_utc='2026-09-25T05:01:00Z')])
    collapsed = catalog_history.collapse_same_day_memberships(rows)
    assert len(collapsed) == 1
    assert collapsed.iloc[0].run_id == 'r1'
    aliases = json.loads(collapsed.iloc[0].source_aliases_json)
    assert {item['query_id'] for item in aliases} == {
        'year_2025_make_chevy', 'year_2025_make_chevy_silverado'}
    conflict = rows.copy()
    conflict.loc[1, 'listing_id'] = '1002'
    with pytest.raises(ValueError, match='Conflicting VIN/listing'):
        catalog_history.collapse_same_day_memberships(conflict)


def test_unverified_cells_come_from_incomplete_union_leaves():
    report = dict(leaf_queries=[
        dict(query_id='tesla', filters=dict(year=dict(min=2020, max=2020),
                                           makes=[dict(name='Tesla')])),
        dict(query_id='chevy', filters=dict(year=dict(min=2020, max=2020),
                                           makes=[dict(name='Chevrolet')]))],
        leaf_union=[dict(query_id='tesla', complete_by_union=False),
                    dict(query_id='chevy', complete_by_union=True)])
    assert catalog_history.unverified_cells_from_report(report) == [
        dict(year_min=2020, year_max=2020, make='Tesla')]
    days = pd.DataFrame([dict(cycle_id='c1', unverified_cells_json=json.dumps(
        catalog_history.unverified_cells_from_report(report)))])
    assert catalog_history.unassessable_cells_from_days(days)['c1'][0]['make'] == 'Tesla'
