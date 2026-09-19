"""Reviewed empty responses preserve failed attempts and the combined allowance."""
import json
from pathlib import Path

import pytest

from vehicle_tracker.gap_failure_review import digest, reviewed_empty_layout


def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf-8')
    return path


@pytest.fixture
def review_config(tmp_path):
    q = dict(filters=dict(makes=[dict(name='Audi',parentModels=[dict(name='A5')])],year=dict(max=2009)))
    plan = write(tmp_path/'plan.json',dict(children=[q]))
    folders,sources = [],{}
    year = dict(appliedMax=2009,min=2010,max=2025)
    for i in range(3):
        folder = tmp_path/f'root{i}'/'2026-09-19';folders.append(folder)
        source = dict(inventory=dict(vehicles=[],pagination=dict(currentPage=1,pageSize=24,
            totalMatchedInventory=0,totalMatchedPages=1)),userDeliveryInfo=dict(zip5='08542'))
        if i==2:source['facetData']=dict(year=year)
        raw = write(folder/'query/source.json',source)
        child = write(folder/'query/run_report.json',dict(filters=q['filters'],zip_code='08542',location_filter=False,
            outcome_kind='schema_failure',query_complete=False,unique_listings=0,
            pages=[dict(stored_rows=0,http_status=200,response_evidence=dict(source_path=str(raw),
                http_diagnostics=dict(media_type='application/json',cf_mitigated=None,body_markers=dict(challenge=False))))]))
        report = dict(status='stopped',requests=1,window_end='2026-09-19T22:13:10Z',cycle_date='2026-09-19',
            plan_sha256=digest(plan))
        if i<2:report['entries']=[dict(report=str(child))]+[dict(status='unattempted') for _ in range(499)]
        else:report.update(diagnostic_only=True,query_report=str(child))
        report_path = write(folder/'catalog_report.json',report)
        budget = write(folder/'catalog_budget.json',dict(budget=dict(requests=1,stopped=True,pending_request=False)))
        stop = write(folder.parent/'access_stop.json',dict(run=str(report_path)))
        original = write(folder/'selected_config.json',dict(max_requests=6000))
        sources.update({str(p):digest(p) for p in [raw,child,report_path,budget,stop,original]})
    diagnosis = write(tmp_path/'diagnosis.json',dict(bindings=sources,capture_directory=str(folders[-1]),
        one_request_reconciled=True,exact_reproduced_missing_key='makes',native_makes_present=False,
        native_applied_year_matches_request=True,native_applied_make_model_verified=False,native_facet_year=year))
    config = dict(capture_root=str(tmp_path/'fresh'),related_capture_roots=[str(p.parent) for p in folders],
        plan_path=str(plan),plan_sha256=digest(plan),max_requests=5997,absolute_window_end='2026-09-19T22:13:10Z',
        authorized_cycle_date='2026-09-19',reviewed_empty_layout=dict(path=str(diagnosis),sha256=digest(diagnosis),
            failures=[str(p) for p in folders]))
    return config,folders,diagnosis


def test_three_reviewed_failures_return_all_bindings_without_writes(review_config):
    config,folders,diagnosis=review_config
    before={p:p.read_bytes() for p in diagnosis.parent.rglob('*') if p.is_file()}
    result,sources=reviewed_empty_layout(config)
    assert result==folders and str(diagnosis) in sources and len(sources)==19
    assert before=={p:p.read_bytes() for p in diagnosis.parent.rglob('*') if p.is_file()}


@pytest.mark.parametrize('key,value',[('max_requests',5998),('absolute_window_end','2026-09-19T22:13:11Z'),
    ('authorized_cycle_date','2026-09-20')])
def test_combined_allowance_never_extended(review_config,key,value):
    config,_,_=review_config;config[key]=value
    with pytest.raises(ValueError,match='allowance'):
        reviewed_empty_layout(config)


def test_all_three_stops_remain_required_and_bound(review_config):
    config,folders,_=review_config
    (folders[1].parent/'access_stop.json').write_text('{}')
    with pytest.raises(ValueError,match='evidence changed'):
        reviewed_empty_layout(config)


def test_no_removed_peer_or_reused_destination(review_config):
    config,folders,_=review_config
    config['capture_root']=str(folders[2].parent)
    with pytest.raises(ValueError,match='locked peer'):
        reviewed_empty_layout(config)
    config['capture_root']=str(folders[2].parent.parent/'fresh')
    config['related_capture_roots'].pop()
    with pytest.raises(ValueError,match='locked peer'):
        reviewed_empty_layout(config)


def test_unreviewed_layout_remains_unsupported(review_config):
    config,_,diagnosis=review_config
    data=json.loads(diagnosis.read_text());data['exact_reproduced_missing_key']='year'
    write(diagnosis,data);config['reviewed_empty_layout']['sha256']=digest(diagnosis)
    with pytest.raises(ValueError,match='exact reviewed'):
        reviewed_empty_layout(config)


def test_root_integration_retains_all_three_stops_and_blocks_extra_attempt(review_config):
    from vehicle_tracker.gap_recovery import _recovery_root_states, _require_clear_roots
    config,folders,_=review_config
    states=_recovery_root_states(config)
    _require_clear_roots(states)
    assert sum(s.get('retained_access_stopped',False) for s in states)==3
    assert all((p.parent/'access_stop.json').exists() for p in folders)
    extra=folders[0].parent/'unexpected_complete_attempt'
    write(extra/'catalog_budget.json',dict(budget=dict(stopped=False,pending_request=False)))
    write(extra/'catalog_report.json',dict(status='collection_finished',ended_at='2026-09-19T17:00:00Z'))
    with pytest.raises(ValueError,match='another or changed'):
        _recovery_root_states(config)


def test_fresh_or_unrelated_access_stop_remains_blocking(review_config):
    from vehicle_tracker.gap_recovery import _recovery_root_states, _require_clear_roots
    config,_,_=review_config
    write(Path(config['capture_root'])/'access_stop.json',dict(reason='HTTP403'))
    with pytest.raises(ValueError,match='access stop'):
        _require_clear_roots(_recovery_root_states(config))
