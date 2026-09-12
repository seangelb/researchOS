"""Actual KY image transcription failures, with all I/O kept offline."""

from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pandas as pd
import pytest

from variant_gaming.states import kentucky


def test_six_complete_tables_keep_native_metrics_and_identities():
    rows = kentucky.load_transcriptions()
    assert len(rows) == 57
    assert rows.groupby("period_start").size().to_dict() == {
        "2025-04-01": 9, "2025-05-01": 9, "2025-06-01": 9,
        "2026-04-01": 10, "2026-05-01": 10, "2026-06-01": 10,
    }
    assert "gross_revenue" not in rows
    assert set(rows.channel) == {"online"}
    assert rows.loc[rows.operator == "Fanduel", "native_licensee"].eq("Turfway Park").all()
    assert rows.loc[rows.operator == "Circa", "tax"].eq(0).all()
    assert sorted(rows.loc[rows.adjusted_revenue < 0, "adjusted_revenue"]) == [-32467, -18891]
    june = rows[(rows.period_start == "2026-06-01") & (rows.operator == "STATEWIDE")].iloc[0]
    assert june.tax == 2728648  # Original printed tax, including the inspected final 648.
    assert june.adjusted_revenue == 19129514
    assert june.handle == 219305572


@pytest.mark.parametrize("column,value", [
    ("channel", "combined"), ("frequency", "annual"), ("physical_pdf_page", 1),
    ("printed_page", 1), ("source_url", "https://example.com/other.pdf"),
    ("period_end", "2025-05-31"), ("rounding_unit_usd", 100),
    ("report_status", "analyst_approved"), ("reported_revenue_name", "GGR"),
    ("native_licensee", "Turfway Park"), ("native_operator", "Other"),
    ("handle", None), ("handle", float("inf")), ("handle", 0.5),
    ("row_type", "unknown"), ("source_sha256", "wrong"),
])
def test_wrong_metadata_or_missing_money_fails_closed(column, value):
    rows = kentucky.load_transcriptions().copy()
    rows[column] = rows[column].astype(object)
    rows.loc[0, column] = value
    with pytest.raises(ValueError, match="Kentucky"):
        kentucky.validate_transcriptions(rows)


def test_missing_competitor_or_duplicate_row_is_rejected():
    rows = kentucky.load_transcriptions()
    with pytest.raises(ValueError, match="census"):
        kentucky.validate_transcriptions(rows.drop(index=0))
    with pytest.raises(ValueError, match="duplicate"):
        kentucky.validate_transcriptions(pd.concat([rows, rows.iloc[[0]]]))


def test_accounting_identity_rejects_bad_transcription():
    rows = kentucky.load_transcriptions().copy()
    rows.loc[0, "adjusted_revenue"] += 10
    with pytest.raises(ValueError, match="AGR identity"):
        kentucky.validate_transcriptions(rows)


def test_balanced_operator_error_still_fails_printed_total_check():
    rows = kentucky.load_transcriptions().copy()
    rows.loc[0, ["handle", "adjusted_revenue"]] += 10
    with pytest.raises(ValueError, match="operator sum"):
        kentucky.validate_transcriptions(rows)


def test_tax_total_misreading_is_rejected_without_forcing_tax_from_agr():
    rows = kentucky.load_transcriptions().copy()
    rows.loc[(rows.period_start == "2026-06-01") & (rows.operator == "STATEWIDE"), "tax"] = 2728618
    with pytest.raises(ValueError, match="operator sum"):
        kentucky.validate_transcriptions(rows)


def test_unknown_or_changed_source_bytes_never_reuse_transcription(tmp_path):
    unknown = tmp_path / "changed_packet.pdf"
    unknown.write_bytes(b"different packet bytes")
    with pytest.raises(ValueError, match="source bytes changed"):
        kentucky.parse_report(unknown)


def test_parser_selects_only_matching_packet_rows(tmp_path, monkeypatch):
    payload = tmp_path / "packet.pdf"
    payload.write_bytes(b"test packet; hash lookup is mocked only in this offline routing test")
    digest = next(iter(kentucky.PACKETS))
    monkeypatch.setattr(kentucky, "sha256_bytes", lambda content: digest)
    rows = kentucky.parse_report(payload)
    assert len(rows) == 27
    assert rows.source_sha256.eq(digest).all()
    assert rows.period_start.str.startswith("2025").all()


def test_legacy_hash_path_still_returns_original_two_rows(tmp_path, monkeypatch):
    payload = tmp_path / "legacy.pdf"
    payload.write_bytes(b"legacy routing test")
    monkeypatch.setattr(kentucky, "sha256_bytes", lambda content: kentucky.LEGACY_SHA256)
    legacy = pd.read_csv(Path(__file__).parents[1] / "config/transcribed_report_rows.csv")
    legacy = legacy[legacy.source_sha256 == kentucky.LEGACY_SHA256]
    monkeypatch.setattr(kentucky, "read_transcribed_report", lambda path: legacy)
    assert len(kentucky.parse_report(payload)) == 2


def test_collector_keeps_legacy_and_three_explicit_packet_urls(tmp_path, monkeypatch):
    collector = Mock(return_value=pd.DataFrame())
    monkeypatch.setattr(kentucky, "collect_reports", collector)
    kentucky.collect_history(root=tmp_path, db_path=tmp_path / "fresh.sqlite")
    kwargs = collector.call_args.kwargs
    assert kwargs["urls"] == [kentucky.REPORT_URL, *(packet[0] for packet in kentucky.PACKETS.values())]
    assert kwargs["root"] == tmp_path
    assert not (tmp_path / "fresh.sqlite").exists()


def test_successful_bounded_collection_still_reports_partial_history(tmp_path, monkeypatch):
    from variant_gaming.storage import connect, ensure_schema, upsert_coverage

    database = tmp_path / "fresh.sqlite"
    connection = connect(database)
    ensure_schema(connection)
    upsert_coverage(connection, {
        "state_code": "KY", "vertical": "online_sports_betting", "status": "ok",
        "reason": "4 parsed reports; 0 failures.", "official_url": kentucky.LANDING_URL,
        "available_frequency": "monthly", "earliest_period": "2025-03-01",
        "latest_period": "2026-06-30", "downloaded_file_count": 4,
        "normalized_row_count": 59, "last_retrieval_utc": "2026-09-12T00:00:00+00:00",
    })
    connection.close()
    monkeypatch.setattr(kentucky, "collect_reports", lambda **kwargs: pd.DataFrame({"operator": ["Fanduel"]}))
    kentucky.collect_history(root=tmp_path, db_path=database)
    connection = sqlite3.connect(database)
    status, reason = connection.execute("SELECT status, reason FROM source_coverage").fetchone()
    connection.close()
    assert status == "partial"
    assert "4 parsed reports; 0 failures" in reason
    assert "not analyst approved" in reason
    assert "other months and newer packets need source review" in reason
