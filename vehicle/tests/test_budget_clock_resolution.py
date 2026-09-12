"""Coarse Windows elapsed ticks must not shorten the minimum start interval."""
from types import SimpleNamespace

import pytest

from vehicle_tracker.collect import CollectionStopped, NavigationBudget


@pytest.mark.parametrize('elapsed,expected_wait', [
    (2.9, 0.115625), (3.0, 0.015625), (3.01, 0.005625), (3.02, 0.0)])
def test_wait_includes_possible_elapsed_overstatement(monkeypatch, elapsed, expected_wait):
    clock = [elapsed]
    waits = []
    monkeypatch.setattr('vehicle_tracker.collect.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('vehicle_tracker.collect.time.get_clock_info',
                        lambda name: SimpleNamespace(resolution=0.015625))
    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep', sleep)
    budget = NavigationBudget(max_requests=2, max_seconds=10)
    budget.started, budget.last_request = 0.0, 0.0
    budget.before_navigation()
    assert waits == pytest.approx([expected_wait])
    # Worst case: coarse elapsed was one tick ahead of actual elapsed.
    assert elapsed + sum(waits) - 0.015625 >= 3.0 - 1e-12
    assert budget.requests == 1


def test_resolution_wait_cannot_extend_live_deadline(monkeypatch):
    clock = [3.0]
    monkeypatch.setattr('vehicle_tracker.collect.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('vehicle_tracker.collect.time.get_clock_info',
                        lambda name: SimpleNamespace(resolution=0.015625))
    monkeypatch.setattr('vehicle_tracker.collect.time.sleep',
                        lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    budget = NavigationBudget(max_requests=2, max_seconds=3.01)
    budget.started, budget.last_request = 0.0, 0.0
    with pytest.raises(CollectionStopped, match='time/access budget'):
        budget.before_navigation()
    assert budget.requests == 0
