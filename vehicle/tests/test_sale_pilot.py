"""Real projection replay and explicitly synthetic failure/transition cases."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pandas as pd
import pytest

from vehicle_tracker.sale_pilot import (known_disjoint_cohorts, load_pilot, parse_capture,
                                       select_extension, summarize_pilot)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/carvana_sale_pilot/chrome_projections_20260909.json'
NOW = '2026-09-09T12:00:00Z'


@pytest.fixture
def captures():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


@pytest.fixture
def cohort():
    return json.loads((ROOT / 'config/carvana_sale_pilot.json').read_text(encoding='utf-8'))


@pytest.fixture
def importer():
    spec = importlib.util.spec_from_file_location('pilot_import', ROOT / 'scripts/import_carvana_sale_pilot.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse(capture, **kwargs):
    return parse_capture(capture, expected=kwargs.pop('expected', capture['expected']),
        available_at=kwargs.pop('available_at', NOW), source='fixture://public-projection', **kwargs)


def details(capture):
    return capture['contexts'][0]['forVehicleContext']['vehicleDetails']


def test_real_seven_page_projection_replay(captures):
    rows = [parse(c) for c in captures]
    assert [r['observed_status'] for r in rows] == [
        'sold_label', 'unavailable', 'pending', 'unavailable', 'available', 'unknown', 'sold_label']
    assert all(r['parse_outcome'] == 'matched' for r in rows)
    assert all(r['vin'] == r['observed_vin'] and r['listing_id'] == r['observed_listing_id'] for r in rows)
    assert rows[5]['purchaseType'] == 'Reservable'


def test_recommendations_and_react_path_changes_do_not_change_target(captures):
    sold, other = captures[0], captures[4]
    sold['contexts'][0]['source_path'] = 'arbitrary.999.children.42.forVehicleContext.vehicleDetails'
    sold['contexts'].insert(0, other['contexts'][0])
    assert parse(sold)['observed_status'] == 'sold_label'
    sold['contexts'].append(deepcopy(sold['contexts'][1]))
    sold['contexts'][-1]['forVehicleContext']['vehicleDetails']['saleStatus'] = 'Available'
    assert parse(sold)['parse_outcome'] == 'conflicting_records'
    assert parse(sold)['observed_status'] == 'unknown'


@pytest.mark.parametrize('problem', ['vin', 'url', 'expected', 'no_target', 'contexts',
    'missing_sale', 'future_status', 'conflict', 'format', 'blocked', '404', 'boilerplate'])
def test_synthetic_failures_never_mean_sold(captures, problem):
    c = captures[0]
    expected = deepcopy(c['expected'])
    if problem == 'vin': details(c)['vin'] = captures[1]['expected']['vin']
    if problem == 'url': c['final_url'] = captures[1]['final_url']
    if problem == 'expected': c['expected']['vin'] = captures[1]['expected']['vin']
    if problem == 'no_target': details(c)['vehicleId'] = 9999999
    if problem == 'contexts': c['contexts'] = None
    if problem == 'missing_sale': details(c).pop('saleStatus')
    if problem == 'future_status': details(c)['saleStatus'] = 'DeliveryScheduled'
    if problem == 'conflict': c['purchase_button'] = 'Get Started'
    if problem == 'format': c['format'] = 'unrecognized'
    if problem == 'blocked': c['access_outcome'] = 'access_blocked'; c['contexts'] = []
    if problem == '404': c['access_outcome'] = 'not_found'; c['contexts'] = []
    if problem == 'boilerplate':
        details(c)['saleStatus'] = 'Available'
        c['hero_badge'] = None
        c['hero_text'] = 'Equipment as originally sold'
    row = parse(c, expected=expected)
    assert row['observed_status'] != 'sold_label'
    if problem == 'blocked': assert row['observed_status'] == 'access_blocked'
    if problem == 'boilerplate': assert row['observed_status'] == 'unavailable'


def test_missing_eligibility_and_unknown_pending_are_not_available(captures):
    c = captures[4]
    c['purchase_button'] = None
    assert parse(c)['observed_status'] == 'unknown'
    details(c).pop('purchaseType')
    assert parse(c)['purchaseType'] is None
    assert parse(c)['observed_status'] == 'unknown'


def test_contradictory_specific_page_wording_stays_unresolved(captures):
    bad = deepcopy(captures[0]); bad['hero_text'] = {'malformed': 'object'}
    assert parse(bad)['parse_outcome'] == 'malformed_fields'
    assert parse(bad)['observed_status'] == 'unknown'
    pending = captures[2]
    pending['purchase_button'] = 'Get Started'
    assert parse(pending)['parse_outcome'] == 'conflicting_status'
    pending['purchase_button'] = 'View Similar'
    details(pending)['purchaseType'] = 'NotPurchasable'
    assert parse(pending)['observed_status'] == 'unknown'
    sold = captures[0]
    sold['hero_badge'] = None
    details(sold)['saleStatus'] = 'Available'
    assert parse(sold)['parse_outcome'] == 'conflicting_status'


def test_bad_times_are_rejected(captures):
    with pytest.raises(ValueError, match='precede'):
        parse(captures[0], available_at='2026-09-08T00:00:00Z')
    captures[0]['checked_at'] = '2026-09-09T11:42:40'
    with pytest.raises(ValueError, match='timezone aware'):
        parse(captures[0])


def synthetic_history(captures):
    # Actual identity, invented later observations: no live files are written.
    initial = parse(captures[4])
    sold = dict(initial, checked_at='2026-09-10T12:00:00Z', available_at='2026-09-10T13:00:00Z',
        saleStatus='Sold', purchaseType='NotPurchasable', observed_status='sold_label')
    again = dict(sold, checked_at='2026-09-11T12:00:00Z', available_at='2026-09-11T12:00:00Z')
    relisted = dict(initial, listing_id='9999999', observed_listing_id='9999999',
        checked_at='2026-09-12T12:00:00Z', available_at='2026-09-12T12:00:00Z')
    return [initial, sold, again, relisted]


def test_first_transition_repeat_and_new_listing_reappearance(captures, cohort):
    records = synthetic_history(captures)
    result = summarize_pilot(pd.DataFrame(records), cohort, as_of='2026-09-13T00:00:00Z')
    target = result.loc[result.vin.eq(records[0]['vin'])].iloc[0]
    assert result.newly_observed_sold.sum() == 1
    assert target.first_sold_at == pd.Timestamp(records[1]['checked_at'])
    assert target.last_non_sold_at == pd.Timestamp(records[0]['checked_at'])
    assert not target.first_encountered_sold
    assert target.reappeared_listing_id == '9999999'
    assert target.reappeared_at == pd.Timestamp(records[-1]['checked_at'])
    assert target.first_sold_listing_id == records[1]['listing_id']
    assert target.last_non_sold_listing_id == records[0]['listing_id']
    assert target.previous_checked_at == pd.Timestamp(records[-2]['checked_at'])
    assert target.previous_saleStatus == 'Sold' and target.latest_saleStatus == 'Available'


def test_initial_sold_and_controls_do_not_become_transitions(captures, cohort):
    history = synthetic_history(captures)
    result = summarize_pilot(pd.DataFrame(history[1:]), cohort, as_of='2026-09-13T00:00:00Z')
    assert not result.newly_observed_sold.any()
    assert result.first_encountered_sold.sum() == 1
    next(v for v in cohort['vehicles'] if v['vin'] == history[0]['vin'])['role'] = 'historical_control'
    result = summarize_pilot(pd.DataFrame(history), cohort, as_of='2026-09-13T00:00:00Z')
    assert not result.newly_observed_sold.any()


def test_cutoffs_include_physical_and_availability_times(captures, cohort):
    history = synthetic_history(captures)
    at = lambda cutoff: summarize_pilot(pd.DataFrame(history), cohort, as_of=cutoff)
    assert not at('2026-09-10T12:30:00Z').newly_observed_sold.any()
    assert at('2026-09-10T13:00:00Z').newly_observed_sold.sum() == 1
    assert at('2026-09-08T00:00:00Z').empty
    history[1]['parse_outcome'] = 'identity_mismatch'
    history[1]['observed_status'] = 'unknown'
    assert not at('2026-09-10T14:00:00Z').newly_observed_sold.any()


def test_import_preview_save_replay_and_tamper(tmp_path, importer, captures, cohort):
    src = tmp_path / 'capture.json'
    src.write_text(json.dumps(captures[0]), encoding='utf-8')
    dest = tmp_path / 'vehicle'
    folder, rows = importer.import_captures([src], cohort, root=dest, now=NOW)
    assert folder is None and not dest.exists()
    folder, rows = importer.import_captures([src], cohort, root=dest, now=NOW, save=True)
    assert (folder / 'capture_01.json').read_bytes() == src.read_bytes()
    assert set(p.name for p in folder.iterdir()) == {'capture_01.json', 'run.json'}
    assert load_pilot(dest, cohort, as_of='2026-09-09T11:59:59Z').empty
    assert len(load_pilot(dest, cohort, as_of=NOW)) == 1
    second, _ = importer.import_captures([src], cohort, root=dest, now=NOW, save=True)
    assert second != folder
    assert len(load_pilot(dest, cohort, as_of=NOW)) == 1
    (folder / 'capture_01.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='changed'):
        load_pilot(dest, cohort, as_of=NOW)


def test_import_unknown_vin_private_fields_and_future_capture_fail_before_writes(tmp_path, importer, captures, cohort):
    for problem in ['unknown_vin', 'private_field', 'nested_private', 'future_capture', 'duplicate']:
        c = deepcopy(captures[0])
        if problem == 'unknown_vin': c['expected']['vin'] = '11111111111111111'
        if problem == 'private_field': c['session'] = 'do not retain'
        if problem == 'nested_private': details(c)['vin'] = {'session': 'do not retain'}
        if problem == 'future_capture': c['checked_at'] = '2027-01-01T00:00:00Z'
        path = tmp_path / 'input.json'
        path.write_text(json.dumps(c), encoding='utf-8')
        with pytest.raises(ValueError):
            importer.import_captures([path, path] if problem == 'duplicate' else [path], cohort,
                root=tmp_path / 'out', now=NOW, save=True)
        assert not (tmp_path / 'out').exists()


def test_pilot_notebook_reads_only_and_skips_alternative_views(tmp_path, monkeypatch, importer, captures, cohort):
    from test_notebook_workflow import hashes
    # Exercise the actual pilot cell with available local evidence and write guards.
    import io
    book = json.loads((ROOT / 'notebooks/22_carvana_sale_status_validation.ipynb').read_text(encoding='utf-8'))
    code = ''.join(next(c for c in book['cells'] if c['id'] == 'daily-sale-signal-evidence')['source'])
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/carvana_sale_pilot.json').write_text(json.dumps(cohort), encoding='utf-8')
    (tmp_path / 'config/carvana_sale_pilot_extension_20260909.json').write_bytes(
        (ROOT / 'config/carvana_sale_pilot_extension_20260909.json').read_bytes())
    src = tmp_path / 'capture.json'; src.write_text(json.dumps(captures[0]), encoding='utf-8')
    importer.import_captures([src], cohort, root=tmp_path, now=NOW, save=True)
    before = hashes(tmp_path)
    original = io.open
    def guarded(path, mode='r', *args, **kwargs):
        assert not any(flag in mode for flag in 'wax+')
        return original(path, mode, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(io, 'open', guarded)
        scope = dict(ROOT=tmp_path, Path=Path, pd=pd, json=json, display=lambda x: None, PILOT_AS_OF=NOW, PILOT_RECHECK_HOURS=24,
            PILOT_CONFIGS=[tmp_path / 'config/carvana_sale_pilot.json',
                tmp_path / 'config/carvana_sale_pilot_extension_20260909.json'])
        exec(code, scope)
        assert len(scope['sale_signal_study']) == 1
        assert not scope['pilot_summary'].newly_observed_sold.any()
        exec(code, dict(scope, CYCLE_REPORTS_OVERRIDE=[]))
        alternate = dict(scope, TRACKING_CONFIG_OVERRIDE='synthetic')
        exec(code, alternate)
        assert alternate['sale_signal_study'].empty
    assert hashes(tmp_path) == before


def test_browser_decoder_synthetic_split_stream_and_moved_context(captures):
    """Node is optional QA tooling; the runtime helper executes in Chrome only."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable; browser helper was validated on real Chrome pages')
    helper = (ROOT / 'scripts/capture_carvana_page.js').read_text(encoding='utf-8')
    # Synthetic RSC framing around genuine public vehicle records, never live evidence.
    record = {'children': [captures[4]['contexts'][0], {'shifted': [captures[0]['contexts'][0]]}]}
    stream = '9z:' + json.dumps(record) + '\n'
    chunks = [stream[:len(stream)//2], stream[len(stream)//2:]]
    scripts = ['self.__next_f.push(' + json.dumps([1, chunk]) + ')' for chunk in chunks]
    program = '''
const fs=require('fs'), vm=require('vm');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const sandbox={expected:input.expected,location:{href:input.url},
 document:{scripts:input.scripts.map(textContent=>({textContent})),querySelector:()=>null}};
const result=vm.runInNewContext(input.helper+';captureCarvanaPage(expected)',sandbox);
process.stdout.write(JSON.stringify(result));
'''
    output = subprocess.run([node, '-e', program], input=json.dumps(dict(helper=helper, scripts=scripts,
        expected=captures[0]['expected'], url=captures[0]['final_url'])), text=True, capture_output=True, check=True)
    c = json.loads(output.stdout)
    assert len(c['contexts']) == 2
    row = parse(c, available_at='2099-01-01T00:00:00Z')
    assert row['observed_status'] == 'sold_label' and row['observed_vin'] == captures[0]['expected']['vin']


@pytest.fixture
def extension():
    return json.loads((ROOT / 'config/carvana_sale_pilot_extension_20260909.json').read_text(encoding='utf-8'))


def selection_rows():
    return pd.DataFrame([dict(retailer='carvana', vin=f'11111111111111{i:03d}', listing_id=str(1000+i),
        listing_url=f'https://www.carvana.com/vehicle/{1000+i}', purchase_pending=flag,
        observed_at_utc='2026-09-09T10:00:00Z', capture_id='synthetic-' + str(i))
        for i, flag in enumerate([True, True, True, False, False, False, None, 'false'])])


def test_extension_selection_is_reproducible_under_shuffled_input(cohort):
    rows = selection_rows()
    selected, counts = select_extension(rows, rows, cohort, seed=42, pending_count=2, nonpending_count=2)
    shuffled, again = select_extension(rows.sample(frac=1, random_state=123),
        rows.sample(frac=1, random_state=321), cohort, seed=42, pending_count=2, nonpending_count=2)
    pd.testing.assert_frame_equal(selected, shuffled)
    pd.testing.assert_frame_equal(counts, again)
    assert counts.eligible.tolist() == [3, 3] and counts.selected.tolist() == [2, 2]
    assert selected.loc[selected.pending_group.eq('unknown'), 'exclusion_reason'].eq('unknown_pending').all()
    assert not selected.loc[selected.pending_group.eq('unknown'), 'selected'].any()


def test_extension_shortages_unknown_flags_and_ambiguous_identities(cohort):
    rows = selection_rows()
    ambiguous = rows.iloc[[0]].assign(vin='22222222222222222')
    history = pd.concat([rows, ambiguous], ignore_index=True)
    conflicting = rows.iloc[[1]].assign(listing_id='9001', listing_url='https://www.carvana.com/vehicle/9001')
    rows = pd.concat([rows, conflicting], ignore_index=True)
    selected, counts = select_extension(rows, history, cohort, seed=42, pending_count=6, nonpending_count=1)
    assert counts.eligible.tolist() == [1, 3]
    assert counts.selected.sum() == 4  # Shortage filled only from remaining eligible identities.
    assert selected.loc[selected.vin.isin(rows.vin.iloc[:2]), 'exclusion_reason'].eq('ambiguous_identity_or_snapshot').all()
    assert selected.loc[selected.pending_group.eq('unknown'), 'selected'].eq(False).all()
    assert selected.loc[selected.selected, 'vin'].nunique() == 4


def test_cohort_overlap_and_selection_clock_are_checked(cohort, extension):
    assert known_disjoint_cohorts([cohort, extension], as_of=NOW) == [cohort]
    assert known_disjoint_cohorts([cohort, extension], as_of='2026-09-09T10:00Z') == []
    known = known_disjoint_cohorts([cohort, extension], as_of='2026-09-11T00:00Z')
    assert sum(len(c['vehicles']) for c in known) == 33
    extension['vehicles'][0] = deepcopy(cohort['vehicles'][0])
    with pytest.raises(ValueError, match='overlap'):
        known_disjoint_cohorts([cohort, extension], as_of='2026-09-11T00:00Z')
    assert known_disjoint_cohorts([cohort, extension], as_of=NOW) == [cohort]
    with pytest.raises(ValueError, match='repeat'):
        known_disjoint_cohorts([cohort, cohort], as_of=NOW)


def run_pilot_book(tmp_path, monkeypatch, cohorts, records=None, cutoff='2026-09-11T00:00Z', inventory=None):
    """Execute actual code cells with network, SQL and file-write guards."""
    import builtins
    import io
    from unittest.mock import Mock
    from test_daily_notebook import checker
    from test_notebook_workflow import hashes
    import vehicle_tracker.sale_pilot as module
    import vehicle_tracker.daily as daily

    config = tmp_path / 'config'
    config.mkdir(exist_ok=True)
    for filename, cohort in zip(['carvana_sale_pilot.json', 'carvana_sale_pilot_extension_20260909.json'], cohorts):
        (config / filename).write_text(json.dumps(cohort), encoding='utf-8')
    if records is not None:
        for frame in records.values():
            for field in ['checked_at', 'available_at']:
                frame[field] = frame[field].map(pd.Timestamp)
        monkeypatch.setattr(module, 'load_pilot', lambda root, cohort, *, as_of:
            records.get(cohort['cohort_id'], pd.DataFrame()).copy())
    if inventory is not None:
        (config / 'carvana_daily_tracking.json').write_text('{}', encoding='utf-8')
        monkeypatch.setattr(daily, 'tracking_settings', lambda path: {'synthetic': True})
        def read_inventory(settings, *, as_of):
            assert as_of == cutoff
            return tuple(frame.copy() for frame in inventory)
        monkeypatch.setattr(daily, 'tracking_history', read_inventory)
    scope = dict(AS_OF_OVERRIDE=cutoff)
    book = json.loads((ROOT / 'notebooks/22_carvana_sale_status_validation.ipynb').read_text(encoding='utf-8'))
    before = hashes(tmp_path)
    def guard(original):
        def read_only(path, mode='r', *args, **kwargs):
            assert not any(flag in mode for flag in 'wax+')
            return original(path, mode, *args, **kwargs)
        return read_only
    monkeypatch.chdir(tmp_path)
    with monkeypatch.context() as guarded, checker.offline_guards():
        guarded.setattr(builtins, 'open', guard(builtins.open))
        guarded.setattr(io, 'open', guard(io.open))
        for name in ['mkdir', 'write_text', 'write_bytes', 'replace', 'unlink']:
            guarded.setattr(Path, name, Mock(side_effect=AssertionError('Run All attempted a write')))
        for cell in book['cells']:
            if cell['cell_type'] == 'code':
                exec(compile(''.join(cell['source']), cell['id'], 'exec'), scope)
    assert hashes(tmp_path) == before
    return scope


@pytest.mark.parametrize('cutoff,count', [('2026-09-09T10:00Z', 0), (NOW, 7), ('2026-09-11T00:00Z', 33)])
def test_unobserved_extension_and_historical_selection_cutoff(tmp_path, monkeypatch, cohort, extension, cutoff, count):
    scope = run_pilot_book(tmp_path, monkeypatch, [cohort, extension], cutoff=cutoff)
    assert len(scope['pilot_summary']) == count
    assert len(scope['no_retained_check']) == count
    assert len(scope['pilot_attention']) == count
    if count:
        assert scope['pilot_attention'].attention_reason.eq('No retained check yet').all()
        coverage = scope['pilot_coverage']
        assert coverage.observed_VINs.sum() == 0
        assert coverage.first_observed_Sold_transitions.isna().all()
        assert coverage.measured_sales.isna().all() and coverage.outcome_rate.isna().all()
        assert scope['combined_coverage'].no_retained_check.iloc[0] == count
    if count == 33:
        ext = scope['pilot_summary'].loc[scope['pilot_summary'].cohort_id.eq(extension['cohort_id'])]
        assert ext.selection_pending_group.value_counts().to_dict() == {'true': 18, 'false': 8}


@pytest.mark.parametrize('include_original', [False, True])
def test_optional_absent_configs_do_not_erase_known_original(tmp_path, monkeypatch, cohort, include_original, capsys):
    scope = run_pilot_book(tmp_path, monkeypatch, [cohort] if include_original else [])
    assert len(scope['pilot_summary']) == (7 if include_original else 0)
    assert 'Optional frozen cohort configuration is absent' in capsys.readouterr().out


def extension_record(captures, vehicle, time, status='available'):
    capture = deepcopy(captures[4])
    capture['expected'] = {field: vehicle[field] for field in ['retailer', 'vin', 'listing_id']}
    capture.update(checked_at=time, requested_url=vehicle['url'], final_url=vehicle['url'])
    details(capture).update(vehicleId=int(vehicle['listing_id']), vin=vehicle['vin'])
    if status == 'access_failed':
        capture.update(access_outcome='access_blocked', contexts=[], hero_text=None, hero_badge=None, purchase_button=None)
    elif status == 'preorder':
        details(capture)['purchaseType'] = 'Reservable'
        capture.update(hero_text='Pre-order now', hero_badge='Pre-order now', purchase_button='Pre-Order Now')
    return parse_capture(capture, expected=capture['expected'], available_at=time, source='synthetic://temporary-pilot')


def test_partial_extension_keeps_failed_and_unresolved_checks_separate(tmp_path, monkeypatch, captures, cohort, extension):
    vehicles = extension['vehicles']
    plan = [(0, '01:00', 'available'), (1, '01:00', 'available'), (1, '02:00', 'available'),
            (2, '01:00', 'access_failed'), (3, '01:00', 'preorder'),
            (4, '01:00', 'available'), (4, '02:00', 'access_failed')]
    rows = pd.DataFrame([extension_record(captures, vehicles[i], '2026-09-10T' + time + ':00Z', status)
                        for i, time, status in plan])
    scope = run_pilot_book(tmp_path, monkeypatch, [cohort, extension], {extension['cohort_id']: rows})
    ext = scope['pilot_summary'].loc[scope['pilot_summary'].cohort_id.eq(extension['cohort_id'])]
    assert ext.checks.eq(0).sum() == 21 and ext.checks.eq(1).sum() == 3 and ext.checks.gt(1).sum() == 2
    assert ext.usable_status_checks.sum() == 4 and ext.usable_native_checks.sum() == 5
    assert ext.access_failures.sum() == 2 and ext.unresolved_checks.sum() == 1
    assert ext.prospective_repeat_observed.sum() == 1
    failed = ext.loc[ext.vin.eq(vehicles[4]['vin'])].iloc[0]
    assert failed.previous_saleStatus == 'Available' and pd.isna(failed.latest_saleStatus)
    assert failed.latest_status == 'access_blocked'
    assert failed.previous_checked_at < failed.checked_at
    latest = scope['checked_vehicles'].set_index('vin')
    assert 'Pre-order' in latest.loc[vehicles[3]['vin'], 'why_unresolved']
    assert 'access_failed' in latest.loc[vehicles[4]['vin'], 'why_unresolved']
    changes = scope['pilot_changes'].set_index('vin')
    failed_change = changes.loc[vehicles[4]['vin']]
    assert pd.isna(failed_change.saleStatus_changed)
    assert pd.isna(failed_change.purchaseType_changed)
    assert pd.isna(failed_change.interpretation_changed)
    assert not failed_change.change_observed
    assert not changes.loc[vehicles[1]['vin'], 'change_observed']
    attention = scope['pilot_attention'].set_index('vin')
    assert attention.loc[vehicles[4]['vin'], 'attention_reason'] == 'Review failed or conflicting check'
    assert attention.loc[vehicles[3]['vin'], 'attention_reason'] == 'Review unresolved page evidence'
    assert attention.loc[vehicles[5]['vin'], 'attention_reason'] == 'No retained check yet'
    assert vehicles[0]['vin'] not in attention.index  # Fresh available evidence needs no reminder.
    coverage = scope['pilot_coverage']
    assert coverage.measured_sales.isna().all() and coverage.outcome_rate.isna().all()
    assert coverage.first_observed_Sold_transitions.dropna().eq(0).all()
    assert coverage.loc[coverage.prospective_repeat_observed.eq(0), 'first_observed_Sold_transitions'].isna().all()


@pytest.mark.parametrize('later_checks', [False, True])
def test_equal_time_native_statuses_do_not_establish_later_reappearance(
        tmp_path, monkeypatch, captures, cohort, extension, later_checks):
    sold = parse(captures[0])
    returned = dict(sold, listing_id='9999999', observed_listing_id='9999999', saleStatus='Available',
                    purchaseType='Purchasable', observed_status='available')
    records = [sold, returned]
    if later_checks:
        for minute in [1, 2]:
            later_time = (pd.Timestamp(returned['checked_at']) + pd.Timedelta(minutes=minute)).isoformat()
            records.append(dict(returned, checked_at=later_time, available_at=later_time))
    rows = pd.DataFrame(records)
    scope = run_pilot_book(tmp_path, monkeypatch, [cohort, extension], {cohort['cohort_id']: rows})
    if later_checks:
        reappeared = scope['later_reappearances']
        assert len(reappeared) == 1 and reappeared.listing_id.iloc[0] == '9999999'
        assert reappeared.checked_at.iloc[0] == pd.Timestamp(records[2]['checked_at'])
    else:
        assert scope['later_reappearances'].empty
        assert scope['pilot_summary'].reappeared_at.isna().all()


def test_importer_retains_twelve_capture_limit(tmp_path, importer, cohort):
    with pytest.raises(ValueError, match='1-12'):
        importer.import_captures([tmp_path/'not-read.json'] * 13, cohort, root=tmp_path, now=NOW, save=True)
    assert list(tmp_path.iterdir()) == []


def test_recent_failed_attempt_does_not_refresh_older_native_evidence(captures, cohort):
    vehicle = cohort['vehicles'][0]
    older = extension_record(captures, vehicle, '2026-09-09T12:00:00Z', 'preorder')
    failed = extension_record(captures, vehicle, '2026-09-10T13:00:00Z', 'access_failed')
    records = pd.DataFrame([older, failed])
    summary = summarize_pilot(records, cohort, as_of='2026-09-10T14:00:00Z')
    row = summary.loc[summary.vin.eq(vehicle['vin'])].iloc[0]
    assert row.hours_since_attempt == 1 and row.hours_since_usable_native == 26
    assert row.last_attempt_at == pd.Timestamp(failed['checked_at'])
    assert row.last_usable_native_at == pd.Timestamp(older['checked_at'])
    assert row.overdue and not row.no_usable_native_observation
    assert row.usable_status_checks == 0 and row.usable_native_checks == 1
    fresh_interval = summarize_pilot(records, cohort, as_of='2026-09-10T14:00Z', recheck_hours=48)
    assert not fresh_interval.loc[fresh_interval.vin.eq(vehicle['vin']), 'overdue'].iloc[0]
    failed['available_at'] = '2026-09-10T15:00:00Z'
    historical = summarize_pilot(pd.DataFrame([older, failed]), cohort, as_of='2026-09-10T14:00Z')
    assert historical.loc[historical.vin.eq(vehicle['vin']), 'hours_since_attempt'].iloc[0] == 26


def test_freshness_without_observations_and_before_selection(cohort):
    summary = summarize_pilot(pd.DataFrame(), cohort, as_of=NOW)
    assert summary.no_usable_native_observation.all() and summary.overdue.all()
    assert summary[['last_attempt_at', 'last_usable_native_at', 'hours_since_attempt', 'hours_since_usable_native']].isna().all().all()
    assert summarize_pilot(pd.DataFrame(), cohort, as_of='2026-09-09T10:00Z').empty


@pytest.mark.parametrize('interval', [0, -1, True, float('inf'), float('nan'), '24'])
def test_freshness_interval_requires_a_finite_positive_number(cohort, interval):
    with pytest.raises(ValueError, match='finite positive'):
        summarize_pilot(pd.DataFrame(), cohort, as_of=NOW, recheck_hours=interval)


def test_transition_interval_uses_last_native_non_sold_not_midpoint(captures, cohort):
    records = synthetic_history(captures)
    preorder = dict(records[0], checked_at='2026-09-10T11:00:00Z', available_at='2026-09-10T11:00:00Z',
                    purchaseType='Reservable', observed_status='unknown')
    summary = summarize_pilot(pd.DataFrame([*records, preorder]), cohort, as_of='2026-09-13T00:00Z')
    target = summary.loc[summary.vin.eq(records[0]['vin'])].iloc[0]
    assert target.transition_interval_hours == 1
    assert target.last_non_sold_at == pd.Timestamp(preorder['checked_at'])
    assert target.first_sold_at == pd.Timestamp(records[1]['checked_at'])
    assert target.newly_observed_sold
    assert summary.loc[~summary.newly_observed_sold, 'transition_interval_hours'].isna().all()
    next(v for v in cohort['vehicles'] if v['vin'] == records[0]['vin'])['role'] = 'historical_control'
    assert summarize_pilot(pd.DataFrame(records), cohort, as_of='2026-09-13T00:00Z').transition_interval_hours.isna().all()


def inventory_for_pilot(cohort):
    days = pd.DataFrame([dict(cycle_id='synthetic-' + day, cycle_date='2026-09-' + day,
        timezone='UTC', scope_id='synthetic-fixed-scope', coverage_complete=True, coverage_reason='Synthetic complete',
        window_start=f'2026-09-{day}T10:00:00Z', window_end=f'2026-09-{day}T11:00:00Z',
        available_at=f'2026-09-{day}T11:05:00Z') for day in ['09', '10']])
    rows = pd.DataFrame([dict(cycle_id='synthetic-' + day, retailer='carvana', vin=cohort['vehicles'][0]['vin'],
        listing_id=listing, observed_at_utc=f'2026-09-{day}T10:30:00Z', capture_id='capture-' + day,
        run_id='run-' + day, source_url='synthetic://inventory/' + day, listing_url='synthetic://listing/' + listing,
        asking_price_usd=price, purchase_pending=pending, vehicle_lock_type=0)
        for day, listing, price, pending in [('09', 'old-id', 100, True), ('10', 'new-id', 110, False)]])
    return days, rows


def test_inventory_availability_after_page_check_cannot_be_joined_backward(tmp_path, monkeypatch, captures, cohort):
    days, rows = inventory_for_pilot(cohort)
    days.loc[1, 'available_at'] = '2026-09-10T13:00:00Z'  # Physically earlier, but not yet available at noon.
    page = extension_record(captures, cohort['vehicles'][0], '2026-09-10T12:00:00Z')
    scope = run_pilot_book(tmp_path, monkeypatch, [cohort], {cohort['cohort_id']: pd.DataFrame([page])},
        cutoff='2026-09-10T14:00Z', inventory=(days, rows))
    comparison = scope['inventory_page_comparison'].iloc[0]
    assert comparison.inventory_listing_id == 'old-id' and comparison.asking_price_usd == 100
    assert comparison.inventory_available_at == pd.Timestamp('2026-09-09T11:05Z')
    assert comparison.inventory_observed_at == pd.Timestamp('2026-09-09T10:30Z')
    assert comparison.elapsed_observation_hours == 25.5
    assert comparison.inventory_context == 'collection_gap_unassessable'


def test_preceding_inventory_preserves_different_page_listing_and_native_values(tmp_path, monkeypatch, captures, cohort):
    days, rows = inventory_for_pilot(cohort)
    rows.loc[1, 'purchase_pending'] = True
    page = extension_record(captures, cohort['vehicles'][0], '2026-09-10T12:00:00Z')
    scope = run_pilot_book(tmp_path, monkeypatch, [cohort], {cohort['cohort_id']: pd.DataFrame([page])}, inventory=(days, rows))
    comparison = scope['inventory_page_comparison'].iloc[0]
    assert comparison.inventory_listing_id == 'new-id'
    assert comparison.page_listing_id == cohort['vehicles'][0]['listing_id']
    assert comparison.purchase_pending and comparison.page_interpretation == 'available'
    assert comparison.elapsed_observation_hours == 1.5
    assert comparison.inventory_context == 'observed_in_latest_complete_cycle'
    assert comparison.saleStatus == 'Available' and comparison.purchaseType == 'Purchasable'


@pytest.mark.parametrize('conflict', ['inventory_binding', 'page_binding', 'duplicate'])
def test_inventory_ambiguous_identities_block_comparison(tmp_path, monkeypatch, captures, cohort, conflict):
    days, rows = inventory_for_pilot(cohort)
    if conflict == 'inventory_binding':
        rows.loc[1, 'listing_id'] = rows.loc[0, 'listing_id']
        rows.loc[1, 'vin'] = '22222222222222222'
    elif conflict == 'page_binding':
        rows.loc[1, 'listing_id'] = cohort['vehicles'][0]['listing_id']
        rows.loc[1, 'vin'] = '22222222222222222'
    else:
        rows = pd.concat([rows, rows.iloc[[1]]], ignore_index=True)
    page = extension_record(captures, cohort['vehicles'][0], '2026-09-10T12:00:00Z')
    scope = run_pilot_book(tmp_path, monkeypatch, [cohort], {cohort['cohort_id']: pd.DataFrame([page])}, inventory=(days, rows))
    comparison = scope['inventory_page_comparison'].iloc[0]
    assert comparison.inventory_context == 'invalid_identity_or_cycle_evidence'
    assert pd.isna(comparison.inventory_listing_id) and pd.isna(comparison.asking_price_usd)
    assert len(scope['inventory_comparison_diagnostics']) == 1


@pytest.mark.parametrize('condition,expected', [
    ('partial', 'partial_cycle_unassessable'), ('missing', 'collection_gap_unassessable'),
    ('complete_without_vin', 'not_observed_in_complete_cycle'),
    ('historical_control', 'historical_control_not_observed_in_scope'),
    ('never_observed', 'scope_membership_not_established')])
def test_inventory_missing_partial_and_controls_remain_distinct(tmp_path, monkeypatch, captures, cohort, condition, expected):
    days, rows = inventory_for_pilot(cohort)
    vehicle = cohort['vehicles'][0]
    if condition == 'partial':
        days.loc[1, 'coverage_complete'] = False
        rows = rows.iloc[:1]
    elif condition == 'missing':
        days, rows = days.iloc[:1], rows.iloc[:1]
    elif condition == 'complete_without_vin':
        rows = rows.iloc[:1]
    elif condition == 'historical_control':
        vehicle = next(v for v in cohort['vehicles'] if v['role'] == 'historical_control')
    else:
        rows = rows.iloc[:0]
    page = extension_record(captures, vehicle, '2026-09-10T12:00:00Z')
    scope = run_pilot_book(tmp_path, monkeypatch, [cohort], {cohort['cohort_id']: pd.DataFrame([page])}, inventory=(days, rows))
    comparison = scope['inventory_page_comparison'].iloc[0]
    assert comparison.inventory_context == expected
    if condition in ['historical_control', 'never_observed']:
        assert pd.isna(comparison.inventory_observed_at) and pd.isna(comparison.asking_price_usd)
    else:
        assert comparison.inventory_observed_at == pd.Timestamp('2026-09-09T10:30Z')
    assert scope['pilot_coverage'].measured_sales.isna().all()
