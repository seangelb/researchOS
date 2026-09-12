"""Focused parser tests for Tennessee SWAC statewide monthly CSVs."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from variant_gaming.states.tennessee import (
    REPORTED_REVENUE_NAME,
    REPORT_STATUS_LIMITED,
    build_normalized_row,
    discover_csv_links,
    parse_period_from_csv_text,
    parse_statewide_monthly_csv,
    resolve_period,
)

FIXTURES = Path(__file__).parent / "fixtures" / "TN"


@pytest.fixture(scope="module")
def sample_text() -> str:
    path = FIXTURES / "sample_statewide_november_2023.csv"
    assert path.exists(), f"Missing fixture {path}"
    return path.read_bytes().decode("utf-8-sig")


def test_parse_maps_handle_and_tax_not_revenue(sample_text: str) -> None:
    metrics = parse_statewide_monthly_csv(sample_text)
    assert metrics["handle"] == pytest.approx(515_512_604.0)
    assert metrics["tax"] == pytest.approx(9_513_707.0)
    assert metrics["gross_wagers"] == pytest.approx(517_141_377.0)
    assert metrics["adjustments"] == pytest.approx(1_628_773.0)


def test_period_from_title(sample_text: str) -> None:
    year, month = parse_period_from_csv_text(sample_text)
    assert year == 2023
    assert month == 11
    assert resolve_period(sample_text, year_hint=2023, month_hint=11) == (2023, 11)


def test_normalized_row_does_not_invent_gross_revenue(sample_text: str) -> None:
    metrics = parse_statewide_monthly_csv(sample_text)
    row = build_normalized_row(
        metrics,
        year=2023,
        month=11,
        source_file="data/raw/TN/2026-09-02/abc_sample.csv",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert row["operator"] == "STATEWIDE"
    assert row["row_type"] == "official_statewide_total"
    assert row["channel"] == "online"
    assert row["frequency"] == "monthly"
    assert row["handle"] == pytest.approx(515_512_604.0)
    assert row["tax"] == pytest.approx(9_513_707.0)
    assert row["gross_revenue"] is None
    assert row["adjusted_revenue"] is None
    assert row["reported_revenue_name"] == REPORTED_REVENUE_NAME
    assert row["report_status"] == REPORT_STATUS_LIMITED


def test_discover_csv_links_from_year_sections() -> None:
    html = """
    <h3>2024</h3>
    <a href="/content/dam/tn/swac/documents/report/2024/Monthly Report for SWC - January (CSV).csv">csv</a>
    <h3>2026</h3>
    <a href="/content/dam/tn/swac/documents/report/2026/Monthly_Report_for_SWC-July2026(UA).csv">csv</a>
    """
    links = discover_csv_links(html)
    assert len(links) == 2
    assert links[0]["year_hint"] == 2024
    assert links[0]["month_hint"] == 1
    assert links[1]["year_hint"] == 2026
    assert links[1]["month_hint"] == 7
    assert links[1]["unaudited"] is True
