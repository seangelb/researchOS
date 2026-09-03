"""Parser tests for Delaware Lottery sports and iGaming HTML tables."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from variant_gaming.states.delaware import (
    build_normalized_rows,
    discover_year_pages,
    parse_igaming_html,
    parse_sports_html,
    sports_headers_have_online_split,
)

FIXTURES = Path(__file__).parent / "fixtures" / "DE"


def test_sports_has_no_online_split() -> None:
    html = (FIXTURES / "sample_sports_fy2027.html").read_text(encoding="utf-8", errors="replace")
    parsed, meta = parse_sports_html(html)
    assert meta["has_online_split"] is False
    assert parsed.empty
    assert not sports_headers_have_online_split(
        ["Accounting Month", "Delaware Park", "Retailers", "Total"]
    )


def test_sports_online_column_would_parse() -> None:
    assert sports_headers_have_online_split(
        ["Month", "Online Sportsbook", "Retailers", "Total"]
    )


def test_igaming_fy_maps_total_net_gaming_revenue() -> None:
    html = (FIXTURES / "sample_igaming_fy2027.html").read_text(encoding="utf-8", errors="replace")
    parsed = parse_igaming_html(html)
    assert not parsed.empty
    july = parsed[parsed["period_start"] == "2026-07-01"]
    park = july[july["operator"] == "Delaware Park"].iloc[0]
    assert park["net_proceeds"] == pytest.approx(5_981_219.0)
    assert park["handle"] == pytest.approx(84_101_246.0 + 93_835_803.0)
    statewide = july[july["operator"] == "STATEWIDE"].iloc[0]
    assert statewide["row_type"] == "official_statewide_total"
    assert statewide["net_proceeds"] == pytest.approx(13_438_251.0)
    rows = build_normalized_rows(
        parsed,
        vertical="online_casino",
        source_url="https://example.test/de.html",
        source_file="data/raw/DE/x.html",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert (rows["channel"] == "online").all()
    assert (rows["reported_revenue_name"] == "Total Net Gaming Revenue").all()
    assert (rows["vertical"] == "online_casino").all()


def test_igaming_classic_2023_net() -> None:
    html = (FIXTURES / "sample_igaming_2023.html").read_text(encoding="utf-8", errors="replace")
    parsed = parse_igaming_html(html)
    jan = parsed[parsed["period_start"] == "2023-01-01"]
    assert not jan.empty
    statewide = jan[jan["operator"] == "STATEWIDE"].iloc[0]
    assert statewide["net_proceeds"] == pytest.approx(1_189_862.70)
    park = jan[jan["operator"].str.contains("Delaware Park", case=False, na=False)]
    assert not park.empty
    assert park.iloc[0]["reported_revenue_name"] == "Net"


def test_discover_year_pages_sports_vs_igaming() -> None:
    sports_html = """
    <a href="/Sports-Lottery/Sportsbooks/Monthly-Proceeds-And-Distribution-Financial-Year/2027">FY 2027</a>
    <a href="/Sports-Lottery/Retailers/Monthly-Distribution/2022">2022 retailers</a>
    """
    igaming_html = """
    <a href="/More/iGaming/Revenue-Distribution/Monthly-Proceeds-And-Distribution-Financial-Year/2027">FY 2027</a>
    <a href="/More/iGaming/Monthly-Net-Proceeds/Monthly-Distribution-Of-Proceeds/2023">2023</a>
    """
    sports = discover_year_pages(
        sports_html, "https://delottery.com/Sports-Lottery/Monthly-Net-Proceeds", kind="sports"
    )
    gaming = discover_year_pages(
        igaming_html, "https://www.delottery.com/More/iGaming/Monthly-Net-Proceeds", kind="igaming"
    )
    assert len(sports) == 1
    assert "Retailers" not in sports[0]["url"]
    assert len(gaming) == 2
