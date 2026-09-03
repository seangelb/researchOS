"""Louisiana mobile sportsbook Excel parser."""

from io import BytesIO

import pandas as pd

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
