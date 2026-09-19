"""Year-first exports retain positive history without inventing complete coverage."""
import copy
import json

import pandas as pd

from test_catalog_years import year_experiment, run_years
from vehicle_tracker import catalog
from vehicle_tracker.catalog_history import observed_catalog_history, read_catalog_history
from vehicle_tracker.history import file_hash


def publication(output):
    return pd.Timestamp(json.loads((output/'manifest.json').read_text())['created_at'])


def test_year_and_tail_contexts_survive_without_zero_category_observations(year_experiment, tmp_path):
    e = year_experiment
    report = run_years(e)
    output = catalog.export_catalog(e.folder, output=tmp_path/'year-history')
    before = {path: file_hash(path) for path in e.path.parent.rglob('*') if path.is_file()}
    history, cohorts, members = observed_catalog_history(catalog_exports=[output], as_of=publication(output))
    queries = {query['query_id']: query for query in report['leaf_queries']}
    assert len(history) == len(members) == len(e.vehicles) == 39
    assert set(members.vin) == {vehicle['vin'] for vehicle in e.vehicles}
    assert cohorts.observed_vins.sum() == 39
    assert members.coverage_complete.all() and members.source_query_complete.eq(1).all()
    for row in members.itertuples():
        context = json.loads(row.context_json)
        assert context['filters'] == queries[row.query_id]['filters']
        assert context['zip_code'] == '08542' and context['location_filter'] is False
        bounds = context['filters']['year']
        if row.year == 2009:
            assert bounds == {'max': 2009}
        elif row.year == 2013:
            assert bounds == {'min': 2013}
        else:
            assert bounds == {'min': row.year, 'max': row.year}
    assert len(report['native_zero_categories']) == 2
    assert not ((members.year.eq(2011) & members.make.eq('Audi'))
                | (members.year.eq(2012) & members.make.eq('Tesla'))).any()
    assert history.earlier_listing_history_unknown.all()
    assert not {'event_type', 'absence_streak', 'estimated_sales', 'reappeared_after_absence'} & set(members)
    assert before == {path: file_hash(path) for path in e.path.parent.rglob('*') if path.is_file()}


def test_year_history_withholds_future_publication_but_keeps_native_source_clocks(year_experiment, tmp_path):
    e = year_experiment
    run_years(e)
    output = catalog.export_catalog(e.folder, output=tmp_path/'cutoff-history')
    available = publication(output)
    assert all(table.empty for table in observed_catalog_history(
        catalog_exports=[output], as_of=available-pd.Timedelta(microseconds=1)))
    days, rows = read_catalog_history(output, as_of=available)
    history, _, members = observed_catalog_history(catalog_exports=[output], as_of=available)
    clocks = pd.to_datetime(rows.observed_at_utc, utc=True)
    assert days.available_at.map(pd.Timestamp).eq(available).all()
    assert days.window_start.map(pd.Timestamp).item() == clocks.min()
    assert days.window_end.map(pd.Timestamp).item() == clocks.max()
    assert history.first_observed_at.lt(available).all()
    assert members.available_at.eq(available).all()
    assert pd.to_datetime(members.evidence_available_at_utc, utc=True).le(available).all()


def test_partial_year_model_keeps_good_page_memberships_without_absence_claim(year_experiment, tmp_path):
    e = year_experiment
    e.add(2010, 'Audi', 'A4', 13)  # The exact-year A4 cell now needs two pages.
    first = next(vehicle for vehicle in e.vehicles
                 if (vehicle['year'], vehicle['make'], vehicle['model']) == (2010, 'Audi', 'A4'))

    def send(url, **kwargs):
        response = e.send(url, **kwargs)
        request = kwargs['json']
        if (request['zip5'] == '08542' and request['pagination']['page'] == 2
                and request['filters'] == {'year': {'min': 2010, 'max': 2010},
                    'makes': [{'name': 'Audi', 'parentModels': [{'name': 'A4'}]}]}):
            data = json.loads(response.content)
            data['inventory']['vehicles'][0] = copy.deepcopy(first)
            response.content = json.dumps(data).encode()
        return response

    report = run_years(e, send)
    assert not report['primary_queries_complete']
    output = catalog.export_catalog(e.folder, output=tmp_path/'partial-year-history')
    history, _, members = observed_catalog_history(catalog_exports=[output], as_of=publication(output))
    selected = members.loc[members.year.eq(2010) & members.make.eq('Audi') & members.model.eq('A4')]
    assert len(selected) == 24 and selected.source_query_complete.eq(0).all()
    assert len(members) == len(history) == 50
    assert members.source_query_complete.eq(1).sum() == 26
    assert selected.context_json.map(lambda value: json.loads(value)['filters']['year']).tolist() == [
        {'min': 2010, 'max': 2010}] * 24
    assert not members.coverage_complete.any() and history.known_only_from_partial_cycles.all()
    assert not {'estimated_sales', 'event_type', 'absence_streak', 'reappeared_after_absence'} & set(members)
