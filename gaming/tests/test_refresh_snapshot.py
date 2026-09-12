"""Refresh failures must preserve the starting data and never certify bad backups."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import zipfile

import pandas as pd
import pytest

from variant_gaming import refresh
from variant_gaming.storage import RESULT_COLUMNS, connect, ensure_schema, upsert_gaming_results


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "repo/gaming"
    (root / "config").mkdir(parents=True)
    (root / "src/variant_gaming").mkdir(parents=True)
    (root / "src/variant_gaming/example.py").write_text("# retained code\n")
    (root / "config/state_gaming_source_inventory.csv").write_text(
        "state_code,vertical,official_landing_url\nMA,online_sports_betting,https://example.gov/reports\n")
    source = root / "data/raw/MA/2026-09-01/report.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"retained official report bytes")
    row = dict.fromkeys(RESULT_COLUMNS)
    row.update(jurisdiction="Massachusetts", state_code="MA", vertical="online_sports_betting",
               channel="online", operator="STATEWIDE", row_type="official_statewide_total",
               period_start="2026-07-01", period_end="2026-07-31", frequency="monthly",
               handle=100.0, gross_revenue=-2.0, reported_revenue_name="Accrual Win",
               source_url="https://example.gov/reports", source_file=source.relative_to(root).as_posix(),
               source_sha256=refresh.file_sha256(source), retrieved_at_utc="2026-09-01T12:00:00+00:00",
               report_status="reported")
    base = root / "data/staging/baseline/gaming_current.sqlite"
    with closing(connect(base)) as connection:
        upsert_gaming_results(connection, pd.DataFrame([row]))
    return dict(root=root, base=base, row=row, source=source,
                run=root / "data/staging/refresh_20260912", backup=tmp_path / "backups/capture.zip")


def options(workspace):
    return dict(root=workspace["root"], base_db=workspace["base"], run_dir=workspace["run"],
                backup_path=workspace["backup"], sources=[("MA", "online_sports_betting")])


def recent_log(success=True):
    return pd.DataFrame([dict(validation="passed" if success else "failed",
                              reason="" if success else "Malformed official report")])


def test_preview_does_not_write_or_download(workspace, monkeypatch):
    monkeypatch.setattr(refresh, "collect_recent", lambda *a, **k: pytest.fail("preview downloaded"))
    before = refresh.file_sha256(workspace["base"])
    result = refresh.run_refresh(**options(workspace))
    assert result["status"] == "preview"
    assert not workspace["run"].exists()
    assert not workspace["backup"].parent.exists()
    assert refresh.file_sha256(workspace["base"]) == before


@pytest.mark.parametrize("change", ["unsupported_recent", "existing_run", "nested_run", "repository_backup", "existing_backup"])
def test_preflight_rejects_bad_destinations_before_collection(workspace, monkeypatch, change):
    args = options(workspace)
    monkeypatch.setattr(refresh, "collect_recent", lambda *a, **k: pytest.fail("bad plan downloaded"))
    if change == "unsupported_recent":
        args["sources"] = [("MI", "online_casino")]
    elif change == "existing_run":
        workspace["run"].mkdir()
    elif change == "nested_run":
        args["run_dir"] = workspace["base"].parent / "nested"
    elif change == "repository_backup":
        args["backup_path"] = workspace["root"].parent / "data.zip"
    else:
        workspace["backup"].parent.mkdir()
        workspace["backup"].write_bytes(b"keep this")
    with pytest.raises((ValueError, FileExistsError)):
        refresh.run_refresh(**args, live=True)


def test_validation_preserves_negative_revenue_and_missing_adjusted(workspace):
    result, manifest = refresh.validate_snapshot(root=workspace["root"], database=workspace["base"])
    assert result["validated_observations"] == 1
    assert result["verified_source_files"] == 1
    assert manifest["files"][0]["sha256"] == workspace["row"]["source_sha256"]


@pytest.mark.parametrize("column,value,error", [
    ("retrieved_at_utc", "2026-09-01T12:00:00", "timezone"),
    ("retrieved_at_utc", "2099-01-01T00:00:00+00:00", "future retrieval"),
    ("period_end", "2099-01-31", "Future reporting"),
    ("source_file", "../../outside.pdf", "outside retained"),
    ("source_sha256", "0" * 64, "hash mismatch"),
    ("handle", float("inf"), "Non-finite"),
])
def test_bad_provenance_and_dates_stop_validation(workspace, column, value, error):
    with closing(sqlite3.connect(workspace["base"])) as connection:
        connection.execute(f"UPDATE gaming_results SET {column}=?", (value,))
        connection.commit()
    with pytest.raises(ValueError, match=error):
        refresh.validate_snapshot(root=workspace["root"], database=workspace["base"])


def test_source_bytes_must_match_not_just_filename(workspace):
    workspace["source"].write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        refresh.run_refresh(**options(workspace), live=True)
    assert not workspace["run"].exists()


def test_wal_input_not_certified_as_standalone_snapshot(workspace):
    with closing(sqlite3.connect(workspace["base"])) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE gaming_results SET handle=101")
        writer.commit()
        with pytest.raises(ValueError, match="WAL"):
            refresh.validate_snapshot(root=workspace["root"], database=workspace["base"])


def test_wrong_primary_key_rejected(workspace):
    with closing(sqlite3.connect(workspace["base"])) as connection:
        connection.execute("CREATE TABLE copy AS SELECT * FROM gaming_results")
        connection.execute("DROP TABLE gaming_results")
        connection.execute("ALTER TABLE copy RENAME TO gaming_results")
        connection.commit()
    with pytest.raises(ValueError, match="primary key"):
        refresh.validate_snapshot(root=workspace["root"], database=workspace["base"])


def test_duplicate_results_rejected_even_if_schema_is_correct(workspace, monkeypatch):
    original = pd.read_sql_query
    def duplicated(*args, **kwargs):
        frame = original(*args, **kwargs)
        return pd.concat([frame, frame], ignore_index=True)
    monkeypatch.setattr(pd, "read_sql_query", duplicated)
    with pytest.raises(ValueError, match="Duplicate observation"):
        refresh.validate_snapshot(root=workspace["root"], database=workspace["base"])


def test_illinois_ordered_pair_hash_validated(workspace):
    other = workspace["source"].parent / "tax.csv"
    other.write_bytes(b"tax report")
    digest = hashlib.sha256((refresh.file_sha256(workspace["source"]) + "|" + refresh.file_sha256(other)).encode()).hexdigest()
    with closing(sqlite3.connect(workspace["base"])) as connection:
        connection.execute("UPDATE gaming_results SET state_code='IL', source_file=?, source_sha256=?",
                           (workspace["row"]["source_file"] + "|" + other.relative_to(workspace["root"]).as_posix(), digest))
        connection.commit()
    result, _ = refresh.validate_snapshot(root=workspace["root"], database=workspace["base"])
    assert result["verified_source_files"] == 2


def test_refresh_preserves_base_and_backs_up_conflicting_versions(workspace, monkeypatch):
    base_hash = refresh.file_sha256(workspace["base"])
    vintage = workspace["root"] / "data/expectations/earlier/expectation.json"
    vintage.parent.mkdir(parents=True)
    vintage.write_text('{"kind":"retained_reference"}')
    def revised(*args, root, db_path, **kwargs):
        source = root / "data/raw/MA/2026-09-01/revision.pdf"
        source.write_bytes(b"revised official bytes")
        row = dict(workspace["row"], handle=110, source_file=source.relative_to(root).as_posix(),
                   source_sha256=refresh.file_sha256(source))
        with closing(connect(db_path)) as connection:
            upsert_gaming_results(connection, pd.DataFrame([row]))
        return recent_log()
    monkeypatch.setattr(refresh, "collect_recent", revised)
    result = refresh.run_refresh(**options(workspace), live=True)
    assert result["status"] == "complete"
    assert result["validation"]["validated_observations"] == 2
    assert result["backup_receipt"]["restore_verified"] is True
    assert refresh.file_sha256(workspace["base"]) == base_hash
    with zipfile.ZipFile(workspace["backup"]) as archive:
        assert archive.testzip() is None
        hashes = json.loads(archive.read("archive_hashes.json"))
        assert "gaming/data/expectations/earlier/expectation.json" in hashes
        for name, digest in hashes.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
    with pytest.raises((FileExistsError, ValueError)):
        refresh.run_refresh(**options(workspace), live=True)


def test_partial_refresh_is_backed_up_but_not_reported_complete(workspace, monkeypatch):
    monkeypatch.setattr(refresh, "collect_recent", lambda *a, **k: recent_log(False))
    result = refresh.run_refresh(**options(workspace), live=True)
    assert result["status"] == "complete_with_exceptions"
    assert result["backup_receipt"]["restore_verified"]
    summary = pd.read_csv(workspace["run"] / "collection_summary.csv")
    assert summary.iloc[0].run_status == "failed"


def test_backup_failure_leaves_failure_record_without_completion(workspace, monkeypatch):
    monkeypatch.setattr(refresh, "collect_recent", lambda *a, **k: recent_log())
    def fail(**kwargs):
        raise OSError("Backup destination unavailable")
    monkeypatch.setattr(refresh, "backup_snapshot", fail)
    with pytest.raises(OSError, match="unavailable"):
        refresh.run_refresh(**options(workspace), live=True)
    assert (workspace["run"] / "failure.json").exists()
    assert not (workspace["run"] / "completion.json").exists()


def test_changed_raw_source_between_validation_and_backup_is_rejected(workspace, monkeypatch):
    monkeypatch.setattr(refresh, "collect_recent", lambda *a, **k: recent_log())
    original = refresh.backup_snapshot
    def tamper(**kwargs):
        workspace["source"].write_bytes(b"changed after validation")
        return original(**kwargs)
    monkeypatch.setattr(refresh, "backup_snapshot", tamper)
    with pytest.raises(ValueError, match="changed after validation"):
        refresh.run_refresh(**options(workspace), live=True)
    assert not workspace["backup"].exists()


def test_cli_preview_has_no_side_effects(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/refresh_gaming.py"
    run = script.parents[1] / "data/staging/test_cli_preview_never_created"
    result = subprocess.run([sys.executable, "-B", str(script), "--source", "MA:online_sports_betting",
                             "--run-dir", str(run), "--backup", str(tmp_path / "preview.zip")],
                            text=True, capture_output=True, check=True)
    assert json.loads(result.stdout)["status"] == "preview"
    assert not run.exists()
    assert not (tmp_path / "preview.zip").exists()
