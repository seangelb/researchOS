"""Massachusetts PDF fixture parsing and collector registration."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from variant_gaming.collect import COLLECTORS
from variant_gaming.common import parse_money
from variant_gaming.states import massachusetts
from variant_gaming.storage import connect, ensure_schema

FIXTURES = Path(__file__).parent / "fixtures" / "MA"
MARCH_2023 = FIXTURES / "March-Rev-Report.pdf"
JULY_2026 = FIXTURES / "MGC-Revenue-Report-July-2026.pdf"


def test_march_2023_parses_six_online_operators() -> None:
    operators, total_online, (year, month) = massachusetts.parse_revenue_pdf(
        MARCH_2023.read_bytes(),
        expected_year=2023,
        expected_month=3,
    )
    assert (year, month) == (2023, 3)
    assert len(operators) == 6
    assert sorted(op["operator"] for op in operators) == [
        "Barstool Sportsbook",
        "BetMGM",
        "Caesars Sportsbook",
        "DraftKings",
        "FanDuel",
        "WynnBet",
    ]
    totals = {
        "wagers_settled": sum(op["wagers_settled"] for op in operators),
        "accrual_win": sum(op["accrual_win"] for op in operators),
        "taxable_revenue": sum(op["taxable_revenue"] for op in operators),
        "tax_collected": sum(op["tax_collected"] for op in operators),
    }
    assert round(totals["wagers_settled"], 2) == 548_102_467.42
    assert round(totals["accrual_win"], 2) == 46_783_507.41
    # March 2023 PDF: operator taxable lines sum 1 cent below the official Total Online row.
    assert abs(round(totals["taxable_revenue"], 2) - 45_605_606.69) <= 0.01
    assert round(totals["tax_collected"], 2) == 9_121_121.34
    assert total_online is not None
    assert round(total_online["accrual_win"], 2) == 46_783_507.41
    assert round(total_online["taxable_revenue"], 2) == 45_605_606.69


def test_july_2026_parses_seven_online_operators() -> None:
    operators, total_online, (year, month) = massachusetts.parse_revenue_pdf(
        JULY_2026.read_bytes(),
        expected_year=2026,
        expected_month=7,
    )
    assert (year, month) == (2026, 7)
    assert len(operators) == 7
    assert sorted((op["operator"] for op in operators), key=str.casefold) == [
        "Bally Bet",
        "BetMGM",
        "Caesars Sportsbook",
        "DraftKings",
        "Fanatics",
        "FanDuel",
        "theScore Bet",
    ]
    totals = {
        "wagers_settled": sum(op["wagers_settled"] for op in operators),
        "accrual_win": sum(op["accrual_win"] for op in operators),
        "taxable_revenue": sum(op["taxable_revenue"] for op in operators),
        "tax_collected": sum(op["tax_collected"] for op in operators),
    }
    assert round(totals["wagers_settled"], 2) == 587_101_900.84
    assert round(totals["accrual_win"], 2) == 66_530_607.93
    assert round(totals["taxable_revenue"], 2) == 65_101_989.55
    assert round(totals["tax_collected"], 2) == 13_020_397.81
    assert total_online is not None
    assert round(total_online["accrual_win"], 2) == 66_530_607.93


def test_draftkings_july_2026_values() -> None:
    operators, _, _ = massachusetts.parse_revenue_pdf(JULY_2026.read_bytes(), expected_year=2026, expected_month=7)
    draftkings = next(op for op in operators if op["operator"] == "DraftKings")
    assert draftkings["wagers_settled"] == 281_176_332.44
    assert draftkings["accrual_win"] == 33_004_446.68
    assert draftkings["taxable_revenue"] == 32_324_402.68
    assert draftkings["tax_collected"] == 6_464_880.54


def test_retail_and_total_rows_not_emitted() -> None:
    for path in (MARCH_2023, JULY_2026):
        operators, _, _ = massachusetts.parse_revenue_pdf(path.read_bytes())
        names = {op["operator"] for op in operators}
        assert "Total Retail" not in names
        assert "Total Online" not in names
        assert "Total" not in names
        assert not names.intersection(massachusetts.RETAIL_OPERATORS)


def test_negative_values_remain_negative() -> None:
    assert parse_money("-$1,234.56") == -1234.56
    assert parse_money("$-1,234.56") == -1234.56
    assert parse_money("($1,234.56)") == -1234.56
    row = "$100.00 $200.00 5.00% -$1,234.56 $50.00"
    amounts = massachusetts.money_fields_from_row(row)
    assert amounts is not None
    assert amounts[2] == -1234.56
    accrual_row = "$100.00 -$1,234.56 5.00% $300.00 $50.00"
    accrual_amounts = massachusetts.money_fields_from_row(accrual_row)
    assert accrual_amounts is not None
    assert accrual_amounts[1] == -1234.56
    inline = massachusetts.parse_inline_operator_row(
        "Test Operator $100.00 -$1,234.56 5.00% $300.00 $50.00"
    )
    assert inline is not None
    operator, values = inline
    assert operator == "Test Operator"
    assert values[1] == -1234.56


def test_normalized_rows_retain_printed_total_and_operators() -> None:
    operators, total_online, (year, month) = massachusetts.parse_revenue_pdf(
        JULY_2026.read_bytes(),
        expected_year=2026,
        expected_month=7,
    )
    retrieved_at = datetime(2026, 9, 3, 2, 38, 16, tzinfo=timezone.utc)
    frame = massachusetts.build_normalized_rows(
        operators,
        year=year,
        month=month,
        source_url="https://massgaming.com/wp-content/uploads/MGC-Revenue-Report-July-2026.pdf",
        source_file="tests/fixtures/MA/MGC-Revenue-Report-July-2026.pdf",
        source_sha256="fixture-sha256",
        retrieved_at=retrieved_at,
        total_online=total_online,
    )
    draftkings = next(op for op in operators if op["operator"] == "DraftKings")
    row = frame.loc[frame["operator"] == "DraftKings"].iloc[0]
    assert row["gross_revenue"] == draftkings["accrual_win"]
    assert row["handle"] == draftkings["wagers_settled"]
    assert row["taxable_revenue"] == draftkings["taxable_revenue"]
    assert row["tax"] == draftkings["tax_collected"]
    assert row["reported_revenue_name"] == massachusetts.REPORTED_REVENUE_NAME
    assert "Accrual Win" in row["reported_revenue_name"]
    assert "Taxable Gaming Revenue" in row["reported_revenue_name"]
    assert row["source_url"].endswith("MGC-Revenue-Report-July-2026.pdf")
    assert row["source_file"] == "tests/fixtures/MA/MGC-Revenue-Report-July-2026.pdf"
    assert row["source_sha256"] == "fixture-sha256"
    assert row["retrieved_at_utc"] == retrieved_at.isoformat()

    operators_out = frame[frame["row_type"] == "operator"]
    official = frame[frame["row_type"] == "official_statewide_total"]
    assert len(operators_out) == 7
    assert len(official) == 1
    assert official.iloc[0]["operator"] == "STATEWIDE"
    assert official.iloc[0]["report_status"] == "reconciled_printed_total"
    assert round(float(official.iloc[0]["gross_revenue"]), 2) == 66_530_607.93
    assert round(float(official.iloc[0]["taxable_revenue"]), 2) == 65_101_989.55
    assert round(float(official.iloc[0]["handle"]), 2) == 587_101_900.84
    assert round(float(official.iloc[0]["tax"]), 2) == 13_020_397.81
    # Printed control values, not a fresh operator-sum approximation.
    assert round(float(official.iloc[0]["gross_revenue"]), 2) == round(total_online["accrual_win"], 2)
    assert round(float(official.iloc[0]["taxable_revenue"]), 2) == round(
        total_online["taxable_revenue"], 2
    )


def test_consolidation_prefers_ma_printed_total_without_double_counting() -> None:
    from variant_gaming.consolidate import build_state_period_revenue

    operators, total_online, (year, month) = massachusetts.parse_revenue_pdf(
        JULY_2026.read_bytes(),
        expected_year=2026,
        expected_month=7,
    )
    frame = massachusetts.build_normalized_rows(
        operators,
        year=year,
        month=month,
        source_url="https://massgaming.com/wp-content/uploads/MGC-Revenue-Report-July-2026.pdf",
        source_file="tests/fixtures/MA/MGC-Revenue-Report-July-2026.pdf",
        source_sha256="fixture-sha256",
        retrieved_at=datetime(2026, 9, 3, 2, 38, 16, tzinfo=timezone.utc),
        total_online=total_online,
    )
    period = build_state_period_revenue(frame).iloc[0]
    assert period["aggregation_source"] == "official_statewide_total"
    assert period["completeness"] == "reported_total"
    assert period["operator_count"] == 7
    assert round(float(period["revenue"]), 2) == 66_530_607.93
    # Must equal the printed control row, not operator_sum + official.
    assert round(float(period["gross_revenue"]), 2) == round(total_online["accrual_win"], 2)


def test_accrual_win_reconciliation_failure_raises() -> None:
    operators = [
        {
            "operator": "DraftKings",
            "wagers_settled": 100.0,
            "accrual_win": 10.0,
            "taxable_revenue": 9.0,
            "tax_collected": 1.0,
        }
    ]
    total_online = {
        "wagers_settled": 100.0,
        "accrual_win": 10.02,
        "taxable_revenue": 9.0,
        "tax_collected": 1.0,
    }
    with pytest.raises(ValueError, match="Online total mismatch for accrual_win"):
        massachusetts.reconcile_operators(operators, total_online)


def test_june_2026_archive_link_is_corrected() -> None:
    reports = massachusetts.apply_url_corrections(
        [
            {
                "url": "https://massgaming.com/wp-content/uploads/MGC-Revenue-Report-May-2026.pdf",
                "filename": "MGC-Revenue-Report-May-2026.pdf",
                "link_text": "June",
                "expected_year": 2026,
                "expected_month": 6,
            }
        ]
    )
    assert reports[0]["url"] == massachusetts.URL_CORRECTIONS[(2026, 6)]
    assert reports[0]["filename"] == "MGC-Revenue-Report-June-2026.pdf"


def test_collector_registered_for_massachusetts() -> None:
    assert COLLECTORS[("MA", "online_sports_betting")] is massachusetts.collect_history


def test_no_duplicate_operator_period_rows_in_one_run(tmp_path: Path, monkeypatch) -> None:
    march_bytes = MARCH_2023.read_bytes()

    class FakeResponse:
        def __init__(self, content: bytes):
            self.content = content
            self.text = ""

        def raise_for_status(self) -> None:
            return None

    def fake_http_get(url, session=None, **kwargs):
        return FakeResponse(march_bytes)

    monkeypatch.setattr(
        massachusetts,
        "discover_all_reports",
        lambda session=None: [
            {
                "url": "https://massgaming.com/wp-content/uploads/March-Rev-Report.pdf",
                "filename": "March-Rev-Report.pdf",
                "link_text": "March",
                "expected_year": 2023,
                "expected_month": 3,
            }
        ],
    )
    monkeypatch.setattr(massachusetts, "http_get", fake_http_get)

    result = massachusetts.collect_history(root=tmp_path, db_path=tmp_path / "gaming.sqlite")
    assert len(result) == 7  # 6 operators + printed Total Online
    assert (result["row_type"] == "official_statewide_total").sum() == 1
    keys = result[["operator", "period_start", "row_type"]].drop_duplicates()
    assert len(keys) == len(result)


def test_february_2023_empty_online_rows_allowed(monkeypatch) -> None:
    fake_pdf = MagicMock()
    fake_pdf.pages = []
    monkeypatch.setattr(
        massachusetts.pdfplumber,
        "open",
        lambda *args, **kwargs: MagicMock(__enter__=lambda s: fake_pdf, __exit__=lambda *a: None),
    )
    monkeypatch.setattr(massachusetts, "parse_report_period", lambda text: (2023, 2))
    monkeypatch.setattr(massachusetts, "find_online_operator_text", lambda pdf: "")
    operators, total_online, period = massachusetts.parse_revenue_pdf(b"%PDF-1.4 fake")
    assert operators == []
    assert total_online is None
    assert period == (2023, 2)


def test_post_launch_missing_online_section_raises(monkeypatch) -> None:
    fake_pdf = MagicMock()
    fake_pdf.pages = []
    monkeypatch.setattr(
        massachusetts.pdfplumber,
        "open",
        lambda *args, **kwargs: MagicMock(__enter__=lambda s: fake_pdf, __exit__=lambda *a: None),
    )
    monkeypatch.setattr(massachusetts, "parse_report_period", lambda text: (2023, 3))
    monkeypatch.setattr(massachusetts, "find_online_operator_text", lambda pdf: "")
    with pytest.raises(ValueError, match="Missing ONLINE LICENSEE section for 2023-03"):
        massachusetts.parse_revenue_pdf(b"%PDF-1.4 fake", expected_year=2023, expected_month=3)


def test_post_launch_zero_operators_raises(monkeypatch) -> None:
    fake_pdf = MagicMock()
    fake_pdf.pages = []
    monkeypatch.setattr(
        massachusetts.pdfplumber,
        "open",
        lambda *args, **kwargs: MagicMock(__enter__=lambda s: fake_pdf, __exit__=lambda *a: None),
    )
    monkeypatch.setattr(massachusetts, "parse_report_period", lambda text: (2024, 1))
    monkeypatch.setattr(
        massachusetts,
        "find_online_operator_text",
        lambda pdf: "ONLINE LICENSEE\nTotal",
    )
    monkeypatch.setattr(massachusetts, "parse_online_section", lambda text: ([], None))
    with pytest.raises(ValueError, match="No Category 3 online operators parsed for 2024-01"):
        massachusetts.parse_revenue_pdf(b"%PDF-1.4 fake", expected_year=2024, expected_month=1)


def test_missing_reporting_month_is_detected() -> None:
    discovered = [(2023, 3), (2023, 4), (2023, 5)]
    parsed = [(2023, 3), (2023, 5)]
    assert massachusetts.missing_reporting_months(discovered, parsed) == [(2023, 4)]


def test_coverage_partial_when_month_missing(tmp_path: Path, monkeypatch) -> None:
    march_bytes = MARCH_2023.read_bytes()

    class FakeResponse:
        def __init__(self, content: bytes):
            self.content = content
            self.text = ""

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        massachusetts,
        "discover_all_reports",
        lambda session=None: [
            {
                "url": "https://massgaming.com/wp-content/uploads/March-Rev-Report.pdf",
                "filename": "March-Rev-Report.pdf",
                "link_text": "March",
                "expected_year": 2023,
                "expected_month": 3,
            },
            {
                "url": "https://massgaming.com/wp-content/uploads/April-Rev-Report.pdf",
                "filename": "April-Rev-Report.pdf",
                "link_text": "April",
                "expected_year": 2023,
                "expected_month": 4,
            },
        ],
    )
    monkeypatch.setattr(massachusetts, "http_get", lambda url, session=None, **kwargs: FakeResponse(march_bytes))

    # April download returns March PDF bytes; heading mismatch fails that month.
    result = massachusetts.collect_history(root=tmp_path, db_path=tmp_path / "gaming.sqlite")
    assert len(result[result["row_type"] == "operator"]) == 6
    assert (result["row_type"] == "official_statewide_total").sum() == 1
    conn = connect(tmp_path / "gaming.sqlite")
    ensure_schema(conn)
    coverage = conn.execute(
        "SELECT status, reason, earliest_period, latest_period FROM source_coverage WHERE state_code='MA'"
    ).fetchone()
    conn.close()
    assert coverage[0] == "partial"
    assert "missing report 2023-04" in coverage[1]
    assert coverage[2] == "2023-03-01"
    assert coverage[3] == "2023-03-31"


def test_coverage_dates_derived_from_parsed_rows() -> None:
    frame = pd.DataFrame(
        {
            "period_start": ["2023-03-01", "2026-07-01"],
            "period_end": ["2023-03-31", "2026-07-31"],
        }
    )
    earliest, latest = massachusetts.coverage_bounds_from_rows(frame)
    assert earliest == "2023-03-01"
    assert latest == "2026-07-31"
    assert massachusetts.coverage_bounds_from_rows(pd.DataFrame()) == (None, None)
