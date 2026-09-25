"""Offline attempt scheduling. No live requests."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from vehicle_tracker.catalog_schedule import daily_decision


def _config(tmp_path):
    source = Path(__file__).parents[1] / 'config' / 'carvana_full_inventory_years.json'
    config = json.loads(source.read_text(encoding='utf-8'))
    root = tmp_path / 'captures'
    config['capture_root'] = str(root)
    config['related_capture_roots'] = [str(tmp_path / 'peer')]
    config['reviewed_peer_failures'] = []
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    return path, root


def _outcome(folder, **fields):
    folder.mkdir(parents=True, exist_ok=True)
    payload = dict(kind='access_stop', ended_at='2026-09-22T16:00:00+00:00',
                   spacing_index=0, spacing_seconds=3, requests=1, http_status=403)
    payload.update(fields)
    (folder / 'attempt_outcome.json').write_text(json.dumps(payload), encoding='utf-8')


def test_decision_cools_down_then_starts_a_slower_second_attempt(tmp_path):
    path, root = _config(tmp_path)
    _outcome(root / '2026-09-23', cooldown_until='2026-09-23T17:00:00+00:00')
    cooling = daily_decision(path, now=datetime(2026, 9, 23, 16, 30, tzinfo=timezone.utc))
    assert cooling['action'] == 'cooling_until'
    assert cooling['cooldown_until'] == '2026-09-23T17:00:00+00:00'
    start = daily_decision(path, now=datetime(2026, 9, 23, 17, 5, tzinfo=timezone.utc))
    assert start['action'] == 'start' and start['attempt'] == 2
    assert start['spacing_seconds'] == 5
    assert start['destination'].endswith('2026-09-23_a02')


def test_three_attempts_exhaust_the_day(tmp_path):
    path, root = _config(tmp_path)
    day = '2026-09-23'
    _outcome(root / day, kind='degraded', ended_at='2026-09-23T14:00:00+00:00',
             cooldown_until='2026-09-23T14:20:00+00:00')
    _outcome(root / f'{day}_a02', kind='degraded', ended_at='2026-09-23T16:00:00+00:00',
             spacing_index=1, spacing_seconds=5, cooldown_until='2026-09-23T17:00:00+00:00')
    _outcome(root / f'{day}_a03', kind='access_stop', ended_at='2026-09-23T18:00:00+00:00',
             spacing_index=2, spacing_seconds=8, cooldown_until='2026-09-23T22:00:00+00:00')
    decision = daily_decision(path, now=datetime(2026, 9, 23, 23, 0, tzinfo=timezone.utc))
    assert decision['action'] == 'exhausted'


def test_prior_access_stop_starts_the_next_day_slower_and_a_clean_day_resets(tmp_path):
    path, root = _config(tmp_path)
    _outcome(root / '2026-09-22', kind='access_stop', ended_at='2026-09-22T16:00:00+00:00',
             spacing_index=0, cooldown_until='2026-09-22T16:45:00+00:00')
    slowed = daily_decision(path, now=datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc))
    assert slowed['action'] == 'start' and slowed['attempt'] == 1
    assert slowed['spacing_seconds'] == 5 and slowed['spacing_index'] == 1
    _outcome(root / '2026-09-23', kind='complete', ended_at='2026-09-23T18:00:00+00:00',
             spacing_index=1, spacing_seconds=5, cooldown_until=None)
    done = daily_decision(path, now=datetime(2026, 9, 23, 19, 0, tzinfo=timezone.utc))
    assert done['action'] == 'done'
    reset = daily_decision(path, now=datetime(2026, 9, 24, 13, 0, tzinfo=timezone.utc))
    assert reset['action'] == 'start' and reset['spacing_seconds'] == 3 and reset['attempt'] == 1


def test_finished_incomplete_attempt_does_not_start_another(tmp_path):
    path, root = _config(tmp_path)
    _outcome(root / '2026-09-23', kind='finished_incomplete', ended_at='2026-09-23T18:00:00+00:00',
             cooldown_until=None)
    decision = daily_decision(path, now=datetime(2026, 9, 23, 19, 0, tzinfo=timezone.utc))
    assert decision['action'] == 'done'


def test_legacy_terminal_report_counts_as_an_attempt(tmp_path):
    path, root = _config(tmp_path)
    folder = root / '2026-09-23'
    folder.mkdir(parents=True)
    (folder / 'catalog_report.json').write_text(json.dumps(dict(
        status='stopped', ended_at='2026-09-23T16:00:00+00:00', requests=3,
        failure_reason='http_access_failure',
        entries=[dict(outcome_kind='access_failure')])), encoding='utf-8')
    now = datetime(2026, 9, 23, 16, 10, tzinfo=timezone.utc)
    cooling = daily_decision(path, now=now)
    assert cooling['action'] == 'cooling_until'
    later = daily_decision(path, now=now + timedelta(minutes=50))
    assert later['action'] == 'start' and later['attempt'] == 2 and later['spacing_seconds'] == 5
