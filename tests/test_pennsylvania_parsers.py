"""Focused parser tests for Pennsylvania PGCB sports and interactive Excels."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from variant_gaming.common import utc_now
from variant_gaming.states.pennsylvania import (
    CASINO_REPORTED_REVENUE,
    SPORTS_REPORTED_REVENUE,
    build_casino_normalized,
    build_sports_normalized,
    classify_pa_excel_link,
    discover_fy_workbook_links,
    parse_interactive_workbook,
    parse_sports_workbook,
)

FIXTURES = Path(__file__).parent / "fixtures" / "PA"


@pytest.fixture(scope="module")
def sports_bytes() -> bytes:
    path = FIXTURES / "sample_sports_wagering.xlsx"
    assert path.exists()
    return path.read_bytes()


@pytest.fixture(scope="module")
def interactive_bytes() -> bytes:
    path = FIXTURES / "sample_interactive_gaming.xlsx"
    assert path.exists()
    return path.read_bytes()


def test_classify_excel_links() -> None:
    assert classify_pa_excel_link("/FY26-27 Monthly Sports Wagering Report Summary.xlsx") == "sports"
    assert classify_pa_excel_link("/FY26-27 Monthly Interactive Gaming Report Summary.xlsx") == "interactive"
    assert classify_pa_excel_link("/Fantasy Contest Monthly.xlsx") is None


def test_discover_fy_links_html() -> None:
    html = """
    <a href="/sites/default/files/s.xlsx">DOWNLOAD EXCEL</a>
    <a href="/sites/x/FY26 Sports Wagering Report.xlsx">DOWNLOAD EXCEL</a>
    <a href="/sites/x/FY26 Interactive Gaming Report.xlsx">DOWNLOAD EXCEL</a>
    """
    found = discover_fy_workbook_links(html, "https://gamingcontrolboard.pa.gov/")
    assert "sports" in found and "interactive" in found


def test_parse_sports_online_only(sports_bytes: bytes) -> None:
    parsed = parse_sports_workbook(sports_bytes)
    assert not parsed.empty
    # Retail must not appear as its own channel rows; only Online Sports Wagering.
    hollywood = parsed[parsed["operator"] == "HOLLYWOOD CASINO"]
    assert len(hollywood) == 2  # July + August
    july = hollywood[hollywood["period_start"] == "2025-07-01"].iloc[0]
    assert july["handle"] == pytest.approx(900)
    assert july["taxable_revenue"] == pytest.approx(90)
    assert july["tax"] == pytest.approx(32.4)  # 30.6 + 1.8
    statewide = parsed[parsed["operator"] == "STATEWIDE"]
    assert not statewide.empty
    assert (statewide["row_type"] == "official_statewide_total").all()


def test_sports_normalize_maps_taxable(sports_bytes: bytes) -> None:
    parsed = parse_sports_workbook(sports_bytes)
    rows = build_sports_normalized(
        parsed,
        source_url="https://gamingcontrolboard.pa.gov/example.xlsx",
        source_file="data/raw/PA/fixture.xlsx",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert (rows["channel"] == "online").all()
    assert (rows["frequency"] == "monthly").all()
    assert (rows["vertical"] == "online_sports_betting").all()
    assert (rows["reported_revenue_name"] == SPORTS_REPORTED_REVENUE).all()
    assert rows["taxable_revenue"].notna().all()


def test_parse_interactive_sums_products(interactive_bytes: bytes) -> None:
    parsed = parse_interactive_workbook(interactive_bytes)
    penn = parsed[parsed["operator"] == "PENN NATIONAL"]
    july = penn[penn["period_start"] == "2025-07-01"].iloc[0]
    # Gross: slots 1000 + banking 200 + poker 50
    assert july["gross_revenue"] == pytest.approx(1250)
    # Handle: slots + banking wagers (poker has no wagers line)
    assert july["handle"] == pytest.approx(12_000)
    # Tax: 340 + 28 + 7
    assert july["tax"] == pytest.approx(375)
    assert len(penn) == 2
    assert (parsed["operator"] == "STATEWIDE").any()
    statewide = parsed[parsed["operator"] == "STATEWIDE"]
    assert not statewide.empty


def test_casino_normalize(interactive_bytes: bytes) -> None:
    parsed = parse_interactive_workbook(interactive_bytes)
    rows = build_casino_normalized(
        parsed,
        source_url="https://gamingcontrolboard.pa.gov/ig.xlsx",
        source_file="data/raw/PA/ig.xlsx",
        source_sha256="def",
        retrieved_at=utc_now(),
    )
    assert (rows["vertical"] == "online_casino").all()
    assert (rows["channel"] == "online").all()
    assert (rows["reported_revenue_name"] == CASINO_REPORTED_REVENUE).all()
    assert rows["gross_revenue"].notna().all()
