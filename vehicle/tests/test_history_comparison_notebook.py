"""Execute the real comparison cells with synthetic events and temporary inputs."""
import hashlib
import json

import pandas as pd
import pytest

from test_daily_events import cycles, observations
from test_sale_pilot import captures, cohort, run_pilot_book, synthetic_history


def add_report(root, vin, listing, *, report_vin=None, available='2026-09-12T13:00:00Z'):
    folder = root / 'data/experiments/vehicle_history_study/20260910T034129Z'
    folder.mkdir(parents=True)
    source = folder / 'projection.json'
    record = dict(report_id='synthetic-report', retailer='carvana', listing_id=listing,
        expected_vin=vin, report_vin=report_vin or vin,
        source_url=f'https://www.carvana.com/vehicle/autocheck/{listing}',
        report_run_at='2026-09-12T10:00:00Z', first_observed_at='2026-09-12T11:00:00Z',
        available_at=available, events=[dict(event_date='2026-09-12',
            event_source='Auto Auction', native_wording='Sold at Auto Auction', event_kind='auction_sale')])
    source.write_text(json.dumps(record), encoding='utf-8')
    record.update(source_file=source.name, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    (folder / 'reports.json').write_text(json.dumps([record]), encoding='utf-8')


@pytest.mark.parametrize('report_case', ['matched', 'wrong_vin', 'later_discovery'])
def test_report_join_and_repeated_sold_are_not_extra_sales(tmp_path, monkeypatch, captures, cohort, report_case):
    records = synthetic_history(captures)
    # Make the invented reappearance a consistent, independently matched page identity.
    records[-1].update(requested_url='https://www.carvana.com/vehicle/9999999',
                       final_url='https://www.carvana.com/vehicle/9999999')
    vin, listing = records[0]['vin'], records[0]['listing_id']
    changes = {'report_vin': '5YJ3E1EA0LF784711'} if report_case == 'wrong_vin' else {}
    if report_case == 'later_discovery':
        changes['available'] = '2026-09-14T00:00:00Z'
    add_report(tmp_path, vin, listing, **changes)
    scope = run_pilot_book(tmp_path, monkeypatch, [cohort],
        {cohort['cohort_id']: pd.DataFrame(records)}, cutoff='2026-09-13T00:00:00Z')
    assert len(scope['newly_observed_sold']) == 1  # Repeated Sold and auction rows do not add sales.
    followup = scope['history_followups'].loc[lambda x: x.vin.eq(vin)].iloc[0]
    assert followup.original_url.endswith('/' + listing)
    assert followup.followup_url.endswith('/9999999')
    comparison = scope['vehicle_history_comparison'].loc[lambda x: x.vin.eq(vin)]
    if report_case == 'matched':
        assert comparison.native_wording.tolist() == ['Sold at Auto Auction']
        assert comparison.interpretation.str.contains('not automatically a Carvana retail sale').all()
        assert comparison.report_listing_id.iloc[0] != followup.followup_listing_id
    else:
        assert comparison.event_date.isna().all()
    if report_case == 'wrong_vin':
        assert len(scope['report_diagnostics']) == 1
    if report_case == 'later_discovery':
        assert scope['report_events'].empty


@pytest.mark.parametrize('condition', ['complete', 'partial', 'missing_day', 'short', 'reappeared'])
def test_absence_sensitivity_uses_complete_days_not_elapsed_age(tmp_path, monkeypatch, cohort, condition):
    cohort['selected_at'] = '2026-09-01T00:00:00Z'
    vehicle = cohort['vehicles'][0]
    day_numbers = (1, 2) if condition == 'short' else (1, 2, 3, 4)
    if condition == 'missing_day':
        day_numbers = (1, 2, 4)
    schedule = cycles(day_numbers, partial=(3,) if condition == 'partial' else ())
    rows = observations((1, vehicle['vin'], vehicle['listing_id']))
    if condition == 'reappeared':
        rows = observations((1, vehicle['vin'], vehicle['listing_id']), (4, vehicle['vin'], '9999999'))
    rows['listing_url'] = 'https://www.carvana.com/vehicle/' + rows.listing_id
    cutoff = '2026-09-02T20:00:00Z' if condition == 'short' else '2026-09-04T20:00:00Z'
    scope = run_pilot_book(tmp_path, monkeypatch, [cohort], cutoff=cutoff, inventory=(schedule, rows))
    result = scope['absence_sensitivity'].loc[lambda x: x.vin.eq(vehicle['vin'])].set_index('absence_days')
    expected = ('persistent-absence candidate' if condition == 'complete' else
        'observed in latest complete cycle' if condition == 'reappeared' else 'not yet evaluable')
    assert result.loc[3, 'assessment'] == expected
    assert result.loc[7, 'assessment'] == ('observed in latest complete cycle'
        if condition == 'reappeared' else 'not yet evaluable')
