"""Independent arithmetic and the failure cases that change a business conclusion."""
import json
from pathlib import Path

import pandas as pd
import pytest

from variant_gaming.flut_scorecard import SPORTS, CASINO, CONTRACTS
from variant_gaming.industry import (
    NATIVE_OPERATORS, build_industry_tables, business_update, ny_weekly_panel,
    operator_scope, export_research_note,
)


def native_month(period, *, state="MA", vertical=SPORTS, fd=40, dk=30, czr=10, other=20):
    month = pd.Period(period, freq="M")
    population = [(NATIVE_OPERATORS["FLUT"][state, vertical], fd),
                  (NATIVE_OPERATORS["DKNG"][state, vertical], dk), ("Other", other)]
    population.append(("Caesars Sportsbook" if state == "MA" else "Other MI license", czr))
    population.append(("STATEWIDE", fd + dk + czr + other))
    return pd.DataFrame([dict(state_code=state, vertical=vertical, channel="online", frequency="monthly",
        operator=name, row_type="official_statewide_total" if name == "STATEWIDE" else "operator",
        period_start=str(month.start_time.date()), period_end=str(month.end_time.date()),
        handle=amount, gross_revenue=amount/10, adjusted_revenue=amount/20, taxable_revenue=amount/25,
        reported_revenue_name=next(iter(CONTRACTS[state, vertical]["labels"])),
        report_status="reconciled_printed_total" if state == "MA" and name == "STATEWIDE" else "ok",
        source_url="https://official.example/source", source_file="data/raw/source",
        source_sha256="a"*64, retrieved_at_utc="2026-09-01T00:00:00Z") for name, amount in population])


def paired(**kwargs):
    return pd.concat([native_month("2026-07", **kwargs), native_month("2025-07", fd=20, dk=40, czr=10, other=30,
                                                                   state=kwargs.get("state", "MA"), vertical=kwargs.get("vertical", SPORTS))])


def tables(rows, through="2026-07"):
    return build_industry_tables(rows, quarter="2026Q3", through_month=through)


def test_market_demand_and_brand_position_can_point_in_different_directions():
    result = tables(paired(fd=40, dk=30, czr=10, other=120))
    demand = result["demand"].query("state_code == 'MA'").iloc[0]
    brands = result["competition"].query("state_code == 'MA'").set_index("company")
    assert demand.market_growth_pct == 100
    assert brands.loc["FLUT", "company_growth_pct"] == 100
    assert brands.loc["FLUT", "share_change_pp"] == 0
    assert brands.loc["DKNG", "share_change_pp"] == -25
    assert brands.loc["CZR", "company_share_pct"] == 5
    assert brands.loc["DKNG", "share_direction"] == "losing"


def test_a_missing_or_changed_brand_identity_does_not_destroy_valid_market_evidence():
    rows = paired()
    rows.loc[rows.operator.eq("FanDuel"), "operator"] = "Unmapped license"
    result = tables(rows)
    assert result["demand"].query("state_code == 'MA'").iloc[0].status == "comparable"
    assert result["competition"].query("state_code == 'MA' and company == 'FLUT'").iloc[0].status == "incomplete_comparison"
    assert result["competition"].query("state_code == 'MA' and company == 'DKNG'").iloc[0].status == "comparable"


def test_michigan_native_labels_do_not_fuzzily_aggregate_golden_nugget_or_caesars():
    rows = paired(state="MI", vertical=CASINO)
    result = tables(rows)
    dk = result["casino"].query("company == 'DKNG' and metric == 'gross_revenue'").iloc[0]
    assert dk.company_amount == 3 and dk.prior_company_amount == 4
    assert result["casino"].query("company == 'CZR'").status.eq("unmapped_company_scope").all()
    assert operator_scope().query("company == 'CZR' and state_code == 'MI'").native_operator.isna().all()


def test_missing_august_blocks_requested_window_and_business_prose_explains_it():
    result = tables(paired(), through="2026-08")
    demand = result["demand"].query("state_code == 'MA'").iloc[0]
    assert demand.missing_or_excluded_months == "2026-08"
    assert pd.isna(demand.market_amount) and demand.direction == "unavailable"
    update = business_update(result)
    assert "2026-08" in update.iloc[0].what_changed
    assert "2 of 3" in update.iloc[0].uncertainty
    assert "unmapped_company_scope" not in " ".join(update.what_changed)


def test_gross_hold_preserves_losses_separately_from_demand_and_adjusted_revenue():
    rows = paired()
    rows.loc[rows.period_start.eq("2026-07-01"), "gross_revenue"] *= -1
    result = tables(rows)
    hold = result["hold"].query("company == 'FLUT' and state_code == 'MA'").iloc[0]
    assert hold.company_hold_pct == -10 and hold.hold_change_pp == -20
    assert result["demand"].query("state_code == 'MA'").iloc[0].direction == "unchanged"
    assert result["quarterly"].query("company == 'FLUT' and state_code == 'MA' and metric == 'taxable_revenue'").iloc[0].company_amount == 1.6


def week(end="2026-09-06"):
    rows = native_month("2026-07")
    rows["state_code"], rows["frequency"], rows["reported_revenue_name"] = "NY", "weekly", "GGR"
    rows["report_status"] = "ok"
    rows["operator"] = rows.operator.replace({"DraftKings": "DraftKings Sport Book", "Caesars Sportsbook": "Caesars Sport Book"})
    rows["period_end"] = end
    rows["period_start"] = (pd.Timestamp(end) - pd.Timedelta(days=6)).date().isoformat()
    rows["source_sha256"] = [str(i)*64 for i in range(len(rows))]  # Separate native NY workbooks.
    return rows


def test_ny_separate_sources_reconcile_and_missing_weeks_are_not_hidden():
    result = ny_weekly_panel(week(), weeks=2)
    assert len(result) == 12
    assert result.query("period_end == '2026-08-30'").reason.eq("missing_week").all()
    row = result.query("period_end == '2026-09-06' and company == 'DKNG' and metric == 'handle'").iloc[0]
    assert row.company_share_pct == 30 and row.status == "eligible"
    assert len(row.source_refs) == 5


@pytest.mark.parametrize("change,expected", [("revision", "conflicting_source_versions"),
    ("missing_operator", "operator_total_does_not_reconcile"), ("invalid_week", "not_one_complete_monday_sunday_week"),
    ("negative_handle", "negative_handle")])
def test_ny_rejects_actual_failures_without_changing_frequency(change, expected):
    rows = week()
    if change == "revision":
        revision = rows.iloc[[0]].copy()
        revision["source_sha256"], revision["handle"] = "z"*64, 999
        rows = pd.concat([rows, revision])
    elif change == "missing_operator":
        rows = rows[~rows.operator.eq("Other")]
    elif change == "invalid_week":
        rows.loc[0, "period_start"] = "2026-08-30"
    else:
        rows.loc[0, "handle"] = -1
    result = ny_weekly_panel(rows, weeks=1)
    assert result.query("metric == 'handle'").reason.eq(expected).all()


def test_research_note_export_is_explicit_dated_and_never_overwrites(tmp_path):
    with pytest.raises(ValueError):
        export_research_note("research", tmp_path / "undated.md")
    destination = tmp_path / "industry_20260912.md"
    export_research_note("research", destination)
    with pytest.raises(FileExistsError):
        export_research_note("edited", destination)
    assert destination.read_text() == "research"


def test_one_daily_notebook_and_optional_reference_use_the_common_loader():
    root = Path(__file__).resolve().parents[1]
    assert len(list((root / "notebooks").glob("94_*.ipynb"))) == 1
    for prefix in ["90", "94", "95", "96"]:
        book = json.loads(next((root / "notebooks").glob(f"{prefix}_*.ipynb")).read_text(encoding="utf-8"))
        source = "\n".join("".join(cell["source"]) for cell in book["cells"] if cell["cell_type"] == "code")
        assert "config/current_snapshot.json" in source and "load_validated_snapshot(" in source
        assert "refresh_20260912" not in source
