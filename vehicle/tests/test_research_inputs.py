"""Shared retained-input loading preserves clocks, membership and source bindings."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
import pandas as pd

from test_detail_batch import study, create, capture, clock, write_json, later_batch
from vehicle_tracker.detail_batch import begin_visit, record_visit
from vehicle_tracker.research_inputs import COHORT_FILES, load_detail_cohorts, load_detail_evidence


@pytest.fixture
def research(study):
    root = study['root']
    (root/'config').mkdir()
    write_json(root/COHORT_FILES[0], study['cohorts'][0])
    future = dict(cohort_id='synthetic-later-extension', selected_at=clock(1000),
                  vehicles=[dict(study['plan']['pages'][1], role='historical_control')])
    write_json(root/COHORT_FILES[1], future)
    folder = create(study)
    begin_visit(folder, now=clock(1))
    record_visit(folder, capture(study, 0, 2), now=clock(3))
    review = root/'data/experiments/vendor_review/20260911T162600Z'
    review.mkdir(parents=True)
    for index, (plan_name, pass_name) in enumerate([
            ('visible_check_plan.json', 'pass.json'), ('exit_batch_plan.json', 'exit_batch_pass.json')]):
        selected, checked, available = clock(10+index*4), clock(11+index*4), clock(12+index*4)
        page = study['plan']['pages'][1]
        write_json(review/plan_name, dict(prepared_at=selected, pages=[page]))
        source = review/f'capture_{index}.json'
        projection = deepcopy(study['captures'][1])
        projection['checked_at'] = checked
        write_json(source, projection)
        entry = dict(file=source.name, sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            expected=projection['expected'], selection_reason=page['selection_reason'], frozen_cohort_member=False)
        write_json(review/pass_name, dict(selection_as_of=selected, available_at=available, captures=[entry]))
    pilot = root/'data/experiments/carvana_sale_signals/synthetic-pilot'
    pilot.mkdir(parents=True)
    source = pilot/'capture.json'
    write_json(source, capture(study, 0, 5))
    write_json(pilot/'run.json', dict(cohort_id=study['cohorts'][0]['cohort_id'],
        cohort=study['cohorts'][0], available_at=clock(6), captures=[dict(file=source.name,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(), expected=study['captures'][0]['expected'])]))
    return study


def test_shared_inputs_preserve_cutoffs_membership_health_and_input_hash_paths(research):
    root = research['root']
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    early = load_detail_evidence(root, clock(2))
    assert early['records'].empty and early['browser_records'].empty
    assert early['browser_health'].outcome.tolist() == ['started_unresolved', 'unattempted']
    assert len(early['selection_plans']) == 2
    loaded = load_detail_evidence(root, clock(20))
    assert loaded['cohorts'] == research['cohorts']
    assert len(loaded['records']) == 4 and len(loaded['browser_records']) == 1
    assert len(loaded['selection_plans']) == 4
    assert loaded['records'].frozen_cohort_member.dropna().tolist() == [False, False, True]
    assert {root/name for name in COHORT_FILES} <= loaded['input_paths']
    assert {Path(p) for p in loaded['records'].source} <= loaded['input_paths']
    assert root/'data/experiments/carvana_sale_signals/synthetic-pilot/run.json' in loaded['input_paths']
    assert {p: p.read_bytes() for p in root.rglob('*') if p.is_file()} == before
    assert load_detail_cohorts(root, clock(-2)) == []


def test_shared_inputs_reject_tampered_capture(research):
    source = research['root']/'data/experiments/vendor_review/20260911T162600Z/capture_0.json'
    source.write_bytes(source.read_bytes()+b'\n')
    with pytest.raises(ValueError, match='Retained capture changed'):
        load_detail_evidence(research['root'], clock(20))


def test_shared_loader_exposes_each_pass_once_without_merging_its_selection(research):
    earlier = load_detail_evidence(research['root'], clock(10))
    assert earlier['research_passes']['pass.json']['report'] is None
    assert earlier['research_passes']['pass.json']['records'].empty
    assert len(earlier['research_passes']['pass.json']['selected']) == 1
    loaded = load_detail_evidence(research['root'], clock(20))
    for name in ['pass.json', 'exit_batch_pass.json']:
        separate = loaded['research_passes'][name]
        assert len(separate['selected']) == len(separate['records']) == 1
        assert not separate['records'].frozen_cohort_member.any()
        source = separate['records'].source.iloc[0]
        assert loaded['records'].source.eq(source).sum() == 1


def test_notebook23_later_browser_evidence_updates_frozen_vin_and_preserves_vintage(study):
    """Exercise the notebook input cell with real retained synthetic browser passes."""
    from vehicle_tracker.daily import daily_tables
    from vehicle_tracker.sales_proxy import (native_visits, native_transition_events,
                                             native_estimate_revisions, cohort_estimates)
    from test_sales_proxy import synthetic_cycles, synthetic_inventory

    root = study['root']
    study['cohorts'][0]['vehicles'][0]['role'] = 'prospective_inventory'
    (root/'config').mkdir()
    write_json(root/COHORT_FILES[0], study['cohorts'][0])
    write_json(root/COHORT_FILES[1], dict(cohort_id='future-outside-cohort', selected_at=clock(1000000),
        vehicles=[dict(study['plan']['pages'][1], role='historical_control')]))
    baseline_folder = root/'data/experiments/carvana_sale_signals/baseline'
    baseline_folder.mkdir(parents=True)
    baseline = capture(study, 0, -10)
    baseline['contexts'][0]['forVehicleContext']['vehicleDetails']['saleStatus'] = 'Available'
    baseline.update(hero_badge=None, hero_text='This vehicle is no longer available')
    source = baseline_folder/'capture.json'
    write_json(source, baseline)
    write_json(baseline_folder/'run.json', dict(cohort_id=study['cohorts'][0]['cohort_id'],
        cohort=study['cohorts'][0], available_at=clock(-9), captures=[dict(file=source.name,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(), expected=baseline['expected'])]))
    # The later Sold/Available records enter only through the browser reader.
    folder = create(study)
    begin_visit(folder, now=clock(1))
    record_visit(folder, capture(study, 0, 2), now=clock(3))
    begin_visit(folder, now=clock(18))
    record_visit(folder, capture(study, 1, 19), now=clock(20))
    later = later_batch(study, seconds=86410)
    begin_visit(later, now=clock(86411))
    returned = deepcopy(baseline)
    returned['checked_at'] = clock(86412)
    record_visit(later, returned, now=clock(86413))
    review = root/'data/experiments/vendor_review/20260911T162600Z'
    review.mkdir(parents=True)
    for plan_name, pass_name in [('visible_check_plan.json', 'pass.json'),
                                 ('exit_batch_plan.json', 'exit_batch_pass.json')]:
        write_json(review/plan_name, dict(prepared_at=clock(1000000), pages=[study['plan']['pages'][1]]))
        write_json(review/pass_name, dict(selection_as_of=clock(1000000), available_at=clock(1000001), captures=[]))
    register = root/'register.json'
    write_json(register, dict(cycles=[]))
    notebook = json.loads((Path(__file__).resolve().parents[1]/'notebooks/23_carvana_daily_sales_research.ipynb').read_text(encoding='utf-8'))
    source_code = ''.join(next(cell['source'] for cell in notebook['cells'] if cell['id'] == 'research-inputs'))
    days, inventory = synthetic_cycles(()), synthetic_inventory()
    unchanged = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    states = []
    for cutoff in [clock(2), clock(3), clock(86412), clock(86413), clock(3)]:
        scope = dict(ROOT=root, AS_OF=cutoff, TZ='America/New_York', pd=pd, Path=Path, json=json,
            COHORT_FILES=[root/name for name in COHORT_FILES],
            SETTINGS=dict(config_path=root/COHORT_FILES[0], plan=register, register=register, database=root/'not-created.sqlite'),
            load_detail_evidence=load_detail_evidence, tracking_history=lambda *args, **kwargs: (days, inventory),
            native_visits=native_visits, native_transition_events=native_transition_events,
            daily_tables=daily_tables, display=lambda *args: None)
        exec(compile(source_code, 'notebook23:research-inputs', 'exec'), scope)
        assert len(scope['panel']) == 1 and scope['cohorts'] == study['cohorts']
        assert set(scope['visits'].vin) == {study['cohorts'][0]['vehicles'][0]['vin']}
        assert len(scope['all_visits'])-len(scope['visits']) == int(pd.Timestamp(cutoff) >= pd.Timestamp(clock(20)))
        assert scope['queue_records'] is scope['records']
        events = scope['native_events']
        states.append(int(events.central_eligible.sum()))
        ledger = cohort_estimates(events, pd.DataFrame(), scope['records'], scope['cohorts'], as_of=cutoff)
        if not ledger.empty:
            assert ledger.initially_non_sold_vins.eq(1).all() and ledger.prospective_vins.eq(1).all()
        if cutoff == clock(86413):
            revisions = native_estimate_revisions(scope['records'], scope['cohorts'], as_of=cutoff,
                                                 cycles=days, observations=inventory)
            assert len(revisions) == 1
            assert (revisions.provisional_event_units.item(), revisions.current_event_units.item(),
                    revisions.unit_revision.item()) == (1, 0, -1)
            assert revisions.provisional_available_at.item() == pd.Timestamp(clock(3))
    assert states == [0, 1, 1, 0, 1]
    assert {p: p.read_bytes() for p in root.rglob('*') if p.is_file()} == unchanged
