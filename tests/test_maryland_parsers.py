"""Focused parser tests for Maryland mobile sports-wagering Excel workbooks."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from variant_gaming.states.maryland import (
    REPORTED_REVENUE_NAME,
    build_normalized_rows,
    discover_release_links,
    find_excel_download,
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
    excel = find_excel_download(release_html, "https://www.mdgaming.com/release/")
    assert excel["filename"].endswith(".xlsx")
    assert "Sports-Wagering-Data" in excel["filename"]
