"""Ohio Type A Online month-sheet parser."""

import pandas as pd

from variant_gaming.states.ohio import discover_sports_excel_links, parse_month_sheet


def test_discover_sports_excel_links() -> None:
    html = """
    <a href="/files/Sports-Gaming-2026.xlsx">2026 Sports Gaming</a>
    <a href="/files/Casino-Only-2026.xlsx">Casino Revenue</a>
    """
    links = discover_sports_excel_links(html, "https://casinocontrol.ohio.gov/")
    assert len(links) == 1
    assert links[0]["year_hint"] == 2026


def test_parse_month_sheet_online_only() -> None:
    frame = pd.DataFrame(
        [
            ["Online Proprietor", "Handle", None, None, None, "Revenue", "Taxable Revenue"],
            ["Type A Proprietors - Online", None, None, None, None, None, None],
            ["FanDuel", 1000, None, None, None, 80, 70],
            ["DraftKings", 2000, None, None, None, 120, 110],
            ["Subtotal Type A Online", 3000, None, None, None, 200, 180],
            ["Type A Proprietors - Retail", None, None, None, None, None, None],
            ["Retail Book", 50, None, None, None, 5, 4],
        ]
    )
    parsed = parse_month_sheet(frame, year=2026, month=1)
    assert set(parsed["row_type"]) == {"operator", "official_statewide_total"}
    assert "Retail Book" not in set(parsed["operator"])
    statewide = parsed.loc[parsed["row_type"] == "official_statewide_total"].iloc[0]
    assert statewide["handle"] == 3000.0
    assert statewide["gross_revenue"] == 200.0
