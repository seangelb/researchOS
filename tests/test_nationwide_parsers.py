"""Official retained reports: financial meaning, historic layouts and missingness."""

from pathlib import Path

import pandas as pd
import pytest

from variant_gaming.common import read_transcribed_report
from variant_gaming.states import colorado, kansas, maine, nevada, oregon, rhode_island, vermont, wyoming

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("state,module,filename,period,metric,value", [
    ("KS", kansas, "january_2023.pdf", "2023-01-01", "net_proceeds", 7049537),
    ("KS", kansas, "january_2026.pdf", "2026-01-01", "net_proceeds", 14509154),
    ("KS", kansas, "june_2025.pdf", "2025-06-01", "net_proceeds", 16882901),
    ("KS", kansas, "august_2025_scan.pdf", "2025-08-01", "net_proceeds", 11922508),
    ("NV", nevada, "january_2020.pdf", "2020-01-01", "gross_revenue", 11234000),
    ("NV", nevada, "july_2026.pdf", "2026-07-01", "gross_revenue", 37302000),
    ("OR", oregon, "july_2026.pdf", "2026-07-01", "gross_revenue", 10424591),
    ("VT", vermont, "january_2024.pdf", "2024-01-01", "adjusted_revenue", 3554437),
    ("VT", vermont, "june_2024.pdf", "2024-06-01", "adjusted_revenue", 922968),
    ("VT", vermont, "november_2024_fonts.pdf", "2024-11-01", "adjusted_revenue", 2055930),
    ("WY", wyoming, "december_2021.pdf", "2021-12-01", "gross_revenue", 813504.21),
    ("WY", wyoming, "january_2024.pdf", "2024-01-01", "gross_revenue", 2662549.56),
    ("WY", wyoming, "split_table_2024.pdf", "2024-04-01", "gross_revenue", 1499242.19),
    ("WY", wyoming, "july_2025.pdf", "2025-07-01", "gross_revenue", 1372017.91),
    ("WY", wyoming, "january_2026.pdf", "2026-01-01", "gross_revenue", 2844266.7),
    ("CO", colorado, "january_2026.pdf", "2026-01-01", "net_proceeds", 49929825.91),
    ("CO", colorado, "december_2021.pdf", "2021-12-01", "gross_revenue", 24576031.99),
    ("CO", colorado, "june_2024_scan.pdf", "2024-06-01", "gross_revenue", 28085098.16),
])
def test_printed_online_monthly_totals(state, module, filename, period, metric, value):
    rows = module.parse_report(FIXTURES / state / filename)
    total = rows[rows.row_type == "official_statewide_total"]
    assert len(total) == 1
    assert total.iloc[0][metric] == pytest.approx(value)
    assert set(rows.period_start) == {period}
    assert set(rows.channel) == {"online"}
    assert set(rows.frequency) == {"monthly"}
    if state in {"KS", "VT"} or filename == "january_2026.pdf" and state == "CO":
        assert "gross_revenue" not in rows or rows.gross_revenue.isna().all()


def test_maine_blank_prelaunch_and_future_months_are_not_ytd_rows():
    older = maine.parse_report(FIXTURES / "ME/penobscot_2023.pdf")
    assert older.period_start.tolist() == ["2023-11-01", "2023-12-01"]
    assert older.adjusted_revenue.tolist() == pytest.approx([382373.51, 269154.10])
    current = maine.parse_report(FIXTURES / "ME/passamaquoddy_2026.pdf")
    assert len(current) == 7
    assert current.iloc[-1].adjusted_revenue == pytest.approx(4806585.23)
    assert set(current.row_type) == {"operator"}


def test_rhode_island_excludes_retail_fiscal_totals_and_unreported_months():
    older = rhode_island.parse_report(FIXTURES / "RI/sports_fy2020.pdf")
    assert len(older) == 10
    assert older.iloc[0].gross_revenue == 512740
    assert older.iloc[0].period_start == "2019-09-01"
    changed_name = rhode_island.parse_report(FIXTURES / "RI/sports_fy2024.pdf")
    assert len(changed_name) == 12
    casino = rhode_island.parse_report(FIXTURES / "RI/casino_fy2024.pdf", "online_casino")
    assert len(casino) == 4
    assert casino.iloc[0].gross_revenue == 1210547
    assert casino.iloc[0].period_start == "2024-03-01"


def test_wyoming_negative_values_and_omitted_tax_stay_distinct():
    rows = wyoming.parse_report(FIXTURES / "WY/december_2023_missing_tax.pdf")
    assert rows.tax.isna().all()
    assert rows.loc[rows.operator == "BetMGM", "taxable_revenue"].iloc[0] == -60692.09
    split = wyoming.parse_report(FIXTURES / "WY/split_table_2024.pdf")
    caesars = split[split.operator == "Caesars"].iloc[0]
    assert caesars.gross_revenue == -25420.55
    assert caesars.taxable_revenue == -35553.09


def test_kansas_transcribed_dash_is_not_zero_or_negative_carryover():
    rows = kansas.parse_report(FIXTURES / "KS/august_2025_scan.pdf")
    bet365 = rows[rows.operator.str.contains("BET365")].iloc[0]
    assert pd.isna(bet365.net_proceeds)
    assert pd.isna(bet365.tax)
    assert bet365.handle == 5472548
    assert set(rows.report_status) == {"visually_transcribed"}


def test_transcription_requires_identical_report_bytes(tmp_path):
    source = FIXTURES / "KS/august_2025_scan.pdf"
    changed = tmp_path / "changed.pdf"
    changed.write_bytes(source.read_bytes() + b"\nchanged")
    assert read_transcribed_report(source) is not None
    assert read_transcribed_report(changed) is None


def test_suppressed_or_unreadable_nevada_is_never_zero():
    for name in ["april_2020_suppressed.pdf", "july_2025_image_heading.pdf"]:
        with pytest.raises(ValueError, match="Nevada"):
            nevada.parse_report(FIXTURES / "NV" / name)


def test_wrong_report_does_not_create_vermont_money():
    with pytest.raises(ValueError, match="Vermont"):
        vermont.parse_report(FIXTURES / "CO/december_2021.pdf")


def test_revised_discovery_links():
    html = '<a href="/content/dam/financial/SportsbookWebsiteData06.2024.pdf">2024</a>'
    assert len(rhode_island.discover_reports(html, "online_sports_betting")) == 1
    assert not rhode_island.discover_reports(html, "online_casino")


def test_new_inventory_and_documented_columns():
    root = FIXTURES.parents[1]
    inventory = pd.read_csv(root / "config/state_gaming_source_inventory.csv")
    assert len(inventory) == 102
    assert inventory.state_code.nunique() == 51
    assert not inventory.duplicated(["state_code", "vertical"]).any()
    assert inventory.status_note.notna().all()
    notes = pd.read_csv(root / "config/state_metric_notes.csv")
    ct = notes[(notes.state_code == "CT") & (notes.vertical == "online_sports_betting")]
    assert {"gross_revenue", "adjusted_revenue", "taxable_revenue", "tax"} <= set(ct.storage_column)
