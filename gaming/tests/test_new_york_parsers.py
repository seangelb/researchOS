"""Focused parser tests for New York weekly sports Excel workbooks."""

from pathlib import Path

import pandas as pd
import pytest

from variant_gaming.states.new_york import (
    REPORTED_REVENUE_NAME,
    build_normalized_rows,
    discover_ny_sports_workbook_links,
    parse_ny_workbook,
    to_money_decimal,
    week_period,
)
from variant_gaming.common import utc_now

FIXTURES = Path(__file__).parent / "fixtures" / "NY"

SAMPLE_LANDING_HTML = """
<html><body>
<h2>Casino Gaming</h2>
<table><tr><td>STATEWIDE</td><td><a href="/casino-weekly.xlsx">Statewide Casino Weekly Excel</a></td></tr></table>
<h2>Sports Wagering</h2>
<table>
  <tr><th>Name</th><th>Reports</th></tr>
  <tr>
    <td>STATEWIDE</td>
    <td><a href="/statewide-sports-wagering-weekly-report-excel">Statewide Sports Wagering Weekly Excel</a></td>
  </tr>
  <tr>
    <td>FanDuel</td>
    <td><a href="/fanduel-weekly-excel">FanDuel Weekly Excel</a></td>
  </tr>
  <tr>
    <td>DraftKings</td>
    <td><a href="/draftkings-weekly-excel">DraftKings Sports Wagering Weekly Excel</a></td>
  </tr>
</table>
</body></html>
"""


@pytest.fixture(scope="module")
def workbook_bytes() -> bytes:
    path = FIXTURES / "sample_statewide_weekly.xlsx"
    assert path.exists(), f"Missing fixture {path}"
    return path.read_bytes()


def test_discover_sports_links_ignores_casino_section() -> None:
    found = discover_ny_sports_workbook_links(
        SAMPLE_LANDING_HTML, landing_url="https://gaming.ny.gov/revenue-reports"
    )
    assert "statewide-sports-wagering-weekly-report-excel" in found["statewide"]["discovered_url"]
    names = [op["source_operator_name"] for op in found["operators"]]
    assert names == ["FanDuel", "DraftKings"]


def test_parse_fixture_preserves_negative_ggr_and_weeks(workbook_bytes: bytes) -> None:
    weekly, reconciliation = parse_ny_workbook(workbook_bytes)
    assert len(weekly) == 2
    assert (weekly["ggr_usd"] < 0).any()
    assert list(weekly["week_ending"].astype(str)) == ["2025-07-06", "2025-07-13"]
    # Future blank week is skipped; Total reconciles to the cent
    assert reconciliation["completed_weeks"].sum() == 2
    assert reconciliation.iloc[0]["reconciled_ggr_total"] == reconciliation.iloc[0]["reported_ggr_total"]


def test_normalize_maps_ggr_to_gross_revenue(workbook_bytes: bytes) -> None:
    weekly, _ = parse_ny_workbook(workbook_bytes)
    retrieved = utc_now()
    rows = build_normalized_rows(
        weekly,
        operator="STATEWIDE",
        row_type="official_statewide_total",
        source_url="https://gaming.ny.gov/example",
        source_file="data/raw/NY/fixture.xlsx",
        source_sha256="abc123",
        retrieved_at=retrieved,
    )
    assert (rows["channel"] == "online").all()
    assert (rows["frequency"] == "weekly").all()
    assert (rows["reported_revenue_name"] == REPORTED_REVENUE_NAME).all()
    assert rows["gross_revenue"].notna().all()
    assert rows["handle"].notna().all()
    # Weeks are not allocated into calendar months
    for _, row in rows.iterrows():
        start, end = week_period(row["period_end"])
        assert row["period_start"] == start
        assert row["period_end"] == end
        assert pd.Timestamp(end) - pd.Timestamp(start) == pd.Timedelta(days=6)


def test_to_money_decimal_parentheses() -> None:
    assert float(to_money_decimal("(10.25)")) == -10.25
