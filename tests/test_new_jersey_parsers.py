"""Parser tests for New Jersey DGE sports and internet-gaming PDFs."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from variant_gaming.states.new_jersey import (
    CASINO_VERTICAL,
    SPORTS_VERTICAL,
    build_normalized_rows,
    discover_monthly_pdf_links,
    parse_nj_pdf,
    parse_source_cell,
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
    # Skin pages do not provide these fields; grouping must not invent zeros.
    assert parsed["tax"].isna().all()
    assert parsed["taxable_revenue"].isna().all()


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
    # Dash cells must not leak form line numbers (defect B).
    assert not (parsed["gross_revenue"] == pytest.approx(15.0)).any()
    assert not (parsed["tax"] == pytest.approx(13.0)).any()


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


def test_parse_sports_january_2024_no_concatenation() -> None:
    parsed = parse_nj_pdf(
        (FIXTURES / "sample_sports_january_2024.pdf").read_bytes(),
        vertical=SPORTS_VERTICAL,
    )
    assert (parsed["period_start"] == "2024-01-01").all()
    by_op = {
        str(row.operator): float(row.gross_revenue)
        for row in parsed.itertuples(index=False)
    }
    assert by_op["Fanduel"] == pytest.approx(80_725_429.0)
    assert by_op["Pointsbet"] == pytest.approx(28_541_559.0)
    assert by_op["SuperBook"] == pytest.approx(45_791.0)
    # Concatenation artifact must not appear.
    assert not any(value > 1e12 for value in by_op.values())
    assert "Total" not in by_op


def test_parse_source_cell_dash_and_negatives() -> None:
    assert parse_source_cell("-")["value"] == 0.0
    assert parse_source_cell("-")["was_dash"] is True
    assert parse_source_cell("$ -")["value"] == 0.0
    assert parse_source_cell("(6,295)")["value"] == pytest.approx(-6295.0)
    assert parse_source_cell("8 0,725,429")["value"] == pytest.approx(80_725_429.0)
    assert parse_source_cell("")["status"] == "absent"
    assert parse_source_cell("7 4")["value"] == pytest.approx(74.0)
    assert parse_source_cell("12,345 and 67,890")["status"] == "ambiguous"


@pytest.mark.parametrize("second_tax,expected", [(None, None), (-1.0, -1.0), (0.0, 0.0)])
def test_brand_sum_requires_every_contributing_tax_cell(monkeypatch, second_tax, expected) -> None:
    from variant_gaming.states import new_jersey as nj

    pages = [SimpleNamespace(extract_text=lambda: "first"), SimpleNamespace(extract_text=lambda: "second")]
    pdf = MagicMock()
    pdf.__enter__.return_value.pages = pages
    monkeypatch.setattr(nj.pdfplumber, "open", lambda *args: pdf)
    monkeypatch.setattr(nj, "_page_period", lambda text: (2026, 7))
    monkeypatch.setattr(nj, "_classify_nj_page", lambda text: "sports_tax")

    def parse_page(page, text):
        return "ok", {"operator": "Same brand", "row_type": "operator", "gross_revenue": -5.0,
                      "tax": 0.0 if text == "first" else second_tax, "reported_revenue_name": "GGR"}

    monkeypatch.setattr(nj, "parse_sports_tax_page", parse_page)
    parsed = nj.parse_nj_pdf(b"fixture", vertical=SPORTS_VERTICAL)
    assert parsed.iloc[0]["gross_revenue"] == -10
    if expected is None:
        assert parsed["tax"].isna().all()
    else:
        assert parsed.iloc[0]["tax"] == expected


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
    assert rows["handle"].isna().all()


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
