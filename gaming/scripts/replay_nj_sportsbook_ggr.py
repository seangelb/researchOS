"""
Controlled New Jersey online-sportsbook GGR replay into a candidate SQLite DB.

Copies data/gaming.sqlite to an ignored candidate path, replaces only
NJ + online_sports_betting rows using the corrected parser, and never mutates
the source database. Candidate replacement is not production authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from variant_gaming.common import project_root  # noqa: E402
from variant_gaming.states.new_jersey import (  # noqa: E402
    SPORTS_VERTICAL,
    build_normalized_rows,
    parse_nj_pdf,
)
from variant_gaming.storage import (  # noqa: E402
    COVERAGE_UPSERT_SQL,
    RESULT_COLUMNS,
    UPSERT_SQL,
    _row_to_params,
    connect,
    ensure_schema,
    validate_gaming_results_frame,
)

STATE_CODE = "NJ"
CASINO_VERTICAL = "online_casino"
EXPECTED_PDF_COUNT = 98
EXPECTED_ROW_COUNT = 1_422
EXPECTED_SOURCE_SHA256 = (
    "FB3BC37E92BA12F5AD08FC55AEB0968BA7DEC207E60DD887BEEE2BE4A5CE87C6"
)
EXPECTED_MONTH_START = "2018-06-01"
EXPECTED_MONTH_END = "2026-07-01"
IMPLAUSIBLE_GGR = 1_000_000_000_000.0  # $1 trillion
JAN_2024 = "2024-01-01"
JAN_2024_EXPECTED = {
    "Fanduel": 80_725_429.0,
    "Pointsbet": 28_541_559.0,
    "SuperBook": 45_791.0,
}
PARSER_SOURCE_PATH = "src/variant_gaming/states/new_jersey.py"
PK_COLUMNS = [
    "state_code",
    "vertical",
    "channel",
    "operator",
    "row_type",
    "period_start",
    "period_end",
    "source_sha256",
]


class ReplayError(RuntimeError):
    """Fail-closed replay error."""


@dataclass
class ReplayReport:
    source_db: str
    source_sha256: str
    expected_source_sha256: str
    candidate_db: str | None = None
    candidate_sha256: str | None = None
    repository_head_sha: str | None = None
    parser_source_commit_sha: str | None = None
    replay_timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    source_pdfs_expected: int = EXPECTED_PDF_COUNT
    source_pdfs_succeeded: int = 0
    source_pdfs_failed: list[str] = field(default_factory=list)
    rows_before: int | None = None
    rows_after: int | None = None
    periods_before: dict | None = None
    periods_after: dict | None = None
    provenance_conflicts: list[dict] = field(default_factory=list)
    validation_results: dict = field(default_factory=dict)
    unchanged_scope: dict = field(default_factory=dict)
    january_2024_checks: dict = field(default_factory=dict)
    monthly_continuity: dict = field(default_factory=dict)
    source_hash_checks: dict = field(default_factory=dict)
    final_status: str = "FAIL"

    def to_dict(self) -> dict:
        return {
            "source_database_path": self.source_db,
            "source_database_sha256": self.source_sha256,
            "expected_source_sha256": self.expected_source_sha256,
            "source_hash_checks": self.source_hash_checks,
            "candidate_database_path": self.candidate_db,
            "candidate_database_sha256": self.candidate_sha256,
            "repository_head_sha": self.repository_head_sha,
            "parser_source_commit_sha": self.parser_source_commit_sha,
            "replay_timestamp": self.replay_timestamp,
            "source_pdfs": {
                "expected": self.source_pdfs_expected,
                "succeeded": self.source_pdfs_succeeded,
                "failed": self.source_pdfs_failed,
            },
            "rows_before": self.rows_before,
            "rows_after": self.rows_after,
            "periods_before": self.periods_before,
            "periods_after": self.periods_after,
            "provenance_conflicts": self.provenance_conflicts,
            "validation_results": self.validation_results,
            "unchanged_scope_comparison": self.unchanged_scope,
            "january_2024_exact_value_checks": self.january_2024_checks,
            "monthly_continuity": self.monthly_continuity,
            "final_status": self.final_status,
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head_sha(root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def git_file_latest_sha(root: Path, relative_path: str) -> str:
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "--", relative_path],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def assert_source_hash(actual: str, expected: str, *, stage: str) -> None:
    if actual.lower() != expected.lower():
        raise ReplayError(
            f"Source database SHA-256 mismatch at {stage}: "
            f"expected {expected}, got {actual}"
        )


def validate_output_paths(source_db: Path, candidate_db: Path, report_path: Path) -> None:
    source_r = source_db.resolve()
    candidate_r = candidate_db.resolve()
    report_r = report_path.resolve()
    if candidate_r == source_r:
        raise ReplayError(
            f"Candidate path must differ from source database: {source_r}"
        )
    if report_r == source_r:
        raise ReplayError(f"Report path must differ from source database: {source_r}")
    if report_r == candidate_r:
        raise ReplayError(
            f"Report path must differ from candidate database: {candidate_r}"
        )
    if report_path.suffix.lower() != ".json":
        raise ReplayError(f"Report path must end with .json: {report_path}")
    if candidate_db.exists():
        raise ReplayError(
            f"Candidate database already exists (refusing overwrite): {candidate_db}"
        )
    if report_path.exists():
        raise ReplayError(
            f"Replay report already exists (refusing overwrite): {report_path}"
        )


def assert_distinct_paths(source_db: Path, candidate_db: Path) -> None:
    """Backward-compatible helper; prefer validate_output_paths."""
    if source_db.resolve() == candidate_db.resolve():
        raise ReplayError(
            f"Candidate path must differ from source database: {source_db.resolve()}"
        )


def assert_candidate_absent(candidate_db: Path) -> None:
    if candidate_db.exists():
        raise ReplayError(
            f"Candidate database already exists (refusing overwrite): {candidate_db}"
        )


def open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _canonical_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    return out.sort_values(list(out.columns)).reset_index(drop=True)


def extract_scope_frames(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    results = pd.read_sql_query("SELECT * FROM gaming_results", conn)
    coverage = pd.read_sql_query("SELECT * FROM source_coverage", conn)
    schema = pd.read_sql_query(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name",
        conn,
    )
    non_nj_sports = results.loc[
        ~((results["state_code"] == STATE_CODE) & (results["vertical"] == SPORTS_VERTICAL))
    ]
    nj_casino = results.loc[
        (results["state_code"] == STATE_CODE) & (results["vertical"] == CASINO_VERTICAL)
    ]
    coverage_other = coverage.loc[
        ~((coverage["state_code"] == STATE_CODE) & (coverage["vertical"] == SPORTS_VERTICAL))
    ]
    return {
        "gaming_results_outside_nj_sportsbook": _canonical_frame(non_nj_sports),
        "nj_online_casino": _canonical_frame(nj_casino),
        "source_coverage_outside_nj_sportsbook": _canonical_frame(coverage_other),
        "schema": _canonical_frame(schema),
    }


def frames_equal(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    if list(a.columns) != list(b.columns):
        return False
    if len(a) != len(b):
        return False
    if a.empty and b.empty:
        return True
    left = a.fillna("__NA__").astype(str).reset_index(drop=True)
    right = b.fillna("__NA__").astype(str).reset_index(drop=True)
    return left.equals(right)


def select_nj_sportsbook_sources(conn: sqlite3.Connection) -> pd.DataFrame:
    """Distinct retained NJ sportsbook sources with fail-closed provenance checks."""
    frame = pd.read_sql_query(
        """
        SELECT source_file, source_sha256, source_url, retrieved_at_utc, COUNT(*) AS row_count
        FROM gaming_results
        WHERE state_code = ? AND vertical = ?
        GROUP BY source_file, source_sha256, source_url, retrieved_at_utc
        ORDER BY source_sha256, source_file
        """,
        conn,
        params=(STATE_CODE, SPORTS_VERTICAL),
    )
    if frame.empty:
        raise ReplayError("No NJ online_sports_betting rows found in source database")

    conflicts: list[dict] = []
    for sha, group in frame.groupby("source_sha256", dropna=False):
        files = sorted(group["source_file"].unique())
        urls = sorted(group["source_url"].unique())
        retrieved = sorted(group["retrieved_at_utc"].unique())
        if len(files) > 1 or len(urls) > 1 or len(retrieved) > 1:
            conflicts.append(
                {
                    "source_sha256": sha,
                    "source_files": files,
                    "source_urls": urls,
                    "retrieved_at_utc_values": retrieved,
                }
            )
    if conflicts:
        raise ReplayError(
            "Conflicting provenance metadata for one or more source hashes: "
            + json.dumps(conflicts, indent=2)
        )
    return frame.drop_duplicates(subset=["source_sha256"]).reset_index(drop=True)


def resolve_source_path(root: Path, source_file: str) -> Path:
    path = Path(source_file)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def verify_source_pdfs(root: Path, sources: pd.DataFrame) -> list[dict]:
    verified: list[dict] = []
    for row in sources.itertuples(index=False):
        path = resolve_source_path(root, row.source_file)
        if not path.exists():
            raise ReplayError(f"Missing source file: {path}")
        if path.suffix.lower() != ".pdf":
            raise ReplayError(f"Source is not a PDF: {path}")
        digest = sha256_file(path)
        if digest.lower() != str(row.source_sha256).lower():
            raise ReplayError(
                f"SHA-256 mismatch for {path}: expected {row.source_sha256}, got {digest}"
            )
        verified.append(
            {
                "source_file": row.source_file,
                "resolved_path": str(path),
                "source_sha256": row.source_sha256,
                "source_url": row.source_url,
                "retrieved_at_utc": row.retrieved_at_utc,
            }
        )
    return verified


def parse_retained_sources(root: Path, verified: list[dict]) -> tuple[pd.DataFrame, list[str]]:
    frames: list[pd.DataFrame] = []
    failed: list[str] = []
    for item in sorted(verified, key=lambda x: x["source_sha256"]):
        path = Path(item["resolved_path"])
        parsed = parse_nj_pdf(path, vertical=SPORTS_VERTICAL)
        if parsed is None or parsed.empty:
            failed.append(item["source_file"])
            continue
        retrieved = datetime.fromisoformat(item["retrieved_at_utc"])
        normalized = build_normalized_rows(
            parsed,
            vertical=SPORTS_VERTICAL,
            source_url=item["source_url"],
            source_file=item["source_file"],
            source_sha256=item["source_sha256"],
            retrieved_at=retrieved,
        )
        if normalized.empty:
            failed.append(item["source_file"])
            continue
        frames.append(normalized)
    if not frames:
        return pd.DataFrame(columns=RESULT_COLUMNS), failed
    out = pd.concat(frames, ignore_index=True)
    out = out[RESULT_COLUMNS]
    out = out.sort_values(PK_COLUMNS).reset_index(drop=True)
    return out, failed


def validate_monthly_continuity(
    frame: pd.DataFrame,
    *,
    expected_month_start: str = EXPECTED_MONTH_START,
    expected_month_end: str = EXPECTED_MONTH_END,
    expected_month_count: int | None = None,
) -> dict:
    """Require one retained source hash per continuous month-start."""
    if frame.empty:
        raise ReplayError("Cannot validate monthly continuity on an empty frame")

    month_hash = (
        frame.groupby("period_start", dropna=False)["source_sha256"]
        .nunique()
        .reset_index(name="hash_count")
    )
    duplicate_months = sorted(
        month_hash.loc[month_hash["hash_count"] > 1, "period_start"].astype(str).tolist()
    )

    hash_month = (
        frame.groupby("source_sha256", dropna=False)["period_start"]
        .nunique()
        .reset_index(name="month_count")
    )
    multi_month_hashes = sorted(
        hash_month.loc[hash_month["month_count"] > 1, "source_sha256"].astype(str).tolist()
    )

    actual_months = sorted(pd.to_datetime(frame["period_start"]).unique())
    actual_month_labels = [str(pd.Timestamp(m).date()) for m in actual_months]
    expected_months = [
        str(m.date())
        for m in pd.date_range(expected_month_start, expected_month_end, freq="MS")
    ]
    if expected_month_count is None:
        expected_month_count = len(expected_months)
    missing_months = [m for m in expected_months if m not in actual_month_labels]
    unexpected_months = [m for m in actual_month_labels if m not in expected_months]

    unique_hashes = int(frame["source_sha256"].nunique())
    ok = (
        not duplicate_months
        and not multi_month_hashes
        and not missing_months
        and not unexpected_months
        and len(actual_month_labels) == expected_month_count
        and unique_hashes == expected_month_count
        and unique_hashes == len(actual_month_labels)
    )
    result = {
        "expected_month_count": expected_month_count,
        "actual_month_count": len(actual_month_labels),
        "unique_source_hashes": unique_hashes,
        "missing_months": missing_months,
        "duplicate_months": duplicate_months,
        "multi_month_source_hashes": multi_month_hashes,
        "unexpected_months": unexpected_months,
        "earliest_month": actual_month_labels[0] if actual_month_labels else None,
        "latest_month": actual_month_labels[-1] if actual_month_labels else None,
        "expected_month_start": expected_month_start,
        "expected_month_end": expected_month_end,
        "status": "PASS" if ok else "FAIL",
    }
    if not ok:
        raise ReplayError(
            "Monthly continuity validation failed: " + json.dumps(result, indent=2)
        )
    return result


def validate_pre_write(
    frame: pd.DataFrame,
    pdf_count: int,
    failed: list[str],
    *,
    expected_pdf_count: int = EXPECTED_PDF_COUNT,
    expected_row_count: int = EXPECTED_ROW_COUNT,
    require_january_2024: bool = True,
    expected_month_start: str = EXPECTED_MONTH_START,
    expected_month_end: str = EXPECTED_MONTH_END,
    expected_month_count: int | None = None,
) -> dict:
    results: dict = {}
    if failed:
        raise ReplayError(f"Unresolved / failed PDF parses ({len(failed)}): {failed}")
    if pdf_count != expected_pdf_count:
        raise ReplayError(
            f"Expected {expected_pdf_count} retained PDFs, found {pdf_count}"
        )
    results["pdf_count"] = {"expected": expected_pdf_count, "actual": pdf_count, "ok": True}

    if len(frame) != expected_row_count:
        raise ReplayError(
            f"Expected {expected_row_count} normalized sportsbook rows, got {len(frame)}"
        )
    results["row_count"] = {
        "expected": expected_row_count,
        "actual": len(frame),
        "ok": True,
    }

    if frame["handle"].notna().any():
        raise ReplayError("NJ sportsbook handle must remain NULL; synthesized handle detected")
    results["handle_all_null"] = True

    if frame["gross_revenue"].isna().any():
        raise ReplayError("gross_revenue must be non-null for every produced row")
    results["gross_revenue_all_non_null"] = True

    operators = set(frame["operator"].astype(str))
    if "Total" in operators:
        raise ReplayError("Operator labeled 'Total' must not appear")
    results["no_total_operator"] = True

    if (frame["gross_revenue"].abs() > IMPLAUSIBLE_GGR).any():
        bad = frame.loc[
            frame["gross_revenue"].abs() > IMPLAUSIBLE_GGR,
            ["operator", "period_start", "gross_revenue"],
        ]
        raise ReplayError(f"Implausible GGR > $1T detected:\n{bad}")
    results["no_implausible_ggr"] = True

    dupes = frame.duplicated(PK_COLUMNS, keep=False)
    if dupes.any():
        raise ReplayError(f"Duplicate primary keys in replay frame:\n{frame.loc[dupes]}")
    results["unique_primary_keys"] = True

    continuity = validate_monthly_continuity(
        frame,
        expected_month_start=expected_month_start,
        expected_month_end=expected_month_end,
        expected_month_count=expected_month_count,
    )
    results["monthly_continuity"] = continuity

    jan_checks: dict = {}
    if require_january_2024:
        jan = frame.loc[frame["period_start"] == JAN_2024]
        for operator, expected in JAN_2024_EXPECTED.items():
            match = jan.loc[jan["operator"] == operator]
            if match.empty:
                raise ReplayError(f"January 2024 missing operator {operator}")
            actual = float(match["gross_revenue"].sum())
            ok = abs(actual - expected) < 0.01
            jan_checks[operator] = {"expected": expected, "actual": actual, "ok": ok}
            if not ok:
                raise ReplayError(
                    f"January 2024 {operator}: expected {expected}, got {actual}"
                )
    else:
        jan_checks = {"skipped": True}
    results["january_2024"] = jan_checks

    validate_gaming_results_frame(frame)
    results["storage_validation"] = True
    return results


def period_span(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"earliest_period": None, "latest_period": None}
    return {
        "earliest_period": str(frame["period_start"].min()),
        "latest_period": str(frame["period_end"].max()),
    }


def nj_summary(conn: sqlite3.Connection) -> dict:
    frame = pd.read_sql_query(
        """
        SELECT * FROM gaming_results
        WHERE state_code = ? AND vertical = ?
        """,
        conn,
        params=(STATE_CODE, SPORTS_VERTICAL),
    )
    summary = {
        "row_count": int(len(frame)),
        "periods": period_span(frame) if not frame.empty else period_span(pd.DataFrame()),
        "handle_non_null": int(frame["handle"].notna().sum()) if not frame.empty else 0,
        "gross_revenue_non_null": int(frame["gross_revenue"].notna().sum()) if not frame.empty else 0,
        "gross_revenue_sum": float(frame["gross_revenue"].sum()) if not frame.empty else 0.0,
        "negative_gross_revenue_rows": int((frame["gross_revenue"] < 0).sum()) if not frame.empty else 0,
        "operator_count": int(frame["operator"].nunique()) if not frame.empty else 0,
    }
    return summary


def post_write_audits(conn: sqlite3.Connection, frame: pd.DataFrame) -> dict:
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise ReplayError(f"PRAGMA integrity_check failed: {integrity}")

    pk_dupes = pd.read_sql_query(
        f"""
        SELECT {", ".join(PK_COLUMNS)}, COUNT(*) AS n
        FROM gaming_results
        GROUP BY {", ".join(PK_COLUMNS)}
        HAVING n > 1
        """,
        conn,
    )
    if not pk_dupes.empty:
        raise ReplayError(f"Duplicate primary keys in candidate DB:\n{pk_dupes}")

    blank_id = pd.read_sql_query(
        """
        SELECT COUNT(*) AS n FROM gaming_results
        WHERE state_code = ? AND vertical = ?
          AND (
            TRIM(COALESCE(jurisdiction, '')) = ''
            OR TRIM(COALESCE(operator, '')) = ''
            OR TRIM(COALESCE(channel, '')) = ''
            OR TRIM(COALESCE(row_type, '')) = ''
            OR TRIM(COALESCE(period_start, '')) = ''
            OR TRIM(COALESCE(period_end, '')) = ''
            OR TRIM(COALESCE(source_sha256, '')) = ''
          )
        """,
        conn,
        params=(STATE_CODE, SPORTS_VERTICAL),
    )
    if int(blank_id.iloc[0]["n"]) != 0:
        raise ReplayError("Blank required identifiers in NJ sportsbook rows")

    periods = pd.read_sql_query(
        """
        SELECT period_start, period_end FROM gaming_results
        WHERE state_code = ? AND vertical = ?
        """,
        conn,
        params=(STATE_CODE, SPORTS_VERTICAL),
    )
    bad_period = periods.loc[periods["period_start"] > periods["period_end"]]
    if not bad_period.empty:
        raise ReplayError(f"Invalid period_start/period_end pairs:\n{bad_period.head()}")

    month_starts = sorted(pd.to_datetime(frame["period_start"]).unique())
    continuity = {
        "distinct_months": len(month_starts),
        "earliest": str(pd.Timestamp(month_starts[0]).date()) if month_starts else None,
        "latest": str(pd.Timestamp(month_starts[-1]).date()) if month_starts else None,
    }

    magnitudes = {
        "min": float(frame["gross_revenue"].min()),
        "max": float(frame["gross_revenue"].max()),
        "negatives": int((frame["gross_revenue"] < 0).sum()),
        "zeros": int((frame["gross_revenue"] == 0).sum()),
    }

    op_month = (
        frame.groupby(["period_start", "operator"], dropna=False)["gross_revenue"]
        .sum()
        .reset_index()
        .sort_values(["period_start", "operator"])
    )
    return {
        "integrity_check": integrity,
        "duplicate_pk_count": 0,
        "blank_identifier_count": 0,
        "period_validity": "ok",
        "monthly_continuity": continuity,
        "revenue_magnitude": magnitudes,
        "operator_by_month_rows": int(len(op_month)),
    }


def build_coverage_payload(
    existing: dict,
    frame: pd.DataFrame,
    pdf_count: int,
) -> dict:
    span = period_span(frame)
    return {
        "state_code": STATE_CODE,
        "vertical": SPORTS_VERTICAL,
        "status": existing.get("status") or "ok",
        "reason": (
            "Controlled parser replay of retained DGE sportsbook PDFs; "
            "handle remains unavailable from official reports"
        ),
        "official_url": existing.get("official_url"),
        "available_frequency": existing.get("available_frequency") or "monthly",
        "earliest_period": span["earliest_period"],
        "latest_period": span["latest_period"],
        "downloaded_file_count": int(pdf_count),
        "normalized_row_count": int(len(frame)),
        "last_retrieval_utc": existing.get("last_retrieval_utc"),
    }


def apply_replay_transaction(
    candidate_db: Path,
    frame: pd.DataFrame,
    coverage_payload: dict,
) -> None:
    conn = connect(candidate_db)
    try:
        ensure_schema(conn)
        validate_gaming_results_frame(frame)
        conn.execute("BEGIN")
        try:
            conn.execute(
                """
                DELETE FROM gaming_results
                WHERE state_code = ? AND vertical = ?
                """,
                (STATE_CODE, SPORTS_VERTICAL),
            )
            records = [_row_to_params(r) for r in frame[RESULT_COLUMNS].to_dict(orient="records")]
            conn.executemany(UPSERT_SQL, records)
            conn.execute(COVERAGE_UPSERT_SQL, coverage_payload)
            post_write_audits(conn, frame)
            after = nj_summary(conn)
            if after["row_count"] != len(frame):
                raise ReplayError(
                    f"Post-write row count {after['row_count']} != replay frame {len(frame)}"
                )
            if after["handle_non_null"] != 0:
                raise ReplayError("Post-write NJ sportsbook handle is not all NULL")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def _write_report(report_path: Path, report: ReplayReport) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")


def run_replay(
    *,
    root: Path | None = None,
    source_db: Path | None = None,
    candidate_db: Path | None = None,
    report_path: Path | None = None,
    expected_source_sha256: str = EXPECTED_SOURCE_SHA256,
    expected_pdf_count: int = EXPECTED_PDF_COUNT,
    expected_row_count: int = EXPECTED_ROW_COUNT,
    require_january_2024: bool = True,
    expected_month_start: str = EXPECTED_MONTH_START,
    expected_month_end: str = EXPECTED_MONTH_END,
    expected_month_count: int | None = None,
    repository_head_sha: str | None = None,
    parser_source_commit_sha: str | None = None,
) -> ReplayReport:
    root = root or project_root()
    source_db = (source_db or (root / "data" / "gaming.sqlite")).resolve()
    candidate_db = (
        candidate_db or (root / "data" / "candidates" / "gaming_nj_repaired.sqlite")
    ).resolve()
    report_path = (
        report_path or (root / "data" / "candidates" / "nj_sportsbook_replay_report.json")
    ).resolve()

    # Path validation before any directory creation, copy, or JSON write.
    validate_output_paths(source_db, candidate_db, report_path)

    source_hash_initial = sha256_file(source_db)
    assert_source_hash(source_hash_initial, expected_source_sha256, stage="initial")

    repo_head = repository_head_sha if repository_head_sha is not None else git_head_sha(root)
    parser_sha = (
        parser_source_commit_sha
        if parser_source_commit_sha is not None
        else git_file_latest_sha(root, PARSER_SOURCE_PATH)
    )
    if not repo_head:
        raise ReplayError("Unable to determine repository HEAD SHA from Git metadata")
    if not parser_sha:
        raise ReplayError(
            f"Unable to determine parser source commit SHA for {PARSER_SOURCE_PATH}"
        )

    report = ReplayReport(
        source_db=str(source_db),
        source_sha256=source_hash_initial,
        expected_source_sha256=expected_source_sha256,
        repository_head_sha=repo_head,
        parser_source_commit_sha=parser_sha,
        source_pdfs_expected=expected_pdf_count,
        source_hash_checks={
            "expected": expected_source_sha256,
            "initial": source_hash_initial,
            "initial_ok": True,
        },
    )
    paths_validated = True

    try:
        source_conn = open_readonly(source_db)
        try:
            before_scope = extract_scope_frames(source_conn)
            before_summary = nj_summary(source_conn)
            report.rows_before = before_summary["row_count"]
            report.periods_before = before_summary["periods"]

            sources = select_nj_sportsbook_sources(source_conn)
            coverage_row = source_conn.execute(
                """
                SELECT * FROM source_coverage
                WHERE state_code = ? AND vertical = ?
                """,
                (STATE_CODE, SPORTS_VERTICAL),
            ).fetchone()
            existing_coverage = dict(coverage_row) if coverage_row else {}
        finally:
            source_conn.close()

        verified = verify_source_pdfs(root, sources)
        frame, failed = parse_retained_sources(root, verified)
        report.source_pdfs_failed = failed
        report.source_pdfs_succeeded = len(verified) - len(failed)

        report.validation_results = validate_pre_write(
            frame,
            len(verified),
            failed,
            expected_pdf_count=expected_pdf_count,
            expected_row_count=expected_row_count,
            require_january_2024=require_january_2024,
            expected_month_start=expected_month_start,
            expected_month_end=expected_month_end,
            expected_month_count=expected_month_count,
        )
        report.january_2024_checks = report.validation_results.get("january_2024", {})
        report.monthly_continuity = report.validation_results.get("monthly_continuity", {})

        coverage_payload = build_coverage_payload(existing_coverage, frame, len(verified))

        source_hash_pre_copy = sha256_file(source_db)
        assert_source_hash(source_hash_pre_copy, expected_source_sha256, stage="pre-copy")
        report.source_hash_checks["pre_copy"] = source_hash_pre_copy
        report.source_hash_checks["pre_copy_ok"] = True

        candidate_db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_db, candidate_db)
        report.candidate_db = str(candidate_db)

        apply_replay_transaction(candidate_db, frame, coverage_payload)

        after_conn = connect(candidate_db)
        try:
            after_scope = extract_scope_frames(after_conn)
            after_summary = nj_summary(after_conn)
            audits = post_write_audits(
                after_conn,
                pd.read_sql_query(
                    """
                    SELECT * FROM gaming_results
                    WHERE state_code = ? AND vertical = ?
                    ORDER BY period_start, operator
                    """,
                    after_conn,
                    params=(STATE_CODE, SPORTS_VERTICAL),
                ),
            )
        finally:
            after_conn.close()

        report.rows_after = after_summary["row_count"]
        report.periods_after = after_summary["periods"]
        report.validation_results["post_write_audits"] = audits
        report.validation_results["before_summary"] = before_summary
        report.validation_results["after_summary"] = after_summary

        scope_ok = {}
        for key in before_scope:
            equal = frames_equal(before_scope[key], after_scope[key])
            scope_ok[key] = {"identical": equal}
            if not equal:
                raise ReplayError(f"Unchanged-scope comparison failed for {key}")
        report.unchanged_scope = scope_ok

        source_hash_final = sha256_file(source_db)
        assert_source_hash(source_hash_final, expected_source_sha256, stage="final")
        report.source_hash_checks["final"] = source_hash_final
        report.source_hash_checks["final_ok"] = True
        report.source_sha256 = source_hash_final

        report.candidate_sha256 = sha256_file(candidate_db)
        report.final_status = "PASS"
        _write_report(report_path, report)
    except Exception as exc:
        report.final_status = "FAIL"
        report.validation_results["error"] = str(exc)
        if paths_validated and not report_path.exists():
            _write_report(report_path, report)
        raise

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-db",
        type=Path,
        default=None,
        help="Source SQLite path (default: data/gaming.sqlite)",
    )
    parser.add_argument(
        "--candidate-db",
        type=Path,
        default=None,
        help="Candidate output path (default: data/candidates/gaming_nj_repaired.sqlite)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="JSON report path (default: data/candidates/nj_sportsbook_replay_report.json)",
    )
    parser.add_argument(
        "--expected-source-sha256",
        type=str,
        default=EXPECTED_SOURCE_SHA256,
        help="Expected source database SHA-256 (production default pinned)",
    )
    args = parser.parse_args(argv)
    report = run_replay(
        source_db=args.source_db,
        candidate_db=args.candidate_db,
        report_path=args.report,
        expected_source_sha256=args.expected_source_sha256,
    )
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.final_status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
