"""Boundary contracts for the readable source checks and follow-up identity flow."""
import json

import pandas as pd
import pytest

from test_vehicle_history_events import cohort, page
from vehicle_tracker.history import read_query_evidence
from vehicle_tracker.vehicle_history import followup_identities


@pytest.mark.parametrize('claimed_complete', [False, True])
def test_no_pages_remains_an_unobserved_attempt_never_complete_inventory(tmp_path, claimed_complete):
    report = tmp_path / 'run_report.json'
    report.write_text(json.dumps(dict(run_id='empty-attempt', pages=[], unique_listings=0,
        query_complete=claimed_complete, reported_total=0, reason='No pages observed',
        started_utc='2026-09-12T10:00:00Z', ended_utc='2026-09-12T10:01:00Z')),
        encoding='utf-8')
    before = report.read_bytes()
    if claimed_complete:
        with pytest.raises(ValueError, match='Claimed complete query does not reconcile'):
            read_query_evidence(report)
    else:
        run, captures, observations = read_query_evidence(report)
        assert run['query_complete'] == 0 and run['stored_rows'] == 0
        assert run['observation_start'] is None and run['observation_end'] is None
        assert captures.empty and observations.empty
    assert report.read_bytes() == before


def test_later_availability_cannot_resolve_simultaneous_different_listing_identities():
    records = pd.DataFrame([
        page('2002', 11, available_at='2026-09-11T11:00:00Z'),
        page('3003', 11, available_at='2026-09-11T12:00:00Z'),
    ])
    with pytest.raises(ValueError, match='Simultaneous verified listings'):
        followup_identities(cohort(), records, pd.DataFrame(), as_of='2026-09-12T00:00:00Z')


def test_same_listing_same_observation_uses_latest_available_source_without_changing_original():
    records = pd.DataFrame([
        page('2002', 11, available_at='2026-09-11T11:00:00Z', source='synthetic://first'),
        page('2002', 11, available_at='2026-09-11T12:00:00Z', source='synthetic://second'),
    ])
    before = records.copy(deep=True)
    selected = followup_identities(cohort(), records, pd.DataFrame(), as_of='2026-09-12T00:00:00Z').iloc[0]
    assert selected.original_listing_id == '1001'
    assert selected.followup_listing_id == '2002'
    assert selected.followup_source == 'synthetic://second'
    assert selected.followup_observed_at == pd.Timestamp('2026-09-11T10:00:00Z')
    pd.testing.assert_frame_equal(records, before)
