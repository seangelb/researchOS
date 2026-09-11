"""Temporary offline crash checkpoints mirror the retained September 10 shape."""
import copy
from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from test_daily_cycles import clock, options, reply
from test_daily_notebook import checker, daily_code
from test_search import response_data
from vehicle_tracker import cycles, daily, search
from vehicle_tracker.history import read_query_evidence


def save(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


@pytest.fixture
def interrupted(tmp_path, response_data, clock, monkeypatch):
    queries = [dict(query_id=f'year_{year}', zip_code='08542', filters={})
               for year in [2024, 2023, 2022, 2021, 2020, 2025, 2026]]
    replies = []
    for offset, count, pages in [(100, 49, 3), (200, 72, 2)]:
        for page in range(1, pages + 1):
            data = copy.deepcopy(response_data)
            indices = range(offset + (page - 1) * 24, offset + min(page * 24, count))
            data['inventory']['vehicles'] = [dict(data['inventory']['vehicles'][0],
                vehicleId=index, vin=f'{index:017}') for index in indices]
            data['inventory']['pagination'].update(currentPage=page, totalMatchedInventory=count, totalMatchedPages=3)
            replies.append(reply(data))
    ticks = 0
    class AdvancingClock:
        @staticmethod
        def now(tz=None):
            nonlocal ticks
            ticks += 1
            return clock + timedelta(milliseconds=ticks)
    monkeypatch.setattr(search, 'datetime', AdvancingClock)
    original = search.write_json_atomic
    def interrupt_after_checkpoint(path, value):
        original(path, value)
        if (path.name == 'run_report.json' and path.parent.name == 'year_2023'
                and len(value['pages']) == 3 and value['pages'][-1]['outcome_kind'] == 'unattempted'):
            raise KeyboardInterrupt('injected process interruption before budget reservation')
    monkeypatch.setattr(search, 'write_json_atomic', interrupt_after_checkpoint)
    post = Mock(side_effect=replies)
    with pytest.raises(KeyboardInterrupt):
        cycles.collect_cycle(queries, **options(tmp_path), post=post)
    assert post.call_count == 5
    monkeypatch.setattr(search, 'write_json_atomic', original)
    settings = dict(capture_root=tmp_path, database=tmp_path/'history.sqlite', register=tmp_path/'cycles.json',
                    queries=queries, timezone='UTC', max_requests=20, max_seconds=3600)
    return tmp_path/'cycle/cycle.json', settings


def edit_checkpoint(path, edit):
    report_path = path.parent/'attempt_0001/year_2023/run_report.json'
    report = json.loads(report_path.read_text())
    edit(report)
    save(report_path, report)
    journal_path = report_path.parent/'attempts/0003.json'
    journal = json.loads(journal_path.read_text())
    save(journal_path, dict(run_id=journal['run_id'], request=journal['request'], **report['pages'][-1]))
    return report_path


def test_real_interrupted_shape_is_read_only_and_not_importable(interrupted, clock, monkeypatch):
    path, settings = interrupted
    before = snapshot(path.parent.parent)
    with checker.offline_guards():
        summary, coverage = cycles.cycle_diagnostic(path, now=clock + timedelta(seconds=1))
        recovery = daily.recovery_candidates(settings, now=clock + timedelta(seconds=1))[0]
    assert coverage.query_id.tolist() == [query['query_id'] for query in settings['queries']]
    assert coverage.status.tolist() == ['complete', 'partial; unattempted checkpoint'] + ['not started'] * 5
    assert coverage.verified_retained_rows.tolist() == [49, 48, 0, 0, 0, 0, 0]
    assert coverage.parsed_pages.tolist() == [3, 2, 0, 0, 0, 0, 0]
    assert summary['durable_requests'] == summary['child_requests'] == summary['page_reservations'] == 5
    assert summary['requests_reconciled'] and not summary['request_outcome_uncertain']
    parent = summary['parent_summaries'][0]
    assert parent['parent_requests'] == 3 and parent['parent_unique_listings'] == 49
    assert parent['child_requests'] == 5 and parent['verified_retained_unique_listings'] == 97
    assert parent['summary_differs_from_children']
    assert not recovery['import_allowed'] and not recovery['live_resume_allowed']
    assert 'checkpoint' in recovery['reason'] and 'separate reviewed recovery change' in recovery['recovery_limit']
    with pytest.raises(ValueError, match='Incomplete query checkpoint'):
        read_query_evidence(path.parent/'attempt_0001/year_2023/run_report.json')
    assert snapshot(path.parent.parent) == before


@pytest.mark.parametrize('case', ['reservation_before_journal', 'unexplained_budget', 'reserved', 'started', 'journal_ahead'])
def test_reservation_uncertainty_never_becomes_unattempted(interrupted, clock, case):
    path, settings = interrupted
    state = json.loads(path.read_text())
    state['budget'].update(requests=6, pending_request=case != 'unexplained_budget')
    save(path, state)
    if case in {'reserved', 'started'}:
        def reserve(report):
            report['requests'] = 3
            report['pages'][-1].update(outcome_kind='request_reserved', request_reserved_at_utc=clock.isoformat())
            if case == 'started':
                report['pages'][-1]['request_started_at_utc'] = clock.isoformat()
        edit_checkpoint(path, reserve)
    if case == 'journal_ahead':
        journal = path.parent/'attempt_0001/year_2023/attempts/0003.json'
        content = json.loads(journal.read_text())
        content.update(outcome_kind='request_reserved', request_reserved_at_utc=clock.isoformat())
        save(journal, content)
    before = snapshot(path.parent.parent)
    summary, coverage = cycles.cycle_diagnostic(path, now=clock + timedelta(seconds=1))
    result = daily.recovery_candidates(settings, now=clock + timedelta(seconds=1))[0]
    assert summary['request_outcome_uncertain']
    assert coverage.status.iloc[1] != 'partial; unattempted checkpoint'
    if case != 'journal_ahead':
        assert coverage.unattempted_pages.iloc[1] == 0 and coverage.uncertain_pages.iloc[1] == 1
    assert not result['import_allowed'] and not result['live_resume_allowed']
    assert snapshot(path.parent.parent) == before


@pytest.mark.parametrize('case', ['success_source', 'response_source', 'source_hash', 'journal_context', 'midstream_checkpoint'])
def test_missing_or_inconsistent_evidence_stays_unverified(interrupted, clock, case):
    path, settings = interrupted
    report_path = path.parent/'attempt_0001/year_2023/run_report.json'
    report = json.loads(report_path.read_text())
    if case == 'success_source':
        report['pages'][0].pop('retained_source')
        save(report_path, report)
    elif case == 'response_source':
        Path(report['pages'][0]['response_evidence']['source_path']).unlink()
    elif case == 'source_hash':
        report['pages'][0]['source_sha256'] = 'bad-hash'
        save(report_path, report)
    elif case == 'journal_context':
        journal = report_path.parent/'attempts/0003.json'
        content = json.loads(journal.read_text())
        content['request']['zip5'] = '90210'
        save(journal, content)
    else:
        report['pages'].append(dict(report['pages'][-1], page=4))
        save(report_path, report)
    before = snapshot(path.parent.parent)
    summary, coverage = cycles.cycle_diagnostic(path, now=clock + timedelta(seconds=1))
    result = daily.recovery_candidates(settings, now=clock + timedelta(seconds=1))[0]
    assert summary['validation_errors']
    assert coverage.status.iloc[0] == 'complete' and coverage.verified_retained_rows.iloc[0] == 49
    assert coverage.status.iloc[1] == 'invalid evidence' and pd.isna(coverage.verified_retained_rows.iloc[1])
    assert len(coverage) == 7 and not summary['coverage_complete']
    assert not result['import_allowed'] and not result['live_resume_allowed']
    assert snapshot(path.parent.parent) == before


def test_missing_child_report_is_unknown_not_unstarted(interrupted, clock):
    path, _ = interrupted
    (path.parent/'attempt_0001/year_2022').mkdir()
    summary, coverage = cycles.cycle_diagnostic(path, now=clock)
    assert coverage.status.iloc[2] == 'invalid evidence'
    assert pd.isna(coverage.verified_retained_rows.iloc[2]) and summary['validation_errors']


def test_expired_window_and_notebook_keep_partial_rows_out_of_inventory(interrupted, clock, monkeypatch):
    path, settings = interrupted
    expired = clock + timedelta(days=1)
    monkeypatch.setattr(daily, 'utcnow', lambda: expired)
    days = pd.DataFrame([dict(cycle_date='2026-09-07', coverage_complete=True)])
    observations = pd.DataFrame([dict(vin='REGISTERED-ONLY')])
    scope = dict(pd=pd, daily_cycles=days.copy(), daily_observations=observations.copy(), tracking=settings,
                 display=lambda *args: None)
    before = snapshot(path.parent.parent)
    with checker.offline_guards():
        exec(compile(daily_code()['interrupted-health-data'], 'interrupted-health-data', 'exec'), scope)
    assert scope['last_complete_registered_day'] == '2026-09-07'
    assert scope['collection_health'].record_type.tolist() == ['registered baseline at AS_OF', 'unregistered retained cycle']
    assert not scope['collection_health'].window_open.any()
    assert not scope['collection_health'].import_allowed.any() and not scope['collection_health'].live_resume_allowed.any()
    assert scope['unregistered_query_health'].verified_retained_rows.sum() == 97
    pd.testing.assert_frame_equal(scope['daily_cycles'], days)
    pd.testing.assert_frame_equal(scope['daily_observations'], observations)
    assert snapshot(path.parent.parent) == before
