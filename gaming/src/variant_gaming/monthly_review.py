"""Twelve explicit monthly reads and fixed rolling-three-month comparisons.

Inputs are the already reconciled native monthly tables in industry.py. No raw
source is reinterpreted here. USD amounts, percent growth/shares, pp differences.
"""
import math
import re

import pandas as pd

from variant_gaming.flut_scorecard import CONTRACTS, SPORTS, CASINO, _share
from variant_gaming.industry import NATIVE_OPERATORS, SCOPE_NOTES, _company_columns, _direction

KEYS = ["company", "state_code", "vertical", "metric"]


def _qualified(group, label):
    if len(group) != 1:
        return None, "missing_month" if group.empty else "duplicate_month"
    row = group.iloc[0]
    try:
        month = pd.Period(row.period_start, freq="M")
    except (ValueError, TypeError):
        return None, "invalid_calendar_month"
    if row.period_start != str(month.start_time.date()) or row.period_end != str(month.end_time.date()):
        return None, "incomplete_calendar_month"
    if row.native_metric != label:
        return None, "native_metric_mismatch"
    if row.status != "eligible":
        return None, row.reason or "excluded_source_month"
    if not all(pd.notna(row[c]) and math.isfinite(row[c]) for c in ["company_amount", "market_amount"]):
        return None, "missing_or_nonfinite_amount"
    return row, ""


def _window(lookup, *, end, size, metric, mapping):
    current = pd.period_range(end=end, periods=size, freq="M").astype(str).tolist()
    previous = [str(pd.Period(month, freq="M") - 12) for month in current]
    item = dict(end_month=str(end), window_months=size, expected_months=", ".join(current),
                prior_months=", ".join(previous), mapping_status=mapping)
    refs, operators, gaps = set(), set(), []
    for prefix, months in [("", current), ("prior_", previous)]:
        values, missing = [], []
        for month in months:
            row, reason, sources = lookup.get(month, (None, "unmapped_company_scope" if mapping == "unmapped" else "missing_month", ()))
            refs.update(sources)
            if reason:
                missing.append(month)
                gaps.append(f"{month}: {reason}")
            else:
                values.append(row)
                if pd.notna(row.company_operator):
                    operators.add(str(row.company_operator))
        item[prefix + "missing_months"] = ", ".join(missing)
        item[prefix + "observed_months"] = len(values)
        item[prefix + "status"] = "complete" if not missing else "incomplete"
        for name, column in [("amount", "company_amount"), ("market_amount", "market_amount")]:
            item[prefix + name] = sum(float(row[column]) for row in values) if not missing else float("nan")
        item[prefix + "share_pct"], item[prefix + "share_status"] = _share(
            item[prefix + "amount"], item[prefix + "market_amount"], handle=metric == "handle")
    item["current_status"] = item.pop("status")
    item["status"] = "comparable" if item["current_status"] == item["prior_status"] == "complete" else "incomplete_comparison"
    for name in ["amount", "market_amount"]:
        old, new = item["prior_" + name], item[name]
        key = "yoy_growth_pct" if name == "amount" else "market_yoy_growth_pct"
        item[key] = 100*(new/old-1) if pd.notna(new) and pd.notna(old) and old > 0 else float("nan")
    item["share_change_pp"] = item["share_pct"] - item["prior_share_pct"]
    item.update(source_refs=tuple(sorted(refs)), native_operators=tuple(sorted(operators)), gap_details="; ".join(gaps),
                growth_status="ok" if pd.notna(item["yoy_growth_pct"]) else "missing_comparison_or_nonpositive_baseline")
    return item


def _with_hold(frame):
    frame = frame.copy()
    for column in ["hold_pct", "prior_hold_pct", "hold_change_pp"]:
        frame[column] = float("nan")
    keys = ["company", "state_code", "vertical", "end_month"]
    handles = frame[frame.metric.eq("handle")].set_index(keys)
    for index, gross in frame[frame.vertical.eq(SPORTS) & frame.metric.eq("gross_revenue")].iterrows():
        key = tuple(gross[k] for k in keys)
        if key not in handles.index:
            continue
        handle = handles.loc[key]
        for prefix in ["", "prior_"]:
            h, g = handle[prefix + "amount"], gross[prefix + "amount"]
            if pd.notna(h) and h > 0 and pd.notna(g):
                frame.loc[index, prefix + "hold_pct"] = 100*g/h
        frame.loc[index, "hold_change_pp"] = frame.loc[index, "hold_pct"] - frame.loc[index, "prior_hold_pct"]
    return frame


def build_monthly_review(tables, *, end_month):
    """Return 12 months, 12 rolling3 reads, the latest assessment and source gaps.

    Missing prior-year evidence does not erase a valid current amount. Growth and
    share/hold changes still require both complete windows. Acceleration compares
    latest 3M YoY with the preceding non-overlapping 3M YoY; prior-read change uses
    the immediately preceding month-end, on this SAME retained capture.
    """
    if not isinstance(end_month, str) or not re.fullmatch(r"\d{4}-\d{2}", end_month):
        raise ValueError("end_month must be explicit YYYY-MM")
    end = pd.Period(end_month, freq="M")
    ends = pd.period_range(end=end, periods=12, freq="M")
    inputs = pd.concat([tables["monthly"], _company_columns(tables["market_monthly"]).assign(company="MARKET")], ignore_index=True)
    groups = {key: group for key, group in inputs.groupby(KEYS)}
    monthly, rolling, coverage = [], [], []
    for company in ["MARKET", *NATIVE_OPERATORS]:
        for (state, vertical), contract in CONTRACTS.items():
            mapping = "market" if company == "MARKET" else "native_brand_only" if (state, vertical) in NATIVE_OPERATORS[company] else "unmapped"
            for metric, label in contract["metrics"].items():
                identity = dict(company=company, state_code=state, vertical=vertical, metric=metric, native_metric=label)
                group = groups.get((company, state, vertical, metric), inputs.iloc[:0])
                lookup = {}
                for month, rows in group.groupby(group.period_start.astype(str).str[:7]):
                    row, reason = _qualified(rows, label)
                    sources = tuple(sorted({ref for refs in rows.source_refs for ref in refs}))
                    lookup[month] = row, reason, sources
                eligible = sorted(month for month, (_, reason, _) in lookup.items() if not reason)
                coverage.append(dict(identity, first_eligible_month=eligible[0] if eligible else None,
                    last_eligible_month=eligible[-1] if eligible else None, mapping_status=mapping,
                    excluded_months="; ".join(f"{month}: {reason}" for month, (_, reason, _) in sorted(lookup.items()) if reason)))
                for endpoint in ends:
                    monthly.append(dict(identity, **_window(lookup, end=endpoint, size=1, metric=metric, mapping=mapping)))
                    rolling.append(dict(identity, **_window(lookup, end=endpoint, size=3, metric=metric, mapping=mapping)))
    monthly, rolling = _with_hold(pd.DataFrame(monthly)), _with_hold(pd.DataFrame(rolling))
    assessment = rolling[rolling.end_month.eq(str(end))].copy().set_index(KEYS)
    for prefix, previous_end in [("preceding3_", end-3), ("previous_read_", end-1)]:
        previous = rolling[rolling.end_month.eq(str(previous_end))].set_index(KEYS)
        assessment[prefix + "end_month"] = str(previous_end)
        for column in ["yoy_growth_pct", "share_change_pp", "gap_details", "status"]:
            assessment[prefix + column] = previous[column]
    assessment["growth_rate_change_pp"] = assessment.yoy_growth_pct - assessment.preceding3_yoy_growth_pct
    assessment["growth_rate_direction"] = assessment.growth_rate_change_pp.map(lambda v: _direction(v, "faster", "slower"))
    assessment["read_change_yoy_pp"] = assessment.yoy_growth_pct - assessment.previous_read_yoy_growth_pct
    assessment["read_change_share_pp"] = assessment.share_change_pp - assessment.previous_read_share_change_pp
    return dict(monthly=monthly, rolling3=rolling, assessment=assessment.reset_index(),
                coverage=pd.DataFrame(coverage), inputs=inputs)


def fundamentals_update(review):
    """Supporting/contrary observations by metric, with no vote or combined rating."""
    output = []
    for company in ["MARKET", *NATIVE_OPERATORS]:
        rows = review["assessment"].query("company == @company")
        evidence = rows[rows.metric.eq("handle") | rows.vertical.eq(CASINO)]
        support, contrary, changes, gaps, rates = [], [], [], [], []
        for row in evidence.itertuples():
            name = f"{row.state_code} {'sportsbook handle' if row.metric == 'handle' else row.native_metric}"
            if pd.notna(row.yoy_growth_pct):
                text = f"{name} {row.yoy_growth_pct:+.1f}% YoY"
                (support if row.yoy_growth_pct > 0 else contrary if row.yoy_growth_pct < 0 else changes).append(text)
            else:
                gaps.append(f"{name}: comparison unavailable ({(row.gap_details or row.growth_status).replace('_', ' ')})")
            if company != "MARKET" and pd.notna(row.share_change_pp):
                text = f"{name} share {row.share_change_pp:+.2f} pp YoY"
                (support if row.share_change_pp > 0 else contrary if row.share_change_pp < 0 else changes).append(text)
            if pd.notna(row.growth_rate_change_pp):
                rate = f"{name}: 3M YoY rate {row.growth_rate_change_pp:+.2f} pp versus 3M ending {row.preceding3_end_month} ({row.growth_rate_direction})"
                rates.append(rate)
                (support if row.growth_rate_change_pp > 0 else contrary if row.growth_rate_change_pp < 0 else changes).append(rate)
            else:
                gaps.append(f"{name}: preceding-three-month rate comparison unavailable")
            if pd.notna(row.read_change_yoy_pp):
                changes.append(f"{name}: rolling3 YoY rate {row.read_change_yoy_pp:+.2f} pp from {row.previous_read_end_month} read")
            if company != "MARKET" and pd.notna(row.read_change_share_pp):
                changes.append(f"{name}: YoY share change moved {row.read_change_share_pp:+.2f} pp from prior read")
        for row in rows[rows.vertical.eq(SPORTS) & rows.metric.eq("gross_revenue")].itertuples():
            if pd.notna(row.hold_change_pp):
                changes.append(f"{row.state_code} gross hold {row.hold_pct:.2f}% ({row.hold_change_pp:+.2f} pp YoY), an observed outcome, not a demand or durable-margin conclusion")
            else:
                gaps.append(f"{row.state_code} comparable gross hold unavailable")
        for label, key in [("Monthly", "monthly"), ("Rolling3", "rolling3")]:
            historical_gaps = review[key].query("company == @company and status != 'comparable'")
            if not historical_gaps.empty:
                gaps.append(label + " history gaps: " + "; ".join(f"{state} {vertical.replace('online_', '')} {metric}: {', '.join(sorted(g.end_month.unique()))}" for (state, vertical, metric), g in historical_gaps.groupby(["state_code", "vertical", "metric"])))
        scope = "Covered MA/MI states only; no national index." if company == "MARKET" else SCOPE_NOTES[company]
        output.append(dict(subject="Covered industry" if company == "MARKET" else company,
            window=rows.expected_months.iloc[0], supporting_evidence="; ".join(support) or "No positive comparable observation in these selected volume/share/growth-rate metrics.",
            contrary_evidence="; ".join(contrary) or "No negative comparable observation in these selected volume/share/growth-rate metrics; unmeasured risks remain.",
            what_changed="; ".join(changes) or "Prior-read comparison unavailable.",
            interpretation="; ".join(rates) + ". Separate volume, share and reported gross outcomes; these observations are not a company profit forecast.",
            uncertainty=scope + " " + ("; ".join(gaps) or "No missing months in the displayed native comparisons.") + " Changes from the prior read use the same retained capture, not historical availability.",
            next_check="Next official month; source gaps/revisions; promotions and issuer economics in the dated company context; legal event rechecks."))
    return pd.DataFrame(output)
