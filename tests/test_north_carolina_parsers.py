"""North Carolina discovery and money-token helpers."""

from datetime import datetime, timezone

from variant_gaming.states.north_carolina import (
    _money_tokens,
    build_normalized_row,
    discover_report_links,
    normalize_month_label,
)


def test_discover_report_links() -> None:
    html = """
    <a href="/docs/July-2026-Sports-Revenue.pdf">July 2026 Revenue</a>
    <a href="/docs/rules-manual.pdf">Rules manual</a>
    """
    links = discover_report_links(html, "https://ncgaming.gov/")
    assert len(links) == 1
    assert links[0]["month_hint"] == 7
    assert links[0]["year_hint"] == 2026


def test_money_tokens_and_normalized_statewide_row() -> None:
    nums = _money_tokens("$551,603,216 $14,746,468 $566,349,683 $4,405,348 $498,728,763 $63,215,572 $14,038,810")
    assert len(nums) == 7
    row = build_normalized_row(
        {"year": 2026, "month": 7, "handle": nums[2], "gross_revenue": nums[5], "tax": nums[6]},
        source_url="https://ncgaming.gov/example.pdf",
        source_file="nc.pdf",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert row.iloc[0]["operator"] == "STATEWIDE"
    assert row.iloc[0]["row_type"] == "official_statewide_total"
    assert row.iloc[0]["handle"] == 566349683.0
    assert row.iloc[0]["reported_revenue_name"] == "Gross Wagering Revenue"


def test_normalize_month_label_plain_month() -> None:
    assert normalize_month_label("July") == "July"
    assert normalize_month_label("  march  ") == "March"
    assert normalize_month_label("March 2024") is None
