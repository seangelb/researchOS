"""Parser tests for West Virginia Lottery weekly sports (mobile) and iGaming Excel."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from variant_gaming.states.west_virginia import (
    CASINO_REPORTED,
    SPORTS_REPORTED,
    build_casino_normalized,
    build_sports_normalized,
    discover_weekly_zip_links,
    parse_igaming_workbook,
    parse_sports_workbook,
)

FIXTURES = Path(__file__).parent / "fixtures" / "WV"


def test_parse_sports_mobile_excludes_retail() -> None:
    parsed = parse_sports_workbook(FIXTURES / "sample_sports_weekly.xlsx")
    assert set(parsed["period_start"]) == {"2026-07-01", "2026-07-05"}
    stub = parsed[parsed["period_end"] == "2026-07-04"].iloc[0]
    assert stub["handle"] == pytest.approx(5000.0)
    assert stub["taxable_revenue"] == pytest.approx(400.0)
    # Retail 1000/100 must not be used.
    assert not (parsed["handle"] == pytest.approx(1000.0)).any()
    fy = parsed[parsed["taxable_revenue"] == pytest.approx(999999)]
    assert fy.empty


def test_parse_sports_combined_header_excludes_retail() -> None:
    parsed = parse_sports_workbook(FIXTURES / "sample_sports_weekly_combined.xlsx")
    assert set(parsed["period_start"]) == {"2026-07-01", "2026-07-05"}
    stub = parsed[parsed["period_end"] == "2026-07-04"].iloc[0]
    assert stub["handle"] == pytest.approx(5000.0)
    assert stub["taxable_revenue"] == pytest.approx(400.0)
    assert not (parsed["handle"] == pytest.approx(1000.0)).any()
    assert parsed[parsed["taxable_revenue"] == pytest.approx(999999)].empty


def test_sports_normalized_weekly() -> None:
    parsed = parse_sports_workbook(FIXTURES / "sample_sports_weekly.xlsx")
    rows = build_sports_normalized(
        parsed,
        source_url="https://business.wvlottery.com/resourcesPayments",
        source_file="data/raw/WV/x.xlsx",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert (rows["channel"] == "online").all()
    assert (rows["frequency"] == "weekly").all()
    assert (rows["reported_revenue_name"] == SPORTS_REPORTED).all()


def test_parse_igaming_weekly() -> None:
    parsed = parse_igaming_workbook(FIXTURES / "sample_igaming_weekly.xlsx")
    week = parsed[parsed["period_end"] == "2026-07-11"].iloc[0]
    assert week["operator"] == "Mountaineer"
    assert week["handle"] == pytest.approx(20000.0)
    assert week["gross_revenue"] == pytest.approx(2000.0)
    assert week["tax"] == pytest.approx(300.0)
    rows = build_casino_normalized(
        parsed,
        source_url="https://example.test/ig.zip",
        source_file="data/raw/WV/x.xlsx",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert (rows["reported_revenue_name"] == CASINO_REPORTED).all()


def test_discover_zip_links() -> None:
    html = """
    <a href="https://assets.example/Sports_Wagering.zip">Sports Wagering Weekly Summary</a>
    <a href="https://assets.example/i-Gaming.zip">iGaming Weekly Summary</a>
    <a href="https://assets.example/Video_Lottery_Summary.zip">Video Lottery Weekly Summary</a>
    """
    found = discover_weekly_zip_links(html, "https://business.wvlottery.com/resourcesPayments")
    assert set(found) == {"sports", "igaming"}
