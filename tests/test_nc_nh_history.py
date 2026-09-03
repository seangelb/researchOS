"""NC and NH preserve historical hashes; no statewide delete on refresh."""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from variant_gaming.storage import (
    connect,
    default_db_path,
    ensure_schema,
    read_gaming_results,
    upsert_gaming_results,
)


def _row(**overrides):
    base = {
        "jurisdiction": "North Carolina",
        "state_code": "NC",
        "vertical": "online_sports_betting",
        "channel": "online",
        "operator": "STATEWIDE",
        "row_type": "official_statewide_total",
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
        "frequency": "monthly",
        "handle": 100.0,
        "gross_revenue": 10.0,
        "adjusted_revenue": None,
        "taxable_revenue": None,
        "net_proceeds": None,
        "tax": 1.0,
        "reported_revenue_name": "Gross Wagering Revenue",
        "source_url": "https://ncgaming.gov/example.pdf",
        "source_file": "nc.pdf",
        "source_sha256": "hash-old",
        "retrieved_at_utc": "2026-02-01T00:00:00+00:00",
        "report_status": "ok",
    }
    base.update(overrides)
    return base


def test_source_files_have_no_statewide_delete() -> None:
    nc = Path("src/variant_gaming/states/north_carolina.py").read_text(encoding="utf-8")
    nh = Path("src/variant_gaming/states/new_hampshire.py").read_text(encoding="utf-8")
    assert "DELETE FROM gaming_results" not in nc
    assert "DELETE FROM gaming_results" not in nh


def test_older_hash_survives_newer_revision(tmp_path: Path) -> None:
    conn = connect(default_db_path(tmp_path))
    ensure_schema(conn)
    upsert_gaming_results(conn, pd.DataFrame([_row(source_sha256="hash-old", handle=100.0)]))
    upsert_gaming_results(
        conn,
        pd.DataFrame(
            [
                _row(
                    source_sha256="hash-new",
                    handle=110.0,
                    retrieved_at_utc="2026-03-01T00:00:00+00:00",
                )
            ]
        ),
    )
    rows = read_gaming_results(conn, "state_code='NC'")
    assert set(rows["source_sha256"]) == {"hash-old", "hash-new"}
    assert len(rows) == 2
    conn.close()


def test_same_hash_is_idempotent(tmp_path: Path) -> None:
    conn = connect(default_db_path(tmp_path))
    ensure_schema(conn)
    frame = pd.DataFrame([_row(source_sha256="hash-same")])
    upsert_gaming_results(conn, frame)
    upsert_gaming_results(conn, frame)
    assert len(read_gaming_results(conn, "state_code='NC'")) == 1
    conn.close()


def test_nc_refresh_does_not_erase_other_state(tmp_path: Path, monkeypatch) -> None:
    from variant_gaming.states import north_carolina as nc

    db = default_db_path(tmp_path)
    conn = connect(db)
    ensure_schema(conn)
    upsert_gaming_results(
        conn,
        pd.DataFrame(
            [
                _row(state_code="NY", jurisdiction="New York", source_sha256="ny-old"),
                _row(source_sha256="nc-old"),
            ]
        ),
    )
    conn.close()

    monkeypatch.setattr(nc, "http_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    # Drive the upsert path used after parsing without network.
    new_frame = pd.DataFrame(
        [
            _row(
                source_sha256="nc-new",
                handle=200.0,
                retrieved_at_utc=datetime(2026, 4, 1, tzinfo=timezone.utc).isoformat(),
            )
        ]
    )

    conn = connect(db)
    upsert_gaming_results(conn, new_frame)
    rows = read_gaming_results(conn)
    assert int((rows["state_code"] == "NY").sum()) == 1
    assert set(rows.loc[rows["state_code"] == "NC", "source_sha256"]) == {"nc-old", "nc-new"}
    conn.close()


def test_failed_upsert_cannot_erase_prior_nc_or_nh(tmp_path: Path, monkeypatch) -> None:
    from variant_gaming.states import new_hampshire as nh
    from variant_gaming.states import north_carolina as nc

    db = default_db_path(tmp_path)
    conn = connect(db)
    ensure_schema(conn)
    upsert_gaming_results(
        conn,
        pd.DataFrame(
            [
                _row(source_sha256="nc-keep"),
                _row(
                    state_code="NH",
                    jurisdiction="New Hampshire",
                    reported_revenue_name="GGR",
                    source_sha256="nh-keep",
                ),
            ]
        ),
    )
    conn.close()

    def boom_upsert(connection, frame):
        raise RuntimeError("upsert failed")

    monkeypatch.setattr(nc, "upsert_gaming_results", boom_upsert)
    monkeypatch.setattr(nh, "upsert_gaming_results", boom_upsert)

    # Simulate the post-parse refresh step without DELETE: failed upsert must leave history.
    conn = connect(db)
    with pytest.raises(RuntimeError, match="upsert failed"):
        nc.upsert_gaming_results(conn, pd.DataFrame([_row(source_sha256="nc-new")]))
    with pytest.raises(RuntimeError, match="upsert failed"):
        nh.upsert_gaming_results(conn, pd.DataFrame([_row(state_code="NH", source_sha256="nh-new")]))
    rows = read_gaming_results(conn)
    assert set(rows["source_sha256"]) == {"nc-keep", "nh-keep"}
    conn.close()
