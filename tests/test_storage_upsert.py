"""Storage upsert must not wipe other states and must not duplicate."""

from pathlib import Path

import pandas as pd

from variant_gaming.storage import (
    RESULT_COLUMNS,
    connect,
    count_by_state,
    ensure_schema,
    migrate_legacy_table,
    read_gaming_results,
    upsert_gaming_results,
)


def _row(**overrides):
    base = {
        "jurisdiction": "Test",
        "state_code": "ZZ",
        "vertical": "online_sports_betting",
        "channel": "online",
        "operator": "OpA",
        "row_type": "operator",
        "period_start": "2024-01-01",
        "period_end": "2024-01-31",
        "frequency": "monthly",
        "handle": 100.0,
        "gross_revenue": None,
        "adjusted_revenue": 10.0,
        "taxable_revenue": None,
        "net_proceeds": None,
        "tax": 1.0,
        "reported_revenue_name": "State AGR",
        "source_url": "https://example.gov",
        "source_file": "a.csv",
        "source_sha256": "abc",
        "retrieved_at_utc": "2024-02-01T00:00:00+00:00",
        "report_status": "ok",
    }
    base.update(overrides)
    return base


def test_upsert_is_idempotent_and_preserves_other_states(tmp_path: Path) -> None:
    db = tmp_path / "gaming.sqlite"
    conn = connect(db)
    migrate_legacy_table(conn)
    ensure_schema(conn)

    ny = pd.DataFrame([_row(state_code="NY", jurisdiction="New York", source_sha256="ny1")])
    il = pd.DataFrame([_row(state_code="IL", jurisdiction="Illinois", source_sha256="il1")])
    upsert_gaming_results(conn, ny)
    upsert_gaming_results(conn, il)
    assert len(read_gaming_results(conn)) == 2

    # Re-upsert IL only — NY must remain untouched
    upsert_gaming_results(conn, il)
    counts = count_by_state(conn)
    assert int(counts.loc[counts["state_code"] == "NY", "n"].sum()) == 1
    assert int(counts.loc[counts["state_code"] == "IL", "n"].sum()) == 1
    assert len(read_gaming_results(conn)) == 2


def test_upsert_rejects_disallowed_row_type(tmp_path: Path) -> None:
    db = tmp_path / "gaming.sqlite"
    conn = connect(db)
    ensure_schema(conn)
    bad = pd.DataFrame([_row(row_type="derived_statewide_total")])
    try:
        upsert_gaming_results(conn, bad)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "row_type" in str(exc)
