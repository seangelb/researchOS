"""Focused parser tests for Maryland mobile sports-wagering Excel workbooks."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from variant_gaming.states.maryland import (
    REPORTED_REVENUE_NAME,
    build_normalized_rows,
    discover_release_links,
    find_report_download,
    parse_mobile_sports_workbook,
)

FIXTURES = Path(__file__).parent / "fixtures" / "MD"


@pytest.fixture(scope="module")
def sample_bytes() -> bytes:
    path = FIXTURES / "sample_july_2026_sports_wagering.xlsx"
    assert path.exists(), f"Missing fixture {path}"
    return path.read_bytes()


def test_parse_mobile_only_excludes_retail(sample_bytes: bytes) -> None:
    parsed, meta = parse_mobile_sports_workbook(sample_bytes)
    assert meta["year"] == 2026
    assert meta["month"] == 7
    assert set(parsed["row_type"]) == {"operator", "official_statewide_total"}
    assert "Retail Shop" not in set(parsed["operator"])
    operators = set(parsed.loc[parsed["row_type"] == "operator", "operator"])
    assert operators == {"Draft Kings", "BetMGM"}
    draft = parsed[parsed["operator"] == "Draft Kings"].iloc[0]
    assert draft["handle"] == pytest.approx(150_000.0)
    assert draft["taxable_revenue"] == pytest.approx(12_000.0)
    assert draft["tax"] == pytest.approx(2_400.0)
    statewide = parsed[parsed["operator"] == "STATEWIDE"].iloc[0]
    assert statewide["handle"] == pytest.approx(200_000.0)


def test_normalized_maps_taxable_win(sample_bytes: bytes) -> None:
    parsed, meta = parse_mobile_sports_workbook(sample_bytes)
    rows = build_normalized_rows(
        parsed,
        year=meta["year"],
        month=meta["month"],
        source_url="https://example.test/file.xlsx",
        source_file="data/raw/MD/x.xlsx",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert (rows["channel"] == "online").all()
    assert (rows["reported_revenue_name"] == REPORTED_REVENUE_NAME).all()
    assert rows["gross_revenue"].isna().all()
    assert rows["adjusted_revenue"].isna().all()


def test_discover_and_excel_link_helpers() -> None:
    landing = """
    <a href="/sports-wagering-contributes-8-4-million-to-the-state-during-july-26/">July 2026</a>
    <a href="/other">ignore</a>
    """
    releases = discover_release_links(
        landing, "https://www.mdgaming.com/maryland-sports-wagering/revenue-reports/"
    )
    assert len(releases) == 1
    release_html = """
    <a href="/wp-content/uploads/2026/08/July-2026-Sports-Wagering-Data.xlsx">
      JULY 2026 SPORTS WAGERING DATA (Excel download)
    </a>
    """
    excel = find_report_download(release_html, "https://www.mdgaming.com/release/")
    assert excel["filename"].endswith(".xlsx")
    assert "Sports-Wagering-Data" in excel["filename"]


def test_discovery_follows_older_entries_and_changed_release_title(monkeypatch):
    from types import SimpleNamespace
    from variant_gaming.states import maryland as md

    pages = {
        md.LANDING_URL: "",
        md.ALL_REPORTS_URL: '<a href="/archive/page/2/">Older Entries</a>',
        "https://www.mdgaming.com/archive/page/2/":
            '<a href="/sports-wagering-sets-new-benchmark-in-march/">March 2023</a>',
    }
    monkeypatch.setattr(md, "http_get", lambda url, **kwargs: SimpleNamespace(text=pages[url]))
    releases = md.discover_all_release_links()
    assert len(releases) == 1
    assert releases[0]["link_text"] == "March 2023"


@pytest.mark.parametrize("filename, period, handle, taxable", [
    ("november_2022.xlsx", "2022-11-01", 186084495.57, -38276855.4151),
    ("july_2024.xlsx", "2024-07-01", 324900679.56, 40689198.037775),
])
def test_real_historical_mobile_subtotal(filename, period, handle, taxable):
    rows, meta = parse_mobile_sports_workbook(FIXTURES / filename)
    total = rows[rows.row_type == "official_statewide_total"].iloc[0]
    assert meta["period_start"] == period
    assert total["handle"] == pytest.approx(handle)
    assert total["taxable_revenue"] == pytest.approx(taxable)
    assert "Combined" not in set(rows.operator)
    if period == "2022-11-01":
        assert len(rows) == 8  # seven mobile licensees, one subtotal; no FYTD or retail
        assert rows.loc[rows.operator == "BetMGM", "taxable_revenue"].iloc[0] < 0


def test_changed_mobile_total_fails_reconciliation(sample_bytes):
    from io import BytesIO
    import openpyxl

    workbook = openpyxl.load_workbook(BytesIO(sample_bytes))
    sheet = workbook["July 2026 SW Data"]
    sheet["C22"] = 900000
    content = BytesIO()
    workbook.save(content)
    with pytest.raises(ValueError, match="handle does not reconcile"):
        parse_mobile_sports_workbook(content.getvalue())


def test_accessible_pdf_mobile_table_excludes_fiscal_totals():
    from variant_gaming.states.maryland import parse_mobile_sports_pdf

    rows, meta = parse_mobile_sports_pdf(FIXTURES / "february_2026.pdf")
    assert len(rows) == 14
    assert meta["period_start"] == "2026-02-01"
    total = rows[rows.row_type == "official_statewide_total"].iloc[0]
    assert total["handle"] == 506673876.82
    assert total["taxable_revenue"] == 36196785.84
    assert total["tax"] == 7239357.17
    assert "Riverboat on the Potomac / Bet365 (M)" in set(rows.operator)
    html = '<a href="/February-2026-Sports-Wagering-Data-accessible.pdf">SPORTS WAGERING DATA</a>'
    assert find_report_download(html, "https://www.mdgaming.com")["url"].endswith(".pdf")
