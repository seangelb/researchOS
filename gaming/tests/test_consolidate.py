"""Consolidation prefers official totals and never mixes revenue definitions."""

from pathlib import Path

import pandas as pd
import pytest

from variant_gaming.consolidate import (
    build_operator_revenue,
    build_state_period_revenue,
    dedupe_latest_observation,
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
    assert period.iloc[0]["completeness"] == "operator_coverage_unverified"


@pytest.mark.parametrize("metric", [None, "gross_revenue"])
def test_missing_operator_revenue_is_not_a_state_total(metric) -> None:
    rows = pd.DataFrame([_row(), _row(operator="OpB", gross_revenue=None)])
    period = build_state_period_revenue(rows, metric=metric).iloc[0]
    assert pd.isna(period["revenue"])
    assert period["completeness"] == "missing_values"


def test_present_values_do_not_prove_complete_operator_coverage() -> None:
    # No evidence says OpA is the whole market, even though its value is known.
    period = build_state_period_revenue(pd.DataFrame([_row()])).iloc[0]
    assert period["revenue"] == 10
    assert period["completeness"] == "operator_coverage_unverified"


@pytest.mark.parametrize(
    "state,status",
    [
        ("IL", "derived_from_operator_sum"),
        ("IN", "derived_from_operator_sum"),
        ("IN", "ok"),  # two early IN months used bare ok for calculated totals
    ],
)
def test_legacy_derived_totals_are_not_treated_as_official(state, status) -> None:
    rows = pd.DataFrame([
        _row(state_code=state, gross_revenue=10),
        _row(state_code=state, operator="STATEWIDE", row_type="official_statewide_total",
             gross_revenue=999, report_status=status),
    ])
    period = build_state_period_revenue(rows).iloc[0]
    assert period["revenue"] == 10
    assert period["aggregation_source"] == "operator_sum"
    assert period["completeness"] == "operator_coverage_unverified"


@pytest.mark.parametrize("status", ["printed_statewide_total", "reconciled_printed_total", "reported_printed_total"])
def test_printed_official_provenance_accepted_for_indiana(status) -> None:
    rows = pd.DataFrame([
        _row(state_code="IN", gross_revenue=10),
        _row(
            state_code="IN",
            operator="STATEWIDE",
            row_type="official_statewide_total",
            gross_revenue=99,
            report_status=status,
            source_sha256="printed",
        ),
    ])
    period = build_state_period_revenue(rows).iloc[0]
    assert period["revenue"] == 99
    assert period["aggregation_source"] == "official_statewide_total"
    assert period["completeness"] == "reported_total"


def test_state_code_alone_does_not_reject_printed_total() -> None:
    # IL with explicit printed provenance must not be rejected solely for being IL.
    rows = pd.DataFrame([
        _row(state_code="IL", gross_revenue=10),
        _row(
            state_code="IL",
            operator="STATEWIDE",
            row_type="official_statewide_total",
            gross_revenue=55,
            report_status="printed_statewide_total",
            source_sha256="printed-il",
        ),
    ])
    period = build_state_period_revenue(rows).iloc[0]
    assert period["revenue"] == 55
    assert period["aggregation_source"] == "official_statewide_total"
    assert period["completeness"] == "reported_total"


def test_explicit_metric_preserves_negative_zero_and_missing_values() -> None:
    rows = pd.DataFrame([_row(gross_revenue=-5, taxable_revenue=0)])
    assert build_state_period_revenue(rows, metric="gross_revenue").iloc[0]["revenue"] == -5
    assert build_state_period_revenue(rows, metric="taxable_revenue").iloc[0]["revenue"] == 0
    assert pd.isna(build_state_period_revenue(rows, metric="adjusted_revenue").iloc[0]["revenue"])


def test_conflicting_reports_do_not_choose_by_download_order() -> None:
    rows = pd.DataFrame([
        _row(gross_revenue=10, source_sha256="old", source_file="old.csv",
             retrieved_at_utc="2026-04-01T00:00:00+00:00"),
        _row(gross_revenue=20, source_sha256="revision", source_file="revision.csv"),
    ])
    forward = dedupe_latest_observation(rows)
    backward = dedupe_latest_observation(rows.iloc[::-1])
    pd.testing.assert_frame_equal(forward, backward)
    assert forward.iloc[0]["observation_status"] == "conflicting_sources"
    assert pd.isna(forward.iloc[0]["gross_revenue"])
    period = build_state_period_revenue(rows).iloc[0]
    assert period["completeness"] == "conflicting_sources"
    assert "old.csv" in period["source_file"] and "revision.csv" in period["source_file"]


def test_penny_and_partial_money_conflicts_keep_agreeing_fields() -> None:
    rows = pd.DataFrame([
        _row(operator="STATEWIDE", row_type="official_statewide_total",
             gross_revenue=100.0, taxable_revenue=50.00, tax=5.0,
             source_sha256="a", source_file="a.pdf"),
        _row(operator="STATEWIDE", row_type="official_statewide_total",
             gross_revenue=100.0, taxable_revenue=50.01, tax=None,
             source_sha256="b", source_file="b.pdf"),
    ])
    resolved = dedupe_latest_observation(rows).iloc[0]
    assert resolved["observation_status"] == "conflicting_sources"
    assert resolved["gross_revenue"] == 100.0
    assert resolved["taxable_revenue"] == 50.0
    assert pd.isna(resolved["tax"])
    period = build_state_period_revenue(rows, metric="gross_revenue").iloc[0]
    assert period["revenue"] == 100.0
    assert period["completeness"] == "conflicting_sources"


def test_equal_reports_retain_all_sources_without_double_counting() -> None:
    rows = pd.DataFrame([_row(), _row(source_sha256="copy", source_file="copy.csv")])
    period = build_state_period_revenue(rows).iloc[0]
    assert period["revenue"] == 10
    assert period["operator_count"] == 1
    assert "a.csv" in period["source_file"] and "copy.csv" in period["source_file"]


def test_conflicting_official_totals_do_not_fall_back_to_operator_sum() -> None:
    rows = pd.DataFrame([
        _row(),
        _row(operator="STATEWIDE", row_type="official_statewide_total", gross_revenue=50),
        _row(operator="STATEWIDE", row_type="official_statewide_total", gross_revenue=60, source_sha256="revision"),
    ])
    period = build_state_period_revenue(rows).iloc[0]
    assert pd.isna(period["revenue"])
    assert period["completeness"] == "conflicting_sources"


def test_state_view_keeps_weekly_frequency_and_missing_handle() -> None:
    rows = pd.DataFrame([_row(frequency="weekly", handle=None)])
    period = build_state_period_revenue(rows, metric="gross_revenue").iloc[0]
    assert period["frequency"] == "weekly"
    assert period["revenue"] == 10
    assert pd.isna(period["handle"])


def test_empty_analysis_has_usable_columns() -> None:
    rows = pd.DataFrame([_row()]).iloc[:0]
    period = build_state_period_revenue(rows, metric="gross_revenue")
    assert period.empty
    assert {"revenue", "source_file", "completeness", "period_start"}.issubset(period.columns)


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
