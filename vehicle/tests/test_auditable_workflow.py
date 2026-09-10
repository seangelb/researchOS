import copy
import hashlib
from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from test_daily_cycles import clock, options, plan, reply
from test_daily_events import cycles as schedule, observations
from test_search import response_data
from vehicle_tracker import cycles
from vehicle_tracker.history import classify_changes


def test_intraday_known_change_survives_unknown_other_native_field():
    left = observations((1, 'VIN', 'listing'))
    right = observations((2, 'VIN', 'listing', {'purchase_pending': True, 'vehicle_lock_type': None}))
    result = classify_changes(left, right)
    assert result.native_status_changed.notna().all()
    assert result.native_status_changed.eq(True).all()


def test_retained_iso_clocks_allow_whole_and_fractional_seconds(tmp_path, response_data, clock):
    from vehicle_tracker.search import collect_search
    from vehicle_tracker.history import read_query_evidence
    first = copy.deepcopy(response_data)
    first['inventory']['vehicles'] = [dict(first['inventory']['vehicles'][0], vehicleId=100+i, vin=f'{i:017}') for i in range(24)]
    first['inventory']['pagination'].update(totalMatchedInventory=27, totalMatchedPages=2)
    second = copy.deepcopy(response_data)
    second['inventory']['pagination'].update(currentPage=2, totalMatchedInventory=27, totalMatchedPages=2)
    collect_search(filters={}, zip_code='08542', destination=tmp_path/'query', target_listings=None,
                   post=Mock(side_effect=[reply(first), reply(second)]))
    path = tmp_path/'query/run_report.json'
    report = json.loads(path.read_text())
    for page, stamp in zip(report['pages'], ['2026-09-08T12:00:00.001000+00:00', '2026-09-08T12:00:01+00:00']):
        source = Path(page['retained_source'])
        capture = json.loads(source.read_text())
        capture['captured_at_utc'] = stamp
        source.write_text(json.dumps(capture))
        page['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    path.write_text(json.dumps(report))
    run, _, rows = read_query_evidence(path)
    assert run['query_complete'] == 1 and len(rows) == 27


def test_cycle_cutoff_does_not_reuse_later_retry(tmp_path, response_data, clock, monkeypatch):
    broken = copy.deepcopy(response_data)
    broken['inventory']['vehicles'][0]['vin'] = 'bad'
    post = Mock(side_effect=[reply(broken), reply(response_data)])
    args = options(tmp_path)
    cycles.collect_cycle(plan(), **args, post=post)
    path = tmp_path/'cycle/cycle.json'
    database = tmp_path/'history.sqlite'
    cycles.import_cycle(path, database)
    cutoff = (clock+timedelta(seconds=1)).isoformat()
    before = cycles.read_cycle_history([path], database, as_of=cutoff)
    later = clock+timedelta(minutes=2)
    monkeypatch.setattr(cycles, 'utcnow', lambda: later)
    class Later:
        @staticmethod
        def now(tz=None): return later
    monkeypatch.setattr('vehicle_tracker.search.datetime', Later)
    cycles.collect_cycle(plan(), **args, resume=True, post=post)
    cycles.import_cycle(path, database)
    after = cycles.read_cycle_history([path], database, as_of=cutoff)
    for actual, expected in zip(after, before):
        pd.testing.assert_frame_equal(actual, expected)
    assert not after[0].coverage_complete.any() and after[1].empty
    assert cycles.read_cycle_history([path], database, as_of=later.isoformat())[0].coverage_complete.all()


def test_cycle_before_creation_is_not_evidence(tmp_path, response_data, clock):
    cycles.collect_cycle(plan(), **options(tmp_path), post=Mock(return_value=reply(response_data)))
    days, rows = cycles.read_cycle_history([tmp_path/'cycle/cycle.json'], tmp_path/'missing.sqlite',
                                         as_of=(clock-timedelta(seconds=1)).isoformat())
    assert days.empty and rows.empty


def test_readiness_keeps_missing_and_multiple_attempts_visible(tmp_path, response_data, clock):
    from vehicle_tracker.readiness import query_readiness
    args = options(tmp_path)
    cycles.collect_cycle(plan(), **args, post=Mock(return_value=reply(response_data)))
    path = tmp_path/'cycle/attempt_0001/all/run_report.json'
    manifest = dict(population='fixture scope', queries=plan()+[
        dict(query_id='missing', zip_code='90210', filters={})])
    table, memberships = query_readiness(manifest, [path])
    assert table.query_complete.tolist() == [True, False]
    assert table.estimated_requests.iloc[0] == 1 and pd.isna(table.estimated_requests.iloc[1])
    assert table.reconciliation_difference.iloc[0] == 0 and len(memberships) == 3
    assert not table.population_verified.any()
    duplicate = tmp_path/'other_report.json'
    duplicate.write_bytes(path.read_bytes())
    repeated, _ = query_readiness(manifest, [path, duplicate])
    assert repeated.attempt_versions.tolist() == [2, 2, 0]
    assert repeated.duplicate_memberships.iloc[:2].tolist() == [3, 3]
    assert repeated.reason.iloc[:2].str.contains('Multiple attempts').all()


@pytest.mark.parametrize('case', ['failed', 'zero', 'tampered'])
def test_readiness_failed_empty_and_changed_source(tmp_path, response_data, clock, case):
    from vehicle_tracker.readiness import query_readiness
    if case == 'zero':
        response_data['inventory']['vehicles'] = []
        response_data['inventory']['pagination'].update(totalMatchedInventory=0, totalMatchedPages=0)
    cycles.collect_cycle(plan(), **options(tmp_path), post=Mock(return_value=reply(response_data, 403 if case == 'failed' else 200)))
    path = tmp_path/'cycle/attempt_0001/all/run_report.json'
    if case == 'tampered':
        native = json.loads(path.read_text())
        Path(native['pages'][0]['retained_source']).write_text('{}')
    table, rows = query_readiness(dict(population='fixture', queries=plan()), [path])
    assert table.query_complete.item() == (case == 'zero')
    if case == 'zero':
        assert table.estimated_requests.item() == 1 and rows.empty
        assert table.duplicate_memberships.item() == 0 and table.conflicting_vins.item() == 0
    if case == 'tampered': assert table.status.item() == 'invalid_evidence'
    if case == 'failed': assert table.reason.item() == 'http_access_failure'


def test_readiness_exposes_same_vin_under_different_listings(tmp_path, response_data, clock):
    from vehicle_tracker.readiness import query_readiness
    queries = [dict(query_id=name, zip_code='08542', filters={'year':{'min':year,'max':2030}})
               for name, year in [('A',2000),('B',2001)]]
    second = copy.deepcopy(response_data)
    for vehicle in second['inventory']['vehicles']: vehicle['vehicleId'] += 100000
    cycles.collect_cycle(queries, **options(tmp_path), post=Mock(side_effect=[reply(response_data), reply(second)]))
    paths = [tmp_path/f'cycle/attempt_0001/{name}/run_report.json' for name in ['A','B']]
    table, memberships = query_readiness(dict(population='synthetic partitions', queries=queries), paths)
    assert table.query_complete.all() and len(memberships) == 6
    assert table.conflicting_vins.tolist() == [3, 3]
    assert table.reason.str.contains('Identity conflicts require review').all()


def test_quarter_calendar_exposes_gaps_partial_days_and_future_evidence():
    from vehicle_tracker.expectations import quarter_coverage
    days = schedule((1, 2, 3), partial=(2,))
    calendar, result = quarter_coverage(days, as_of='2026-09-02T23:59:00Z', quarter='2026Q3', timezone_name='America/New_York')
    assert calendar.cycle_date.iloc[0] == '2026-07-01'
    assert calendar.day_status.value_counts().to_dict() == {'missing': 62, 'complete': 1, 'partial': 1}
    assert result.remaining_days.item() == 28 and result.estimated_retail_units.isna().all()
    before = (calendar, result)
    days.loc[2, 'scope_id'] = 'future scope must not leak'
    after = quarter_coverage(days, as_of='2026-09-02T23:59:00Z', quarter='2026Q3', timezone_name='America/New_York')
    for a, b in zip(before, after): pd.testing.assert_frame_equal(a, b)


def test_quarter_calendar_accepts_mixed_iso_clock_precision():
    from vehicle_tracker.expectations import quarter_coverage
    days = schedule((1, 2))
    days.loc[0, 'window_end'] = '2026-09-01T15:00:00.001000+00:00'
    days.loc[0, 'available_at'] = '2026-09-01T15:01:00+00:00'
    calendar, result = quarter_coverage(days, as_of='2026-09-02T23:59:00Z', quarter='2026Q3', timezone_name='America/New_York')
    assert result.complete_dates.item() == 2


@pytest.mark.parametrize('quarter,as_of,remaining', [('2026Q3','2026-09-30T23:59:00Z',0), ('2026Q4','2026-10-01T23:59:00Z',91)])
def test_quarter_boundaries_and_no_data(quarter, as_of, remaining):
    from vehicle_tracker.expectations import quarter_coverage
    calendar, result = quarter_coverage(schedule(()), as_of=as_of, quarter=quarter, timezone_name='UTC')
    assert result.remaining_days.item() == remaining and calendar.day_status.eq('missing').all()
    assert 'fresh' in result.reason.item()


def input_record():
    return dict(input_id='fixture-v1', source='synthetic://input', available_at='2026-09-01T00:00:00Z',
                quarter='2026Q3', scope_id='synthetic', metric='retail_units', units='vehicles', value=100)


@pytest.mark.parametrize('change', [{'available_at':'2026-09-03T00:00:00Z'}, {'value':float('nan')}, {'value':True},
    {'quarter':'2026Q4'}, {'scope_id':'different'}, {'units':'USD'}, {'source':''}, {'available_at':'2026-01-01T00:00:00Z'}])
def test_dated_inputs_reject_unavailable_or_incomparable_values(change):
    from vehicle_tracker.expectations import dated_input
    with pytest.raises(ValueError):
        dated_input(dict(input_record(), **change), as_of='2026-09-02T23:59:00Z', quarter='2026Q3',
                    scope_id='synthetic', metric='retail_units', units='vehicles', max_age_days=7)


def test_revision_bridge_reconciles_and_blocks_scope_changes():
    from vehicle_tracker.expectations import revision_bridge
    previous = dict(quarter='2026Q3', scope_id='example', metric='scenario_units', units='vehicles',
        coverage_basis='complete declared dates', as_of='2026-09-01T23:59:00Z', activity=10., conversion=.5,
        remaining_days=29, daily_rate=2.)
    current = dict(previous, as_of='2026-09-02T23:59:00Z', activity=15., conversion=.6, remaining_days=28, daily_rate=3.)
    bridge = revision_bridge(previous, current, prior_activity_revised=11.)
    assert bridge.scenario_unit_change.sum() == pytest.approx(15*.6+28*3-(10*.5+29*2))
    with pytest.raises(ValueError, match='coverage'):
        revision_bridge(previous, dict(current, coverage_basis='partial dates'), prior_activity_revised=11.)


def test_explicit_export_preserves_vintages_and_hashes(tmp_path):
    from vehicle_tracker.expectations import export_research
    source = tmp_path/'retained.json'
    source.write_text('{"value": 1}')
    destination = tmp_path/'vintage'
    export_research({'coverage':pd.DataFrame({'complete':[False]})}, destination=destination,
        as_of='2026-09-02T00:00:00Z', source_paths=[source], assumptions={'sales_conversion':None})
    manifest = json.loads((destination/'manifest.json').read_text())
    assert str(source.resolve()) in manifest['file_sha256']
    before = {p:p.read_bytes() for p in destination.iterdir()}
    with pytest.raises(FileExistsError):
        export_research({}, destination=destination, as_of='2026-09-03T00:00:00Z', source_paths=[source], assumptions={})
    assert all(p.read_bytes() == b for p,b in before.items())
