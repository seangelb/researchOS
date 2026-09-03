"""Fail-closed collector run summary: run_status vs coverage_status."""

from pathlib import Path

import pandas as pd

from variant_gaming.coverage import record_coverage
from variant_gaming.storage import connect, default_db_path, ensure_schema


def _inventory(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "jurisdiction": "Arizona",
                "state_code": "AZ",
                "vertical": "online_sports_betting",
                "official_landing_url": "https://gaming.az.gov/resources/reports",
                "typical_format": "PDF",
                "public_granularity": "operator",
                "recommended_wave": 3,
                "status_note": "test",
            },
            {
                "jurisdiction": "Delaware",
                "state_code": "DE",
                "vertical": "online_sports_betting",
                "official_landing_url": "https://delottery.com/Sports-Lottery/Monthly-Net-Proceeds",
                "typical_format": "HTML",
                "public_granularity": "licensee",
                "recommended_wave": 2,
                "status_note": "test",
            },
        ]
    ).to_csv(tmp_path / "config" / "state_gaming_source_inventory.csv", index=False)


def test_exception_keeps_run_failed_when_prior_coverage_ok(tmp_path: Path, monkeypatch) -> None:
    from variant_gaming import collect as collect_mod

    _inventory(tmp_path)
    # Keep only AZ in the map for this unit test.
    monkeypatch.setattr(
        collect_mod,
        "COLLECTORS",
        {("AZ", "online_sports_betting"): lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))},
    )

    db = default_db_path(tmp_path)
    conn = connect(db)
    ensure_schema(conn)
    conn.close()
    record_coverage(
        state_code="AZ",
        vertical="online_sports_betting",
        status="ok",
        reason="Previously collected rows remain valid",
        official_url="https://gaming.az.gov/resources/reports",
        root=tmp_path,
        db_path=db,
    )

    summary = collect_mod.run_all_collectors(root=tmp_path, db_path=db)
    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["run_status"] == "failed"
    assert row["run_error"] == "boom"
    assert row["coverage_status"] == "ok"
    assert "Previously collected" in str(row["coverage_reason"])


def test_blocked_completion_is_not_runtime_failure(tmp_path: Path, monkeypatch) -> None:
    from variant_gaming import collect as collect_mod

    _inventory(tmp_path)

    def blocked_collector(**kwargs):
        record_coverage(
            state_code="DE",
            vertical="online_sports_betting",
            status="blocked",
            reason="No online vs retail split",
            official_url="https://delottery.com/Sports-Lottery/Monthly-Net-Proceeds",
            root=tmp_path,
            db_path=kwargs["db_path"],
        )
        return pd.DataFrame()

    monkeypatch.setattr(
        collect_mod,
        "COLLECTORS",
        {("DE", "online_sports_betting"): blocked_collector},
    )

    summary = collect_mod.run_all_collectors(root=tmp_path, db_path=default_db_path(tmp_path))
    row = summary.iloc[0]
    assert row["run_status"] == "completed"
    assert row["coverage_status"] == "blocked"
    assert row["run_error"] is None or pd.isna(row["run_error"])


def test_failure_without_prior_coverage_uses_inventory_url(tmp_path: Path, monkeypatch) -> None:
    from variant_gaming import collect as collect_mod

    _inventory(tmp_path)
    monkeypatch.setattr(
        collect_mod,
        "COLLECTORS",
        {("AZ", "online_sports_betting"): lambda **kwargs: (_ for _ in ()).throw(RuntimeError("down"))},
    )

    db = default_db_path(tmp_path)
    summary = collect_mod.run_all_collectors(root=tmp_path, db_path=db)
    assert summary.iloc[0]["run_status"] == "failed"
    conn = connect(db)
    coverage = conn.execute(
        "SELECT status, reason, official_url FROM source_coverage WHERE state_code='AZ'"
    ).fetchone()
    conn.close()
    assert coverage["status"] == "failed"
    assert coverage["official_url"] == "https://gaming.az.gov/resources/reports"
    assert "down" in coverage["reason"]
