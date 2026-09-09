"""Replay the retained public counterexamples without any live collection."""
import copy
import json
from pathlib import Path

import pytest

from vehicle_tracker.carvana import parse_capture
from vehicle_tracker.coverage import run_coverage, partition_coverage, compare_runs

EXPERIMENT = Path(__file__).parents[1] / 'data/experiments/20260907_approaches/evidence.json'


@pytest.fixture
def runs():
    if not EXPERIMENT.exists():
        pytest.skip('Local retained experiment absent; synthetic edge tests remain active')
    captures = json.loads(EXPERIMENT.read_text())['captures']
    return {name: [c for c in captures if c['run_id'] == name] for name in {c['run_id'] for c in captures}}


def test_actual_count_tie_still_misses_a_listing(runs):
    west = run_coverage(runs['west_parent'])
    assert west['parsed_rows'] == west['reported_total'] == 36
    assert west['unique_listings'] == 35 and not west['query_complete']
    east = run_coverage(runs['east_parent'])
    assert east['query_complete'] and east['unique_listings'] == 36


def test_actual_recommendations_must_be_excluded(runs):
    with pytest.raises(ValueError, match='visible'):
        parse_capture(runs['west_2026_unscoped'][0])
    assert len(parse_capture(runs['west_2026'][0])) == 9
    assert runs['west_2026'][0]['excluded_recommendation_count'] == 12


def test_actual_partition_union_and_repeat_prices(runs):
    result = partition_coverage(runs['east_parent'],
        {'2025': runs['west_2025'], '2026+': runs['west_2026']}, expected_partitions=['2025', '2026+'])
    assert result['matches_parent'] and result['union_listings'] == 36
    prices = compare_runs(runs['east_parent'], runs['east_repeat'])
    assert len(prices) == 36 and prices.asking_price_change_usd.eq(0).all()
    assert prices.observation_change.eq('observed_both').all()


def test_projection_malformed_and_missing_are_not_zero(runs):
    capture = copy.deepcopy(runs['west_2026'][0])
    capture['rows'][0]['asking_price_usd'] = None
    assert parse_capture(capture).asking_price_usd.isna().sum() == 1
    capture['rows'][0]['asking_price_usd'] = 0
    assert parse_capture(capture).asking_price_usd.iloc[0] == 0
    capture['rows'][0]['vin'] = 'changed-format'
    with pytest.raises(ValueError, match='VIN'):
        parse_capture(capture)
