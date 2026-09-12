"""Louisiana mobile sportsbook Excel parser."""

from io import BytesIO

import pandas as pd
import pytest

from variant_gaming.states.louisiana import discover_mobile_excel_links, parse_mobile_workbook


def test_discover_mobile_excel_links() -> None:
    html = """
    <a href="/files/SB-Mobile-FY26.xlsx">Sportsbook Mobile</a>
    <a href="/files/SB-Retail-FY26.xlsx">Sportsbook Retail</a>
    """
    links = discover_mobile_excel_links(html, "https://lsp.org/")
    assert len(links) == 1
    assert "Mobile" in links[0]["filename"]


def test_parse_mobile_workbook_skips_placeholders() -> None:
    frame = pd.DataFrame(
        [
            ["header", None, "wagers", None, "net", "tax"],
            [pd.Timestamp("2026-01-01"), None, 1000, None, 80, 12],
            [pd.Timestamp("2026-02-01"), None, -1, None, -1, -1],
        ]
    )
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="FY26", header=False, index=False)
    parsed = parse_mobile_workbook(buffer.getvalue())
    assert list(parsed["month"]) == [1]
    assert parsed.iloc[0]["handle"] == 1000.0
    assert parsed.iloc[0]["net_proceeds"] == 80.0


def test_full_fiscal_sheet_rejects_conflicting_printed_dates():
    rows = [["header", None, "wagers", None, "net", "tax"]]
    for date in pd.date_range("2021-07-01", periods=12, freq="MS"):
        rows.append([date, None, 1000, None, -20, 0])
    rows[11][0] = pd.Timestamp("2021-08-01")  # Actual source error in FY22.
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="FY22", header=False, index=False)
    with pytest.warns(UserWarning, match="conflicts with fiscal position"):
        parsed = parse_mobile_workbook(buffer.getvalue())
    assert len(parsed) == 11
    assert not ((parsed.year == 2022) & (parsed.month == 5)).any()
    assert (parsed.net_proceeds == -20).all()
    assert (parsed.tax == 0).all()
