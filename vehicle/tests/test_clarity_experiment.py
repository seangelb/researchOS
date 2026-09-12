"""Synthetic boundary tests for the Clarity-method research experiment."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from vehicle_tracker.clarity_experiment import (capture_public_html, exit_rule_experiment,
    load_detail_probe, pending_entry_experiment)
from test_sales_proxy import synthetic_cycles, synthetic_inventory, synthetic_vehicle, native


def http_script():
    path = Path(__file__).parents[1] / 'scripts/probe_carvana_details.py'
    spec = importlib.util.spec_from_file_location('detail_probe_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected():
    vehicle = synthetic_vehicle()
    return {key: vehicle[key] for key in ['retailer', 'vin', 'listing_id']}


def html_fixture(*, vin=None, status='Available', conflicting=False):
    vehicle = dict(vehicleId=100001, vin=vin or expected()['vin'], saleStatus=status,
                   purchaseType='NotPurchasable', inventoryType='Test')
    value = {'recommendations': [{'vehicleId': 100002, 'saleStatus': 'Sold'}],
             'forVehicleContext': {'vehicleDetails': vehicle}, 'sessionToken': 'must-not-retain'}
    if conflicting:
        value['other'] = {'forVehicleContext': {'vehicleDetails': dict(vehicle, saleStatus='Sold')}}
    stream = '3:' + json.dumps(value) + '\n'
    # RSC records can cross script tags, as in the retained browser helper.
    return '<p>Sold recommendation</p>' + ''.join('<script>self.__next_f.push(' + json.dumps([1, chunk]) + ')</script>'
           for chunk in [stream[:40], stream[40:]])


def plan():
    return dict(prepared_at='2026-09-01T09:00Z', as_of='2026-09-01T08:00Z', expires_at='2026-09-01T11:00Z',
                pages=[dict(expected(), url='https://www.carvana.com/vehicle/100001', selection_reason='synthetic')])


def test_public_html_extracts_only_target_native_fields_and_preserves_unavailable():
    capture = capture_public_html(html_fixture(), expected(), checked_at='2026-09-01T10:00Z',
                                  final_url='https://www.carvana.com/vehicle/100001')
    details = capture['contexts'][0]['forVehicleContext']['vehicleDetails']
    assert details['saleStatus'] == 'Available'
    assert details['purchaseType'] == 'NotPurchasable'
    assert 'must-not-retain' not in json.dumps(capture)
    assert capture['hero_text'] is None


@pytest.mark.parametrize('html', [html_fixture(vin=synthetic_vehicle(2)['vin']), html_fixture(conflicting=True), '<p>Sold</p>'])
def test_public_html_rejects_wrong_identity_conflicting_status_and_body_only_sold(html):
    with pytest.raises(ValueError, match='matched native'):
        capture_public_html(html, expected(), checked_at='2026-09-01T10:00Z', final_url='https://www.carvana.com/vehicle/100001')


def test_probe_stops_at_first_access_failure_without_retry(tmp_path):
    module = http_script()
    selected = plan()
    second = synthetic_vehicle(2)
    selected['pages'].append(dict(second, selection_reason='synthetic second'))
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(content=b'blocked', status_code=403, encoding='utf-8', url=url)
    report = module.run_probe(selected, tmp_path / 'run', get=get, now=lambda: pd.Timestamp('2026-09-01T10:00Z'), sleep=lambda _: None)
    assert len(calls) == report['requests'] == 1
    assert calls[0][1]['allow_redirects'] is False
    assert report['status'] == 'stopped'
    assert not list((tmp_path / 'run').glob('capture*'))


def test_expired_probe_plan_makes_no_directory_or_request(tmp_path):
    with pytest.raises(ValueError, match='expired'):
        http_script().run_probe(plan(), tmp_path / 'run', get=lambda *a, **k: pytest.fail('network'),
                                now=lambda: pd.Timestamp('2026-09-01T12:00Z'))
    assert not (tmp_path / 'run').exists()


def test_probe_hash_plan_and_cutoff_are_enforced(tmp_path):
    module = http_script()
    selected = plan()
    frozen = tmp_path / 'plan.json'
    frozen.write_text(json.dumps(selected))
    def get(url, **kwargs):
        return SimpleNamespace(content=html_fixture(status='Sold').encode(), status_code=200, encoding='utf-8', url=url)
    module.run_probe(selected, tmp_path / 'run', get=get, now=lambda: pd.Timestamp('2026-09-01T10:00Z'))
    report, records = load_detail_probe(tmp_path/'run/run.json', frozen, as_of='2026-09-01T09:30Z')
    assert report is None and records.empty
    report, records = load_detail_probe(tmp_path/'run/run.json', frozen, as_of='2026-09-01T11:00Z')
    assert records.saleStatus.tolist() == ['Sold']
    (tmp_path/'run/capture_01.json').write_text('{}')
    with pytest.raises(ValueError, match='changed'):
        load_detail_probe(tmp_path/'run/run.json', frozen, as_of='2026-09-01T11:00Z')


def test_pending_cooldown_tracks_reentries_and_does_not_recount_continuous_pending():
    days = synthetic_cycles(tuple(range(1, 21)))
    # First entry day8, repeatday10, fresh entryday17 (exactly seven days later).
    pending_days = {8, 10, 11, 12, 13, 14, 15, 17, 18, 19, 20}
    inventory = synthetic_inventory(*[(d, 1, {'purchase_pending': d in pending_days}) for d in range(1,21)])
    result = pending_entry_experiment(days, inventory, as_of='2026-09-20T23:00Z')
    assert result.cycle_date.tolist() == ['2026-09-08', '2026-09-10', '2026-09-17']
    assert result.order_proxy_eligible.tolist() == [True, False, True]


def test_pending_missing_week_first_pending_and_late_available_are_not_orders():
    days = synthetic_cycles(tuple(range(1,10)), partial=(5,))
    inventory = synthetic_inventory(*[(d, 1, {'purchase_pending': d>=8}) for d in range(1,10)], (9,2,{'purchase_pending':True}))
    result = pending_entry_experiment(days, inventory, as_of='2026-09-09T23:00Z')
    assert not result.order_proxy_eligible.any()
    assert 'first/unknown pending baseline' in result.reason.tolist()
    days.loc[days.cycle_date.eq('2026-09-08'), 'available_at'] = '2026-09-10T01:00Z'
    earlier = pending_entry_experiment(days, inventory, as_of='2026-09-09T23:00Z')
    assert not earlier.order_proxy_eligible.any()


def test_exit_rules_need_later_identity_matched_evidence_and_keep_unchecked_cases():
    days = synthetic_cycles((1,2,3,4))
    inventory = synthetic_inventory((1,1,{'purchase_pending':True}),(1,2,{}),(1,3,{}),
        (2,3,{}),(3,3,{}),(4,3,{}))
    records = pd.DataFrame([native(3,'Sold'), native(3,'Sold',vehicle=synthetic_vehicle(2),listing_id='200002')])
    result = exit_rule_experiment(days, inventory, records, as_of='2026-09-04T23:00Z')
    all_exits=result[result.rule.eq('all_exits')]
    assert len(all_exits)==2 and all_exits.selected.all()
    assert all_exits.matched_later_checks.tolist()==[1,0]
    assert int(all_exits.later_sold_seen.eq(True).sum())==1
    assert not result.loc[result.rule.eq('absence_7d'),'selected'].any()
    # Day3 Sold already known at the day4 three-day decision is not a later success.
    known=result[result.rule.eq('absence_3d') & result.vin.eq(expected()['vin'])].iloc[0]
    assert known.sold_known_at_decision and not known.evaluable


def test_exit_reappearance_is_reported_and_old_sold_checks_do_not_validate_a_new_exit():
    days=synthetic_cycles((1,2,3))
    inventory=synthetic_inventory((1,1,{}),(1,2,{}),(2,2,{}),(3,1,{}),(3,2,{}))
    records=pd.DataFrame([native(1,'Sold')])
    result=exit_rule_experiment(days,inventory,records,as_of='2026-09-03T23:00Z')
    row=result[result.rule.eq('all_exits')].iloc[0]
    assert row.inventory_reappeared and row.sold_known_at_decision and not row.evaluable


def test_empty_early_cutoff_has_no_claimed_orders_or_exit_outcomes():
    days=synthetic_cycles((1,2))
    inventory=synthetic_inventory((1,1,{}))
    assert pending_entry_experiment(days,inventory,as_of='2026-08-31T23:00Z').empty
    assert exit_rule_experiment(days,inventory,pd.DataFrame(),as_of='2026-08-31T23:00Z').empty


def test_later_sold_only_corroborates_the_current_exit_episode():
    days=synthetic_cycles(tuple(range(1,7)))
    inventory=synthetic_inventory((1,1,{}),(3,1,{}),*[(d,2,{}) for d in range(1,7)])
    result=exit_rule_experiment(days,inventory,pd.DataFrame([native(6,'Sold')]),as_of='2026-09-06T23:00Z')
    exits=result[result.rule.eq('all_exits')].sort_values('first_absent_date')
    assert exits.matched_later_checks.tolist()==[0,1]
    assert exits.followup_attempts.tolist()==[0,1]


def test_later_version_does_not_erase_sold_known_at_the_decision():
    days=synthetic_cycles((1,2,3,4,5))
    inventory=synthetic_inventory((1,1,{}),*[(d,2,{}) for d in range(1,6)])
    original=native(2,'Sold')
    later=dict(original,available_at='2026-09-05T10:00Z',source='synthetic://re-export')
    result=exit_rule_experiment(days,inventory,pd.DataFrame([original,later]),as_of='2026-09-05T23:00Z')
    row=result[result.rule.eq('all_exits')].iloc[0]
    assert row.sold_known_at_decision and not row.evaluable
