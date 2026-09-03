"""Parser tests for Iowa IRGC monthly sports-wagering PDFs."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from variant_gaming.states.iowa import (
    REPORTED_REVENUE_NAME,
    build_normalized_rows,
    discover_archive_fy_links,
    discover_current_month_links,
    parse_ia_pdf,
    page_period,
)

FIXTURES = Path(__file__).parent / "fixtures" / "IA"


def test_parse_july_2026_internet_only() -> None:
    parsed = parse_ia_pdf((FIXTURES / "sample_july_2026.pdf").read_bytes())
    assert set(parsed["period_start"]) == {"2026-07-01"}
    statewide = parsed[parsed["operator"] == "STATEWIDE"].iloc[0]
    assert statewide["row_type"] == "official_statewide_total"
    assert statewide["net_proceeds"] == pytest.approx(16_786_386.62)
    assert statewide["handle"] == pytest.approx(173_175_506.24)
    ameristar = parsed[parsed["operator"].str.contains("Ameristar", case=False, na=False)]
    assert not ameristar.empty
    assert ameristar.iloc[0]["net_proceeds"] == pytest.approx(267_142.75)
    # Retail-only Hard Rock internet is $0 and should not be required, but must not
    # use retail net receipts as online.
    retail_like = parsed[parsed["net_proceeds"] == pytest.approx(213_989.63)]
    assert retail_like.empty


def test_normalized_internet_net_receipts() -> None:
    parsed = parse_ia_pdf((FIXTURES / "sample_july_2026.pdf").read_bytes())
    rows = build_normalized_rows(
        parsed,
        source_url="https://irgc.iowa.gov/media/505/download",
        source_file="data/raw/IA/x.pdf",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert (rows["channel"] == "online").all()
    assert (rows["reported_revenue_name"] == REPORTED_REVENUE_NAME).all()
    assert rows["gross_revenue"].isna().all()


def test_page_period_skips_fytd() -> None:
    assert page_period("SPORTS WAGERING REVENUE REPORT -- FYTD 2026") is None
    assert page_period("SPORTS WAGERING REVENUE REPORT -- JULY 2026") == (2026, 7)
    assert page_period("ONLINE SPORTS WAGERING BY OPERATOR -- JULY 2026") is None


def test_discover_links() -> None:
    current = discover_current_month_links(
        '<a href="/media/505/download?inline">July 2026 Sports Wagering Revenue</a>'
        '<a href="/publications-reports/sports-wagering-revenue/archived-sports-revenue">Archived</a>',
        "https://irgc.iowa.gov/publications-reports/sports-wagering-revenue",
    )
    assert len(current) == 1
    archive = discover_archive_fy_links(
        '<a href="/media/508/download?inline">Fiscal Year 2026 Sports Wagering Revenue</a>',
        "https://irgc.iowa.gov/publications-reports/sports-wagering-revenue/archived-sports-revenue",
    )
    assert archive[0]["year"] == 2026
