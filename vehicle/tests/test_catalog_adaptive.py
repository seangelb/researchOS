"""Adaptive year/make planning. No live requests."""
from datetime import datetime, timezone
import json
from pathlib import Path

import importlib.util

import pandas as pd
import pytest

from vehicle_tracker.catalog_plan import adaptive_cell, adaptive_estimate, page_count
from vehicle_tracker.catalog_reconcile import _coverage_union
from vehicle_tracker.catalog_schedule import daily_decision, read_attempt_outcome
from vehicle_tracker.catalog import export_catalog
from test_catalog_schedule import _config
from test_catalog_years import run_years, year_experiment


def _runner():
    path = Path(__file__).parents[1] / 'scripts' / 'run_carvana_full_inventory.py'
    spec = importlib.util.spec_from_file_location('run_carvana_full_inventory', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_small_cell_is_one_query_and_a_large_cell_waits_for_models():
    small = adaptive_cell(dict(count=10, parentModels=[]), make='Audi', query_id='year_2010_make_000',
                          year_bounds={'min': 2010, 'max': 2010}, zip_code='08542', threshold=480)
    assert small['kind'] == 'whole' and small['requests'] == 1 and small['probe'] is None
    large = adaptive_cell(dict(count=500, parentModels=[]), make='Jeep', query_id='year_2026_make_001',
                          year_bounds={'min': 2026, 'max': 2026}, zip_code='08542', threshold=480)
    assert large['kind'] == 'probe' and large['leaves'] == []
    assert large['requests'] == 1 + page_count(500)
    models = [dict(key='A', count=200, modelIds=[1]), dict(key='B', count=300, modelIds=[2])]
    split = adaptive_cell(dict(count=500, parentModels=models), make='Jeep', query_id='year_2026_make_001',
                          year_bounds={'min': 2026, 'max': 2026}, zip_code='08542', threshold=480)
    assert split['kind'] == 'split' and len(split['leaves']) == 2 and split['overlap'] is None
    shared = [dict(key='Silverado 3500', count=11, modelIds=[497, 3]),
              dict(key='Silverado 3500 HD Chassis Cab', count=1, modelIds=[497, 4])]
    overlap = adaptive_cell(dict(count=11, parentModels=shared), make='Chevrolet',
                            query_id='year_2025_make_006',
                            year_bounds={'min': 2025, 'max': 2025}, zip_code='08542', threshold=5)
    assert overlap['kind'] == 'split'
    assert overlap['reason'] == 'overlapping model ids; each model collected'
    assert overlap['overlap']['shared_model_ids'] == [497]
    assert overlap['overlap']['clusters'][0]['models'] == [
        'Silverado 3500', 'Silverado 3500 HD Chassis Cab']
    assert adaptive_estimate([small, split]) == small['requests'] + split['requests']


def test_union_of_two_passes_covers_a_reshuffled_leaf(monkeypatch):
    frames = {
        'leaf': pd.DataFrame({'vin': ['A', 'B']}),
        'leaf_pass2': pd.DataFrame({'vin': ['B', 'C']}),
    }

    def rows(folder):
        return frames[Path(folder).name]

    monkeypatch.setattr('vehicle_tracker.catalog_reconcile._rows', rows)
    report = dict(leaf_queries=[dict(query_id='leaf')], entries=[
        dict(query=dict(query_id='leaf'), reported_total=3, context_validated=True,
             report='x/leaf/run_report.json'),
        dict(query=dict(query_id='leaf_pass2'), retry_of='leaf', reported_total=3,
             context_validated=True, report='x/leaf_pass2/run_report.json')])
    result = _coverage_union(report)
    assert result['leaf_union'][0]['complete_by_union'] is True
    assert result['leaf_union'][0]['union_vins'] == 3
    assert result['unverified_native_count'] == 0


def test_legacy_http_520_is_degraded_not_an_access_stop(tmp_path):
    folder = tmp_path / '2026-09-23'
    child = folder / 'leaf'
    child.mkdir(parents=True)
    (child / 'run_report.json').write_text(json.dumps(dict(
        pages=[dict(http_status=520, status='failed')])), encoding='utf-8')
    (folder / 'catalog_report.json').write_text(json.dumps(dict(
        status='stopped', ended_at='2026-09-23T21:34:17+00:00', requests=1,
        failure_reason='http_access_failure',
        entries=[dict(outcome_kind='access_failure', report=str(child / 'run_report.json'))])),
        encoding='utf-8')
    assert read_attempt_outcome(folder)['kind'] == 'degraded'


def test_estimate_that_does_not_fit_the_evening_window_skips(tmp_path):
    path, _root = _config(tmp_path)
    config = json.loads(path.read_text(encoding='utf-8'))
    config['plan_estimate_requests'] = 7000
    path.write_text(json.dumps(config), encoding='utf-8')
    # 00:00 UTC is 20:00 Eastern the previous evening, with four hours left.
    decision = daily_decision(path, now=datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc))
    assert decision['action'] == 'skip'
    assert 'does not fit' in decision['reason']


def test_exit_codes_follow_the_attempt_kind():
    exit_code = _runner().exit_code
    assert exit_code(dict(attempt_outcome='complete', status='collection_finished')) == 0
    assert exit_code(dict(attempt_outcome='complete_with_gaps', status='collection_finished')) == 0
    assert exit_code(dict(attempt_outcome='incomplete', status='collection_finished')) == 2
    assert exit_code(dict(attempt_outcome='infeasible', status='collection_finished')) == 2
    assert exit_code(dict(attempt_outcome='access_stop', status='stopped')) == 1


def test_adaptive_run_collects_small_cells_without_a_separate_probe(year_experiment, tmp_path):
    experiment = year_experiment
    experiment.config.update(format='carvana-full-inventory-v3', partition_strategy='year_make_adaptive',
                             split_threshold_vehicles=480, max_unverified_share=0.005,
                             plan_estimate_requests=500)
    experiment.path.write_text(json.dumps(experiment.config), encoding='utf-8')
    report = run_years(experiment)
    assert report['format'] == 'carvana-full-inventory-run-v3'
    assert report['status'] == 'collection_finished'
    assert report['attempt_outcome'] in {'complete', 'complete_with_gaps'}
    assert report['primary_observed_vins'] == len(experiment.vehicles) == 39
    assert report['leaf_union'] and all(row['complete_by_union'] for row in report['leaf_union'])
    assert (experiment.folder / 'catalog_events.jsonl').is_file()
    events = (experiment.folder / 'catalog_events.jsonl').read_text(encoding='utf-8')
    assert 'query_intent' in events and 'query_result' in events
    export_catalog(experiment.folder, output=tmp_path / 'adaptive-export')
    assert (tmp_path / 'adaptive-export' / 'history.sqlite').is_file()


def test_adaptive_overlap_is_recorded_and_export_accepts_a_missing_cluster(year_experiment, tmp_path):
    experiment = year_experiment
    experiment.config.update(format='carvana-full-inventory-v3', partition_strategy='year_make_adaptive',
                             split_threshold_vehicles=24, max_unverified_share=0.005,
                             plan_estimate_requests=500)
    experiment.path.write_text(json.dumps(experiment.config), encoding='utf-8')
    shared = next(v for v in experiment.vehicles if v['year'] == 2010 and v['parentModel'] == 'A5')

    def send(url, **kwargs):
        response = experiment.send(url, **kwargs)
        request = kwargs['json']
        filters = request['filters']
        makes = filters.get('makes') or []
        if (makes and makes[0].get('name') == 'Audi'
                and filters.get('year') == {'min': 2010, 'max': 2010}
                and not makes[0].get('parentModels')):
            data = json.loads(response.content)
            bucket = data['facetData']['makes']['Audi']
            for child in bucket['parentModels']:
                if child['key'] in ('A4', 'A5'):
                    child['modelIds'] = [497, *child['modelIds']]
            data['inventory']['pagination']['totalMatchedInventory'] = (
                sum(child['count'] for child in bucket['parentModels']) - 1)
            data['inventory']['pagination']['totalMatchedPages'] = (
                data['inventory']['pagination']['totalMatchedInventory'] + 23) // 24
            bucket['count'] = data['inventory']['pagination']['totalMatchedInventory']
            response.content = json.dumps(data).encode()
        models = makes[0].get('parentModels') if makes else None
        if (models and models[0]['name'] == 'A4'
                and filters.get('year') == {'min': 2010, 'max': 2010}
                and request['pagination']['page'] == 1):
            data = json.loads(response.content)
            bucket = data['facetData']['makes']['Audi']
            for child in bucket['parentModels']:
                if child['key'] in ('A4', 'A5'):
                    child['modelIds'] = [497, *child.get('modelIds', [])]
            if not any(v.get('parentModel') == 'A5' for v in data['inventory']['vehicles']):
                data['inventory']['vehicles'] = [dict(shared, parentModel='A5', model='A5'),
                                                *data['inventory']['vehicles']]
                data['inventory']['pagination']['totalMatchedInventory'] += 1
                data['inventory']['pagination']['totalMatchedPages'] = (
                    data['inventory']['pagination']['totalMatchedInventory'] + 23) // 24
            response.content = json.dumps(data).encode()
        return response

    report = run_years(experiment, send)
    make = next(entry for entry in report['entries']
                if entry['role'] == 'make_discovery'
                and entry['query']['filters'].get('year') == {'min': 2010, 'max': 2010}
                and entry['query']['filters']['makes'][0]['name'] == 'Audi')
    assert make['partition_reason'] == 'overlapping model ids; each model collected'
    assert make['model_id_overlap']['shared_model_ids'] == [497]
    assert report['explained_duplicate_primary_memberships'] == 1
    assert report['unexplained_duplicate_primary_memberships'] == 0
    export_catalog(experiment.folder, output=tmp_path / 'recorded-export')
    saved = json.loads((experiment.folder / 'catalog_report.json').read_text(encoding='utf-8'))
    for entry in saved['entries']:
        if entry.get('role') == 'make_discovery':
            entry.pop('partition_reason', None)
            entry.pop('model_id_overlap', None)
    entries = {entry['query']['query_id']: entry for entry in saved['entries']}
    for row in saved.get('year_reconciliation') or []:
        bounds = {}
        if row.get('year_min') is not None:
            bounds['min'] = row['year_min']
        if row.get('year_max') is not None:
            bounds['max'] = row['year_max']
        probes = [query for query in saved.get('planned_make_probes') or []
                  if query['filters']['year'] == bounds]
        row['validated_make_queries'] = sum(
            'partition_reason' in entries.get(query['query_id'], {}) for query in probes)
    (experiment.folder / 'catalog_report.json').write_text(json.dumps(saved), encoding='utf-8')
    output = export_catalog(experiment.folder, output=tmp_path / 'derived-export')
    observations = pd.read_csv(output / 'observations.csv')
    assert shared['vin'] in set(observations.vin)


def test_retained_september_24_make_facets_plan_about_4068_requests():
    root = Path(__file__).parents[1] / 'data' / 'experiments' / 'carvana_full_inventory_years_r4' / '2026-09-24'
    report_path = root / 'catalog_report.json'
    if not report_path.is_file():
        pytest.skip('Retained September 24 capture is not in this checkout')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    cells = []
    for entry in report['entries']:
        if entry.get('role') != 'make_discovery' or not entry.get('report'):
            continue
        child = json.loads(Path(entry['report']).read_text(encoding='utf-8'))
        page = next((item for item in child['pages'] if item.get('facet_source')), None)
        if page is None:
            continue
        facet = json.loads(Path(page['facet_source']).read_text(encoding='utf-8'))
        make = entry['query']['filters']['makes'][0]['name']
        bucket = facet['facet_data']['makes'].get(make)
        if not bucket or not bucket.get('count'):
            continue
        cells.append(adaptive_cell(bucket, make=make, query_id=entry['query']['query_id'],
                                   year_bounds=entry['query']['filters']['year'], zip_code='08542', threshold=480))
    # Broad and year probes are the 21 requests already spent before this plan.
    estimate = 21 + adaptive_estimate(cells)
    assert 3900 <= estimate <= 4300
    assert estimate * 3 <= 21600 and estimate * 5 <= 21600
    assert estimate * 8 > 21600
