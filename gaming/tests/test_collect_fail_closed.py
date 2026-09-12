"""Fail-closed collector run summary: run_status vs coverage_status."""

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

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


def test_selected_state_uses_real_parser_and_temporary_database(tmp_path, monkeypatch, capsys) -> None:
    from variant_gaming import collect
    from variant_gaming.states import massachusetts
    from variant_gaming.storage import read_gaming_results, upsert_gaming_results

    _inventory(tmp_path)
    report = {"url": "https://massgaming.com/july.pdf", "filename": "july.pdf",
              "expected_year": 2026, "expected_month": 7}
    fixture = Path(__file__).parent / "fixtures" / "MA" / "MGC-Revenue-Report-July-2026.pdf"
    downloads = []

    def download(url, **kwargs):
        downloads.append(url)
        return SimpleNamespace(content=fixture.read_bytes(), raise_for_status=lambda: None)

    def unexpected(**kwargs):
        pytest.fail("An unselected collector ran")

    monkeypatch.setattr(massachusetts, "discover_all_reports", lambda **kwargs: [report])
    monkeypatch.setattr(massachusetts, "http_get", download)
    monkeypatch.setattr(collect, "COLLECTORS", {
        ("MA", "online_sports_betting"): massachusetts.collect_history,
        ("NY", "online_sports_betting"): unexpected,
    })
    summary = collect.run_all_collectors(root=tmp_path, selected=[("ma", "online_sports_betting")])
    assert downloads == [report["url"]]
    assert summary["state_code"].tolist() == ["MA"]
    assert summary.iloc[0]["run_status"] == "completed"
    assert summary.iloc[0]["returned_rows"] == 8
    assert "MA online_sports_betting" in capsys.readouterr().out
    conn = connect(default_db_path(tmp_path))
    stored = read_gaming_results(conn)
    # Seed another state's row, then prove a repeat selected update leaves it alone.
    other = stored.iloc[:1].assign(state_code="NY", jurisdiction="New York")
    upsert_gaming_results(conn, other)
    conn.close()
    collect.run_all_collectors(root=tmp_path, selected=[("MA", "online_sports_betting")])
    conn = connect(default_db_path(tmp_path))
    assert len(read_gaming_results(conn)) == 9
    conn.close()
    assert len(list((tmp_path / "data" / "raw" / "MA").rglob("*.pdf"))) == 1


@pytest.mark.parametrize("selected", [[], [("ZZ", "online_casino")]])
def test_bad_selection_is_rejected_before_database_creation(tmp_path, selected) -> None:
    from variant_gaming.collect import run_all_collectors

    _inventory(tmp_path)
    with pytest.raises(ValueError, match="registered"):
        run_all_collectors(root=tmp_path, selected=selected)
    assert not default_db_path(tmp_path).exists()
