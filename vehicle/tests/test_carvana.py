import copy
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pandas as pd
import pytest

from vehicle_tracker.carvana import parse_capture
from vehicle_tracker.collect import collect_pages
from vehicle_tracker.storage import read_snapshots, retain_capture, store_capture

FIXTURE = Path(__file__).parent / 'fixtures/carvana_browser_sample_20260907.json'


@pytest.fixture
def capture():
    return json.loads(FIXTURE.read_text())


def test_real_retained_schema_fields(capture):
    rows = parse_capture(capture)
    assert rows.listing_id.tolist() == ['4474057', '4711830', '4482313']
    assert rows.asking_price_usd.tolist() == [14590, 36590, 15590]
    assert rows.mileage_miles.tolist() == [80683, 51150, 78416]
    assert rows.availability_native.eq('http://schema.org/InStock').all()
    assert 'sales' not in rows.columns


@pytest.mark.parametrize('value', [None, 0, 14590.5])
def test_missing_zero_fractional_price(capture, value):
    capture['records'][0]['offers']['price'] = value
    actual = parse_capture(capture).iloc[0].asking_price_usd
    assert pd.isna(actual) if value is None else actual == value


@pytest.mark.parametrize('problem', ['duplicate', 'empty', 'currency', 'vin', 'negative_price', 'bad_miles', 'naive_clock', 'card_mismatch'])
def test_invalid_capture_rejected(capture, problem):
    if problem == 'duplicate': capture['records'].append(capture['records'][0])
    elif problem == 'empty': capture['records'] = []
    elif problem == 'currency': capture['records'][0]['offers']['priceCurrency'] = 'EUR'
    elif problem == 'vin': capture['records'][0]['vehicleIdentificationNumber'] = 'bad'
    elif problem == 'negative_price': capture['records'][0]['offers']['price'] = -1
    elif problem == 'bad_miles': capture['records'][0]['mileageFromOdometer'] = '81k'
    elif problem == 'naive_clock': capture['captured_at_utc'] = '2026-09-07'
    else: capture['sample_only'] = False
    with pytest.raises(ValueError): parse_capture(capture)


def test_retention_and_snapshot_versions_are_immutable(capture, tmp_path):
    first = retain_capture(capture, tmp_path / 'raw')
    assert retain_capture(capture, tmp_path / 'raw') == first
    second_capture = copy.deepcopy(capture)
    second_capture['records'][0]['offers']['price'] = 14000
    second = retain_capture(second_capture, tmp_path / 'raw')
    assert first != second
    database = tmp_path / 'vehicle.sqlite'
    store_capture(database, run_id='first', page_number=1, raw_file=first)
    store_capture(database, run_id='second', page_number=1, raw_file=second)
    before = database.read_bytes()
    attempts, rows = read_snapshots(database)
    assert len(attempts) == 2 and len(rows) == 6
    assert attempts.coverage.eq('unverified').all()
    assert database.read_bytes() == before
    with pytest.raises(sqlite3.IntegrityError):
        store_capture(database, run_id='first', page_number=1, raw_file=second)
    assert database.read_bytes() == before


def test_unrelated_database_is_not_modified(capture, tmp_path):
    database = tmp_path / 'gaming.sqlite'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE gaming_results (x int)')
    before = database.read_bytes()
    with pytest.raises(ValueError, match='unrelated'):
        store_capture(database, run_id='r', page_number=1, raw_file=retain_capture(capture, tmp_path / 'raw'))
    assert database.read_bytes() == before


@pytest.mark.parametrize('problem', ['http403', 'parser', 'duplicate_page', 'page_limit', 'end', 'wrong_zip'])
def test_bounded_collector_retains_failures_and_stops(capture, tmp_path, monkeypatch, problem):
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', lambda _: None)
    page = Mock()
    page.goto.return_value.status = 403 if problem == 'http403' else 200
    page.reload.return_value.status = 200
    capture['zip_code'] = '90210' if problem == 'wrong_zip' else '08542'
    capture['has_next_page'] = problem != 'end'
    if problem == 'parser': capture['records'] = []
    page.evaluate.return_value = capture
    limit = 1 if problem == 'page_limit' else 2
    result = collect_pages(page, source_url='https://www.carvana.com/cars',
        database=tmp_path / 'vehicle.sqlite', raw_directory=tmp_path / 'raw', max_pages=limit)
    assert result.coverage.eq('unverified').all()
    assert result.status.iloc[-1] == ('parsed' if problem in {'end', 'page_limit'} else 'failed')
    attempts, rows = read_snapshots(tmp_path / 'vehicle.sqlite')
    assert len(attempts) == len(result)
    assert len(rows) == (0 if problem in {'http403', 'parser', 'wrong_zip'} else 3)
    assert len(result) <= limit
    if problem == 'http403': page.evaluate.assert_not_called()
