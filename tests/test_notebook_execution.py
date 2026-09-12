"""Exercise the offline guardrails and actual notebook approval gate without local data."""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_notebooks", ROOT / "scripts/check_notebooks.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def notebook(tmp_path, source):
    path = tmp_path / "test.ipynb"
    path.write_text(json.dumps({"cells": [{"cell_type": "code", "source": source}]}), encoding="utf-8")
    return path


def test_guards_allow_only_explicit_readonly_sqlite(tmp_path):
    database = tmp_path / "example.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("create table example (value int)")
    connection.commit()
    connection.close()
    original = database.read_bytes()
    with checker.offline_guards():
        with pytest.raises(RuntimeError, match="mode=ro"):
            sqlite3.connect(database)
        with pytest.raises(RuntimeError, match="mode=ro"):
            sqlite3.connect(database.as_uri() + "?mode=rw", uri=True)
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        assert connection.execute("select count(*) from example").fetchone() == (0,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("insert into example values (1)")
        connection.close()
    assert database.read_bytes() == original


@pytest.mark.parametrize("action", [
    lambda: requests.get("https://example.invalid"),
    lambda: pd.DataFrame({"x": [1]}).to_csv("blocked.csv"),
    lambda: pd.Series([1]).to_csv("blocked.csv"),
])
def test_guards_block_http_and_exports(action):
    with checker.offline_guards(), pytest.raises(RuntimeError, match="blocks network and exports"):
        action()


def test_checker_reports_regular_errors_as_failures(tmp_path):
    result = checker.run_notebook(notebook(tmp_path, "raise ValueError('bad parse')"), tmp_path)
    assert result["status"] == "FAIL"
    assert result["reason"] == "bad parse"


def test_actual_ma_approval_gate_rejects_changed_pipeline_file(tmp_path):
    source = json.loads((ROOT / "notebooks/91_flut_ma_sportsbook_signal.ipynb").read_text(encoding="utf-8"))
    setup = next("".join(c["source"]) for c in source["cells"]
                 if c["cell_type"] == "code" and "PIPELINE_BIND_PATHS =" in "".join(c["source"]))
    prefix, gate = setup.split("hash_original_before = sha256(ORIGINAL_DB)", 1)
    gate = "hash_original_before = sha256(ORIGINAL_DB)" + gate.split('print(f"approved_pipeline_commit:', 1)[0]
    # Run the notebook's actual gate. Stub all hashes and Git reads, not the decision.
    stubs = '''
sha256 = lambda path: EXPECTED_STAGING_SHA256
git_rev_parse = lambda *args: APPROVED_PIPELINE_COMMIT
git_blob_hash = lambda *args: "approved-blob"
working_tree_blob_hash = lambda path: "changed-blob" if path.name == "massachusetts.py" else "approved-blob"
'''
    result = checker.run_notebook(notebook(tmp_path, prefix + stubs + gate), ROOT)
    assert result["status"] == "BLOCKED"
    assert "analyst-approval binding mismatch" in result["reason"]
    assert "massachusetts.py: working tree differs" in result["reason"]


def test_walkthrough_executes_with_retained_fixture(tmp_path):
    source = ROOT / "notebooks/31_massachusetts_pdf_walkthrough.ipynb"
    assert checker.run_notebook(source, ROOT)["status"] == "PASS"
