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
    # Unit scenarios must not depend on the user's ignored local database.
    scope["base_database_path"] = None
    return scope


@pytest.mark.parametrize("downloads,writes", [(False, False), (True, False), (False, True)])
def test_collection_requires_both_switches(downloads, writes):
    scope = settings()
    assert scope["run_downloads"] is False and scope["allow_database_writes"] is False
    refresh = Mock(side_effect=AssertionError("Disabled collection must not run"))
    scope.update(run_downloads=downloads, allow_database_writes=writes,
                 run_refresh=refresh)
    execute_cell(scope, 4)
    execute_cell(scope, 6)
    assert not refresh.called


@pytest.mark.parametrize("sources", [None, [("NJ", "online_casino")]])
def test_recent_plan_never_falls_back_to_full_history(sources):
    scope = settings()
    scope["selected_sources"] = sources
    with pytest.raises(ValueError, match="Recent mode supports"):
        execute_cell(scope, 4)


def test_history_must_be_explicit():
    scope = settings()
    scope.update(collection_mode="history", selected_sources=[("NJ", "online_casino")])
    execute_cell(scope, 4)
    assert scope["plan"]["mode"].tolist() == ["history"]


def test_live_notebook_delegates_validation_and_backup(tmp_path):
    scope = settings()
    scope.update(run_downloads=True, allow_database_writes=True)
    execute_cell(scope, 4)
    scope["run_directory"] = tmp_path / "snapshot"
    def completed(**kwargs):
        assert kwargs["live"] is True
        assert kwargs["backup_path"] == scope["backup_path"]
        assert kwargs["run_dir"] == tmp_path / "snapshot"
        kwargs["run_dir"].mkdir()
        (kwargs["run_dir"] / "collection_summary.csv").write_text("state_code,vertical,run_status,coverage_status\nMA,online_sports_betting,completed,recent_only\n")
        return {"status": "complete", "backup_receipt": {"restore_verified": True}}
    scope["run_refresh"] = Mock(side_effect=completed)
    # The preflight requires a new staging destination, so use the already checked
    # options but redirect this isolated execution's fake output to tmp_path.
    scope["refresh_options"]["run_dir"] = tmp_path / "snapshot"
    execute_cell(scope, 6)
    assert scope["run_refresh"].call_count == 1
