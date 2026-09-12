"""Configuration selection uses isolated histories and mocked inventory responses."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from test_detail_batch_cli import cli
from test_daily_cycles import reply
from test_sales_proxy import native, synthetic_cohort, synthetic_vehicle
from test_search import response_data
from vehicle_tracker import cycles, daily
from vehicle_tracker.detail_batch import begin_visit, create_batch, fail_visit, validate_inventory_plan


@pytest.fixture
def panel(cli, tmp_path, monkeypatch, response_data):
    """The real collector/history path receives fixture responses, never HTTP."""
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    monkeypatch.setattr(cli, 'cohort_inputs', lambda now: [])
    evidence = dict(cohorts=[], records=pd.DataFrame(), selection_plans=pd.DataFrame(),
                    browser_health=pd.DataFrame(), input_paths=set())
    monkeypatch.setattr(cli, 'load_detail_evidence', lambda *a, **kw: evidence)
    moment = [datetime(2026, 9, 8, 12, tzinfo=timezone.utc)]
    monkeypatch.setattr(cycles, 'utcnow', lambda: moment[0])
    monkeypatch.setattr(daily, 'utcnow', lambda: moment[0])
    monkeypatch.setattr(cli, 'utc_now', lambda: moment[0].isoformat())

    class Clock:
        @staticmethod
        def now(tz=None): return moment[0]

    monkeypatch.setattr('vehicle_tracker.search.datetime', Clock)
    monkeypatch.setattr('requests.sessions.Session.request', Mock(side_effect=AssertionError('No HTTP in these tests')))

    def configuration(base, name):
        config = base/'config'
        config.mkdir(parents=True)
        (config/'queries.json').write_text(json.dumps({'queries': [dict(query_id=name, zip_code='08542',
            filters={}, location_filter=False)]}), encoding='utf-8')
        path = config/'carvana_daily_tracking.json'
        path.write_text(json.dumps(dict(plan='config/queries.json', timezone='UTC', capture_root='captures',
            database='analysis/history.sqlite', register='analysis/cycles.json', exports='analysis/tables',
            max_requests=3, max_seconds=60, followup_limit=2)), encoding='utf-8')
        return daily.tracking_settings(path)

    default = configuration(tmp_path, 'original')
    alternate = configuration(tmp_path/'broader', 'broader')

    def capture(settings, day, *, count=3, status=200):
        moment[0] = datetime(2026, 9, day, 12, tzinfo=timezone.utc)
        data = deepcopy(response_data)
        data['inventory']['vehicles'] = data['inventory']['vehicles'][:count]
        data['inventory']['pagination']['totalMatchedInventory'] = count
        post = Mock(return_value=reply(data, status))
        daily.run_tracking(settings, live=True, post=post)
        assert post.call_count == 1
        moment[0] += timedelta(minutes=2)

    def plan(settings=alternate, **kwargs):
        return cli.fresh_plan([], as_of=moment[0].isoformat(), limit=2, controls=1,
            seed='synthetic-config', minutes=20, config_path=settings['config_path'], **kwargs)

    return dict(default=default, alternate=alternate, capture=capture, plan=plan, moment=moment,
                evidence=evidence, root=tmp_path)


def file_bytes(root):
    return {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_two_dates_select_only_configured_exits_and_controls_and_bind_sources(panel, cli, capsys):
    settings = panel['alternate']
    panel['capture'](settings, 8)
    panel['capture'](settings, 9, count=2)
    outside = synthetic_vehicle(900)
    panel['evidence']['cohorts'] = [synthetic_cohort(outside)]
    panel['evidence']['selection_plans'] = pd.DataFrame([dict(outside,
        selected_at='2026-09-01T00:00:00Z', available_at='2026-09-01T00:00:00Z')])
    before = file_bytes(panel['root'])
    plan = panel['plan']()
    assert {p['selection_group'] for p in plan['pages']} == {'new_exit', 'control'}
    assert outside['vin'] not in {p['vin'] for p in plan['pages']}
    assert plan['preparation_blocked_reason'] is None
    assert all(c['fits'] for c in plan['capacity']['checks'])
    context = plan['inventory_context']
    assert context['config_path'] == str(settings['config_path'])
    assert context['cycle_dates'] == ['2026-09-08', '2026-09-09']
    assert all(page['inventory_scope_id'] == context['scope_id'] for page in plan['pages'])
    assert len(context['source_paths']) == 6  # Two cycles, query reports and source projections.
    assert set(context['source_paths']) <= set(plan['input_hashes'])
    assert plan['as_of'] == panel['moment'][0].isoformat()
    assert cli.main(['preview', '--config', str(settings['config_path']), '--limit', '2', '--controls', '1']) == 0
    output = capsys.readouterr().out
    assert str(settings['config_path']) in output and context['scope_id'] in output
    assert 'new_exit' in output and 'control' in output
    assert file_bytes(panel['root']) == before


def test_omitted_config_preserves_original_population(panel, cli, capsys):
    panel['capture'](panel['default'], 8)
    panel['capture'](panel['default'], 9, count=2)
    assert cli.main(['preview', '--limit', '2', '--controls', '1']) == 0
    output = capsys.readouterr().out
    assert str(panel['default']['config_path']) in output
    assert str(panel['alternate']['config_path']) not in output


def test_default_keeps_legacy_cohort_frame_ranks_and_probabilities(panel, cli):
    """Existing reminders outside inventory must not shrink the default frame."""
    settings = panel['default']
    panel['capture'](settings, 8)
    panel['capture'](settings, 9, count=2)
    outside, previously_selected = synthetic_vehicle(900), synthetic_vehicle(901)
    evidence = panel['evidence']
    evidence['cohorts'] = [synthetic_cohort(outside)]
    evidence['records'] = pd.DataFrame([native(1, 'Sold', vehicle=outside)])
    evidence['selection_plans'] = pd.DataFrame([dict(previously_selected,
        selected_at='2026-09-01T00:00:00Z', available_at='2026-09-01T00:00:00Z')])
    cutoff = panel['moment'][0].isoformat()
    days, inventory = daily.tracking_history(settings, as_of=cutoff)
    original_queue = cli.inventory_followup_queue(days, inventory, evidence['records'], evidence['cohorts'],
        as_of=cutoff, selection_plans=evidence['selection_plans'], browser_health=evidence['browser_health'],
        batch_limit=2, control_count=1, seed='synthetic-config')
    reference = json.loads(original_queue.loc[original_queue.selected_for_check].to_json(
        orient='records', date_format='iso'))
    result = panel['plan'](settings)
    actual = [{key: value for key, value in page.items() if key != 'inventory_scope_id'} for page in result['pages']]
    assert actual == reference  # Includes candidate_frame_vins, queue_rank and selection_probability.
    assert outside['vin'] in {page['vin'] for page in actual}
    assert all(page['candidate_frame_vins'] == 2 for page in actual)


@pytest.mark.parametrize('case', ['absent', 'one_date', 'gap', 'partial', 'earlier_cutoff'])
def test_incomparable_history_is_unavailable_and_cannot_prepare(panel, cli, case, capsys):
    settings = panel['alternate']
    if case != 'absent':
        panel['capture'](settings, 8)
    if case in {'gap', 'partial', 'earlier_cutoff'}:
        panel['capture'](settings, 10 if case == 'gap' else 9, count=2, status=403 if case == 'partial' else 200)
    if case == 'earlier_cutoff':
        panel['moment'][0] = datetime(2026, 9, 9, 11, tzinfo=timezone.utc)
    before = file_bytes(panel['root'])
    plan = panel['plan']()
    assert plan['pages'] == [] and 'two complete consecutive dates' in plan['preparation_blocked_reason']
    assert cli.main(['preview', '--config', str(settings['config_path'])]) == 0
    assert 'Preparation unavailable:' in capsys.readouterr().out
    folder = panel['root']/'data/experiments/carvana_detail_batches/unavailable'
    with pytest.raises(ValueError, match='two complete consecutive dates'):
        cli.main(['prepare', '--config', str(settings['config_path']), '--batch', str(folder)])
    assert not folder.exists() and file_bytes(panel['root']) == before


def prepared_source(panel):
    panel['capture'](panel['alternate'], 8)
    panel['capture'](panel['alternate'], 9, count=2)
    path = panel['root']/'selection.json'
    path.write_text(json.dumps(panel['plan']()), encoding='utf-8')
    return path


@pytest.mark.parametrize('change', ['wrong_config', 'query_plan', 'source', 'page_scope'])
def test_scope_or_source_mismatch_blocks_preparation(panel, cli, change):
    source = prepared_source(panel)
    plan = json.loads(source.read_text(encoding='utf-8'))
    selected_config = panel['alternate']['config_path']
    if change == 'wrong_config':
        selected_config = panel['default']['config_path']
    elif change == 'query_plan':
        path = panel['alternate']['plan']
        path.write_text(path.read_text(encoding='utf-8')+' ', encoding='utf-8')
    elif change == 'source':
        path = Path(plan['inventory_context']['source_paths'][0])
        path.write_text(path.read_text(encoding='utf-8')+' ', encoding='utf-8')
    else:
        plan['pages'][0]['inventory_scope_id'] = 'another-population'
        source.write_text(json.dumps(plan), encoding='utf-8')
    folder = panel['root']/'data/experiments/carvana_detail_batches/wrong_scope'
    with pytest.raises(ValueError, match='scope|changed'):
        cli.main(['prepare', '--plan', str(source), '--config', str(selected_config), '--batch', str(folder)])
    assert not folder.exists()


def test_configuration_change_blocks_new_start_but_preserves_failure_recovery(panel, cli):
    source = prepared_source(panel)
    folder = panel['root']/'data/experiments/carvana_detail_batches/bound'
    helper = Path(__file__).resolve().parents[1]/'scripts/capture_carvana_page.js'
    now = panel['moment'][0].isoformat()
    create_batch(source, folder, helper_path=helper, cohorts=[], now=now)
    # Supplying another history cannot reserve a visit against this batch.
    with pytest.raises(ValueError, match='configuration differs'):
        cli.main(['next', '--batch', str(folder), '--config', str(panel['default']['config_path'])])
    assert not list(folder.glob('visit_*'))
    begin_visit(folder, now=now)
    config = panel['alternate']['config_path']
    config.write_text(config.read_text(encoding='utf-8')+' ', encoding='utf-8')
    with pytest.raises(ValueError, match='configuration.*changed'):
        begin_visit(folder, now=now)
    # Closing an existing reservation never depends on mutable inventory files.
    assert cli.main(['fail', '--batch', str(folder), '--config', str(config), '--reason', 'interrupted']) == 2
    assert (folder/'visit_01/run.json').is_file()


def test_legacy_plan_cannot_be_relabelled_with_config(panel):
    with pytest.raises(ValueError, match='Legacy plan'):
        validate_inventory_plan({'pages': []}, config_path=panel['alternate']['config_path'])
    validate_inventory_plan({'pages': []})  # Existing frozen-batch lifecycle remains supported.


def test_infeasible_selection_does_not_silently_change_sample_or_prepare(panel, cli, monkeypatch):
    panel['capture'](panel['alternate'], 8)
    panel['capture'](panel['alternate'], 9, count=2)
    # The real capacity helper is covered by the shared-budget tests. This checks
    # that its unavailable result cannot be ignored by the planning entry point.
    monkeypatch.setattr(cli, 'browser_capacity', lambda root, requests, **kw:
        {'checks': [dict(r, fits=False) for r in requests], 'minutes_per_check': 1.5, 'operator_minutes_per_day': 20})
    plan = panel['plan']()
    assert len(plan['pages']) == 2 and 'do not fit' in plan['preparation_blocked_reason']
    folder = panel['root']/'data/experiments/carvana_detail_batches/infeasible'
    with pytest.raises(ValueError, match='do not fit'):
        cli.main(['prepare', '--config', str(panel['alternate']['config_path']), '--batch', str(folder),
                  '--limit', '2', '--controls', '1'])
    assert not folder.exists()
