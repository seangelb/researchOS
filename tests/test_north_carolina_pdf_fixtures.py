"""North Carolina PDF fixture parsing for footnoted and cumulative reports."""

import re
from io import BytesIO
from pathlib import Path

import pdfplumber
import pytest

from variant_gaming.common import project_root
from variant_gaming.states.north_carolina import (
    _cell_money,
    _money_tokens,
    normalize_month_label,
    parse_revenue_pdf,
)

FIXTURES = Path(__file__).parent / "fixtures" / "NC"
MARCH_2024 = FIXTURES / "NCSLC-Sports-Betting-Revenue-Report-March-2024.pdf"
JULY_2024 = FIXTURES / "NCSLC-Sports-Betting-Revenue-Report-July-2024.pdf"
APRIL_2024 = FIXTURES / "NCSLC-Sports-Betting-Revenue-Report-April-2024.pdf"


def _nc_raw_pdfs() -> list[Path]:
    root = project_root()
    by_name: dict[str, Path] = {}
    for path in sorted((root / "data" / "raw" / "NC").rglob("*.pdf")):
        name = path.name.split("_", 1)[-1]
        by_name.setdefault(name, path)
    return list(by_name.values())


def test_normalize_month_label_accepts_footnote_suffix() -> None:
    assert normalize_month_label("March1") == "March"
    assert normalize_month_label("July1") == "July"
    assert normalize_month_label("December") == "December"


def test_normalize_month_label_rejects_unrelated_text() -> None:
    assert normalize_month_label("Total") is None
    assert normalize_month_label("Month") is None
    assert normalize_month_label("March 2024") is None
    assert normalize_month_label("Q1") is None
    assert normalize_month_label("January2024") is None


def test_negative_monetary_values_remain_supported() -> None:
    assert _cell_money("-$1,234") == -1234.0
    assert _cell_money("($5,678)") == -5678.0
    assert _money_tokens("$100 $200 $300 $400 $500 $600 $700")[-1] == 700.0


@pytest.mark.parametrize(
    ("path", "year", "month", "handle", "gross", "tax"),
    [
        (
            MARCH_2024,
            2024,
            3,
            659_308_541,
            66_496_213,
            11_969_318,
        ),
        (
            JULY_2024,
            2024,
            7,
            340_375_354,
            42_226_041,
            7_600_687,
        ),
    ],
)
def test_footnoted_pdf_parses_official_values(
    path: Path,
    year: int,
    month: int,
    handle: float,
    gross: float,
    tax: float,
) -> None:
    records = parse_revenue_pdf(path.read_bytes())
    assert len(records) == 1
    row = records[0]
    assert row["year"] == year
    assert row["month"] == month
    assert row["handle"] == handle
    assert row["gross_revenue"] == gross
    assert row["tax"] == tax


def test_april_cumulative_pdf_emits_march_and_april() -> None:
    records = parse_revenue_pdf(APRIL_2024.read_bytes())
    assert len(records) == 2
    by_month = {(r["year"], r["month"]): r for r in records}
    march = by_month[(2024, 3)]
    april = by_month[(2024, 4)]
    assert march["handle"] == 659_308_541
    assert march["gross_revenue"] == 66_496_213
    assert march["tax"] == 11_969_318
    assert april["handle"] == 648_934_226
    assert april["gross_revenue"] == 105_251_672
    assert april["tax"] == 18_945_301


def test_total_row_not_emitted_as_observation() -> None:
    for path in (MARCH_2024, JULY_2024, APRIL_2024):
        records = parse_revenue_pdf(path.read_bytes())
        assert all(r["month"] != 0 for r in records)
        assert len({(r["year"], r["month"]) for r in records}) == len(records)


def test_all_saved_nc_raw_pdfs_parse_without_exception() -> None:
    pdfs = _nc_raw_pdfs()
    assert len(pdfs) == 29

    saw_footnote = False
    saw_plain = False
    for path in pdfs:
        content = path.read_bytes()
        records = parse_revenue_pdf(content)
        periods = [(r["year"], r["month"]) for r in records]
        assert len(periods) == len(set(periods)), path.name
        assert periods, path.name

        with pdfplumber.open(BytesIO(content)) as pdf:
            text = pdf.pages[0].extract_text() or ""
        if "March1" in text or "July1" in text:
            saw_footnote = True
        if re.search(
            r"\b(April|May|June|August|September|October|November|December)\s+\$",
            text,
        ):
            saw_plain = True

    assert saw_footnote
    assert saw_plain
