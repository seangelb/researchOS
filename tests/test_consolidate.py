"""Consolidation prefers official totals and never mixes revenue definitions."""

from pathlib import Path

import pandas as pd

from variant_gaming.consolidate import (
    build_operator_revenue,
    build_state_period_revenue,
    export_all,
)
from variant_gaming.storage import (
    connect,
    default_db_path,
    ensure_schema,
    upsert_gaming_results,
)


def _row(**overrides):
    base = {
        "jurisdiction": "Testland",
        "state_code": "ZZ",
        "vertical": "online_sports_betting",
        "channel": "online",
        "operator": "OpA",
        "row_type": "operator",
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
        "frequency": "monthly",
        "handle": 100.0,
        "gross_revenue": 10.0,
        "adjusted_revenue": None,
        "taxable_revenue": None,
        "net_proceeds": None,
        "tax": 1.0,
        "reported_revenue_name": "GGR",
        "source_url": "https://example.gov",
        "source_file": "a.csv",
        "source_sha256": "sha-a",
        "retrieved_at_utc": "2026-02-01T00:00:00+00:00",
        "report_status": "ok",
    }
    base.update(overrides)
    return base


def test_prefers_official_statewide_total() -> None:
    results = pd.DataFrame(
        [
            _row(operator="OpA", gross_revenue=10.0),
            _row(operator="OpB", gross_revenue=15.0, source_sha256="sha-b"),
            _row(
                operator="STATEWIDE",
                row_type="official_statewide_total",
                gross_revenue=99.0,
                source_sha256="sha-s",
            ),
        ]
    )
    period = build_state_period_revenue(results)
    assert len(period) == 1
    assert period.iloc[0]["aggregation_source"] == "official_statewide_total"
    assert period.iloc[0]["revenue"] == 99.0


def test_operator_sum_when_no_official_total() -> None:
    results = pd.DataFrame(
        [
            _row(operator="OpA", gross_revenue=10.0),
            _row(operator="OpB", gross_revenue=15.0, source_sha256="sha-b"),
        ]
    )
    period = build_state_period_revenue(results)
    assert period.iloc[0]["aggregation_source"] == "operator_sum"
    assert period.iloc[0]["revenue"] == 25.0


def test_does_not_silently_combine_revenue_definitions() -> None:
    results = pd.DataFrame(
        [
            _row(operator="OpA", gross_revenue=10.0, adjusted_revenue=None),
            _row(
                operator="OpB",
                gross_revenue=None,
                adjusted_revenue=20.0,
                reported_revenue_name="State AGR",
                source_sha256="sha-b",
            ),
        ]
    )
    period = build_state_period_revenue(results)
    assert set(period["revenue_basis"]) == {"GGR", "AGR"}
    assert set(period["aggregation_source"]) == {"operator_sum_split_by_basis"}


def test_repeated_builder_has_no_duplicate_business_keys() -> None:
    results = pd.DataFrame(
        [
            _row(),
            _row(retrieved_at_utc="2026-03-01T00:00:00+00:00", source_sha256="sha-later"),
        ]
    )
    first = build_operator_revenue(results)
    second = build_operator_revenue(results)
    keys = ["state_code", "vertical", "channel", "operator", "period_start", "period_end"]
    assert first.duplicated(keys).sum() == 0
    assert len(first) == len(second) == 1


def test_export_csv_matches_sqlite_table(tmp_path: Path, monkeypatch) -> None:
    from variant_gaming import consolidate

    monkeypatch.setattr(consolidate, "project_root", lambda: tmp_path)
    (tmp_path / "config").mkdir()
    pd.DataFrame(
        [
            {
                "jurisdiction": "Testland",
                "state_code": "ZZ",
                "vertical": "online_sports_betting",
                "official_landing_url": "https://example.gov",
                "typical_format": "CSV",
                "public_granularity": "operator",
                "recommended_wave": 1,
                "status_note": "test",
            }
        ]
    ).to_csv(tmp_path / "config" / "state_gaming_source_inventory.csv", index=False)

    conn = connect(default_db_path(tmp_path))
    ensure_schema(conn)
    upsert_gaming_results(conn, pd.DataFrame([_row()]))
    conn.close()

    paths = export_all(tmp_path)
    exported = pd.read_csv(paths["gaming_results"])
    conn = connect(default_db_path(tmp_path))
    sqlite_df = pd.read_sql_query("SELECT * FROM gaming_results", conn)
    conn.close()
    def _norm(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        for col in out.columns:
            out[col] = out[col].where(out[col].notna(), None)
            out[col] = out[col].astype(str)
        return out.sort_values(list(out.columns)).reset_index(drop=True)

    assert list(exported.columns) == list(sqlite_df.columns)
    pd.testing.assert_frame_equal(_norm(exported), _norm(sqlite_df))

    rebuilt = build_state_period_revenue(sqlite_df)
    exported_period = pd.read_csv(paths["state_period_revenue"])
    pd.testing.assert_frame_equal(_norm(exported_period), _norm(rebuilt))
    rebuilt_ops = build_operator_revenue(sqlite_df)
    exported_ops = pd.read_csv(paths["operator_revenue"])
    pd.testing.assert_frame_equal(_norm(exported_ops), _norm(rebuilt_ops))
