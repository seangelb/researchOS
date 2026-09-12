"""Parser tests for DC OLG monthly financial HTML tables."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from variant_gaming.states.district_of_columbia import (
    REPORTED_REVENUE_NAME,
    build_normalized_rows,
    discover_month_links,
    is_online_location,
    parse_online_operator_table,
)

FIXTURES = Path(__file__).parent / "fixtures" / "DC"


def test_class_c_mobile_only_excludes_class_a() -> None:
    html = (FIXTURES / "sample_july_2026.html").read_text(encoding="utf-8", errors="replace")
    parsed = parse_online_operator_table(html)
    operators = set(parsed["operator"])
    assert "DraftKings Inc." in operators
    assert "Fanatics" in operators
    assert "FanDuel" not in operators
    assert "Sports & Social" not in operators
    dk = parsed[parsed["operator"] == "DraftKings Inc."].iloc[0]
    assert dk["period_start"] == "2026-07-01"
    assert dk["handle"] == pytest.approx(19_823_762.46)
    assert dk["gross_revenue"] == pytest.approx(1_761_139.72)
    assert dk["tax"] == pytest.approx(528_341.92)


def test_normalized_ggr_and_unaudited() -> None:
    html = (FIXTURES / "sample_july_2026.html").read_text(encoding="utf-8", errors="replace")
    parsed = parse_online_operator_table(html)
    rows = build_normalized_rows(
        parsed,
        source_url="https://dclottery.com/olg/financials/july-2026-unaudited",
        source_file="data/raw/DC/x.html",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        unaudited=True,
    )
    assert (rows["channel"] == "online").all()
    assert (rows["reported_revenue_name"] == REPORTED_REVENUE_NAME).all()
    assert (rows["report_status"] == "unaudited").all()


def test_online_location_helper() -> None:
    assert is_online_location("Mobile APP and Online Platform", "Class C")
    assert not is_online_location("Audi Field, 100 Potomac Ave, SW", "Class A")


def test_discover_month_links() -> None:
    html = """
    <a href="/olg/financials/july-2026-unaudited">July 2026 - (Unaudited)</a>
    <a href="/olg/financials?page=1">Next</a>
    """
    links = discover_month_links(html, "https://dclottery.com/olg/financials")
    assert len(links) == 1
    assert links[0]["year"] == 2026 and links[0]["month"] == 7
    assert links[0]["unaudited"] is True
