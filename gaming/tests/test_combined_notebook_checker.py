"""Distinguish unavailable local evidence from broken code in the shared checker."""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("combined_checker", REPOSITORY / "scripts/check_notebooks.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def write_notebook(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cells": [{"cell_type": "code", "source": source}]}), encoding="utf-8")
    return path


@pytest.mark.parametrize("project", ["gaming", "vehicle"])
@pytest.mark.parametrize("source", [
    "from pathlib import Path; Path('data/missing.csv').read_bytes()",
    "from pathlib import Path; import sqlite3; sqlite3.connect(Path('data/missing database.sqlite').resolve().as_uri() + '?mode=ro', uri=True)",
])
def test_missing_retained_files_are_blocked(tmp_path, project, source):
    root = tmp_path / project
    notebook = write_notebook(root / "notebooks/test.ipynb", source)
    result = checker.run_notebook(notebook, root)
    assert result["status"] == "BLOCKED"
    assert result["reason"].startswith("Missing retained data:")
    assert not (root / "data").exists()


def test_missing_source_file_is_still_a_failure(tmp_path):
    notebook = write_notebook(tmp_path / "test.ipynb", "from pathlib import Path; Path('src/missing.py').read_bytes()")
    assert checker.run_notebook(notebook, tmp_path)["status"] == "FAIL"


def test_existing_sqlite_stays_readonly(tmp_path):
    path = tmp_path / "retained database.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE evidence (value INTEGER)")
    before = path.read_bytes()
    with checker.offline_guards():
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone() == (0,)
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                conn.execute("INSERT INTO evidence VALUES (1)")
        finally:
            conn.close()
    assert path.read_bytes() == before


def test_all_projects_reports_missing_data_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    for prefix in checker.GAMING_NOTEBOOKS:
        write_notebook(tmp_path / f"gaming/notebooks/{prefix}_test.ipynb", "assert 1 + 1 == 2")
    write_notebook(tmp_path / "vehicle/notebooks/test.ipynb", "from pathlib import Path; Path('data/missing.csv').read_bytes()")
    monkeypatch.setattr("sys.argv", [str(REPOSITORY / "scripts/check_notebooks.py"), "--root", str(tmp_path), "--project", "all"])
    assert checker.main() == 1
    results = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(results) == 7
    assert sum(r["status"] == "PASS" and r["project"] == "gaming" for r in results) == 6
    assert results[-1]["project"] == "vehicle"
    assert results[-1]["status"] == "BLOCKED"


def test_daily_path_keeps_historical_blocks_and_reference_experiments_are_optional(tmp_path, capsys):
    assert {"20", "90", "94", "91", "92", "93"} == set(checker.GAMING_NOTEBOOKS)
    assert {"95", "96"}.issubset(checker.GAMING_REFERENCES)
    for prefix in checker.GAMING_NOTEBOOKS + checker.GAMING_REFERENCES:
        source = "assert 1 + 1 == 2"
        if prefix in {"91", "92", "93"}:
            source = "from pathlib import Path; Path('data/missing_historical.sqlite').read_bytes()"
        write_notebook(tmp_path / f"gaming/notebooks/{prefix}_test.ipynb", source)
    assert checker.main(["--root", str(tmp_path), "--project", "gaming", "--include-reference"]) == 1
    results = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(results) == 13
    assert [result["notebook"][:2] for result in results if result["status"] == "BLOCKED"] == ["91", "92", "93"]
    assert all(result["status"] == "PASS" for result in results if result["notebook"][:2] in {"20", "90", "94", "95", "96"})
