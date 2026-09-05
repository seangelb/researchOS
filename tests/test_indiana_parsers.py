"""Focused parser tests for Indiana IGC sports Excel workbooks."""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from variant_gaming.states.indiana import (
    REPORTED_GROSS_RECEIPTS,
    REPORTED_TAXABLE_AGR,
    build_normalized_rows,
    discover_xlsx_links,
    is_online_brand_label,
    parse_workbook,
)

FIXTURES = Path(__file__).parent / "fixtures" / "IN"


@pytest.fixture(scope="module")
def workbook_bytes() -> bytes:
    path = FIXTURES / "sample_monthly_revenue.xlsx"
    assert path.exists(), f"Missing fixture {path}"
    return path.read_bytes()


def test_online_brand_label_excludes_retail_and_otb() -> None:
    assert is_online_brand_label("AS - Sportsbook.DraftKings.com")
    assert is_online_brand_label("BC - in.sportsbook.FanDuel.com")
    assert not is_online_brand_label("Retail")
    assert not is_online_brand_label("WC Downtown Indianapolis")
    assert not is_online_brand_label("Adjustments")
    assert not is_online_brand_label("Taxable AGR")


def test_parse_fixture_online_brands_only(workbook_bytes: bytes) -> None:
    brands, tax_summary, meta = parse_workbook(workbook_bytes, filename="2026-07-Revenue.xlsx")
    assert meta["year"] == 2026 and meta["month"] == 7
    assert meta["online_brand_rows"] == 3
    operators = set(brands["operator"])
    assert "AS - Sportsbook.DraftKings.com" in operators
    assert "BC - in.sportsbook.FanDuel.com" in operators
    assert "RS - getsbk.com" in operators
    assert "Retail" not in operators
    assert not tax_summary.empty
    assert (tax_summary["is_total"]).any()


def test_normalize_prefers_taxable_agr_when_unambiguous(workbook_bytes: bytes) -> None:
    brands, tax_summary, meta = parse_workbook(workbook_bytes, filename="2026-07-Revenue.xlsx")
    rows = build_normalized_rows(
        brands,
        tax_summary,
        year=meta["year"],
        month=meta["month"],
        source_url="https://www.in.gov/igc/example.xlsx",
        source_file="data/raw/IN/fixture.xlsx",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert (rows["channel"] == "online").all()
    assert (rows["frequency"] == "monthly").all()
    draft = rows[rows["operator"] == "AS - Sportsbook.DraftKings.com"].iloc[0]
    assert draft["handle"] == pytest.approx(137030149.87)
    assert draft["gross_revenue"] == pytest.approx(14058728.33)
    assert draft["reported_revenue_name"] == REPORTED_GROSS_RECEIPTS
    rising = rows[rows["operator"] == "RS - getsbk.com"].iloc[0]
    # Single online brand, no retail → Taxable AGR preferred
    assert rising["reported_revenue_name"] == REPORTED_TAXABLE_AGR
    assert rising["taxable_revenue"] == pytest.approx(0.0)
    # The old assertion blessed a calculated sum as a published total.
    assert set(rows["row_type"]) == {"operator"}
    assert "STATEWIDE" not in set(rows["operator"])
    assert rows["handle"].sum() == pytest.approx(brands["handle"].sum())


def test_discover_xlsx_links_from_landing_html() -> None:
    html = """
    <a href="/igc/files/reports/2026/2026-07-Revenue.pdf">July 2026</a>
    <a href="/igc/files/reports/2026/2026-07-Revenue.xlsx">XLSX Version</a>
    <a href="/igc/files/reports/2025/2025-01-Revenue.xlsx">XLSX Version</a>
    """
    links = discover_xlsx_links(html, "https://www.in.gov/igc/publications/monthly-revenue/")
    assert len(links) == 2
    assert links[0]["year"] == 2025 and links[0]["month"] == 1
    assert links[1]["year"] == 2026 and links[1]["month"] == 7
