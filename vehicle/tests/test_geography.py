"""Geographic overlap is conditional on complete, comparable retained queries."""
import copy
import json
import sqlite3
from unittest.mock import Mock

import pandas as pd
import pytest

from test_search import response_data
from test_search_evidence import response
from vehicle_tracker.collect import NavigationBudget
from vehicle_tracker.geography import load_geography, geography_pairs, geography_discovery
from vehicle_tracker.search_plan import collect_plan


def tables():
    coverage = pd.DataFrame([dict(query_id=f'q{i}', **{'pass':1}, cohort='cars',
        zip_code=z, context_role=role, anchor_repeat=False, context_order=i,
        status='complete', complete=True, observation_start='2026-09-14T14:30:00Z',
        observation_end='2026-09-14T14:31:00Z') for i,(z,role) in enumerate([
            ('08542','discovery'),('90012','discovery'),('98101','held_out'),('33130','held_out')])])
    observations = pd.DataFrame([dict(query_id=f'q{i}',retailer='Carvana',vin=v,listing_id=v,
        asking_price_usd=100+i,transport_cost_usd=None if i==0 else 50,
        observed_at_utc=f'2026-09-14T14:3{i}:00Z',source_path=f'source{i}')
        for i,vins in enumerate(['AB','BC','CD','CD']) for v in vins])
    return coverage,observations


def test_overlap_prices_and_temporal_gap():
    pairs,membership,prices = geography_pairs(*tables())
    first = pairs.iloc[0]
    assert (first.shared_vins,first.left_only,first.right_only,first.union_vins)==(1,1,1,3)
    assert first.jaccard==pytest.approx(1/3)
    assert first.capture_span_seconds==60
    assert len(membership.loc[membership.right_query.eq('q1')])==3
    price=prices.loc[prices.right_query.eq('q1')].iloc[0]
    assert price.asking_price_usd_difference==1
    assert pd.isna(price.transport_cost_usd_difference)
    assert price.observation_gap_seconds==60


def test_held_out_contexts_do_not_train_discovery_union():
    result=geography_discovery(*tables())
    assert result.additional_vins.tolist()==[2,1,1,1]
    assert result.previous_union_vins.tolist()==[0,2,3,3]


def test_incomplete_query_withholds_absence_and_downstream_contributions():
    coverage,observations=tables()
    coverage.loc[0,['complete','status']]=[False,'partial']
    pairs,membership,prices=geography_pairs(coverage,observations)
    assert pairs.status.str.startswith('unavailable').all()
    assert membership.empty and prices.empty
    assert geography_discovery(coverage,observations).status.str.startswith('unavailable').all()


def test_identity_conflict_withholds_comparison():
    coverage,observations=tables()
    observations.loc[observations.query_id.eq('q1') & observations.vin.eq('B'),'listing_id']='other'
    pairs,_,_=geography_pairs(coverage,observations)
    assert pairs.iloc[0].status.startswith('identity conflict')
    assert geography_discovery(coverage,observations).status.str.startswith('identity conflict').all()


def test_complete_empty_queries_are_zero_not_missing():
    coverage,observations=tables()
    pairs,_,_=geography_pairs(coverage,observations.iloc[:0])
    assert pairs.union_vins.eq(0).all() and pairs.jaccard.isna().all()
    assert geography_discovery(coverage,observations.iloc[:0]).additional_vins.eq(0).all()


def test_anchor_and_second_pass_compare_separately():
    coverage,observations=tables()
    anchor=coverage.iloc[[0]].assign(query_id='repeat',anchor_repeat=True)
    second=coverage.copy().assign(**{'pass':2})
    second['query_id']=second.query_id+'second'
    repeated=observations.loc[observations.query_id.eq('q0')].assign(query_id='repeat')
    later=observations.copy()
    later['query_id']=later.query_id+'second'
    pairs,_,_=geography_pairs(pd.concat([coverage,anchor,second]),pd.concat([observations,repeated,later]))
    assert pairs.comparison_kind.value_counts().to_dict()=={'between_zip':6,'between_passes':4,'anchor_repeat':1}
    assert pairs.loc[pairs.comparison_kind.ne('between_zip'),'jaccard'].eq(1).all()


@pytest.fixture
def retained(tmp_path,response_data,monkeypatch):
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep',lambda _:None)
    queries=[dict(query_id=f'q{i}',zip_code=z,filters={},location_filter=False,
        **{'pass':1},cohort='cars',context_role='discovery',anchor_repeat=False)
        for i,z in enumerate(['08542','90012'])]
    plan=dict(prepared_at='2020-01-01T00:00:00Z',proposed_start='2020-01-02T00:00:00Z',
        proposed_end='2099-01-01T00:00:00Z',contexts=[dict(zip_code=q['zip_code'],role='discovery') for q in queries],
        cohorts=[dict(id='cars',filters={})],queries=queries)
    path=tmp_path/'plan.json'
    path.write_text(json.dumps(plan),encoding='utf-8')
    replies=[]
    for q in queries:
        data=copy.deepcopy(response_data)
        data['userDeliveryInfo']['zip5']=q['zip_code']
        replies.append(response(data))
    root=tmp_path/'captures'
    collect_plan(queries,destination=root,full_plan=True,budget=NavigationBudget(max_requests=2),post=Mock(side_effect=replies))
    return path,root


def test_reader_replays_without_modifying_retained_files(retained):
    path,root=retained
    before={p:p.read_bytes() for p in path.parent.rglob('*') if p.is_file()}
    coverage,observations,inputs=load_geography(path,root,as_of='2099-01-01T00:00:00Z')
    assert coverage.complete.all() and coverage.observed_vins.tolist()==[3,3]
    assert len(observations)==6 and len(inputs)>3
    assert before=={p:p.read_bytes() for p in path.parent.rglob('*') if p.is_file()}


def test_reader_missing_future_and_outside_window(retained):
    path,root=retained
    coverage,observations,_=load_geography(path,root,as_of='2019-01-01T00:00:00Z')
    assert coverage.empty and observations.empty
    coverage,observations,_=load_geography(path,root,as_of='2020-02-01T00:00:00Z')
    assert coverage.status.eq('query unavailable at cutoff').all() and observations.empty
    coverage,observations,_=load_geography(path,root/'absent',as_of='2020-02-01T00:00:00Z')
    assert coverage.status.eq('missing at cutoff').all() and coverage.observed_vins.isna().all()
    plan=json.loads(path.read_text())
    plan['proposed_end']='2020-02-01T00:00:00Z'
    path.write_text(json.dumps(plan))
    coverage,_,_=load_geography(path,root,as_of='2099-01-01T00:00:00Z')
    assert coverage.status.eq('outside proposed window').all() and not coverage.complete.any()


def test_manifest_mismatch_rejected(retained):
    path,root=retained
    manifest=root/'query_plan.json'
    content=json.loads(manifest.read_text())
    content['queries'][0]['zip_code']='12345'
    manifest.write_text(json.dumps(content))
    with pytest.raises(ValueError,match='differs'):
        load_geography(path,root,as_of='2099-01-01T00:00:00Z')


@pytest.mark.parametrize('damage',['source','database','unreadable_database'])
def test_changed_evidence_is_withheld(retained,damage):
    path,root=retained
    if damage=='unreadable_database':
        (root/'q0/vehicle.sqlite').write_bytes(b'not a database')
    elif damage=='database':
        with sqlite3.connect(root/'q0/vehicle.sqlite') as connection:
            connection.execute('UPDATE vehicle_observations SET asking_price_usd=asking_price_usd+0.001')
    else:
        report=json.loads((root/'q0/run_report.json').read_text())
        from pathlib import Path
        source=Path(report['pages'][0]['retained_source'])
        source.write_bytes(source.read_bytes()+b' ')
    coverage,observations,_=load_geography(path,root,as_of='2099-01-01T00:00:00Z')
    assert coverage.iloc[0].status=='invalid retained evidence'
    assert not coverage.iloc[0].complete and coverage.iloc[1].complete
    assert observations.query_id.eq('q1').all()


def test_reader_under_offline_guards(retained):
    from test_daily_notebook import checker
    with checker.offline_guards():
        coverage,_,_=load_geography(*retained,as_of='2099-01-01T00:00:00Z')
    assert coverage.complete.all()
