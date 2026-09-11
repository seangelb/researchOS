"""Small offline examples; all reports, dates and vehicle events here are synthetic."""
import hashlib
import json

import pandas as pd
import pytest

from vehicle_tracker.vehicle_history import (FOLLOWUP_COLUMNS, REPORT_COLUMNS,
    followup_identities, load_report_events)

VIN = '5YJ3E1EA0LF632279'
OTHER_VIN = '5YJ3E1EA0LF784711'
CUTOFF = '2026-09-13T00:00:00Z'


def study(tmp_path, **changes):
    source = tmp_path / 'report_projection.json'
    report = dict(report_id='fixture-report', retailer='carvana', listing_id='1001',
        expected_vin=VIN, report_vin=VIN, source_url='https://www.carvana.com/vehicle/autocheck/1001',
        report_run_at='2026-09-10T09:00:00Z', first_observed_at='2026-09-10T10:00:00Z',
        available_at='2026-09-10T11:00:00Z', events=[dict(event_date='2026-09-08',
            event_source='Motor Vehicle Dept.', native_wording='Title renewal',
            location='TX', event_kind='registration', odometer_miles=0)])
    report.update(changes)
    projection = {name: value for name, value in report.items() if name not in ['source_file', 'source_sha256']}
    source.write_text(json.dumps(projection), encoding='utf-8')
    report.setdefault('source_file', source.name)
    report.setdefault('source_sha256', hashlib.sha256(source.read_bytes()).hexdigest())
    (tmp_path / 'reports.json').write_text(json.dumps([report]), encoding='utf-8')
    return report


def test_wrong_vin_is_visible_but_not_a_match(tmp_path):
    study(tmp_path, report_vin=OTHER_VIN)
    row = load_report_events(tmp_path, as_of=CUTOFF).iloc[0]
    assert not row.identity_match
    assert row.expected_vin == VIN and row.report_vin == OTHER_VIN
    assert row.native_wording == 'Title renewal' and row.odometer_miles == 0
    assert row.event_date == '2026-09-08'
    assert row.report_run_at < row.first_observed_at < row.available_at
    assert 'sale_date' not in row.index and 'confirmed_sale' not in row.index


def test_later_auction_is_preserved_as_an_auction_not_a_retail_sale(tmp_path):
    study(tmp_path, events=[dict(event_date='2026-09-09', event_source='Auto Auction',
        native_wording='Reported at Auto Auction as Dealer Vehicle', location='FL',
        event_kind='auction_sale')])
    row = load_report_events(tmp_path, as_of=CUTOFF).iloc[0]
    assert row.event_kind == 'auction_sale'
    assert row.event_source == 'Auto Auction'
    assert 'unresolved' in row.event_note


def test_later_discovery_never_appears_at_earlier_cutoff(tmp_path):
    study(tmp_path)
    assert load_report_events(tmp_path, as_of='2026-09-10T10:30:00Z').empty
    assert len(load_report_events(tmp_path, as_of='2026-09-10T11:00:00Z')) == 1


def test_future_relative_to_report_event_excluded_with_diagnostic(tmp_path):
    study(tmp_path, events=[dict(event_date='2026-09-11', event_source='DMV',
        native_wording='Title issued', location='TX', event_kind='title')])
    row = load_report_events(tmp_path, as_of=CUTOFF).iloc[0]
    assert pd.isna(row.event_date) and 'No eligible dated events' in row.event_note


def test_unknown_report_run_time_is_not_invented(tmp_path):
    study(tmp_path, report_run_at=None)
    row = load_report_events(tmp_path, as_of=CUTOFF).iloc[0]
    assert pd.isna(row.report_run_at) and pd.notna(row.first_observed_at)


def test_blocked_report_retains_access_context_without_invented_events(tmp_path):
    study(tmp_path, report_vin=None, report_run_at=None, events=[],
        report_outcome='access_challenge', sample_role='prospective_inventory', context='Device check blocked access.')
    row = load_report_events(tmp_path, as_of=CUTOFF).iloc[0]
    assert not row.identity_match and pd.isna(row.event_date)
    assert row.report_outcome == 'access_challenge'
    assert row.sample_role == 'prospective_inventory' and row.context == 'Device check blocked access.'


@pytest.mark.parametrize('problem', ['hash', 'outside', 'missing_source'])
def test_retained_report_requires_safe_matching_source(tmp_path, problem):
    changes = dict(source_sha256='0' * 64) if problem == 'hash' else dict(
        source_file='../outside.txt' if problem == 'outside' else 'absent.txt')
    study(tmp_path, **changes)
    with pytest.raises((ValueError, FileNotFoundError)):
        load_report_events(tmp_path, as_of=CUTOFF)


@pytest.mark.parametrize('field', ['report_vin', 'events', 'available_at'])
def test_manifest_cannot_override_retained_identity_events_or_clocks(tmp_path, field):
    report = study(tmp_path)
    if field == 'report_vin':
        report[field] = OTHER_VIN
    elif field == 'events':
        report[field][0]['native_wording'] = 'Vehicle sold'
    else:
        report[field] = '2027-01-01T00:00:00Z'  # Cannot hide tampering behind the cutoff.
    (tmp_path / 'reports.json').write_text(json.dumps([report]), encoding='utf-8')
    with pytest.raises(ValueError, match='differs from the retained source'):
        load_report_events(tmp_path, as_of=CUTOFF)


def test_blocked_report_also_requires_a_retained_projection(tmp_path):
    report = study(tmp_path, events=[], report_vin=None)
    report.pop('source_file')
    (tmp_path / 'reports.json').write_text(json.dumps([report]), encoding='utf-8')
    with pytest.raises(ValueError, match='Every report requires'):
        load_report_events(tmp_path, as_of=CUTOFF)


def test_missing_optional_study_has_stable_columns(tmp_path):
    assert load_report_events(tmp_path / 'absent', as_of=CUTOFF).columns.tolist() == REPORT_COLUMNS
    result = followup_identities(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), as_of=CUTOFF)
    assert result.columns.tolist() == FOLLOWUP_COLUMNS


def cohort():
    return pd.DataFrame([dict(retailer='carvana', vin=VIN, listing_id='1001',
        url='https://www.carvana.com/vehicle/1001')])


def page(listing, day, **changes):
    row = dict(retailer='carvana', vin=VIN, listing_id=listing, observed_listing_id=listing,
        observed_vin=VIN, requested_url='https://www.carvana.com/vehicle/' + listing,
        final_url='https://www.carvana.com/vehicle/' + listing, parse_outcome='matched',
        saleStatus='Available', checked_at=f'2026-09-{day:02}T10:00:00Z',
        available_at=f'2026-09-{day:02}T11:00:00Z', source='synthetic://page/' + listing)
    row.update(changes)
    return row


def test_changed_listing_reappearance_uses_verified_link_despite_later_failure():
    records = pd.DataFrame([page('1001', 9, saleStatus='Sold'), page('1001', 10, saleStatus='Sold'),
        page('2002', 11), page('3003', 12, parse_outcome='identity_mismatch', observed_vin=OTHER_VIN)])
    result = followup_identities(cohort(), records, pd.DataFrame(), as_of=CUTOFF)
    row = result.iloc[0]
    assert len(result) == 1
    assert row.original_listing_id == '1001' and row.original_url.endswith('/1001')
    assert row.followup_listing_id == '2002' and row.followup_url.endswith('/2002')
    assert row.followup_observed_at == pd.Timestamp('2026-09-11T10:00:00Z')


def test_inventory_and_page_identity_compete_on_physical_not_import_time():
    inventory = pd.DataFrame([dict(retailer='carvana', vin=VIN, listing_id='2002',
        listing_url='https://www.carvana.com/vehicle/2002',
        inventory_observed_at='2026-09-11T10:30:00Z', inventory_available_at='2026-09-11T11:00:00Z',
        source_url='synthetic://inventory')])
    records = pd.DataFrame([page('1001', 9, available_at='2026-09-12T11:00:00Z')])
    row = followup_identities(cohort(), records, inventory, as_of=CUTOFF).iloc[0]
    assert row.followup_listing_id == '2002' and row.followup_source == 'synthetic://inventory'


def test_future_discovered_identity_does_not_override_or_cause_backward_conflict():
    records = pd.DataFrame([page('2002', 10, available_at='2026-09-12T11:00:00Z'),
        page('1001', 10, vin=OTHER_VIN, observed_vin=OTHER_VIN, available_at='2026-09-12T11:00:00Z')])
    row = followup_identities(cohort(), records, pd.DataFrame(), as_of='2026-09-11T00:00:00Z').iloc[0]
    assert row.followup_listing_id == '1001' and pd.isna(row.followup_observed_at)
    assert 'no verified observation' in row.followup_reason
    with pytest.raises(ValueError, match='conflicting VINs'):
        followup_identities(cohort(), records, pd.DataFrame(), as_of=CUTOFF)


def test_wrong_url_never_becomes_followup_identity():
    records = pd.DataFrame([page('2002', 11, final_url='https://www.carvana.com/vehicle/9999')])
    row = followup_identities(cohort(), records, pd.DataFrame(), as_of=CUTOFF).iloc[0]
    assert row.followup_listing_id == '1001' and pd.isna(row.followup_observed_at)
