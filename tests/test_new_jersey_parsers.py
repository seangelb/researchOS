"""Parser tests for New Jersey DGE sports and internet-gaming PDFs."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from variant_gaming.states.new_jersey import (
    CASINO_VERTICAL,
    SPORTS_VERTICAL,
    build_normalized_rows,
    discover_monthly_pdf_links,
    parse_nj_pdf,
    repair_pdf_number,
)

FIXTURES = Path(__file__).parent / "fixtures" / "NJ"


def test_parse_sports_online_skins_july_2026() -> None:
    parsed = parse_nj_pdf(
        (FIXTURES / "sample_sports_july_2026.pdf").read_bytes(),
        vertical=SPORTS_VERTICAL,
    )
    assert (parsed["period_start"] == "2026-07-01").all()
    brands = set(parsed["operator"])
    assert "BETFANATICS" in brands or "BetFanatics" in {b.upper() for b in brands}
    fanatics = parsed[parsed["operator"].str.contains("FANATIC", case=False, na=False)]
    assert not fanatics.empty
    assert fanatics["gross_revenue"].sum() == pytest.approx(10_624_022.0)
    # Lounge-only (retail) figures must not appear as online GGR.
    assert not (parsed["gross_revenue"] == pytest.approx(-3277.0)).any()


def test_parse_sports_june_2019_internet_line() -> None:
    parsed = parse_nj_pdf(
        (FIXTURES / "sample_sports_june_2019.pdf").read_bytes(),
        vertical=SPORTS_VERTICAL,
    )
    assert (parsed["period_start"] == "2019-06-01").all()
    assert not parsed.empty
    assert (parsed["reported_revenue_name"] == "Monthly Internet Sports Wagering Gross Revenue").all()
    assert parsed["gross_revenue"].sum() != pytest.approx(0.0)
    assert not (parsed["gross_revenue"] == pytest.approx(667_738)).any()


def test_parse_sports_online_wagering_skins_july_2024() -> None:
    parsed = parse_nj_pdf(
        (FIXTURES / "sample_sports_july_2024.pdf").read_bytes(),
        vertical=SPORTS_VERTICAL,
    )
    assert (parsed["period_start"] == "2024-07-01").all()
    brands = {b.upper() for b in parsed["operator"]}
    assert any("FANATIC" in b for b in brands)
    fanatics = parsed[parsed["operator"].str.contains("FANATIC", case=False, na=False)]
    assert fanatics["gross_revenue"].sum() == pytest.approx(2_661_170.0)
    assert (parsed["reported_revenue_name"] == "Monthly Online Sports Wagering Gross Revenue").all()
    assert not (parsed["gross_revenue"] == pytest.approx(21_540)).any()


def test_sports_normalized_channel() -> None:
    parsed = parse_nj_pdf(
        (FIXTURES / "sample_sports_july_2026.pdf").read_bytes(),
        vertical=SPORTS_VERTICAL,
    )
    rows = build_normalized_rows(
        parsed,
        vertical=SPORTS_VERTICAL,
        source_url="https://www.nj.gov/oag/ge/docs/Financials/SWRTaxReturns/2026/July2026.pdf",
        source_file="data/raw/NJ/x.pdf",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert (rows["channel"] == "online").all()
    assert (rows["vertical"] == SPORTS_VERTICAL).all()


def test_parse_igr_win_july_2026() -> None:
    parsed = parse_nj_pdf(
        (FIXTURES / "sample_igr_july_2026.pdf").read_bytes(),
        vertical=CASINO_VERTICAL,
    )
    assert (parsed["period_start"] == "2026-07-01").all()
    bally = parsed[parsed["operator"].str.contains("Bally", case=False, na=False)]
    assert not bally.empty
    # Casino-level Total Win 20,724,887 if skins fail; skins should sum to the same.
    assert parsed["gross_revenue"].max() >= 20_000_000


def test_repair_pdf_number() -> None:
    assert repair_pdf_number("1 3,429,459") == pytest.approx(13_429_459.0)
    assert repair_pdf_number("6 ,023,048") == pytest.approx(6_023_048.0)
    assert repair_pdf_number("-") is None


def test_discover_skips_amendments() -> None:
    html = """
    <a href="https://www.nj.gov/oag/ge/docs/Financials/SWRTaxReturns/2026/July2026.pdf">JULY</a>
    <a href="https://www.nj.gov/oag/ge/docs/Financials/SWRTaxReturns/2026/107AmendmentsList2026.pdf">AMENDMENTS LIST</a>
    """
    links = discover_monthly_pdf_links(html, "https://www.njoag.gov/", kind="sports")
    assert len(links) == 1
    assert "July2026" in links[0]["filename"]
