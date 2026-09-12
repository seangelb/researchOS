"""Regression checks for frozen membership, study admission and local publication."""
from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest

from test_detail_batch import capture, clock, study
from test_sale_pilot import captures, cohort, diagnostic_pass, importer
from test_status_experiment import frozen_study
from test_status_experiment_cli import cli
from vehicle_tracker import detail_batch
from vehicle_tracker.sale_pilot import load_pilot, load_research_pass, load_selection_plan


def test_later_cohort_does_not_reclassify_an_earlier_diagnostic_pass(diagnostic_pass, captures):
    plan_path, report_path, _, available_at = diagnostic_pass
    selected = load_selection_plan(plan_path, as_of=available_at)
    later = (pd.Timestamp(available_at) + pd.Timedelta(days=1)).isoformat()
    cohort = dict(cohort_id='later-synthetic-cohort', selected_at=later, vehicles=[
        dict(captures[0]['expected'], url=captures[0]['requested_url'],
             role='prospective_inventory', selection_reason='future enrollment')])
    before = load_research_pass(report_path, selected, [cohort], as_of=available_at)[1]
    after = load_research_pass(report_path, selected, [cohort], as_of=later)[1]
    pd.testing.assert_frame_equal(before, after)
    assert not after.frozen_cohort_member.any()


@pytest.mark.parametrize('selected_at, member', [(10, True), (30, False)])
def test_browser_membership_uses_batch_creation_clock(study, selected_at, member):
    # The plan was prepared at 0, while membership is frozen at batch creation 20.
    cohort = deepcopy(study['cohorts'][0])
    cohort['selected_at'] = clock(selected_at)
    folder = detail_batch.create_batch(study['source'], study['folder'],
        helper_path=Path(__file__).resolve().parents[1]/'scripts/capture_carvana_page.js',
        cohorts=[cohort], now=clock(20))
    detail_batch.begin_visit(folder, now=clock(21))
    detail_batch.record_visit(folder, capture(study, 0, 22), now=clock(23))
    rows, _, _, _ = detail_batch.load_browser_batches(study['root'], [cohort], as_of=clock(60))
    assert rows.frozen_cohort_member.tolist() == [member]


def setup_batch_cli(cli, tmp_path, monkeypatch, *, clocks=None):
    plan, _, _ = frozen_study()
    fixed = pd.Timestamp(plan['primary_start']) + pd.Timedelta(hours=1)
    values = iter(clocks or [fixed, fixed, fixed])
    class CurrentClock:
        @staticmethod
        def now(tz):
            return pd.Timestamp(next(values)).to_pydatetime()
    monkeypatch.setattr(cli, 'datetime', CurrentClock)
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    evidence = dict(records=pd.DataFrame(), browser_health=pd.DataFrame(), cohorts=[])
    monkeypatch.setattr(cli, 'load_detail_evidence', lambda *args, **kwargs: evidence)
    study = tmp_path/'study'
    study.mkdir()
    path = study/'plan.json'
    detail_batch.write_new(path, plan)
    detail_batch.write_new(study/'manifest.json',
        dict(plan_sha256=detail_batch.digest(path), input_hashes={}, cycles=[]))
    scripts = tmp_path/'scripts'
    scripts.mkdir()
    helper = Path(__file__).resolve().parents[1]/'scripts/capture_carvana_page.js'
    (scripts/helper.name).write_bytes(helper.read_bytes())
    destination = tmp_path/'data/experiments/carvana_detail_batches/synthetic'
    return plan, destination, ['batch', '--study', str(path), '--destination', str(destination)]


def test_study_batch_saves_current_capacity_without_changing_frozen_targets(cli, tmp_path, monkeypatch):
    plan, destination, args = setup_batch_cli(cli, tmp_path, monkeypatch)
    assert cli.main(args) == 0
    _, batch, _ = detail_batch.read_batch(destination)
    assert {p['vin'] for p in batch['pages']} == {p['vin'] for p in plan['pages']}
    assert len(batch['capacity']['checks']) == 4
    assert all(c['fits'] for c in batch['capacity']['checks'])
    assert batch['capacity']['workload']['as_of'] == batch['prepared_at']
    assert pd.Timestamp(batch['expires_at']) <= pd.Timestamp(plan['primary_end'])


def test_study_admission_refreshes_the_clock_after_loading_evidence(cli, tmp_path, monkeypatch):
    plan, _, _ = frozen_study()
    end = pd.Timestamp(plan['primary_end'])
    earlier, later = end-pd.Timedelta(minutes=4), end-pd.Timedelta(seconds=1)
    _, destination, args = setup_batch_cli(cli, tmp_path, monkeypatch, clocks=[earlier, earlier, later])
    with pytest.raises(ValueError, match='do not fit the current browser budget/window'):
        cli.main(args)
    assert not destination.exists()
    saved = json.loads((tmp_path/'study/plan.json').read_text())
    assert saved == plan


@pytest.mark.parametrize('blocked_by', ['rolling_budget', 'unresolved_lock', 'operator_effort'])
def test_study_admission_blocks_unavailable_capacity_without_creating_batch(cli, tmp_path, monkeypatch, blocked_by):
    _, destination, args = setup_batch_cli(cli, tmp_path, monkeypatch)
    if blocked_by == 'rolling_budget':
        def exhausted(root, *, now):
            return (dict(as_of=pd.Timestamp(now).isoformat(), rolling_24h_limit=12,
                attempted_last_24h=12, remaining_starts=0, unresolved_visits=0,
                access_blocks_last_24h=0, reservation_lock_present=False,
                next_start_at=(pd.Timestamp(now)+pd.Timedelta(hours=24)).isoformat(),
                blocked_reason='Rolling 24-hour limit of 12 browser starts reached'), [])
        monkeypatch.setattr(detail_batch, '_workload', exhausted)
    elif blocked_by == 'unresolved_lock':
        destination.parent.mkdir(parents=True)
        (destination.parent/detail_batch.RESERVATION_LOCK).write_text('synthetic unresolved owner')
    else:
        args.extend(['--minutes-per-check', '30'])
    with pytest.raises(ValueError, match='do not fit the current browser budget/window'):
        cli.main(args)
    assert not destination.exists()


def test_interrupted_pilot_manifest_is_not_discoverable(importer, captures, cohort, tmp_path, monkeypatch):
    source = tmp_path/'input.json'
    source.write_text(json.dumps(captures[0]), encoding='utf-8')
    def interrupted(value, stream, **kwargs):
        stream.write('{"cohort_id":')
        stream.flush()
        raise OSError('synthetic interruption while writing completion')
    monkeypatch.setattr(detail_batch.json, 'dump', interrupted)
    with pytest.raises(OSError, match='synthetic interruption'):
        importer.import_captures([source], cohort, root=tmp_path, now=clock(0), save=True)
    runs = list((tmp_path/'data/experiments/carvana_sale_signals').iterdir())
    assert len(runs) == 1
    assert (runs[0]/'capture_01.json').read_bytes() == source.read_bytes()
    assert not (runs[0]/'run.json').exists()
    assert load_pilot(tmp_path, cohort, as_of=clock(0)).empty


def test_atomic_pilot_manifest_preserves_existing_serialization(importer, captures, cohort, tmp_path):
    source = tmp_path/'input.json'
    source.write_text(json.dumps(captures[0]), encoding='utf-8')
    folder, rows = importer.import_captures([source], cohort, root=tmp_path, now=clock(0), save=True)
    manifest = folder/'run.json'
    value = json.loads(manifest.read_text(encoding='utf-8'))
    prior_serialization = tmp_path/'old_writer.json'
    prior_serialization.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')
    assert manifest.read_bytes() == prior_serialization.read_bytes()
    assert len(rows) == len(load_pilot(tmp_path, cohort, as_of=clock(0))) == 1
