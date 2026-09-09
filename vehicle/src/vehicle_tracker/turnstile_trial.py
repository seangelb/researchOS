"""Mock-first challenge trial. Paid transport is deliberately unavailable.

The observed Cloudflare page did not establish a usable Turnstile task payload.
2Captcha's documented createTask contract does not expose a per-task dollar cap.
Do not enable paid submission until both are resolved and the concrete trial approved.
"""
from decimal import Decimal
import time


def trial_turnstile(*, trial_id, create_task, get_result, verify_inventory, ledger,
                   max_tasks=0, max_dollars='0', reserved_task_cost='0', max_polls=3,
                   pause_seconds=5, timeout_seconds=30, live=False):
    """Exercise a bounded trial using injected mocks; never log token/error payloads.

    ledger is the caller-owned mapping of attempted trial IDs to reserved costs.
    Reservation survives uncertain submission/timeout; repeating an ID never pays twice.
    Mock costs are reservations, not actual provider charges. No API key is read here.
    """
    if live:
        raise ValueError('Paid path blocked: verified task parameters, enforceable price cap and approval required')
    cap, cost = Decimal(str(max_dollars)), Decimal(str(reserved_task_cost))
    if not cap.is_finite() or not cost.is_finite() or cap < 0 or cost <= 0:
        raise ValueError('Require finite positive mock task cost and a nonnegative cap')
    if not 1 <= max_polls <= 20 or pause_seconds < 1 or not 0 < timeout_seconds <= 120:
        raise ValueError('Use bounded polling and timeout')
    if trial_id in ledger:
        return {'status': 'duplicate_trial_blocked', 'inventory_usable': False}
    if len(ledger) >= max_tasks or sum(Decimal(str(v)) for v in ledger.values()) + cost > cap:
        return {'status': 'spending_cap_blocked', 'inventory_usable': False}
    ledger[trial_id] = str(cost)  # Reserve before the submission, including uncertain errors.
    started = time.monotonic()
    status = 'timeout'
    try:
        task_id = create_task(timeout_seconds=timeout_seconds)
        for _ in range(max_polls):
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                break
            response = get_result(task_id, timeout_seconds=remaining)
            if response.get('status') == 'failed':
                status = 'solver_failed'
                break
            if response.get('status') == 'ready':
                remaining = timeout_seconds - (time.monotonic() - started)
                if remaining > 0:
                    usable = verify_inventory(response.get('token'), timeout_seconds=remaining)
                    status = 'usable_inventory' if usable is True else 'inventory_validation_failed'
                break
            time.sleep(min(pause_seconds, max(0, timeout_seconds - (time.monotonic() - started))))
    except Exception:
        status = 'trial_failed'  # Never retain provider exceptions, task IDs or solver tokens.
    return {'status': status, 'inventory_usable': status == 'usable_inventory', 'mock_reserved_cost': str(cost)}
