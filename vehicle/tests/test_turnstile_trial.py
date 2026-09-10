from unittest.mock import Mock

import pytest

from vehicle_tracker.turnstile_trial import trial_turnstile


def run(**kwargs):
    return trial_turnstile(trial_id='one', create_task=Mock(return_value='task'),
        get_result=kwargs.pop('get_result', Mock(return_value={'status': 'ready', 'token': 'private'})),
        verify_inventory=kwargs.pop('verify_inventory', Mock(return_value=True)),
        ledger=kwargs.pop('ledger', {}), max_tasks=kwargs.pop('max_tasks', 1),
        max_dollars=kwargs.pop('max_dollars', '.01'), reserved_task_cost='.01', **kwargs)


@pytest.mark.parametrize('status,expected', [('failed', 'solver_failed'), ('processing', 'timeout')])
def test_solver_failure_and_bounded_polling(monkeypatch, status, expected):
    monkeypatch.setattr('vehicle_tracker.turnstile_trial.time.sleep', lambda _: None)
    poll = Mock(return_value={'status': status, 'error': 'private'})
    result = run(get_result=poll, max_polls=2)
    assert result['status'] == expected and poll.call_count <= 2
    assert 'private' not in str(result)


def test_token_is_not_inventory_success():
    assert run(verify_inventory=Mock(return_value=False))['status'] == 'inventory_validation_failed'
    assert run()['inventory_usable']


def test_mock_spend_cap_and_duplicate_uncertain_submission():
    ledger = {}
    result = run(ledger=ledger, get_result=Mock(side_effect=RuntimeError('private-token')))
    assert result['status'] == 'trial_failed' and 'private' not in str(result)
    assert run(ledger=ledger)['status'] == 'duplicate_trial_blocked'
    assert run(max_dollars='.009')['status'] == 'spending_cap_blocked'
    assert run(max_tasks=0)['status'] == 'spending_cap_blocked'
    assert ledger == {'one': '0.01'}


def test_live_path_cannot_spend_or_read_a_key():
    create = Mock()
    with pytest.raises(ValueError, match='Paid path blocked'):
        trial_turnstile(trial_id='one', create_task=create, get_result=Mock(),
                        verify_inventory=Mock(), ledger={}, live=True)
    create.assert_not_called()
