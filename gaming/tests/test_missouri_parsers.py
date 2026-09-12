"""Focused parser tests for Missouri mobile sports-wagering financial Excel."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from variant_gaming.states.missouri import (
    REPORTED_REVENUE_NAME,
    build_normalized_rows,
    discover_monthly_financial_links,
    parse_mobile_monthly_financials,
)

FIXTURES = Path(__file__).parent / "fixtures" / "MO"


@pytest.fixture(scope="module")
def sample_bytes() -> bytes:
    path = FIXTURES / "sample_monthly_financials_0726.xlsx"
    assert path.exists(), f"Missing fixture {path}"
    return path.read_bytes()


def test_parse_mobile_taxable_agr(sample_bytes: bytes) -> None:
    parsed, meta = parse_mobile_monthly_financials(
        sample_bytes,
        filename="sample_monthly_financials_0726.xlsx",
        report_month_only=True,
    )
    assert meta["report_year"] == 2026
    assert meta["report_month"] == 7
    assert set(parsed["row_type"]) == {"operator", "official_statewide_total"}
    bet365 = parsed[parsed["operator"] == "BET365 - MOBILE"].iloc[0]
    assert bet365["handle"] == pytest.approx(10_000.0)
    assert bet365["taxable_revenue"] == pytest.approx(2_000.0)
    assert bet365["tax"] == pytest.approx(200.0)
    statewide = parsed[parsed["operator"] == "STATEWIDE"].iloc[0]
    assert statewide["taxable_revenue"] == pytest.approx(7_000.0)


def test_normalized_reported_revenue_name(sample_bytes: bytes) -> None:
    parsed, _meta = parse_mobile_monthly_financials(
        sample_bytes,
        filename="sample_monthly_financials_0726.xlsx",
    )
    rows = build_normalized_rows(
        parsed,
        source_url="https://example.test/mo.xlsx",
        source_file="data/raw/MO/x.xlsx",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert (rows["channel"] == "online").all()
    assert (rows["reported_revenue_name"] == REPORTED_REVENUE_NAME).all()
    assert rows["gross_revenue"].isna().all() or (rows["gross_revenue"].astype(object) == None).all()


def test_discover_prefers_monthly_financials() -> None:
    html = """
    <a href="FY27_SWFinReport/07_Jul/SW Monthly Financials 0726.xlsx">Excel</a>
    <a href="FY27_SWFinReport/07_Jul/SW Revenue Detail Report 0726.xlsx">Excel</a>
    """
    links = discover_monthly_financial_links(
        html, "https://www.mgc.dps.mo.gov/SportsWagering/sw_financials/rb_SWFin_main.html"
    )
    assert len(links) == 1
    assert "Monthly Financials" in links[0]["filename"]
    assert links[0]["year_hint"] == 2026
    assert links[0]["month_hint"] == 7
