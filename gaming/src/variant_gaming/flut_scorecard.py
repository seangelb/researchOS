"""Read-only, source-qualified FanDuel monthly and same-window quarterly tables.

Amounts are native USD, shares/hold are percent, changes are percentage points.
Only the explicit monthly contracts below qualify. There is no national rollup,
forecast mapping, revision selection, or analyst approval in this module.
"""
from __future__ import annotations

import math

import pandas as pd

from variant_gaming.denominators import denominator_provenance

SPORTS = "online_sports_betting"
CASINO = "online_casino"
MI_LABEL = "Gross Receipts (gross_revenue); Adjusted Gross (adjusted_revenue)"
CONTRACTS = {
    ("MA", SPORTS): {
        "operator": "FanDuel", "operator_status": "ok",
        "labels": {"Accrual Win (gross_revenue); Taxable Gaming Revenue (taxable_revenue)"},
        "metrics": {"handle": "MA: Handle", "gross_revenue": "MA: Accrual Win",
                    "taxable_revenue": "MA: Taxable Gaming Revenue"},
    },
    ("MI", SPORTS): {
        "operator": "FanDuel (MotorCity Casino)", "operator_status": "ok",
        "labels": {"Adjusted Gross", MI_LABEL},
        "metrics": {"handle": "MI: Total Handle", "gross_revenue": "MI: Gross Receipts",
                    "adjusted_revenue": "MI: Adjusted Gross"},
    },
    ("MI", CASINO): {
        "operator": "FanDuel (MotorCity Casino)", "operator_status": "ok",
        "labels": {"Gross Receipts", MI_LABEL},
        "metrics": {"gross_revenue": "MI: Gross Receipts", "adjusted_revenue": "MI: Adjusted Gross"},
    },
}
SERIES = ["state_code", "vertical", "metric", "native_metric"]
MONTHLY_COLUMNS = [*SERIES, "period_start", "period_end", "fd_operator", "fd_amount",
                   "market_amount", "operator_sum", "reconciliation_difference",
                   "fd_share_pct", "share_status", "status", "reason", "source_refs"]


def _refs(rows):
    return tuple(sorted(set(rows[["source_url", "source_file", "source_sha256"]]
                            .itertuples(index=False, name=None))))


def _share(numerator, denominator, *, handle=False):
    if not math.isfinite(numerator) or not math.isfinite(denominator):
        return float("nan"), "missing_value"
    if denominator <= 0:
        return float("nan"), "nonpositive_market"
    if handle and (numerator < 0 or numerator > denominator):
        return float("nan"), "invalid_handle"
    # Negative revenue and shares above 100% are possible when rivals lose.
    return 100 * numerator / denominator, "ok"


def _month_values(group, contract, metric, *, common_source=True):
    """Reject unknown denominators, source splicing and conflicting revisions."""
    if not group.reported_revenue_name.isin(contract["labels"]).all():
        raise ValueError("unrecognized_native_metric_label")
    if group[["source_url", "source_file", "source_sha256"]].isna().any().any():
        raise ValueError("missing_source_reference")
    if group[["source_url", "source_file", "source_sha256"]].eq("").any().any():
        raise ValueError("missing_source_reference")
    # Equal copies remain traceable. Any disagreement blocks the metric; capture
    # recency never supplies revision authority, and missing differs from zero.
    resolved, cohorts = [], []
    for _, versions in group.groupby(["operator", "row_type"], dropna=False):
        if versions[[metric, "report_status"]].nunique(dropna=False).gt(1).any():
            raise ValueError("conflicting_source_versions")
        resolved.append(versions.iloc[0])
        cohorts.append(set(versions.source_sha256))
    if not cohorts or (common_source and not set.intersection(*cohorts)):
        raise ValueError("no_common_source_version")
    rows = pd.DataFrame(resolved)
    official = rows[rows.row_type.eq("official_statewide_total")]
    operators = rows[rows.row_type.eq("operator")]
    if len(official) != 1 or denominator_provenance(official.iloc[0]) is None:
        raise ValueError("unverified_official_denominator")
    if len(rows) != len(operators) + 1 or operators.empty:
        raise ValueError("unexpected_or_missing_operator_rows")
    if not operators.report_status.eq(contract["operator_status"]).all():
        raise ValueError("unverified_operator_status")
    identities = [contract["operator"]]
    fd = official if contract["operator"] is None else operators[operators.operator.isin(identities)]
    if len(fd) != 1:
        raise ValueError(contract.get("identity_error", "missing_or_ambiguous_native_fanduel_identity"))
    values = pd.to_numeric(rows[metric], errors="coerce")
    if not values.map(lambda value: pd.notna(value) and math.isfinite(value)).all():
        raise ValueError("missing_or_nonfinite_metric")
    if metric == "handle" and (values < 0).any():
        raise ValueError("negative_handle")
    fd_value = float(pd.to_numeric(fd[metric]).sum())
    market = float(official.iloc[0][metric])
    total = float(pd.to_numeric(operators[metric]).sum())
    if abs(total - market) > 0.010001:
        raise ValueError("operator_total_does_not_reconcile")
    return fd_value, market, total


def build_monthly_scorecard(results: pd.DataFrame, *, operators=None) -> pd.DataFrame:
    """One row per supported state/product/month/native metric, including failures.

    Callers must validate retained source bytes separately. Reconciliation proves
    agreement to the printed population, not that regulator reports are final.
    Excluded rows have no decision-ready amounts; source references remain visible.
    """
    output = []
    for (state, vertical), contract in CONTRACTS.items():
        if operators is not None:
            if (state, vertical) not in operators:
                continue
            contract = dict(contract, operator=operators[state, vertical],
                            identity_error="missing_or_ambiguous_native_operator_identity")
        selected = results[results.state_code.eq(state) & results.vertical.eq(vertical)
                           & results.channel.eq("online") & results.frequency.eq("monthly")]
        for (start, end), group in selected.groupby(["period_start", "period_end"], dropna=False):
            try:
                month = pd.Period(start, freq="M")
                valid_period = start == str(month.start_time.date()) and end == str(month.end_time.date())
            except (ValueError, TypeError):
                valid_period = False
            for metric, label in contract["metrics"].items():
                item = dict(state_code=state, vertical=vertical, metric=metric, native_metric=label,
                            period_start=start, period_end=end, fd_operator=contract["operator"],
                            status="eligible", reason="", source_refs=_refs(group))
                try:
                    if not valid_period:
                        raise ValueError("not_one_complete_calendar_month")
                    fd, market, total = _month_values(group, contract, metric)
                    share, share_status = _share(fd, market, handle=metric == "handle")
                    item.update(fd_amount=fd, market_amount=market, operator_sum=total,
                                reconciliation_difference=total-market, fd_share_pct=share,
                                share_status=share_status)
                except ValueError as exc:
                    item.update(status="excluded", reason=str(exc), share_status="excluded")
                output.append(item)
    return pd.DataFrame(output, columns=MONTHLY_COLUMNS)


def scorecard_scope(results: pd.DataFrame) -> pd.DataFrame:
    """Show every retained state/product/channel/frequency and its scope decision."""
    keys = ["state_code", "vertical", "channel", "frequency"]
    rows = results.groupby(keys, dropna=False).agg(
        observations=("operator", "size"), latest_period=("period_end", "max")).reset_index()
    def reason(row):
        if row.channel != "online":
            return "excluded: channel is not internet-wide"
        if row.frequency != "monthly":
            return "excluded: weekly and other periods stay separate (see notebook 94 for NY)"
        if (row.state_code, row.vertical) in CONTRACTS:
            return "supported: each month still requires identity, version and denominator checks"
        if row.state_code in {"PA", "NJ"}:
            return "excluded: licensee totals do not establish a native FanDuel brand mapping"
        if row.state_code == "OH":
            return "excluded: two native FanDuel licensee rows require transition/adjustment aggregation review"
        if row.state_code == "KS":
            return "excluded: settled wagers / Net Revenues need a separate contract and rounding policy; no gross hold"
        return "excluded: source-specific identity and metric contract not yet validated"
    rows["scope_reason"] = rows.apply(reason, axis=1)
    return rows


def build_quarterly_scorecard(monthly: pd.DataFrame, *, quarter: str, through_month: str) -> pd.DataFrame:
    """Compare the exact requested quarter-to-date months against one year earlier.

    Every expected month must qualify in both years. No intersection-only partial
    totals, no averaged monthly shares, no revenue-basis substitution. Growth is
    undefined for a zero/negative baseline, but the native amounts stay visible.
    """
    q = pd.Period(quarter, freq="Q-DEC")
    through = pd.Period(through_month, freq="M")
    if through.asfreq("Q-DEC") != q:
        raise ValueError("through_month must belong to the requested calendar quarter")
    expected = pd.period_range(q.start_time, through.start_time, freq="M").astype(str).tolist()
    previous = [str(pd.Period(month, freq="M") - 12) for month in expected]
    output = []
    for (state, vertical), contract in CONTRACTS.items():
        for metric, label in contract["metrics"].items():
            group = monthly[monthly.state_code.eq(state) & monthly.vertical.eq(vertical)
                            & monthly.metric.eq(metric)].copy()
            group["month"] = group.period_start.astype(str).str[:7]
            current = group[group.month.isin(expected)]
            prior = group[group.month.isin(previous)]
            observed = [m for m in expected if len(current[current.month.eq(m)]) == 1
                        and current[current.month.eq(m)].iloc[0].status == "eligible"]
            matched = [m for m, p in zip(expected, previous) if m in observed
                       and len(prior[prior.month.eq(p)]) == 1
                       and prior[prior.month.eq(p)].iloc[0].status == "eligible"]
            complete = len(matched) == len(expected)
            refs = tuple(sorted({ref for values in pd.concat([current, prior]).source_refs for ref in values}))
            item = dict(state_code=state, vertical=vertical, metric=metric, native_metric=label,
                        quarter=str(q), through_month=str(through), expected_months=", ".join(expected),
                        expected_window_months=len(expected), observed_window_months=len(observed),
                        matched_window_months=len(matched), quarter_months=3,
                        quarter_complete=len(expected) == 3 and complete,
                        missing_or_excluded_months=", ".join(m for m in expected if m not in matched),
                        status="comparable" if complete else "incomplete_comparison",
                        source_refs=refs)
            if complete:
                fd, market = float(current.fd_amount.sum()), float(current.market_amount.sum())
                old_fd, old_market = float(prior.fd_amount.sum()), float(prior.market_amount.sum())
                share, share_status = _share(fd, market, handle=metric == "handle")
                old_share, old_status = _share(old_fd, old_market, handle=metric == "handle")
                item.update(fd_amount=fd, prior_fd_amount=old_fd, market_amount=market,
                            prior_market_amount=old_market, fd_growth_pct=100*(fd/old_fd-1) if old_fd > 0 else float("nan"),
                            market_growth_pct=100*(market/old_market-1) if old_market > 0 else float("nan"),
                            growth_status="ok" if old_fd > 0 else "nonpositive_prior_fanduel",
                            market_growth_status="ok" if old_market > 0 else "nonpositive_prior_market",
                            fd_share_pct=share, prior_fd_share_pct=old_share, share_change_pp=share-old_share,
                            share_status=share_status, prior_share_status=old_status)
            output.append(item)
    frame = pd.DataFrame(output)
    # Empty or fully blocked captures still provide a stable consumer schema.
    for column in ["fd_amount", "prior_fd_amount", "market_amount", "prior_market_amount", "fd_growth_pct",
                   "market_growth_pct", "fd_share_pct", "prior_fd_share_pct", "share_change_pp"]:
        if column not in frame:
            frame[column] = float("nan")
    for column in ["growth_status", "market_growth_status", "share_status", "prior_share_status"]:
        if column not in frame:
            frame[column] = "unavailable"
        else:
            frame[column] = frame[column].fillna("unavailable")
    return frame


def sportsbook_hold(quarterly: pd.DataFrame) -> pd.DataFrame:
    """Native gross revenue / handle, after exact same-window comparisons qualify."""
    rows = []
    for state in [s for s, v in CONTRACTS if v == SPORTS]:
        group = quarterly[quarterly.state_code.eq(state) & quarterly.vertical.eq(SPORTS)]
        handle, gross = group[group.metric.eq("handle")], group[group.metric.eq("gross_revenue")]
        item = {"state_code": state, "vertical": SPORTS, "status": "unavailable"}
        if len(handle) == len(gross) == 1:
            h, g = handle.iloc[0], gross.iloc[0]
            item.update(quarter=h.quarter, through_month=h.through_month,
                        native_metric=g.native_metric, matched_window_months=h.matched_window_months)
            if h.status == g.status == "comparable" and h.expected_months == g.expected_months:
                for prefix in ["", "prior_"]:
                    denominator = h[prefix + "fd_amount"]
                    item[prefix + "fd_hold_pct"] = (100*g[prefix + "fd_amount"]/denominator
                                                       if denominator > 0 else float("nan"))
                item["hold_change_pp"] = item["fd_hold_pct"] - item["prior_fd_hold_pct"]
                item["status"] = "ok" if h.fd_amount > 0 and h.prior_fd_amount > 0 else "nonpositive_handle"
        rows.append(item)
    return pd.DataFrame(rows).reindex(columns=[
        "state_code", "vertical", "status", "quarter", "through_month", "native_metric",
        "matched_window_months", "fd_hold_pct", "prior_fd_hold_pct", "hold_change_pp"])
