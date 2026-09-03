"""Connecticut source-CSV parsers (operator rows only)."""

from datetime import datetime, timezone

from variant_gaming.states.connecticut import build_normalized_rows, parse_casino_csv, parse_sports_csv

SPORTS_CSV = """Month Ending,Licensee,Wagers,Total Gross Gaming Revenue,Payment (7)
2026-06-30,DraftKings,"1,000,000","80,000","5,600"
2026-06-30,FanDuel,"2,000,000","-10,000","-700"
"""

CASINO_CSV = """Month Ending,Licensee,Wagers,Total Gross Gaming Revenue,Payment (5)
2026-06-30,Mohegan,"500,000","40,000","7,200"
"""


def test_parse_sports_preserves_negative_ggr() -> None:
    parsed = parse_sports_csv(SPORTS_CSV)
    assert len(parsed) == 2
    fanduel = parsed.loc[parsed["operator"] == "FanDuel"].iloc[0]
    assert fanduel["gross_revenue"] == -10000.0
    assert fanduel["handle"] == 2000000.0


def test_parse_casino_csv() -> None:
    parsed = parse_casino_csv(CASINO_CSV)
    assert parsed.iloc[0]["operator"] == "Mohegan"
    assert parsed.iloc[0]["tax"] == 7200.0


def test_normalized_rows_are_operators_only() -> None:
    parsed = parse_sports_csv(SPORTS_CSV)
    frame = build_normalized_rows(
        parsed,
        vertical="online_sports_betting",
        source_url="https://data.ct.gov/example",
        source_file="ct.csv",
        source_sha256="abc",
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert set(frame["row_type"]) == {"operator"}
    assert "derived_statewide_total" not in set(frame["row_type"])
    assert frame["reported_revenue_name"].iloc[0] == "Total Gross Gaming Revenue"
