"""Fixed-window arithmetic, like-for-like rate changes and visible gaps."""
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from test_industry_update import native_month
from variant_gaming.flut_scorecard import SPORTS, CASINO
from variant_gaming.industry import build_industry_tables, caesars_michigan_monthly, CZR_GRAND_TRAVERSE, CZR_SAULT_CASINO
from variant_gaming.monthly_review import build_monthly_review, fundamentals_update


@pytest.fixture
def source_tables():
    values = {"2026-05": (50, 100), "2026-06": (20, 200), "2026-07": (70, 700),
              "2025-05": (10, 50), "2025-06": (20, 50), "2025-07": (30, 100)}
    for month in ["02", "03", "04"]:
        values[f"2026-{month}"] = (20, 100)
        values[f"2025-{month}"] = (10, 50)
    rows = []
    for month in pd.period_range("2024-06", "2026-07", freq="M").astype(str):
        amount, market = values.get(month, (20, 100))
        rows.append(native_month(month, fd=amount, dk=10, czr=5, other=market-amount-15))
    return build_industry_tables(pd.concat(rows), quarter="2026Q3", through_month="2026-07")


def one(frame, *, end="2026-07", company="FLUT", metric="handle"):
    return frame[(frame.company == company) & (frame.state_code == "MA") & (frame.metric == metric) & (frame.end_month == end)].iloc[0]


def test_twelve_months_fixed_three_month_ratios_and_distinct_rate_comparisons(source_tables):
    review = build_monthly_review(source_tables, end_month="2026-07")
    assert set(review["monthly"].end_month) == set(pd.period_range("2025-08", "2026-07", freq="M").astype(str))
    row = one(review["assessment"])
    assert row.expected_months == "2026-05, 2026-06, 2026-07"
    assert row.prior_months == "2025-05, 2025-06, 2025-07"
    assert row.amount == 140 and row.prior_amount == 60
    assert row.share_pct == 14 and row.prior_share_pct == 30
    assert row.share_change_pp == -16  # Not the average of monthly shares.
    assert row.yoy_growth_pct == pytest.approx(100*(140/60-1))
    assert row.preceding3_end_month == "2026-04"
    assert row.growth_rate_change_pp == pytest.approx(100*(140/60-1)-100)
    assert row.previous_read_end_month == "2026-06"
    assert row.read_change_yoy_pp == pytest.approx(100*(140/60-1)-125)
    assert one(review["assessment"], company="MARKET").yoy_growth_pct == 400


@pytest.mark.parametrize("field,change,reason", [("status", "excluded", "source failure"),
    ("native_metric", "Unknown net figure", "native_metric_mismatch"),
    ("company_amount", float("inf"), "missing_or_nonfinite_amount")])
def test_bad_prior_month_keeps_current_amount_but_blocks_all_dependent_changes(source_tables, field, change, reason):
    monthly = source_tables["monthly"]
    selected = monthly.company.eq("FLUT") & monthly.state_code.eq("MA") & monthly.metric.eq("handle") & monthly.period_start.eq("2025-06-01")
    monthly.loc[selected, field] = change
    if field == "status":
        monthly.loc[selected, "reason"] = reason
    result = build_monthly_review(source_tables, end_month="2026-07")
    row = one(result["assessment"])
    assert row.amount == 140 and row.share_pct == 14
    assert pd.isna(row.prior_amount) and pd.isna(row.yoy_growth_pct)
    assert pd.isna(row.share_change_pp) and pd.isna(row.growth_rate_change_pp) and pd.isna(row.read_change_yoy_pp)
    assert row.prior_missing_months == "2025-06" and reason in row.gap_details
    assert one(result["monthly"], end="2026-06").amount == 20


def test_gap_is_not_shrunk_and_duplicate_month_is_not_double_counted(source_tables):
    monthly = source_tables["monthly"]
    selected = monthly.company.eq("FLUT") & monthly.state_code.eq("MA") & monthly.metric.eq("handle") & monthly.period_start.eq("2026-06-01")
    source_tables["monthly"] = pd.concat([monthly, monthly[selected]], ignore_index=True)
    row = one(build_monthly_review(source_tables, end_month="2026-07")["assessment"])
    assert row.missing_months == "2026-06" and row.observed_months == 2
    assert pd.isna(row.amount) and "duplicate_month" in row.gap_details


def test_acceleration_needs_complete_preceding_nonoverlapping_window(source_tables):
    monthly = source_tables["monthly"]
    mask = monthly.company.eq("FLUT") & monthly.state_code.eq("MA") & monthly.metric.eq("handle") & monthly.period_start.eq("2025-02-01")
    source_tables["monthly"] = monthly[~mask]
    row = one(build_monthly_review(source_tables, end_month="2026-07")["assessment"])
    assert row.status == "comparable" and pd.notna(row.yoy_growth_pct)
    assert pd.isna(row.growth_rate_change_pp) and row.growth_rate_direction == "unavailable"
    assert "2025-02" in row.preceding3_gap_details
    assert pd.notna(row.read_change_yoy_pp)  # April-June comparison remains independently available.


def test_nonpositive_baselines_and_losses_do_not_become_false_growth_or_hold(source_tables):
    monthly = source_tables["monthly"]
    old = monthly.company.eq("FLUT") & monthly.state_code.eq("MA") & monthly.metric.eq("gross_revenue") & monthly.period_start.between("2025-05-01", "2025-07-01")
    monthly.loc[old, "company_amount"] = 0
    now = monthly.company.eq("FLUT") & monthly.state_code.eq("MA") & monthly.metric.eq("gross_revenue") & monthly.period_start.between("2026-05-01", "2026-07-01")
    monthly.loc[now, "company_amount"] *= -1
    row = one(build_monthly_review(source_tables, end_month="2026-07")["assessment"], metric="gross_revenue")
    assert row.amount == -14 and row.prior_amount == 0
    assert pd.isna(row.yoy_growth_pct) and row.hold_pct == -10
    assert row.prior_hold_pct == 0 and row.hold_change_pp == -10


def test_supporting_volume_and_contrary_share_are_visible_together(source_tables):
    review = build_monthly_review(source_tables, end_month="2026-07")
    row = fundamentals_update(review).query("subject == 'FLUT'").iloc[0]
    assert "+133.3%" in row.supporting_evidence
    assert "-16.00 pp" in row.contrary_evidence
    assert "same retained capture" in row.uncertainty
    assert "not a company profit forecast" in row.interpretation


def mi_license_month(period, vertical, *, new_names=False):
    rows = native_month(period, state="MI", vertical=vertical)
    rows["operator"] = rows.operator.replace({"Other": CZR_GRAND_TRAVERSE[int(new_names)], "Other MI license": CZR_SAULT_CASINO[int(new_names)]})
    return rows


@pytest.mark.parametrize("vertical,before,after,amount", [(SPORTS, "2021-04", "2021-05", 20),
                                                       (CASINO, "2024-06", "2024-07", 3)])
def test_caesars_ownership_boundaries_and_no_sault_sportsbook(vertical, before, after, amount):
    result = caesars_michigan_monthly(pd.concat([mi_license_month(before, vertical), mi_license_month(after, vertical)]))
    metric = "handle" if vertical == SPORTS else "gross_revenue"
    excluded = result[(result.period_start.str[:7] == before) & (result.metric == metric)].iloc[0]
    allowed = result[(result.period_start.str[:7] == after) & (result.metric == metric)].iloc[0]
    assert excluded.reason == "before_reviewed_full_ownership_month" and pd.isna(excluded.fd_amount)
    assert allowed.fd_amount == amount


def test_caesars_alias_change_preserves_actual_names_and_rejects_duplicate_aliases():
    old, new = mi_license_month("2025-07", CASINO), mi_license_month("2026-07", CASINO, new_names=True)
    result = caesars_michigan_monthly(pd.concat([old, new]))
    assert result.status.eq("eligible").all()
    assert "William Hill" in result.iloc[0].fd_operator and "Wynn" in result.iloc[0].fd_operator
    assert "Caesars/WSOP" in result.iloc[-1].fd_operator and "Caesars Horseshoe" in result.iloc[-1].fd_operator
    duplicate = new[new.operator.eq(CZR_GRAND_TRAVERSE[1])].copy()
    duplicate["operator"] = CZR_GRAND_TRAVERSE[0]
    rejected = caesars_michigan_monthly(pd.concat([new, duplicate]))
    assert rejected.reason.eq("missing_or_multiple_aliases_for_caesars_license").all()


def test_ma_january_source_conflict_has_exact_monthly_rolling_and_hold_consequences(source_tables):
    for frame in [source_tables["monthly"], source_tables["market_monthly"]]:
        mask = frame.state_code.eq("MA") & frame.metric.eq("handle") & frame.period_start.eq("2025-01-01")
        frame.loc[mask, "status"] = "excluded"
        frame.loc[mask, "reason"] = "operator_total_does_not_reconcile"
    result = build_monthly_review(source_tables, end_month="2026-07")
    assert pd.notna(one(result["monthly"], end="2026-01").amount)
    assert pd.isna(one(result["monthly"], end="2026-01").yoy_growth_pct)
    for month in ["2026-01", "2026-02", "2026-03"]:
        assert one(result["rolling3"], end=month).prior_missing_months == "2025-01"
        assert pd.isna(one(result["rolling3"], end=month, metric="gross_revenue").hold_change_pp)
    assert one(result["assessment"]).status == "comparable"
    assert pd.notna(one(result["assessment"]).growth_rate_change_pp)


@pytest.mark.parametrize("failure", [None, "source_bytes", "source_clock", "naive_clock"])
def test_notebook_checks_retained_mapping_and_dated_context_before_use(tmp_path, failure):
    source = tmp_path / "data/raw/context.html"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"retained ownership release")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "company_context_sources_20260912.json").write_text(json.dumps({"reviewed_on": "2026-09-12", "sources": []}))
    binding = dict(source_file="data/raw/context.html", source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), captured_at="2026-09-12T12:00:00Z")
    if failure == "source_bytes":
        source.write_bytes(b"changed source")
    elif failure == "source_clock":
        binding["captured_at"] = "2026-09-13T00:00:00Z"
    elif failure == "naive_clock":
        binding["captured_at"] = "2026-09-12T12:00:00"
    path = Path(__file__).resolve().parents[1] / "notebooks/94_gaming_industry_update.ipynb"
    notebook = json.loads(path.read_text(encoding="utf-8"))
    code = next("".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code" and "# Verify mapping evidence" in "".join(c["source"]))
    scope = dict(ROOT=tmp_path, json=json, hashlib=hashlib, pd=pd, CZR_MAPPING_SOURCES=[binding], cutoff="2026-09-12T23:00:00Z")
    if failure:
        with pytest.raises(ValueError, match="Mapping/context source"):
            exec(code, scope)
    else:
        exec(code, scope)
