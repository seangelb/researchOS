"""The collection notebook must require both switches and reject implicit history."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

NOTEBOOK = Path(__file__).resolve().parents[1] / "notebooks/20_run_all_collectors.ipynb"


def execute_cell(scope, index):
    cell = json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"][index]
    exec(compile("".join(cell["source"]), f"notebook20:cell{index}", "exec"), scope)


def settings():
    scope = {}
    for index in [1, 2]:
        execute_cell(scope, index)
    return scope


@pytest.mark.parametrize("downloads,writes", [(False, False), (True, False), (False, True)])
def test_collection_requires_both_switches(downloads, writes):
    scope = settings()
    assert scope["run_downloads"] is False and scope["allow_database_writes"] is False
    assert scope["force_reparse"] is False
    recent = Mock(side_effect=AssertionError("Disabled collection must not run"))
    history = Mock(side_effect=AssertionError("Disabled history must not run"))
    scope.update(run_downloads=downloads, allow_database_writes=writes,
                 collect_recent=recent, run_all_collectors=history)
    execute_cell(scope, 4)
    execute_cell(scope, 6)
    assert not recent.called and not history.called


@pytest.mark.parametrize("sources", [None, [("NJ", "online_casino")]])
def test_recent_plan_never_falls_back_to_full_history(sources):
    scope = settings()
    scope["selected_sources"] = sources
    with pytest.raises(ValueError, match="Recent mode supports only"):
        execute_cell(scope, 4)


def test_history_must_be_explicit():
    scope = settings()
    scope.update(collection_mode="history", selected_sources=[("NJ", "online_casino")])
    execute_cell(scope, 4)
    assert scope["plan"]["mode"].tolist() == ["history"]
