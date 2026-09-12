"""Explicit fresh-destination refresh, source validation, and tested data backup.

State modules still own collection and accounting. This small orchestration layer
never promotes a snapshot or changes an approved baseline. Preview is read-only.
"""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile

import pandas as pd

from variant_gaming.collect import COLLECTORS, inventory_official_url, run_all_collectors
from variant_gaming.recent import RECENT_SOURCES, collect_recent
from variant_gaming.storage import RESULT_COLUMNS, connect_readonly, validate_gaming_results_frame

OBSERVATION_KEY = ["state_code", "vertical", "channel", "operator", "row_type",
                   "period_start", "period_end", "source_sha256"]


def file_sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def refresh_plan(*, root, run_dir, backup_path, sources, base_db=None, mode="recent", report_limit=2):
    """Check explicit inputs without creating files or contacting sources."""
    root, run_dir, backup_path = map(lambda p: Path(p).resolve(), (root, run_dir, backup_path))
    sources = list(dict.fromkeys((state.upper(), product) for state, product in sources))
    if not sources or set(sources) - COLLECTORS.keys():
        raise ValueError("Choose nonempty registered state/product sources")
    if mode not in {"recent", "history"}:
        raise ValueError("Choose recent or history explicitly")
    if mode == "recent" and set(sources) - RECENT_SOURCES:
        raise ValueError("Recent mode supports MA/NY sportsbook; choose history for other sources")
    if isinstance(report_limit, bool) or not isinstance(report_limit, int) or report_limit < 1:
        raise ValueError("report_limit must be a positive integer")
    staging = (root / "data/staging").resolve()
    if run_dir.parent != staging or run_dir.suffix or run_dir.exists():
        raise ValueError("Use a new dated directory directly inside gaming/data/staging")
    if backup_path.is_relative_to(root.parent) or backup_path.suffix.lower() != ".zip":
        raise ValueError("Choose an explicit .zip backup outside the research repository")
    if backup_path.exists() or backup_path.with_suffix(".receipt.json").exists():
        raise FileExistsError("Backup or receipt already exists; choose a new path")
    base = Path(base_db).resolve() if base_db is not None else None
    if base is not None and not base.is_file():
        raise FileNotFoundError(base)
    return dict(root=str(root), run_dir=str(run_dir), database=str(run_dir / "gaming_current.sqlite"),
                backup=str(backup_path), base_database=str(base) if base else None,
                mode=mode, report_limit=report_limit,
                sources=[dict(state_code=s, vertical=v, official_url=inventory_official_url(s, v, root))
                         for s, v in sources])


def validate_snapshot(*, root, database, as_of=None):
    """Read-only schema/date/source-byte checks; not economic or analyst approval.

Returns (validation, source_manifest). Monetary blanks and negative revenue are
valid; infinities, missing provenance, future periods and escaping paths are not.
Illinois binds its two ordered handle/tax CSVs with a composite SHA-256.
"""
    root, database = Path(root).resolve(), Path(database).resolve()
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(database) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError("A frozen snapshot must have no nonempty WAL or journal")
    before = file_sha256(database)
    cutoff = pd.Timestamp(as_of if as_of is not None else datetime.now(timezone.utc))
    if cutoff.tzinfo is None:
        raise ValueError("Validation cutoff must include a timezone")
    cutoff = cutoff.tz_convert("UTC")
    with closing(connect_readonly(database)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        frame = pd.read_sql_query("SELECT * FROM gaming_results", connection)
        info = connection.execute("PRAGMA table_info(gaming_results)").fetchall()
        primary_key = [row["name"] for row in sorted(info, key=lambda row: row["pk"]) if row["pk"]]
        if primary_key != OBSERVATION_KEY:
            raise ValueError("Snapshot does not have the expected observation primary key")
    if frame.empty:
        raise ValueError("No observations were collected")
    validate_gaming_results_frame(frame[RESULT_COLUMNS])
    if frame.duplicated(OBSERVATION_KEY).any():
        raise ValueError("Duplicate observation keys")
    for column in ("source_url", "source_file", "source_sha256", "retrieved_at_utc", "report_status"):
        if frame[column].isna().any() or frame[column].astype(str).str.strip().eq("").any():
            raise ValueError(f"Missing provenance: {column}")
    starts = pd.to_datetime(frame.period_start, errors="raise")
    ends = pd.to_datetime(frame.period_end, errors="raise")
    if starts.isna().any() or ends.isna().any() or (starts > ends).any():
        raise ValueError("Invalid reporting dates")
    if (ends > cutoff.tz_localize(None).normalize()).any():
        raise ValueError("Future reporting period found")
    if any(pd.Timestamp(value).tzinfo is None for value in frame.retrieved_at_utc):
        raise ValueError("Retrieval timestamps must include an explicit timezone")
    captured = pd.to_datetime(frame.retrieved_at_utc, utc=True, errors="raise")
    if captured.isna().any() or (captured > cutoff).any():
        raise ValueError("Invalid or future retrieval timestamp")
    if not frame.frequency.isin(["monthly", "weekly"]).all():
        raise ValueError("Unsupported reporting frequency")
    for column in ("handle", "gross_revenue", "adjusted_revenue", "taxable_revenue", "net_proceeds", "tax"):
        values = pd.to_numeric(frame[column], errors="raise")
        if values.isin([float("inf"), -float("inf")]).any():
            raise ValueError(f"Non-finite monetary value: {column}")
    files, bindings = {}, []
    for row in frame[["state_code", "source_file", "source_sha256"]].drop_duplicates().itertuples(index=False):
        components = row.source_file.split("|")
        if len(components) != 1 and not (row.state_code == "IL" and len(components) == 2):
            raise ValueError("Unrecognized composite source binding")
        digests = []
        for relative in components:
            path = (root / relative).resolve()
            if not path.is_relative_to(root / "data/raw"):
                raise ValueError(f"Source path is outside retained raw data: {relative}")
            key = path.relative_to(root).as_posix()
            if key not in files:
                files[key] = dict(path=key, sha256=file_sha256(path), byte_length=path.stat().st_size)
            digests.append(files[key]["sha256"])
        expected = digests[0] if len(digests) == 1 else hashlib.sha256("|".join(digests).encode()).hexdigest()
        if expected != row.source_sha256:
            raise ValueError(f"Source hash mismatch: {row.source_file}")
        bindings.append(dict(source_file=row.source_file, source_sha256=row.source_sha256, components=components))
    if file_sha256(database) != before or any(Path(str(database) + suffix).exists() and Path(str(database) + suffix).stat().st_size for suffix in ("-wal", "-journal")):
        raise ValueError("Database changed during validation")
    validation = dict(database=str(database), database_sha256=before, integrity="ok",
                      validated_at=cutoff.isoformat(), validated_observations=len(frame),
                      verified_source_files=len(files), future_period_rows=0,
                      state_products_with_rows=len(frame.groupby(["state_code", "vertical"])),
                      approval_status="unreviewed_current_capture", original_approved_snapshot_recreated=False)
    return validation, dict(files=list(files.values()), bindings=bindings)


def backup_snapshot(*, root, run_dir, backup_path):
    """Create a new ZIP, verify every member, then restore and open its database."""
    root, run_dir, backup_path = map(lambda p: Path(p).resolve(), (root, run_dir, backup_path))
    database = run_dir / "gaming_current.sqlite"
    validation = json.loads((run_dir / "validation.json").read_text())
    sources = json.loads((run_dir / "source_manifest.json").read_text())
    if file_sha256(database) != validation["database_sha256"]:
        raise ValueError("Database changed after validation")
    if backup_path.exists() or backup_path.with_suffix(".receipt.json").exists():
        raise FileExistsError("Backup or receipt already exists")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    paths = {root / item["path"] for item in sources["files"]}
    # Preserve raw sidecars and failed downloads from the run as well as row bindings.
    paths.update(p for p in (root / "data/raw").rglob("*") if p.is_file())
    paths.update(p for p in (root / "data/expectations").rglob("*") if p.is_file())
    paths.update(p for p in run_dir.rglob("*") if p.is_file())
    paths.update(p for p in (root / "config").rglob("*") if p.is_file())
    paths.update((root / "src").rglob("*.py"))
    members = {}
    for path in sorted(paths):
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"Backup file escapes the gaming project: {path}")
        members["gaming/" + path.relative_to(root).as_posix()] = dict(path=path, sha256=file_sha256(path))
    for item in sources["files"]:
        if members["gaming/" + item["path"]]["sha256"] != item["sha256"]:
            raise ValueError(f"Source changed after validation: {item['path']}")
    with zipfile.ZipFile(backup_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, item in members.items():
            archive.write(item["path"], name)
        archive.writestr("archive_hashes.json", json.dumps({name: item["sha256"] for name, item in members.items()}, indent=2))
    restored = None
    try:
        with zipfile.ZipFile(backup_path) as archive:
            if archive.testzip() is not None:
                raise ValueError("Backup CRC validation failed")
            for name, item in members.items():
                with archive.open(name) as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
                        raise ValueError(f"Backup member differs: {name}")
            with tempfile.NamedTemporaryFile(dir=backup_path.parent, suffix=".sqlite", delete=False) as destination:
                restored = Path(destination.name)
                with archive.open("gaming/" + database.relative_to(root).as_posix()) as source:
                    shutil.copyfileobj(source, destination)
        if file_sha256(restored) != validation["database_sha256"]:
            raise ValueError("Restored database hash differs")
        with closing(connect_readonly(restored)) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Restored SQLite integrity failed")
            count = connection.execute("SELECT COUNT(*) FROM gaming_results").fetchone()[0]
        if count != validation["validated_observations"]:
            raise ValueError("Restored observation count differs")
    finally:
        if restored is not None:
            restored.unlink(missing_ok=True)
    receipt = dict(backup=str(backup_path), backup_sha256=file_sha256(backup_path),
                   backup_bytes=backup_path.stat().st_size, database_sha256=validation["database_sha256"],
                   archive_members=len(members) + 1, all_member_hashes_verified=True,
                   restore_verified=True, restored_observations=count)
    _write_json(backup_path.with_suffix(".receipt.json"), receipt)
    return receipt


def run_refresh(*, root, run_dir, backup_path, sources, base_db=None, mode="recent", report_limit=2, live=False):
    """Preview by default; --live-style explicit opt-in collects to a new snapshot.

Valid partial captures are backed up and return complete_with_exceptions, never
silently promoted. Validation/backup failure leaves the new run for inspection.
"""
    plan = refresh_plan(root=root, run_dir=run_dir, backup_path=backup_path, sources=sources,
                        base_db=base_db, mode=mode, report_limit=report_limit)
    if not live:
        return dict(status="preview", **plan)
    root, run_dir = Path(plan["root"]), Path(plan["run_dir"])
    database = Path(plan["database"])
    base_hash = file_sha256(base_db) if base_db is not None else None
    if base_db is not None:
        validate_snapshot(root=root, database=base_db)
    run_dir.mkdir(parents=True)
    manifest = dict(**plan, started_at=datetime.now(timezone.utc).isoformat(),
                    base_database_sha256=base_hash, approval_status="unreviewed_current_capture",
                    code_hashes={p.relative_to(root).as_posix(): file_sha256(p) for p in (root / "src").rglob("*.py")},
                    config_hashes={p.relative_to(root).as_posix(): file_sha256(p) for p in (root / "config").glob("*.csv")})
    _write_json(run_dir / "run_manifest.json", manifest)
    try:
        if base_db is not None:
            with closing(connect_readonly(Path(base_db))) as source, closing(sqlite3.connect(database)) as destination:
                source.backup(destination)
        summaries = []
        for item in plan["sources"]:
            state, product = item["state_code"], item["vertical"]
            if mode == "history":
                summary = run_all_collectors(root=root, db_path=database, selected=[(state, product)])
            else:
                log = collect_recent(state, product, root=root, db_path=database, report_limit=report_limit)
                log.to_csv(run_dir / f"{state}_{product}_reports.csv", index=False)
                successful = log.validation.isin(["passed", "previously_ingested"]).all() and not log.empty
                summary = pd.DataFrame([dict(state_code=state, vertical=product,
                    run_status="completed" if successful else "failed",
                    coverage_status="recent_only" if successful else "partial",
                    coverage_reason="Recent reports only; historical coverage is retained separately",
                    run_error="; ".join(log.loc[~log.validation.isin(["passed", "previously_ingested"]), "reason"].astype(str)))])
            summaries.append(summary)
            pd.concat(summaries, ignore_index=True).to_csv(run_dir / "collection_summary.csv", index=False)
        if base_db is not None and file_sha256(base_db) != base_hash:
            raise ValueError("Base database changed during refresh")
        validation, sources_manifest = validate_snapshot(root=root, database=database)
        _write_json(run_dir / "validation.json", validation)
        _write_json(run_dir / "source_manifest.json", sources_manifest)
        combined = pd.concat(summaries, ignore_index=True)
        exceptions = combined.run_status.ne("completed") | ~combined.coverage_status.isin(["ok", "recent_only"])
        manifest.update(finished_at=datetime.now(timezone.utc).isoformat(),
                        capture_status="validated_with_exceptions" if exceptions.any() else "validated",
                        database_sha256=validation["database_sha256"], observation_count=validation["validated_observations"])
        _write_json(run_dir / "run_manifest.json", manifest)
        receipt = backup_snapshot(root=root, run_dir=run_dir, backup_path=backup_path)
        result = dict(status="complete_with_exceptions" if exceptions.any() else "complete", **plan,
                      validation=validation, backup_receipt=receipt)
        _write_json(run_dir / "completion.json", result)
        return result
    except Exception as exc:
        _write_json(run_dir / "failure.json", dict(status="failed", error=str(exc),
                                                   failed_at=datetime.now(timezone.utc).isoformat()))
        raise
