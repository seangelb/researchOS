"""Optional native price fields; no inference of transaction price or update completeness."""
import copy
from unittest.mock import Mock

import pandas as pd
import pytest

from test_search import packet, response_data
from test_search_evidence import response
from vehicle_tracker.search import collect_search, parse_search_capture
from vehicle_tracker.search_evidence import public_source, replay_response, verify_response_evidence


def test_optional_price_history_survives_selected_source_and_replay(tmp_path, response_data):
    data = copy.deepcopy(response_data)
    # Exact observed price values from the retained September 8 public search page,
    # listing 4635054; the surrounding three-row response is a synthetic test fixture.
    vehicle = data['inventory']['vehicles'][0]
    vehicle.update(previousPrice=49990.0, priceUpdateDate='2026-09-02T15:02:19.188Z')
    vehicle['price']['total'] = 49590.0
    data['account'] = {'email': 'private@example.com'}
    result = collect_search(filters={}, zip_code='08542', destination=tmp_path/'query',
        post=Mock(return_value=response(data)))
    assert result['query_complete']
    entry = result['pages'][0]
    evidence = entry['response_evidence']
    assert evidence['kind'] == 'selected_source' and evidence['redacted_values'] == 0
    selected = verify_response_evidence(evidence)
    assert 'account' not in selected
    projection, rows = replay_response(evidence, packet(data)['request'],
        observed_at=entry['response_received_at_utc'])
    retained = projection['vehicles'][0]
    assert retained['previousPrice'] == 49990.0
    assert retained['priceUpdateDate'] == '2026-09-02T15:02:19.188Z'
    assert rows.asking_price_usd.iloc[0] == 49590.0
    assert 'previousPrice' not in projection['vehicles'][1]
    assert 'priceUpdateDate' not in projection['vehicles'][1]
    baseline = copy.deepcopy(data)
    baseline['inventory']['vehicles'][0].pop('previousPrice')
    baseline['inventory']['vehicles'][0].pop('priceUpdateDate')
    # Only retained optional source fields change; normalized inventory/SQL columns do not.
    pd.testing.assert_frame_equal(parse_search_capture(packet(data)), parse_search_capture(packet(baseline)))


def test_absent_optional_fields_preserve_existing_projection_shape(response_data):
    from vehicle_tracker.search import FIELDS
    projection = packet(response_data)
    assert set(projection['vehicles'][0]) == {*FIELDS, 'price'}
    assert public_source(response_data) == response_data


@pytest.mark.parametrize('value', ['private@example.com', '2026-09-02T15:02:19.188Z SECRET',
                                  '2026-09-02', '49590', '2026-99-99T99:99:99Z',
                                  '2026-02-29T15:02:19Z', '2026-09-02T24:00:00Z'])
def test_price_timestamp_does_not_admit_unobserved_text(response_data, value):
    response_data['inventory']['vehicles'][0]['priceUpdateDate'] = value
    changes = []
    selected = public_source(response_data, changes=changes)
    assert selected['inventory']['vehicles'][0]['priceUpdateDate'] == '[redacted string]'
    assert changes.count('redacted_value') == 1


@pytest.mark.parametrize('value', [123, False, [], {'date': '2026-09-02T15:02:19Z'}])
def test_wrong_type_price_timestamp_is_redacted(response_data, value):
    response_data['inventory']['vehicles'][0]['priceUpdateDate'] = value
    changes = []
    selected = public_source(response_data, changes=changes)
    assert selected['inventory']['vehicles'][0]['priceUpdateDate'] is None
    assert changes.count('redacted_value') == 1


@pytest.mark.parametrize('value', [None, '2024-02-29T15:02:19Z', '2026-09-02T15:02:19.188Z'])
def test_null_and_valid_calendar_price_timestamp_are_preserved(response_data, value):
    response_data['inventory']['vehicles'][0]['priceUpdateDate'] = value
    changes = []
    selected = public_source(response_data, changes=changes)
    assert selected['inventory']['vehicles'][0]['priceUpdateDate'] == value
    assert not changes


def test_timestamp_exception_does_not_broaden_other_string_fields(response_data):
    response_data['inventory']['vehicles'][0]['previousPrice'] = '2026-09-02T15:02:19.188Z'
    changes = []
    selected = public_source(response_data, changes=changes)
    assert selected['inventory']['vehicles'][0]['previousPrice'] == '[redacted string]'
    assert changes.count('redacted_value') == 1
