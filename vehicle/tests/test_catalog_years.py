"""Synthetic year-first transport tests; no live endpoint support is implied."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from vehicle_tracker import catalog


@pytest.fixture
def year_experiment(tmp_path, monkeypatch):
    class Clock:
        seconds = 0

        def now(self, tz=None):
            return datetime(2026, 9, 19, 13, tzinfo=timezone.utc) + timedelta(seconds=self.seconds)

        def sleep(self, seconds):
            self.seconds += seconds

        def monotonic(self):
            return 1000 + self.seconds

    clock = Clock()
    monkeypatch.setattr(catalog, 'utcnow', clock.now)
    monkeypatch.setattr('vehicle_tracker.cycles.utcnow', clock.now)
    monkeypatch.setattr('vehicle_tracker.search.datetime', SimpleNamespace(now=clock.now))
    monkeypatch.setattr('vehicle_tracker.collect.time.monotonic', clock.monotonic)
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', clock.sleep)
    config = json.loads((Path(__file__).parents[1] / 'config/carvana_full_inventory.json').read_text())
    config.update(format='carvana-full-inventory-v2', partition_strategy='year_then_make_model',
                  capture_root=str(tmp_path / 'captures'), validation_queries_per_zip=2,
                  related_capture_roots=[str(tmp_path / 'peer_captures')])
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    vehicles = []

    def add(year, make, model, count):
        for _ in range(count):
            number = len(vehicles) + 1
            vehicles.append(dict(vehicleId=number, vin=f'5YJ3E1EA0MF{number:06d}',
                year=year, make=make, model=model, parentModel=model, mileage=10000,
                price={'total': 25000}, isPurchasePending=False, vehicleLockType=0,
                vehiclePurchaseType='Purchase', isOnDemand=False))

    for group in [(2009, 'Audi', 'A4', 2), (2009, 'Tesla', 'Model 3', 1),
                  (2010, 'Audi', 'A4', 13), (2010, 'Audi', 'A5', 13),
                  (2010, 'Tesla', 'Model 3', 2), (2011, 'Tesla', 'Model Y', 4),
                  (2012, 'Audi', 'A5', 1), (2013, 'Audi', 'A4', 1),
                  (2013, 'Tesla', 'Model Y', 2)]:
        add(*group)

    starts, requests = [], []
    model_ids = {'A4': 1, 'A5': 2, 'Model 3': 3, 'Model Y': 4}

    def send(url, **kwargs):
        assert url == catalog.ENDPOINT and kwargs['allow_redirects'] is False
        assert 0 < kwargs['timeout'] <= 30
        starts.append(clock.now())
        request = copy.deepcopy(kwargs['json'])
        requests.append(request)
        filters = request['filters']
        bounds = filters.get('year', {})
        selected_make = filters.get('makes', [{}])[0].get('name')
        selected_models = filters.get('makes', [{}])[0].get('parentModels', [])
        year_rows = [v for v in vehicles
                     if ('min' not in bounds or v['year'] >= bounds['min'])
                     and ('max' not in bounds or v['year'] <= bounds['max'])]
        rows = [v for v in year_rows if (selected_make is None or v['make'] == selected_make)
                and (not selected_models or v['parentModel'] == selected_models[0]['name'])]
        makes = {}
        for make in ['Audi', 'Tesla']:
            make_rows = [v for v in year_rows if v['make'] == make]
            children = [dict(key=model, count=sum(v['parentModel'] == model for v in make_rows),
                             isApplied=False, modelIds=[model_ids[model]])
                        for model in sorted({v['parentModel'] for v in make_rows})]
            makes[make] = dict(key=make, count=len(make_rows), isApplied=make == selected_make,
                               parentModels=children if make == selected_make else [])
        native_year = dict(min=2010, max=2012)
        native_year.update({'applied' + key.title(): value for key, value in bounds.items()})
        page = request['pagination']['page']
        data = dict(userDeliveryInfo={'zip5': request['zip5']},
            inventory=dict(pagination=dict(currentPage=page, pageSize=24,
                totalMatchedInventory=len(rows), totalMatchedPages=(len(rows) + 23) // 24),
                vehicles=copy.deepcopy(rows[(page - 1) * 24:page * 24])),
            facetData=dict(year=native_year, makes=makes))
        clock.sleep(.1)
        response = SimpleNamespace(content=json.dumps(data).encode(), status_code=200,
            headers={'content-type': 'application/json'}, close=lambda: None)
        response.json = lambda: json.loads(response.content)
        return response

    return SimpleNamespace(path=path, config=config, folder=tmp_path / 'captures/2026-09-19',
        send=send, clock=clock, vehicles=vehicles, add=add, starts=starts, requests=requests)


def run_years(experiment, post=None):
    return catalog.collect_catalog(experiment.path,
        expected_sha256=catalog.digest(experiment.path), post=post or experiment.send)


run = run_years


def test_year_first_complete_capture_keeps_positive_tails_zeros_and_replay(year_experiment, tmp_path):
    e = year_experiment
    report = run(e)
    assert report['status'] == 'collection_finished'
    assert report['partition_strategy'] == 'year_then_make_model'
    assert report['primary_queries_complete'] and report['declared_collection_complete']
    assert report['primary_observed_vins'] == len(e.vehicles) == 39
    assert report['year_native_count_sum'] == report['year_native_count_partial_sum'] == 39
    assert report['opening_minus_year_native_count'] == 0
    assert report['year_tail_counts'] == {'lower_tail': 3, 'upper_tail': 3}
    assert report['missing_or_noninteger_year_count'] is None
    assert report['national_coverage_verified'] is False and report['estimated_sales'] is None
    assert [q['filters']['year'] for q in report['planned_year_probes']] == [
        {'max': 2009}, {'min': 2010, 'max': 2010}, {'min': 2011, 'max': 2011},
        {'min': 2012, 'max': 2012}, {'min': 2013}]
    assert len(report['year_discoveries']) == len(report['year_reconciliation']) == 5
    assert all(row['context_validated'] for row in report['year_reconciliation'])
    assert all(row['native_minus_observed_vins'] == 0 for row in report['year_reconciliation'])
    assert {(row['year_query_id'], row['make'], row['native_count'])
            for row in report['native_zero_categories']} == {
                ('year_2011', 'Audi', 0), ('year_2012', 'Tesla', 0)}
    leaves = report['leaf_queries']
    assert all('year' in q['filters'] for q in leaves)
    assert {q['filters']['year'].get('max') for q in leaves
            if q['query_id'].startswith('year_lower_tail')} == {2009}
    assert {q['filters']['year'].get('min') for q in leaves
            if q['query_id'].startswith('year_upper_tail')} == {2013}
    assert all((b - a).total_seconds() >= 3 for a, b in zip(e.starts, e.starts[1:]))
    before = {p: p.read_bytes() for p in e.folder.rglob('*') if p.is_file()}
    output = catalog.export_catalog(e.folder, output=tmp_path / 'export')
    observations = pd.read_csv(output / 'observations.csv')
    assert set(observations.vin) == {v['vin'] for v in e.vehicles}
    assert observations.year.min() == 2009 and observations.year.max() == 2013
    assert len(observations) == 39 and observations.query_id.isin([q['query_id'] for q in leaves]).all()
    year_coverage = pd.read_csv(output / 'year_reconciliation.csv')
    assert set(year_coverage.query_id) == {q['query_id'] for q in report['planned_year_probes']}
    assert year_coverage.observed_unique_vins.sum() == 39
    assert json.loads((output / 'native_zero_categories.json').read_text()) == report['native_zero_categories']
    with sqlite3.connect((output / 'history.sqlite').as_uri() + '?mode=ro', uri=True) as connection:
        assert connection.execute('SELECT COUNT(*), COUNT(DISTINCT vin) FROM observations').fetchone() == (39, 39)
        assert connection.execute('SELECT COUNT(*) FROM observations WHERE year < 2010 OR year > 2012').fetchone()[0] == 6
    assert all(row['observed_at_utc'] for row in report['year_reconciliation'])
    assert before == {p: p.read_bytes() for p in e.folder.rglob('*') if p.is_file()}


def test_small_year_make_probe_is_reused_and_large_cell_splits_models(year_experiment):
    e = year_experiment
    report = run(e)
    by_id = {entry['query']['query_id']: entry for entry in report['entries']}
    small = next(q for q in report['leaf_queries']
                 if q['filters'] == {'makes': [{'name': 'Tesla'}], 'year': {'min': 2011, 'max': 2011}})
    assert by_id[small['query_id']]['role'] == 'make_discovery'
    assert by_id[small['query_id']]['query_complete']
    assert len([r for r in e.requests if r['zip5'] == '08542' and r['filters'] == small['filters']]) == 1
    audi = [q for q in report['leaf_queries']
            if q['filters']['year'] == {'min': 2010, 'max': 2010}
            and q['filters']['makes'][0]['name'] == 'Audi']
    assert {q['filters']['makes'][0]['parentModels'][0]['name'] for q in audi} == {'A4', 'A5'}
    assert all(by_id[q['query_id']]['query_complete'] for q in audi)


def test_empty_year_is_validated_zero_without_invented_make_or_leaf_queries(year_experiment):
    e = year_experiment
    e.vehicles[:] = [vehicle for vehicle in e.vehicles if vehicle['year'] != 2011]
    report = run(e)
    assert report['primary_queries_complete']
    assert report['primary_observed_vins'] == report['year_native_count_sum'] == 35
    row = next(row for row in report['year_reconciliation'] if row['query_id'] == 'year_2011')
    assert row['context_validated'] and row['reported_total'] == 0
    assert row['declared_make_queries'] == row['declared_leaf_queries'] == row['observed_unique_vins'] == 0
    assert not any(q['query_id'].startswith('year_2011_') for q in report['planned_make_probes'])
    assert {(zero['make'], zero['native_count']) for zero in report['native_zero_categories']
            if zero['year_query_id'] == 'year_2011'} == {('Audi', 0), ('Tesla', 0)}


@pytest.mark.parametrize('problem', ['missing', 'extra', 'wrong', 'boolean'])
@pytest.mark.parametrize('stage', ['year_only', 'make_year'])
def test_applied_year_bounds_fail_globally_before_later_work(year_experiment, problem, stage):
    e = year_experiment
    mutated = []

    def send(url, **kwargs):
        response = e.send(url, **kwargs)
        filters = kwargs['json']['filters']
        if filters.get('year') == {'max': 2009} and bool(filters.get('makes')) == (stage == 'make_year'):
            data = json.loads(response.content)
            year = data['facetData']['year']
            if problem == 'missing':
                del year['appliedMax']
            elif problem == 'extra':
                year['appliedMin'] = 1900
            elif problem == 'wrong':
                year['appliedMax'] = 2010
            else:
                year['appliedMax'] = True
            response.content = json.dumps(data).encode()
            mutated.append(len(e.requests))
        return response

    result = run(e, send)
    assert result['status'] == 'stopped' and not result['primary_queries_complete']
    assert (e.folder.parent / 'access_stop.json').is_file()
    assert len(mutated) == 1 and result['requests'] == mutated[0] == len(e.requests)
    assert result['year_native_count_sum'] is None


def test_budget_stop_keeps_all_years_and_known_make_candidates_in_denominator(year_experiment, tmp_path):
    e = year_experiment
    e.config['max_requests'] = 5
    e.path.write_text(json.dumps(e.config), encoding='utf-8')
    result = run(e)
    assert result['requests'] == len(e.requests) == 5
    assert not result['primary_queries_complete'] and not result['declared_collection_complete']
    assert len(result['planned_year_probes']) == len(result['year_reconciliation']) == 5
    assert result['year_native_count_sum'] is None
    assert result['opening_minus_year_native_count'] is None
    assert result['year_native_count_partial_sum'] == sum(d['reported_total'] for d in result['year_discoveries'])
    known_candidates = {c['query_id'] for d in result['year_discoveries'] for c in d['candidates']}
    assert known_candidates == {q['query_id'] for q in result['planned_make_probes']}
    not_validated = [row for row in result['year_reconciliation'] if not row['context_validated']]
    assert not_validated and all(row['reported_total'] is None for row in not_validated)
    output = catalog.export_catalog(e.folder, output=tmp_path / 'partial')
    coverage = pd.read_csv(output / 'coverage.csv')
    assert coverage.query_id.is_unique
    assert {q['query_id'] for q in result['planned_year_probes']} | known_candidates <= set(coverage.query_id)
    assert coverage.status.eq('unattempted').any()


def test_year_model_pagination_gap_is_preserved_while_other_leaves_finish(year_experiment, tmp_path):
    e = year_experiment
    e.add(2010, 'Audi', 'A4', 13)
    failed_request_indexes = []

    def send(url, **kwargs):
        response = e.send(url, **kwargs)
        request = kwargs['json']
        if (request['zip5'] == '08542' and request['pagination']['page'] == 2
                and request['filters'] == {'makes': [{'name': 'Audi', 'parentModels': [{'name': 'A4'}]}],
                                           'year': {'min': 2010, 'max': 2010}}):
            data = json.loads(response.content)
            data['inventory']['vehicles'][0] = copy.deepcopy(next(v for v in e.vehicles
                if v['year'] == 2010 and v['make'] == 'Audi' and v['parentModel'] == 'A4'))
            response.content = json.dumps(data).encode()
            failed_request_indexes.append(len(e.requests))
        return response

    result = run(e, send)
    assert len(failed_request_indexes) == 1
    assert not result['primary_queries_complete'] and not result['declared_collection_complete']
    assert result['year_native_count_sum'] == len(e.vehicles)
    assert not (e.folder.parent / 'access_stop.json').exists()
    failed = [entry for entry in result['entries'] if entry.get('failure_scope')]
    assert len(failed) == 1 and not failed[0]['query_complete']
    assert any(entry.get('query_complete') and entry['query']['query_id'].startswith('year_upper_tail')
               for entry in result['entries'])
    year = next(row for row in result['year_reconciliation'] if row['query_id'] == 'year_2010')
    assert year['completed_leaf_queries'] < year['declared_leaf_queries']
    assert year['native_minus_observed_vins'] > 0
    output = catalog.export_catalog(e.folder, output=tmp_path / 'pagination-gap')
    coverage = pd.read_csv(output / 'coverage.csv')
    assert not coverage.loc[coverage.query_id.eq(failed[0]['query']['query_id']), 'query_complete'].item()
    assert result['estimated_sales'] is None


@pytest.mark.parametrize('field', ['year_plan', 'year_probes', 'candidate', 'zero',
                                  'year_reconciliation', 'year_sum', 'year_partial_sum', 'tail_count'])
def test_replay_rejects_changed_year_population_and_diagnostics(year_experiment, tmp_path, field):
    e = year_experiment
    report = run(e)
    assert report['primary_queries_complete']
    catalog.export_catalog(e.folder, output=tmp_path / 'untampered-export')
    if field == 'year_plan':
        report['year_plan']['partitions'].pop(0)
    elif field == 'year_probes':
        report['planned_year_probes'].pop(0)
    elif field == 'candidate':
        report['year_discoveries'][0]['candidates'][0]['native_count'] += 1
    elif field == 'zero':
        report['native_zero_categories'][0]['native_count'] = 1
    elif field == 'year_reconciliation':
        report['year_reconciliation'][0]['observed_unique_vins'] += 1
    elif field == 'year_sum':
        report['year_native_count_sum'] += 1
    elif field == 'year_partial_sum':
        report['year_native_count_partial_sum'] += 1
    else:
        report['year_tail_counts']['lower_tail'] = 0
    (e.folder / 'catalog_report.json').write_text(json.dumps(report), encoding='utf-8')
    output = tmp_path / 'tampered-export'
    with pytest.raises(ValueError):
        catalog.export_catalog(e.folder, output=output)
    assert not output.exists()


@pytest.mark.parametrize('format_name,strategy', [
    ('carvana-full-inventory-v1', 'year_then_make_model'),
    ('carvana-full-inventory-v2', 'all_year_models'),
    ('carvana-full-inventory-v2', None),
    ('carvana-full-inventory-v2', 'fixed_year_grid'),
])
def test_inconsistent_config_version_and_strategy_rejected_without_requests(year_experiment, format_name, strategy):
    e = year_experiment
    e.config['format'] = format_name
    if strategy is None:
        e.config.pop('partition_strategy', None)
    else:
        e.config['partition_strategy'] = strategy
    e.path.write_text(json.dumps(e.config), encoding='utf-8')
    post = Mock()
    with pytest.raises(ValueError):
        run(e, post)
    post.assert_not_called()
    assert not e.folder.exists()


def test_legacy_config_keeps_default_all_year_strategy(year_experiment):
    e = year_experiment
    e.config['format'] = 'carvana-full-inventory-v1'
    e.config.pop('partition_strategy')
    e.path.write_text(json.dumps(e.config), encoding='utf-8')
    result = run(e)
    assert result['partition_strategy'] == 'all_year_models'
    assert result['primary_queries_complete'] and result['primary_observed_vins'] == 39
    assert all('year' not in q['filters'] for q in result['leaf_queries'])


@pytest.mark.parametrize('failure', ['identity_failure', 'schema_failure'])
def test_fatal_year_probe_keeps_evidence_without_admitting_valid_facet_metadata(year_experiment, tmp_path, failure):
    e = year_experiment

    def send(url, **kwargs):
        response = e.send(url, **kwargs)
        if kwargs['json']['filters'] == {'year': {'min': 2010, 'max': 2010}}:
            data = json.loads(response.content)
            vehicle = data['inventory']['vehicles'][0]
            if failure == 'identity_failure':
                # Its original listing/VIN was observed by the broad opening probe.
                vehicle['vin'] = '5YJ3E1EA0MF999999'
            else:
                # Schema validation fails before the query's total is admitted.
                vehicle['year'] = 2009
            response.content = json.dumps(data).encode()
        return response

    result = run(e, send)
    failed = next(entry for entry in result['entries'] if entry['query']['query_id'] == 'year_2010')
    assert failed['outcome_kind'] == failure
    assert not failed.get('year_context_validated')
    child = json.loads(Path(failed['report']).read_text())
    assert child['pages'][0]['facet_source']
    assert child['reported_total'] == (28 if failure == 'identity_failure' else None)
    assert result['status'] == 'stopped' and (e.folder.parent/'access_stop.json').exists()
    assert [item['partition']['query_id'] for item in result['year_discoveries']] == ['year_lower_tail']
    assert len(result['planned_year_probes']) == 5
    assert all(q['filters']['year'] == {'max': 2009} for q in result['planned_make_probes'])
    assert result['year_native_count_sum'] is None
    before = {p: p.read_bytes() for p in e.folder.rglob('*') if p.is_file()}
    output = catalog.export_catalog(e.folder, output=tmp_path/'fatal-year-export')
    assert len(pd.read_csv(output/'observations.csv')) == 3
    ledger = pd.read_csv(output/'coverage.csv')
    assert set(q['query_id'] for q in result['planned_year_probes']) <= set(ledger.query_id)
    years = pd.read_csv(output/'year_reconciliation.csv')
    assert not years.loc[years.query_id.eq('year_2010'), 'context_validated'].item()
    assert years.loc[years.query_id.eq('year_2010'), 'reported_total'].isna().all()
    manifest = json.loads((output/'manifest.json').read_text())
    page = child['pages'][0]
    assert manifest['sources'][page['facet_source']] == page['facet_sha256']
    assert manifest['sources'][page['retained_source']] == page['source_sha256']
    assert before == {p: p.read_bytes() for p in e.folder.rglob('*') if p.is_file()}


def test_model_subdivision_cannot_clip_a_positive_tail_year_filter(year_experiment, monkeypatch):
    e = year_experiment
    e.add(2009, 'Audi', 'A4', 26)
    original = catalog.model_partitions

    def clipped(*args, **kwargs):
        children, reason = original(*args, **kwargs)
        if kwargs.get('year_bounds') == {'max': 2009}:
            children = copy.deepcopy(children)
            children[0]['filters']['year']['min'] = 1900
        return children, reason

    monkeypatch.setattr(catalog, 'model_partitions', clipped)
    result = run(e)
    assert result['status'] == 'stopped'
    assert 'clips or changes its mandatory year context' in result['failure_reason']
    assert not result['leaf_queries'] and not result['primary_queries_complete']
    assert len(e.requests) == 3  # Broad, year tail, make probe; no clipped child request.
