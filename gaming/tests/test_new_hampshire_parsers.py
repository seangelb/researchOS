"""New Hampshire mobile-column extraction and discovery."""

from datetime import datetime, timezone

import pandas as pd

from variant_gaming.states.new_hampshire import (
    _extract_mobile_amounts,
    build_normalized_rows,
    discover_summary_links,
)


def test_discover_summary_links() -> None:
    html = """
    <a href="/docs/FY2026_Sports_Betting_Summary.pdf">FY2026 Sports Betting Summary</a>
    <a href="/docs/other.pdf">Unrelated</a>
    """
    links = discover_summary_links(html, "https://nhlottery.com/")
    assert len(links) == 1
    assert links[0]["fy"] == 2026


def test_extract_mobile_amounts_repairs_split_digits() -> None:
    values = _extract_mobile_amounts(" 4 4,191,929  3,200,000  480,000  Jan-26 retail leftover")
    assert values[0] == 44191929.0
    assert len(values) == 3


def test_normalized_rows_are_statewide_monthly() -> None:
    parsed = pd.DataFrame(
        [{"year": 2026, "month": 1, "handle": 100.0, "gross_revenue": 8.0, "tax": 1.0}]
    )
    frame = build_normalized_rows(
        parsed,
        source_url="https://nhlottery.com/example.pdf",
        source_file="nh.pdf",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert frame.iloc[0]["row_type"] == "official_statewide_total"
    assert frame.iloc[0]["channel"] == "online"
    assert frame.iloc[0]["reported_revenue_name"] == "GGR"
