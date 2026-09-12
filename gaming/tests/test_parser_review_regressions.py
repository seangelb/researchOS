"""Regression cases from the September 2026 parser review; fixtures stay unchanged."""
from pathlib import Path

from bs4 import BeautifulSoup
import pandas as pd
import pytest

from variant_gaming.states.delaware import parse_igaming_html
from variant_gaming.states.pennsylvania import parse_interactive_operator_sections
from variant_gaming.states.new_jersey import money_tokens, parse_source_cell, parse_igr_tax_page
from variant_gaming.states.new_hampshire import _extract_mobile_amounts

FIXTURES = Path(__file__).parent / "fixtures"


def de_table():
    soup = BeautifulSoup((FIXTURES / "DE/sample_igaming_2023.html").read_text(encoding="utf-8"), "html.parser")
    table = soup.find("table")
    total = next(row for row in table.find_all("tr") if len(row.find_all("td")) == 13 and not row.find("td").get_text(strip=True))
    return table, total


@pytest.mark.parametrize("index", [1, 3, 6, 9])
def test_de_missing_cell_keeps_all_casino_positions(index):
    table, total = de_table()
    expected = parse_igaming_html(str(table)).set_index("operator")
    total.find_all("td")[index].string = ""
    actual = parse_igaming_html(str(table)).set_index("operator")
    assert list(actual.index) == list(expected.index)
    operator = expected.index[(index - 1) // 3]
    metric = "handle" if (index - 1) % 3 == 0 else "net_proceeds"
    expected.loc[operator, metric] = float("nan")
    pd.testing.assert_frame_equal(actual, expected)


def test_de_missing_physical_cell_rejected():
    table, total = de_table()
    total.find_all("td")[3].decompose()
    with pytest.raises(ValueError, match="column|shape|layout"):
        parse_igaming_html(str(table))


@pytest.mark.parametrize("row,operator,metric", [(9,"PENN NATIONAL","gross_revenue"), (29,"STATEWIDE","gross_revenue"), (7,"PENN NATIONAL","handle"), (30,"STATEWIDE","tax")])
def test_pa_missing_component_is_unknown_not_partial(row, operator, metric):
    frame = pd.read_excel(FIXTURES / "PA/sample_interactive_gaming.xlsx", header=None)
    frame.iloc[row, 2] = None
    actual = parse_interactive_operator_sections(frame).set_index(["operator", "period_start"])
    assert pd.isna(actual.loc[(operator, "2025-07-01"), metric])
    assert actual.loc[(operator, "2025-08-01"), "gross_revenue"] == 1510


@pytest.mark.parametrize("text", ["", "-", "$ -", "100 200"])
def test_nj_casino_absent_or_ambiguous_total_never_uses_line_number(text):
    assert parse_igr_tax_page("Casino Example\n3 Total " + text) is None


@pytest.mark.parametrize("text,value", [("0",0), ("$1,234.56",1234.56), ("-1,234.56",-1234.56), ("($1,234.56)",-1234.56)])
def test_nj_casino_total_numeric_amount(text, value):
    assert parse_igr_tax_page("Casino Example\n3 Total " + text)["gross_revenue"] == value


@pytest.mark.parametrize("text", ["-1,234.56", "$-1,234.56", "-$1,234.56", "$ -1,234.56", "- $ 1,234.56", "(1,234.56)", "($ 1,234.56)"])
def test_nj_negative_formats_are_equivalent(text):
    assert parse_source_cell(text)["value"] == -1234.56


def test_nj_dash_token_does_not_consume_next_amount_sign():
    assert money_tokens("-  -1,234.56  $0") == [None, -1234.56, 0.0]


@pytest.mark.parametrize("text,expected", [("$1,000 ($200) -$100",[1000,-200,-100]), ("4 4,191,929 ($ 2 0,000) -$0",[44191929,-20000,0]), ("$1,000 $-200 - 100",[1000,-200,-100])])
def test_nh_preserves_signed_tokens_and_split_digits(text, expected):
    assert _extract_mobile_amounts(text) == expected

@pytest.mark.parametrize("row,operator", [(9, "PENN NATIONAL"), (29, "STATEWIDE")])
def test_pa_missing_expected_metric_row_is_unknown(row, operator):
    frame = pd.read_excel(FIXTURES / "PA/sample_interactive_gaming.xlsx", header=None)
    frame = frame.drop(index=row).reset_index(drop=True)
    actual = parse_interactive_operator_sections(frame).set_index(["operator", "period_start"])
    assert pd.isna(actual.loc[(operator, "2025-07-01"), "gross_revenue"])


def test_pa_zero_component_is_present_and_negative_is_preserved():
    frame = pd.read_excel(FIXTURES / "PA/sample_interactive_gaming.xlsx", header=None)
    frame.iloc[9, 2] = 0
    frame.iloc[17, 2] = -200
    actual = parse_interactive_operator_sections(frame).set_index(["operator", "period_start"])
    assert actual.loc[("PENN NATIONAL", "2025-07-01"), "gross_revenue"] == -150


def test_pa_missing_statewide_component_stays_unusable_after_consolidation():
    from datetime import datetime, timezone
    from variant_gaming.states.pennsylvania import build_casino_normalized
    from variant_gaming.consolidate import build_state_period_revenue

    frame = pd.read_excel(FIXTURES / "PA/sample_interactive_gaming.xlsx", header=None)
    frame.iloc[29, 2] = None  # GRAND TOTAL, July slots gross revenue.
    parsed = parse_interactive_operator_sections(frame)
    normalized = build_casino_normalized(
        parsed,
        source_url="https://example.test/pa.xlsx",
        source_file="tests/fixtures/PA/sample_interactive_gaming.xlsx",
        source_sha256="fixture-with-missing-component",
        retrieved_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )
    periods = build_state_period_revenue(normalized, metric="gross_revenue").set_index("period_start")
    july = periods.loc["2025-07-01"]
    assert pd.isna(july["gross_revenue"])
    assert pd.isna(july["revenue"])
    assert july["completeness"] == "missing_values"
    assert july["aggregation_source"] == "official_statewide_total"
    # A valid operator subtotal must not replace an incomplete published control total.
    assert periods.loc["2025-08-01", "revenue"] == 1510
    assert periods.loc["2025-08-01", "completeness"] == "reported_total"


@pytest.mark.parametrize("missing_index", [None, 3, 6])
@pytest.mark.parametrize("layout", ["named", "unnamed_statewide", "nested_rows"])
def test_de_combined_casino_metric_headers_preserve_positions(missing_index, layout):
    table, total = de_table()
    expected = parse_igaming_html(str(table)).set_index("operator")
    rows = table.find_all("tr")
    names = [cell.get_text(strip=True) for cell in rows[1].find_all("th")][1:]
    cells = rows[2].find_all("th")
    for index, cell in enumerate(cells[1:]):
        cell.string = names[index // 3] + " " + cell.get_text(strip=True)
    rows[1].decompose()
    if layout == "unnamed_statewide":
        for cell, metric in zip(cells[1:4], ["Amount Played", "Amount Won", "Net"]):
            cell.string = metric
    if layout == "nested_rows":
        # One retained January 2014 table has a stray outer <tr> wrapper.
        wrapper = BeautifulSoup("<tr></tr>", "html.parser").tr
        for row in list(table.find_all("tr")):
            wrapper.append(row.extract())
        table.append(wrapper)
    if missing_index is not None:
        total.find_all("td")[missing_index].string = ""
        expected.loc[expected.index[(missing_index - 1) // 3], "net_proceeds"] = float("nan")
    actual = parse_igaming_html(str(table)).set_index("operator")
    pd.testing.assert_frame_equal(actual, expected)


@pytest.mark.parametrize("text,expected", [
    ("-  100  200", [None, 100, 200]),
    ("100  -  200", [100, None, 200]),
    ("$ -  $100  $200", [None, 100, 200]),
    ("-  -100  200", [None, -100, 200]),
])
def test_nj_row_dash_preserves_following_column(text, expected):
    assert money_tokens(text) == expected


def test_nj_casino_skin_dash_keeps_revenue_with_correct_brand():
    from types import SimpleNamespace
    from variant_gaming.states.new_jersey import parse_igr_skin_page

    page = SimpleNamespace(extract_tables=lambda: [
        [["Line", "Description", "Brand A", "Brand B", "Brand C", "Total"]]
    ])
    rows = parse_igr_skin_page(page, "3 Total -  100  200  300")
    assert [(row["operator"], row["gross_revenue"]) for row in rows] == [
        ("Brand B", 100), ("Brand C", 200)
    ]
