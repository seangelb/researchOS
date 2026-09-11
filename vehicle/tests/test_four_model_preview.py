"""The real frozen expansion uses the daily CLI without transport or output writes."""
import builtins
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import Mock

from test_daily_notebook import checker
from vehicle_tracker.daily import tracking_settings

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config/carvana_four_model_tracking.json'


def test_frozen_expansion_queries_limits_and_isolated_destinations():
    expanded = tracking_settings(CONFIG)
    operating = tracking_settings(ROOT / 'config/carvana_daily_tracking.json')
    proposal = ROOT / 'data/experiments/collection_method_review/20260910T020000Z/proposed_broader_manifest.json'
    frozen = json.loads(proposal.read_text(encoding='utf-8'))
    assert expanded['plan'] == proposal
    assert expanded['queries'] == frozen['queries'] and len(expanded['queries']) == 16
    assert expanded['max_requests'] == frozen['collection']['maximum_explicit_requests'] == 120
    assert expanded['max_seconds'] == frozen['collection']['maximum_seconds'] == 900
    assert frozen['collection']['minimum_search_spacing_seconds'] == 3
    assert frozen['collection']['page_size'] == 24 and frozen['collection']['sort_by'] == 'MostPopular'
    fields = ['capture_root', 'database', 'register', 'exports', 'checks', 'reviews']
    assert len({expanded[key] for key in fields}) == len(fields)
    for key in fields:
        assert expanded[key] != operating[key]
        assert not expanded[key].is_relative_to(operating['capture_root'])
        assert not expanded[key].is_relative_to(operating['database'].parent)
    expected = {'Tesla': ('Model 3', range(2020, 2027)), 'Chevrolet': ('Equinox', range(2022, 2025)),
        'Ford': ('Escape', range(2022, 2025)), 'Toyota': ('Corolla', range(2022, 2025))}
    for make, (model, years) in expected.items():
        queries = [q for q in expanded['queries'] if q['filters']['makes'][0]['name'] == make]
        assert sorted(q['filters']['year']['min'] for q in queries) == list(years)
        assert all(q['filters']['year']['min'] == q['filters']['year']['max'] for q in queries)
        assert all(q['filters']['makes'][0]['parentModels'] == [{'name': model}] for q in queries)
    assert all(q['zip_code'] == '08542' and q['location_filter'] is False for q in expanded['queries'])


def test_actual_expansion_cli_preview_has_no_requests_or_files(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location('four_model_daily_cli', ROOT / 'scripts/run_carvana_daily.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    settings = tracking_settings(CONFIG)
    fields = ['capture_root', 'database', 'register', 'exports', 'checks', 'reviews']
    before = {p for key in fields for p in [settings[key], *settings[key].rglob('*')] if p.exists()}
    def readonly(original):
        def guarded(path, mode='r', *args, **kwargs):
            assert not any(flag in mode for flag in 'wax+'), 'Preview attempted a file write'
            return original(path, mode, *args, **kwargs)
        return guarded
    deny = Mock(side_effect=AssertionError('Preview attempted a mutation'))
    with monkeypatch.context() as guarded, checker.offline_guards():
        guarded.setattr(builtins, 'open', readonly(builtins.open))
        guarded.setattr(io, 'open', readonly(io.open))
        for method in ['mkdir', 'write_text', 'write_bytes', 'replace', 'unlink']:
            guarded.setattr(Path, method, deny)
        assert cli.main(['--config', str(CONFIG)]) == 0
    assert not deny.called
    assert before == {p for key in fields for p in [settings[key], *settings[key].rglob('*')] if p.exists()}
    output = capsys.readouterr().out
    state = json.loads(output[output.index('{'):output.rindex('}') + 1])
    assert state['queries'] == settings['queries']
    assert state['max_requests'] == 120 and state['max_seconds'] == 900
    assert state['destinations'] == {key: str(settings[key]) for key in fields}
