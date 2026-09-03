"""Focused parser tests for Michigan MGCB wide Excel workbooks."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from variant_gaming.states.michigan import (
    CASINO_VERTICAL,
    SPORTS_REPORTED_REVENUE_NAME,
    SPORTS_VERTICAL,
    build_normalized_rows,
    discover_michigan_workbook_links,
    parse_michigan_workbook,
)

FIXTURES = Path(__file__).parent / "fixtures" / "MI"


def test_parse_sports_reshapes_wide() -> None:
    path = FIXTURES / "sample_internet_sports_betting_2025.xlsx"
    parsed = parse_michigan_workbook(
        path.read_bytes(), vertical=SPORTS_VERTICAL, year_hint=2025
    )
    assert set(parsed["period_start"]) == {"2025-01-01", "2025-02-01"}
    op = parsed[(parsed["row_type"] == "operator") & (parsed["period_start"] == "2025-01-01")]
    assert len(op) == 1
    assert op.iloc[0]["handle"] == pytest.approx(1000.0)
    assert op.iloc[0]["gross_revenue"] == pytest.approx(200.0)
    assert op.iloc[0]["adjusted_revenue"] == pytest.approx(150.0)
    assert op.iloc[0]["tax"] == pytest.approx(10.0)
    statewide = parsed[parsed["operator"] == "STATEWIDE"]
    assert not statewide.empty


def test_parse_casino_maps_gross_and_adjusted() -> None:
    path = FIXTURES / "sample_internet_gaming_2025.xlsx"
    parsed = parse_michigan_workbook(
        path.read_bytes(), vertical=CASINO_VERTICAL, year_hint=2025
    )
    jan = parsed[(parsed["row_type"] == "operator") & (parsed["period_start"] == "2025-01-01")].iloc[0]
    assert jan["handle"] is None or (jan["handle"] != jan["handle"])  # NaN/None
    assert jan["gross_revenue"] == pytest.approx(5000.0)
    assert jan["adjusted_revenue"] == pytest.approx(4500.0)
    assert jan["tax"] == pytest.approx(800.0)
    rows = build_normalized_rows(
        parsed,
        vertical=CASINO_VERTICAL,
        source_url="https://example.test/ig.xlsx",
        source_file="data/raw/MI/x.xlsx",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert (rows["channel"] == "online").all()
    assert (rows["vertical"] == CASINO_VERTICAL).all()


def test_sports_normalized_reported_revenue_name() -> None:
    path = FIXTURES / "sample_internet_sports_betting_2025.xlsx"
    parsed = parse_michigan_workbook(
        path.read_bytes(), vertical=SPORTS_VERTICAL, year_hint=2025
    )
    rows = build_normalized_rows(
        parsed,
        vertical=SPORTS_VERTICAL,
        source_url="https://example.test/sb.xlsx",
        source_file="data/raw/MI/x.xlsx",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert (rows["reported_revenue_name"] == SPORTS_REPORTED_REVENUE_NAME).all()


def test_discover_filters_sports_vs_gaming() -> None:
    html = """
    <a href="/media/Internet-Sports-Betting---2025.xlsx">2025 Internet Sports Betting Revenues and Taxes Excel</a>
    <a href="/media/Internet-Gaming---2025.xlsx">2025 Internet Gaming Revenues and Taxes Excel</a>
    <a href="/media/Retail-Sports-Betting-2025.xlsx">2025 Retail Sports Betting Revenues and Taxes Excel</a>
    """
    sports = discover_michigan_workbook_links(html, vertical=SPORTS_VERTICAL)
    gaming = discover_michigan_workbook_links(html, vertical=CASINO_VERTICAL)
    assert len(sports) == 1
    assert "Sports-Betting" in sports[0]["filename"]
    assert len(gaming) == 1
    assert "Gaming" in gaming[0]["filename"]
