"""Focused tests for controlled NJ sportsbook GGR replay."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from variant_gaming.common import sha256_bytes
from variant_gaming.states.new_jersey import (
    SPORTS_VERTICAL,
    build_normalized_rows,
    parse_nj_pdf,
)
from variant_gaming.storage import (
    connect,
    ensure_schema,
    read_gaming_results,
    upsert_coverage,
    upsert_gaming_results,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PDF = Path(__file__).parent / "fixtures" / "NJ" / "sample_sports_january_2024.pdf"
SCRIPT_PATH = ROOT / "scripts" / "replay_nj_sportsbook_ggr.py"


def _load_replay_module():
    spec = importlib.util.spec_from_file_location("replay_nj_sportsbook_ggr", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


replay = _load_replay_module()


def _row(**overrides):
    base = {
        "jurisdiction": "New Jersey",
        "state_code": "NJ",
        "vertical": SPORTS_VERTICAL,
        "channel": "online",
        "operator": "CorruptOp",
        "row_type": "operator",
        "period_start": "2024-01-01",
        "period_end": "2024-01-31",
        "frequency": "monthly",
        "handle": None,
        "gross_revenue": 8.0,
        "adjusted_revenue": None,
        "taxable_revenue": None,
        "net_proceeds": None,
        "tax": None,
        "reported_revenue_name": "Monthly Online Sportsbook Gross Revenue",
        "source_url": "https://example.gov/nj/jan2024.pdf",
        "source_file": "data/raw/NJ/fixture/sample_sports_january_2024.pdf",
        "source_sha256": "pending",
        "retrieved_at_utc": "2026-09-03T01:45:37.659759+00:00",
        "report_status": "ok",
    }
    base.update(overrides)
    return base


def _seed_source_db(tmp_root: Path) -> tuple[Path, pd.DataFrame, str]:
    """Build a mini source DB with one fixture PDF + casino + other-state rows."""
    pdf_rel = Path("data/raw/NJ/fixture/sample_sports_january_2024.pdf")
    pdf_dest = tmp_root / pdf_rel
    pdf_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FIXTURE_PDF, pdf_dest)
    digest = sha256_bytes(pdf_dest.read_bytes())

    source_db = tmp_root / "data" / "gaming.sqlite"
    conn = connect(source_db)
    ensure_schema(conn)

    parsed = parse_nj_pdf(pdf_dest, vertical=SPORTS_VERTICAL)
    expected = build_normalized_rows(
        parsed,
        vertical=SPORTS_VERTICAL,
        source_url="https://example.gov/nj/jan2024.pdf",
        source_file=pdf_rel.as_posix(),
        source_sha256=digest,
        retrieved_at=datetime(2026, 9, 3, 1, 45, 37, 659759, tzinfo=timezone.utc),
    )

    corrupt = pd.DataFrame(
        [
            _row(
                operator="CorruptOp",
                gross_revenue=8.0,
                source_file=pdf_rel.as_posix(),
                source_sha256=digest,
            )
        ]
    )
    casino = pd.DataFrame(
        [
            _row(
                vertical="online_casino",
                operator="CasinoBrand",
                period_start="2024-02-01",
                period_end="2024-02-29",
                gross_revenue=12345.0,
                source_file="data/raw/NJ/fixture/casino.pdf",
                source_sha256="casino-sha",
                source_url="https://example.gov/nj/casino.pdf",
            )
        ]
    )
    ny = pd.DataFrame(
        [
            _row(
                jurisdiction="New York",
                state_code="NY",
                operator="NYOp",
                gross_revenue=999.0,
                handle=1000.0,
                source_file="data/raw/NY/x.csv",
                source_sha256="ny-sha",
                source_url="https://example.gov/ny",
                retrieved_at_utc="2026-01-01T00:00:00+00:00",
            )
        ]
    )
    upsert_gaming_results(conn, corrupt)
    upsert_gaming_results(conn, casino)
    upsert_gaming_results(conn, ny)
    upsert_coverage(
        conn,
        {
            "state_code": "NJ",
            "vertical": SPORTS_VERTICAL,
            "status": "ok",
            "reason": "seed",
            "official_url": "https://example.gov/nj",
            "available_frequency": "monthly",
            "earliest_period": "2024-01-01",
            "latest_period": "2024-01-31",
            "downloaded_file_count": 1,
            "normalized_row_count": 1,
            "last_retrieval_utc": "2026-09-03T01:45:37.659759+00:00",
        },
    )
    upsert_coverage(
        conn,
        {
            "state_code": "NJ",
            "vertical": "online_casino",
            "status": "ok",
            "reason": "casino seed",
            "official_url": "https://example.gov/casino",
            "available_frequency": "monthly",
            "earliest_period": "2024-02-01",
            "latest_period": "2024-02-29",
            "downloaded_file_count": 1,
            "normalized_row_count": 1,
            "last_retrieval_utc": "2026-09-03T01:45:37.647547+00:00",
        },
    )
    upsert_coverage(
        conn,
        {
            "state_code": "NY",
            "vertical": SPORTS_VERTICAL,
            "status": "ok",
            "reason": "ny seed",
            "official_url": "https://example.gov/ny",
            "available_frequency": "weekly",
            "earliest_period": "2024-01-01",
            "latest_period": "2024-01-31",
            "downloaded_file_count": 1,
            "normalized_row_count": 1,
            "last_retrieval_utc": "2026-01-01T00:00:00+00:00",
        },
    )
    conn.close()
    return source_db, expected, digest


def _replay_kwargs(
    tmp_path: Path,
    source_db: Path,
    expected: pd.DataFrame,
    candidate: Path,
    report: Path,
) -> dict:
    return {
        "root": tmp_path,
        "source_db": source_db,
        "candidate_db": candidate,
        "report_path": report,
        "expected_source_sha256": replay.sha256_file(source_db),
        "expected_pdf_count": 1,
        "expected_row_count": len(expected),
        "require_january_2024": True,
        "expected_month_start": "2024-01-01",
        "expected_month_end": "2024-01-01",
        "expected_month_count": 1,
        "repository_head_sha": "test-repo-head",
        "parser_source_commit_sha": "test-parser-commit",
    }


def test_source_and_output_paths_cannot_be_the_same(tmp_path: Path) -> None:
    db = tmp_path / "gaming.sqlite"
    db.write_bytes(b"x")
    with pytest.raises(replay.ReplayError, match="must differ"):
        replay.validate_output_paths(db, db, tmp_path / "report.json")


def test_report_path_equal_to_source_rejected_without_db_change(tmp_path: Path) -> None:
    source_db, expected, _digest = _seed_source_db(tmp_path)
    before = source_db.read_bytes()
    candidate = tmp_path / "candidate.sqlite"
    with pytest.raises(replay.ReplayError, match="Report path must differ from source"):
        replay.run_replay(
            **_replay_kwargs(tmp_path, source_db, expected, candidate, source_db)
        )
    assert source_db.read_bytes() == before
    assert not candidate.exists()


def test_report_path_equal_to_candidate_rejected_without_db_change(tmp_path: Path) -> None:
    source_db, expected, _digest = _seed_source_db(tmp_path)
    before = source_db.read_bytes()
    candidate = tmp_path / "candidate.sqlite"
    with pytest.raises(replay.ReplayError, match="Report path must differ from candidate"):
        replay.run_replay(
            **_replay_kwargs(tmp_path, source_db, expected, candidate, candidate)
        )
    assert source_db.read_bytes() == before
    assert not candidate.exists()


def test_existing_output_is_not_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    candidate = tmp_path / "candidate.sqlite"
    report = tmp_path / "report.json"
    source.write_bytes(b"source")
    candidate.write_bytes(b"existing")
    with pytest.raises(replay.ReplayError, match="already exists"):
        replay.validate_output_paths(source, candidate, report)
    report.write_text("{}", encoding="utf-8")
    candidate.unlink()
    with pytest.raises(replay.ReplayError, match="report already exists"):
        replay.validate_output_paths(source, candidate, report)


def test_parse_validation_failure_leaves_candidate_rows_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_db, expected, digest = _seed_source_db(tmp_path)
    candidate = tmp_path / "candidate.sqlite"
    shutil.copy2(source_db, candidate)
    before = read_gaming_results(connect(candidate))

    def boom(*_args, **_kwargs):
        raise replay.ReplayError("forced post-write failure")

    monkeypatch.setattr(replay, "post_write_audits", boom)
    coverage = {
        "state_code": "NJ",
        "vertical": SPORTS_VERTICAL,
        "status": "ok",
        "reason": "replay",
        "official_url": "https://example.gov/nj",
        "available_frequency": "monthly",
        "earliest_period": "2024-01-01",
        "latest_period": "2024-01-31",
        "downloaded_file_count": 1,
        "normalized_row_count": len(expected),
        "last_retrieval_utc": "2026-09-03T01:45:37.659759+00:00",
    }
    with pytest.raises(replay.ReplayError, match="forced post-write failure"):
        replay.apply_replay_transaction(candidate, expected, coverage)

    after = read_gaming_results(connect(candidate))
    assert len(after) == len(before)
    nj = after.loc[
        (after["state_code"] == "NJ") & (after["vertical"] == SPORTS_VERTICAL)
    ]
    assert len(nj) == 1
    assert float(nj.iloc[0]["gross_revenue"]) == pytest.approx(8.0)


def test_replay_replaces_only_nj_sportsbook_and_preserves_scope(tmp_path: Path) -> None:
    source_db, expected, digest = _seed_source_db(tmp_path)
    candidate = tmp_path / "data" / "candidates" / "gaming_nj_repaired.sqlite"
    report = tmp_path / "data" / "candidates" / "report.json"

    result = replay.run_replay(
        **_replay_kwargs(tmp_path, source_db, expected, candidate, report)
    )
    assert result.final_status == "PASS"
    assert result.repository_head_sha == "test-repo-head"
    assert result.parser_source_commit_sha == "test-parser-commit"
    assert result.monthly_continuity["status"] == "PASS"

    conn = connect(candidate)
    rows = read_gaming_results(conn)
    nj_sports = rows.loc[
        (rows["state_code"] == "NJ") & (rows["vertical"] == SPORTS_VERTICAL)
    ]
    nj_casino = rows.loc[
        (rows["state_code"] == "NJ") & (rows["vertical"] == "online_casino")
    ]
    ny = rows.loc[rows["state_code"] == "NY"]

    assert len(nj_sports) == len(expected)
    assert nj_sports["handle"].isna().all()
    assert float(nj_casino.iloc[0]["gross_revenue"]) == pytest.approx(12345.0)
    assert float(ny.iloc[0]["gross_revenue"]) == pytest.approx(999.0)
    assert ny.iloc[0]["retrieved_at_utc"] == "2026-01-01T00:00:00+00:00"

    assert set(nj_sports["source_sha256"]) == {digest}
    assert set(nj_sports["retrieved_at_utc"]) == {"2026-09-03T01:45:37.659759+00:00"}
    assert set(nj_sports["source_url"]) == {"https://example.gov/nj/jan2024.pdf"}

    coverage = pd.read_sql_query("SELECT * FROM source_coverage", conn)
    nj_cov = coverage.loc[
        (coverage["state_code"] == "NJ") & (coverage["vertical"] == SPORTS_VERTICAL)
    ].iloc[0]
    assert nj_cov["last_retrieval_utc"] == "2026-09-03T01:45:37.659759+00:00"
    assert int(nj_cov["normalized_row_count"]) == len(expected)
    casino_cov = coverage.loc[
        (coverage["state_code"] == "NJ") & (coverage["vertical"] == "online_casino")
    ].iloc[0]
    assert casino_cov["reason"] == "casino seed"
    conn.close()


def test_replay_is_deterministic(tmp_path: Path) -> None:
    source_db, expected, _digest = _seed_source_db(tmp_path)
    c1 = tmp_path / "c1.sqlite"
    c2 = tmp_path / "c2.sqlite"
    r1 = tmp_path / "r1.json"
    r2 = tmp_path / "r2.json"
    replay.run_replay(**_replay_kwargs(tmp_path, source_db, expected, c1, r1))
    replay.run_replay(**_replay_kwargs(tmp_path, source_db, expected, c2, r2))
    a = read_gaming_results(connect(c1))
    b = read_gaming_results(connect(c2))
    a_nj = a.loc[(a.state_code == "NJ") & (a.vertical == SPORTS_VERTICAL)].sort_values(
        ["operator", "period_start"]
    ).reset_index(drop=True)
    b_nj = b.loc[(b.state_code == "NJ") & (b.vertical == SPORTS_VERTICAL)].sort_values(
        ["operator", "period_start"]
    ).reset_index(drop=True)
    assert list(a_nj["operator"]) == list(b_nj["operator"])
    assert list(a_nj["gross_revenue"]) == list(b_nj["gross_revenue"])
    assert list(a_nj["source_sha256"]) == list(b_nj["source_sha256"])
    assert list(a_nj["retrieved_at_utc"]) == list(b_nj["retrieved_at_utc"])


def test_conflicting_provenance_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "gaming.sqlite"
    conn = connect(db)
    ensure_schema(conn)
    rows = pd.DataFrame(
        [
            _row(source_url="https://a.example/x.pdf", source_sha256="same"),
            _row(
                operator="Other",
                source_url="https://b.example/x.pdf",
                source_sha256="same",
                source_file="data/raw/NJ/other.pdf",
            ),
        ]
    )
    upsert_gaming_results(conn, rows)
    with pytest.raises(replay.ReplayError, match="Conflicting provenance"):
        replay.select_nj_sportsbook_sources(conn)
    conn.close()


def test_duplicate_source_month_fails_before_candidate_mutation(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        [
            {
                "period_start": "2024-01-01",
                "source_sha256": "hash-a",
                "gross_revenue": 1.0,
                "operator": "A",
            },
            {
                "period_start": "2024-01-01",
                "source_sha256": "hash-b",
                "gross_revenue": 2.0,
                "operator": "B",
            },
        ]
    )
    with pytest.raises(replay.ReplayError, match="Monthly continuity validation failed"):
        replay.validate_monthly_continuity(
            frame,
            expected_month_start="2024-01-01",
            expected_month_end="2024-01-01",
            expected_month_count=1,
        )
    assert not (tmp_path / "candidate.sqlite").exists()


def test_missing_month_fails_before_candidate_mutation(tmp_path: Path) -> None:
    source_db, expected, _digest = _seed_source_db(tmp_path)
    before = source_db.read_bytes()
    candidate = tmp_path / "candidate.sqlite"
    report = tmp_path / "report.json"
    kwargs = _replay_kwargs(tmp_path, source_db, expected, candidate, report)
    kwargs["expected_month_start"] = "2024-01-01"
    kwargs["expected_month_end"] = "2024-02-01"
    kwargs["expected_month_count"] = 2
    with pytest.raises(replay.ReplayError, match="Monthly continuity validation failed"):
        replay.run_replay(**kwargs)
    assert source_db.read_bytes() == before
    assert not candidate.exists()
