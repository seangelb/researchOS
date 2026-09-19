"""Changed inventory and interrupted plans remain distinct from execution failure."""
import copy
import json
import pandas as pd
import pytest

from test_catalog import experiment, run
from vehicle_tracker import catalog
from vehicle_tracker.collect import CollectionStopped


def test_complete_sweep_keeps_closing_count_drift_explicit(experiment, tmp_path):
    e = experiment

    def send(url, **kwargs):
        response = e.send(url, **kwargs)
        if len(e.starts) == 11:  # Closing discovery after every declared query.
            data = json.loads(response.content)
            data['inventory']['pagination']['totalMatchedInventory'] += 1
            data['facetData']['makes']['Tesla']['count'] += 1
            response.content = json.dumps(data).encode()
        return response

    report = run(e, send)
    assert report['declared_collection_complete']
    assert report['primary_queries_complete']
    assert not report['primary_scope_reconciled']
    assert report['closing_count_residual'] == 1
    assert report['national_coverage_verified'] is False
    catalog.export_catalog(e.folder, output=tmp_path/'drift')


def test_complete_zip_check_can_identify_membership_difference(experiment):
    e = experiment

    def send(url, **kwargs):
        response = e.send(url, **kwargs)
        if kwargs['json']['zip5'] != '08542':
            data = json.loads(response.content)
            for vehicle in data['inventory']['vehicles']:
                vehicle['vehicleId'] += 1000
                vehicle['vin'] = f"5YJ3E1EA0MF{vehicle['vehicleId']:06d}"
            response.content = json.dumps(data).encode()
        return response

    report = run(e, send)
    assert report['declared_collection_complete']
    assert report['geographic_checks_complete']
    assert not report['geographic_membership_stable']
    assert all(check['additional_vins'] for check in report['geographic_checks'])
    assert report['national_coverage_verified'] is False


def test_early_stop_keeps_unattempted_declared_leaves(experiment, monkeypatch, tmp_path):
    e = experiment
    original = catalog.collect_search

    def stop_before_first_leaf(**kwargs):
        if kwargs['destination'].name == 'make_000_model_000':
            raise CollectionStopped('Original window exhausted before query')
        return original(**kwargs)

    monkeypatch.setattr(catalog, 'collect_search', stop_before_first_leaf)
    report = run(e)
    assert not report['declared_collection_complete']
    output = catalog.export_catalog(e.folder, output=tmp_path/'stopped')
    coverage = pd.read_csv(output/'coverage.csv').set_index('query_id')
    for q in report['leaf_queries']:
        assert coverage.loc[q['query_id'], 'status'] == 'unattempted'
        assert not coverage.loc[q['query_id'], 'query_complete']
    reconciled = pd.read_csv(output/'make_reconciliation.csv').iloc[0]
    assert reconciled.declared_leaf_queries == 2
    assert reconciled.attempted_leaf_queries == 0
    assert not reconciled.all_leaf_queries_complete
    assert pd.isna(reconciled.leaf_reported_total_sum)


def test_early_stop_keeps_unattempted_geographic_checks(experiment, monkeypatch, tmp_path):
    e = experiment
    original = catalog.collect_search

    def stop_before_geography(**kwargs):
        if kwargs['destination'].name.startswith('broad_zip_'):
            raise CollectionStopped('Original window exhausted before geographic query')
        return original(**kwargs)

    monkeypatch.setattr(catalog, 'collect_search', stop_before_geography)
    report = run(e)
    output = catalog.export_catalog(e.folder, output=tmp_path/'stopped')
    coverage = pd.read_csv(output/'coverage.csv').set_index('query_id')
    for check in report['planned_geographic_checks']:
        assert coverage.loc[check['query']['query_id'], 'status'] == 'unattempted'
    assert len(report['planned_geographic_checks']) == 4
    for name in ['broad_zip_98101', 'broad_zip_33130', 'broad_close']:
        assert coverage.loc[name, 'status'] == 'unattempted'


def test_failed_opening_retains_all_configured_broad_probe_denominators(experiment, tmp_path):
    e = experiment

    def send(url, **kwargs):
        response = e.send(url, **kwargs)
        response.status_code = 403
        return response

    run(e, send)
    output = catalog.export_catalog(e.folder, output=tmp_path/'failed_opening')
    coverage = pd.read_csv(output/'coverage.csv').set_index('query_id')
    assert coverage.loc['broad_open', 'status'] == 'blocked'
    for name in ['broad_zip_98101', 'broad_zip_33130', 'broad_close']:
        assert coverage.loc[name, 'status'] == 'unattempted'
    assert pd.read_csv(output/'make_reconciliation.csv').empty


def test_reconciliation_failure_still_publishes_terminal_stop(experiment, monkeypatch):
    e = experiment

    def fail_reconciliation(*args):
        raise ValueError('Invalid diagnostic source clock')

    monkeypatch.setattr(catalog, '_make_reconciliation', fail_reconciliation)
    report = run(e)
    assert report['status'] == 'stopped'
    assert report['ended_at'] and report['reconciliation_failure_type'] == 'ValueError'
    assert not report['declared_collection_complete']
    assert (e.folder.parent/'access_stop.json').is_file()


def test_fatal_make_probe_is_not_reinterpreted_as_admitted_discovery(experiment, tmp_path):
    e = experiment

    def send(url, **kwargs):
        response = e.send(url, **kwargs)
        if kwargs['json']['filters']:
            data = json.loads(response.content)
            data['inventory']['vehicles'][0]['vin'] = '5YJ3E1EA0MF999999'
            response.content = json.dumps(data).encode()
        return response

    report = run(e, send)
    assert report['status'] == 'stopped' and report['leaf_queries'] == []
    output = catalog.export_catalog(e.folder, output=tmp_path/'fatal')
    coverage = pd.read_csv(output/'coverage.csv').set_index('query_id')
    assert coverage.loc['make_000', 'status'] == 'blocked'
    assert not coverage.loc['make_000', 'query_complete']


def test_make_reconciliation_uses_native_totals_rows_and_clocks(experiment, tmp_path):
    e = experiment
    report = run(e)
    output = catalog.export_catalog(e.folder, output=tmp_path/'reconciliation')
    saved = report['make_reconciliation'][0]
    assert saved['discovery_native_count'] == saved['native_model_count_sum'] == 26
    assert saved['leaf_reported_total_sum'] == saved['observed_unique_vins'] == 26
    assert saved['native_model_count_residual'] == saved['discovery_minus_leaf_native_count'] == 0
    assert saved['discovery_minus_observed_vins'] == saved['leaf_native_minus_observed_vins'] == 0
    assert saved['all_leaf_queries_complete'] and saved['completed_leaf_queries'] == 2
    assert saved['discovery_observed_at_utc'] < saved['inventory_observation_start'] <= saved['inventory_observation_end']
    assert pd.read_csv(output/'make_reconciliation.csv').shape[0] == 1


def test_opposite_make_residuals_do_not_cancel_in_diagnostics():
    report = dict(planned_make_probes=[], leaf_queries=[], entries=[])
    primary = {}
    for make, count in [('Tesla', 12), ('Ford', 8)]:
        q = catalog.query(make, '08542', {'makes': [{'name': make}]})
        report['planned_make_probes'].append(q)
        report['leaf_queries'].append(q)
        report['entries'].append(dict(query=q, reported_total=10, query_complete=False,
            report='retained-report', native_model_count_sum=10))
        primary[make] = pd.DataFrame(dict(retailer=['carvana']*count,
            vin=[f'{make}{i}' for i in range(count)], observed_at_utc=['2026-09-19T13:00:00Z']*count))
    rows = catalog._make_reconciliation(report, primary)
    assert [row['discovery_minus_observed_vins'] for row in rows] == [-2, 2]
    assert not any(row['all_leaf_queries_complete'] for row in rows)


def test_make_clocks_accept_mixed_iso_precision():
    q = catalog.query('Tesla', '08542', {'makes': [{'name': 'Tesla'}]})
    report = dict(planned_make_probes=[q], leaf_queries=[q],
        entries=[dict(query=q, reported_total=2, query_complete=True, report='retained-report')])
    primary = {'Tesla': pd.DataFrame(dict(retailer=['carvana']*2, vin=['a','b'],
        observed_at_utc=['2026-09-19T13:00:00Z', '2026-09-19T13:00:01.125+00:00']))}
    row = catalog._make_reconciliation(report, primary)[0]
    assert row['inventory_observation_start'] == '2026-09-19T13:00:00+00:00'
    assert row['inventory_observation_end'] == '2026-09-19T13:00:01.125000+00:00'


@pytest.mark.parametrize('field', ['appliedMin', 'appliedMax'])
def test_applied_year_boundary_stops_all_year_discovery(experiment, field):
    e = experiment

    def send(url, **kwargs):
        response = e.send(url, **kwargs)
        data = json.loads(response.content)
        data['facetData']['year'][field] = 2020
        response.content = json.dumps(data).encode()
        return response

    report = run(e, send)
    assert report['status'] == 'stopped' and report['requests'] == 1
    assert 'applied year' in report['failure_reason']
    assert (e.folder.parent/'access_stop.json').is_file()


@pytest.mark.parametrize('make', ['Tesla', 'Ford'])
def test_unrequested_applied_model_stops_make_discovery_without_fallback(experiment, make):
    e = experiment

    def send(url, **kwargs):
        response = e.send(url, **kwargs)
        if kwargs['json']['filters']:
            data = json.loads(response.content)
            bucket = data['facetData']['makes']['Tesla']
            if make != 'Tesla':
                bucket = copy.deepcopy(bucket)
                bucket.update(key=make, isApplied=False)
                data['facetData']['makes'][make] = bucket
            bucket['parentModels'][0]['isApplied'] = True
            response.content = json.dumps(data).encode()
        return response

    report = run(e, send)
    assert report['status'] == 'stopped' and report['requests'] == 2
    assert 'applied model' in report['failure_reason']
    assert report['leaf_queries'] == []
    assert not any(entry['role'] == 'primary_inventory' for entry in report['entries'])
    assert (e.folder.parent/'access_stop.json').is_file()


@pytest.mark.parametrize('kind', ['model_count', 'model_id', 'other_make_count'])
@pytest.mark.parametrize('value', [-1, 1.5, True, None])
def test_invalid_native_numbers_stop_instead_of_whole_make_fallback(experiment, kind, value):
    e = experiment

    def send(url, **kwargs):
        response = e.send(url, **kwargs)
        if kwargs['json']['filters']:
            data = json.loads(response.content)
            bucket = data['facetData']['makes']['Tesla']
            if kind == 'model_count':
                bucket['parentModels'][0]['count'] = value
            elif kind == 'model_id':
                bucket['parentModels'][0]['modelIds'] = [value]
            else:
                other = copy.deepcopy(bucket)
                other.update(key='Ford', count=value, isApplied=False, parentModels=[])
                data['facetData']['makes']['Ford'] = other
            response.content = json.dumps(data).encode()
        return response

    report = run(e, send)
    assert report['status'] == 'stopped' and report['requests'] == 2
    assert report['leaf_queries'] == []
    assert (e.folder.parent/'access_stop.json').is_file()
