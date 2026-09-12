"""Economic and evidence failures must remain visible in the current scorecard."""
import pandas as pd
import pytest

from variant_gaming.flut_scorecard import (
    CONTRACTS, MI_LABEL, SPORTS, build_monthly_scorecard,
    build_quarterly_scorecard, scorecard_scope, sportsbook_hold,
)


def month(period="2026-07", *, fd=40, market=100, state="MI", vertical=SPORTS):
    p = pd.Period(period, freq="M")
    contract = CONTRACTS[state, vertical]
    rows = []
    for operator, amount, row_type in [(contract["operator"], fd, "operator"),
                                      ("Other", market-fd, "operator"),
                                      ("STATEWIDE", market, "official_statewide_total")]:
        rows.append(dict(state_code=state, vertical=vertical, channel="online", frequency="monthly",
                         operator=operator, row_type=row_type, period_start=str(p.start_time.date()),
                         period_end=str(p.end_time.date()), handle=amount, gross_revenue=amount/10,
                         adjusted_revenue=amount/20, taxable_revenue=amount/25,
                         reported_revenue_name=MI_LABEL if state == "MI" else next(iter(contract["labels"])),
                         report_status="reconciled_printed_total" if state == "MA" and row_type != "operator" else "ok",
                         source_url="https://official.example/report", source_file="raw/report.xlsx",
                         source_sha256="a"*64, retrieved_at_utc="2026-09-12T00:00:00Z"))
    return pd.DataFrame(rows)


def one(frame, metric="handle", state="MI"):
    return frame[frame.state_code.eq(state) & frame.vertical.eq(SPORTS) & frame.metric.eq(metric)].iloc[0]


def test_native_metrics_remain_separate_and_missing_gross_does_not_use_adjusted():
    data = month()
    data.loc[data.row_type.eq("operator"), "gross_revenue"] = None
    monthly = build_monthly_scorecard(data)
    assert one(monthly, "gross_revenue").status == "excluded"
    assert one(monthly, "adjusted_revenue").fd_amount == 2
    assert one(monthly, "adjusted_revenue").native_metric == "MI: Adjusted Gross"


@pytest.mark.parametrize("change, reason", [
    ({"report_status": "unknown"}, "unverified_official_denominator"),
    ({"operator": "OTHER TOTAL"}, "unverified_official_denominator"),
    ({"handle": 101}, "operator_total_does_not_reconcile"),
    ({"source_sha256": "b"*64}, "no_common_source_version"),
])
def test_invalid_denominator_cannot_qualify_even_with_numeric_total(change, reason):
    data = month()
    for field, value in change.items():
        data.loc[data.row_type.eq("official_statewide_total"), field] = value
    row = one(build_monthly_scorecard(data))
    assert row.status == "excluded" and row.reason == reason
    assert pd.isna(row.fd_amount)


def test_missing_operator_is_detected_by_reconciliation():
    row = one(build_monthly_scorecard(month().query("operator != 'Other'")))
    assert row.reason == "operator_total_does_not_reconcile"


def test_conflicting_revisions_are_not_resolved_by_newer_capture():
    data = month()
    changed = data.iloc[[0]].copy()
    changed["source_sha256"] = "b"*64
    changed["retrieved_at_utc"] = "2026-09-13T00:00:00Z"
    changed["handle"] = 50
    row = one(build_monthly_scorecard(pd.concat([data, changed])))
    assert row.reason == "conflicting_source_versions"
    assert len(row.source_refs) == 2


def test_matching_copies_preserve_all_source_references():
    data = month()
    copy = data.copy()
    copy["source_sha256"] = "b"*64
    copy["source_file"] = "raw/equal_copy.xlsx"
    row = one(build_monthly_scorecard(pd.concat([copy, data])))
    assert row.status == "eligible" and row.fd_amount == 40
    assert len(row.source_refs) == 2


def test_identity_is_exact_not_fuzzy():
    data = month()
    data.loc[0, "operator"] = "FanDuel (a different licensee)"
    assert one(build_monthly_scorecard(data)).reason == "missing_or_ambiguous_native_fanduel_identity"


def test_partial_or_ambiguous_calendar_month_is_not_accepted():
    data = month()
    data["period_end"] = "2026-07-30"
    assert one(build_monthly_scorecard(data)).reason == "not_one_complete_calendar_month"


def test_negative_handles_are_rejected_but_negative_gross_is_retained():
    data = month(fd=-10)
    panel = build_monthly_scorecard(data)
    assert one(panel).reason == "negative_handle"
    assert one(panel, "gross_revenue").fd_amount == -1
    assert one(panel, "gross_revenue").fd_share_pct == -10


@pytest.mark.parametrize("legacy", ["Adjusted Gross", MI_LABEL])
def test_michigan_legacy_metadata_does_not_relabel_gross_column(legacy):
    data = month()
    data["reported_revenue_name"] = legacy
    row = one(build_monthly_scorecard(data), "gross_revenue")
    assert row.native_metric == "MI: Gross Receipts" and row.fd_amount == 4


def test_zero_market_keeps_amounts_and_suppresses_ratio():
    data = month(fd=0, market=0)
    row = one(build_monthly_scorecard(data))
    assert row.status == "eligible" and row.fd_amount == 0
    assert pd.isna(row.fd_share_pct) and row.share_status == "nonpositive_market"


def test_nonfinite_financial_values_cannot_enter_the_scorecard():
    data = month()
    data["gross_revenue"] = float("inf")
    assert one(build_monthly_scorecard(data), "gross_revenue").reason == "missing_or_nonfinite_metric"


def test_unknown_source_metric_label_is_not_silently_interpreted():
    data = month()
    data["reported_revenue_name"] = "Taxable amount assumed gross"
    assert one(build_monthly_scorecard(data), "gross_revenue").reason == "unrecognized_native_metric_label"


def test_quarterly_uses_same_months_and_ratio_of_sums_not_average_shares():
    data = pd.concat([month("2026-07", fd=20, market=100), month("2026-08", fd=180, market=900),
                      month("2025-07", fd=10, market=50), month("2025-08", fd=90, market=150)])
    q = one(build_quarterly_scorecard(build_monthly_scorecard(data), quarter="2026Q3", through_month="2026-08"))
    assert q.fd_amount == 200 and q.prior_fd_amount == 100 and q.fd_growth_pct == 100
    assert q.fd_share_pct == 20 and q.prior_fd_share_pct == 50 and q.share_change_pp == -30
    assert q.expected_window_months == q.observed_window_months == q.matched_window_months == 2
    assert not q.quarter_complete and q.quarter_months == 3


@pytest.mark.parametrize("missing", ["2026-08", "2025-08"])
def test_missing_month_never_becomes_intersection_only_growth(missing):
    data = pd.concat([month(p) for p in ["2026-07", "2026-08", "2025-07", "2025-08"] if p != missing])
    q = one(build_quarterly_scorecard(build_monthly_scorecard(data), quarter="2026Q3", through_month="2026-08"))
    assert q.status == "incomplete_comparison" and q.missing_or_excluded_months == "2026-08"
    assert pd.isna(q.fd_amount) and pd.isna(q.fd_growth_pct) and q.matched_window_months == 1


def test_zero_and_negative_prior_do_not_produce_misleading_growth():
    for fd in [0, -10]:
        data = pd.concat([month("2026-07"), month("2025-07", fd=fd)])
        q = one(build_quarterly_scorecard(build_monthly_scorecard(data), quarter="2026Q3", through_month="2026-07"), "gross_revenue")
        assert q.prior_fd_amount == fd/10 and pd.isna(q.fd_growth_pct)
        assert q.growth_status == "nonpositive_prior_fanduel"


def test_same_window_hold_uses_gross_and_preserves_losses():
    current, prior = month(), month("2025-07")
    current["gross_revenue"] *= -1
    q = build_quarterly_scorecard(build_monthly_scorecard(pd.concat([current, prior])),
                                 quarter="2026Q3", through_month="2026-07")
    h = sportsbook_hold(q).query("state_code == 'MI'").iloc[0]
    assert h.fd_hold_pct == -10 and h.prior_fd_hold_pct == 10 and h.hold_change_pp == -20


def test_unsupported_licensee_and_weekly_series_have_visible_exclusions():
    data = month()
    pa, ny = data.copy(), data.copy()
    pa["state_code"] = "PA"
    ny["state_code"], ny["frequency"] = "NY", "weekly"
    scope = scorecard_scope(pd.concat([data, pa, ny])).set_index("state_code")
    assert "licensee" in scope.loc["PA", "scope_reason"]
    assert "weekly" in scope.loc["NY", "scope_reason"]


def test_quarter_parameter_rejects_a_different_calendar_quarter():
    with pytest.raises(ValueError, match="belong"):
        build_quarterly_scorecard(build_monthly_scorecard(month()), quarter="2026Q2", through_month="2026-07")


def test_entirely_missing_supported_series_remain_visible():
    q = build_quarterly_scorecard(build_monthly_scorecard(month()), quarter="2026Q3", through_month="2026-07")
    ma = one(q, state="MA")
    assert ma.status == "incomplete_comparison" and ma.observed_window_months == 0
    assert pd.isna(ma.fd_amount)
