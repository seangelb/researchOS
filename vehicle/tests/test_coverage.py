"""Synthetic edge cases modeled on the September 7 Chrome experiment."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from vehicle_tracker.carvana import parse_capture
def run_coverage(*args, **kwargs):
    from vehicle_tracker.coverage import run_coverage as implementation
    return implementation(*args, **kwargs)


def compare_runs(*args, **kwargs):
    from vehicle_tracker.coverage import compare_runs as implementation
    return implementation(*args, **kwargs)


def partition_coverage(*args, **kwargs):
    from vehicle_tracker.coverage import partition_coverage as implementation
    return implementation(*args, **kwargs)


@pytest.fixture
def pages():
    source = Path(__file__).parent / 'fixtures/carvana_browser_sample_20260907.json'
    capture = json.loads(source.read_text())
    capture.update(sample_only=False, visible_listing_count=3, reported_total_text='3 cars',
                   results_container_present=True, requested_zip='08542', zip_code='08542',
                   has_next_page=False, page_number=1, sort='Recommended')
    capture['cards'] = [{'url': r['offers']['url'], 'text': 'Used'} for r in capture['records']]
    return [capture]


def test_duplicate_visible_cards_are_not_silently_collapsed(pages):
    pages[0]['cards'].append(pages[0]['cards'][0])
    with pytest.raises(ValueError, match='Duplicate visible'):
        parse_capture(pages[0])


def test_complete_bounded_query_is_distinct_from_national_coverage(pages):
    result = run_coverage(pages)
    assert result['query_complete'] and result['unique_listings'] == 3
    assert result['complete_query_count'] == 3
    assert not result['national_coverage_verified']


@pytest.mark.parametrize('problem', ['duplicate', 'missing_page', 'truncated', 'wrong_zip',
    'changing_total', 'changing_sort', 'changing_filter', 'sample', 'missing_container', 'missing_vin', 'empty'])
def test_incomplete_runs_never_become_complete_counts(pages, problem):
    if problem == 'duplicate':
        pages[0]['reported_total_text'] = '6 cars'
        pages[0]['has_next_page'] = True
        pages.append(copy.deepcopy(pages[0]))
        pages[1].update(page_number=2, has_next_page=False)
    elif problem == 'missing_page': pages[0]['page_number'] = 2
    elif problem == 'truncated': pages[0]['has_next_page'] = True
    elif problem == 'wrong_zip': pages[0]['zip_code'] = '90210'
    elif problem in {'changing_total', 'changing_sort', 'changing_filter'}:
        pages.append(copy.deepcopy(pages[0]))
        pages[1].update(page_number=2, has_next_page=False)
        if problem == 'changing_total': pages[1]['reported_total_text'] = '4 cars'
        if problem == 'changing_sort': pages[1]['sort'] = 'Price'
        if problem == 'changing_filter': pages[1]['page_url'] += '/different'
    elif problem == 'sample': pages[0]['sample_only'] = True
    elif problem == 'missing_container': pages[0]['results_container_present'] = False
    elif problem == 'missing_vin': pages[0]['records'][0]['vehicleIdentificationNumber'] = None
    elif problem == 'empty': pages[0]['records'] = []
    result = run_coverage(pages)
    assert not result['query_complete']
    assert result['complete_query_count'] is None and result['reason']


def test_absent_next_button_only_supports_tied_single_page(pages):
    pages[0]['has_next_page'] = None
    assert run_coverage(pages)['query_complete']
    pages[0]['reported_total_text'] = '4 cars'
    assert not run_coverage(pages)['query_complete']


def test_query_partition_union_and_missing_manifest_entry(pages):
    parent = pages
    child = copy.deepcopy(pages)
    result = partition_coverage(parent, {'one': child}, expected_partitions=['one'])
    assert result['matches_parent'] and result['union_listings'] == 3
    assert not result['national_coverage_verified']
    assert not partition_coverage(parent, {'one': child}, expected_partitions=['one', 'two'])['matches_parent']


def test_reappearance_and_incomplete_comparisons(pages):
    earlier = copy.deepcopy(pages)
    current = copy.deepcopy(pages)
    current[0]['captured_at_utc'] = '2026-09-09T01:44:11Z'
    missing = earlier[0]['records'].pop()
    earlier[0]['cards'].pop()
    earlier[0].update(visible_listing_count=2, reported_total_text='2 cars')
    result = compare_runs(earlier, current, seen_before={missing['offers']['url'].split('/')[-1]})
    assert result.observation_change.eq('reappearing').sum() == 1
    assert not any('sold' in c for c in result.columns)
    earlier[0]['has_next_page'] = True
    with pytest.raises(ValueError, match='Incomplete'):
        compare_runs(earlier, current)


def test_comparison_rejects_different_query_or_zip(pages):
    after = copy.deepcopy(pages)
    after[0].update(zip_code='90210', requested_zip='90210')
    with pytest.raises(ValueError, match='context'):
        compare_runs(pages, after)


def test_same_time_comparisons_are_not_changes(pages):
    with pytest.raises(ValueError, match='intervals'):
        compare_runs(pages, pages)


def test_malformed_partition_has_a_blocked_diagnostic(pages):
    broken = copy.deepcopy(pages)
    broken[0]['records'] = []
    assert not partition_coverage(pages, {'one': broken}, expected_partitions=['one'])['matches_parent']


def test_partition_listing_vin_conflict_is_visible(pages):
    child = copy.deepcopy(pages)
    child[0]['records'][0]['vehicleIdentificationNumber'] = '5YJ3E1EA2TF142040'
    result = partition_coverage(pages, {'one': child}, expected_partitions=['one'])
    assert not result['matches_parent'] and result['identity_conflicts']
