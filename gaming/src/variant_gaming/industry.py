"""Small, offline industry update: demand, native brand share, gross hold and gaps.

Amounts are USD; ratios percent; changes percentage points. These are specific
state/brand observations, never consolidated issuer revenue or a national index.
"""
from pathlib import Path
import re

import pandas as pd

from variant_gaming.flut_scorecard import (
    CONTRACTS, SPORTS, CASINO, MONTHLY_COLUMNS, _month_values, _refs, _share,
    build_monthly_scorecard, build_quarterly_scorecard, sportsbook_hold,
)

NATIVE_OPERATORS = {
    "FLUT": {("MA", SPORTS): "FanDuel", ("MI", SPORTS): "FanDuel (MotorCity Casino)",
             ("MI", CASINO): "FanDuel (MotorCity Casino)"},
    "DKNG": {("MA", SPORTS): "DraftKings", ("MI", SPORTS): "DraftKings (Bay Mills Indian Community)",
             ("MI", CASINO): "DraftKings (Bay Mills Indian Community)"},
    "CZR": {("MA", SPORTS): "Caesars Sportsbook",
            ("MI", SPORTS): "Grand Traverse license: source labels shown per month",
            ("MI", CASINO): "Grand Traverse plus Sault licenses: source labels shown per month"},
}
NY_OPERATORS = {"FLUT": "FanDuel", "DKNG": "DraftKings Sport Book", "CZR": "Caesars Sport Book"}
SCOPE_NOTES = {
    "FLUT": "Native FanDuel evidence in these US states; excludes Flutter International and other US activity.",
    "DKNG": "Native DraftKings brand/license evidence; Golden Nugget and other company activity are not aggregated.",
    "CZR": "Covered MA/NY sportsbook and MI licensed digital operations only; no land-based group inference. MI sportsbook uses Grand Traverse from May 2021; casino adds Sault from July 2024.",
}

# Primary evidence for the bounded license/ownership mapping; checked by notebook 94.
CZR_MAPPING_SOURCES = [{'source_id': 'czr_william_hill_acquisition',
  'source_url': 'https://investor.caesars.com/news-releases/news-release-details/caesars-entertainment-announces-completion-william-hill-plc',
  'source_file': 'data/raw/GAMING_CONTEXT/2026-09-12/czr_william_hill_acquisition_ce1d951916c80522.html',
  'source_sha256': 'ce1d951916c805226df0c78da9fec81230adfc2fa4ec75510259e9a6fb2cbf3f',
  'captured_at': '2026-09-12T22:33:31.501217+00:00'},
 {'source_id': 'czr_wsop_license',
  'source_url': 'https://s202.q4cdn.com/508919455/files/doc_news/Caesars-Entertainment-Closes-Sale-of-World-Series-of-Poker-Brand-to-NSUS-Group-for-US500-million-2024.pdf',
  'source_file': 'data/raw/GAMING_CONTEXT/2026-09-12/czr_wsop_license_cea990d7d3b230c2.pdf',
  'source_sha256': 'cea990d7d3b230c27a85eea05bccfc6916afd4ccf4d3916c94bb1acfaea13ad7',
  'captured_at': '2026-09-12T22:33:31.245116+00:00'},
 {'source_id': 'mi_authorized_providers',
  'source_url': 'https://www.michigan.gov/mgcb/internet-gaming-and-fantasy-contests/authorized-online-gaming-and-sports-betting-platform-providers-in-michigan',
  'source_file': 'data/raw/GAMING_CONTEXT/2026-09-12/mi_authorized_providers_13f00f52b68d8775.html',
  'source_sha256': '13f00f52b68d8775b708034890a6ae4e3d682d49f30886ee106bc1e914b3e8e2',
  'captured_at': '2026-09-12T22:34:10.918979+00:00'},
 {'source_id': 'czr_wynn_acquisition',
  'source_url': 'https://newsroom.caesars.com/press-releases/press-release-details/2024/Caesars-Entertainment-Inc.-Completes-Previously-Announced-Acquisition-of-Michigan-iGaming-Operations-from-Wynn-Resorts/default.aspx',
  'source_file': 'data/raw/GAMING_CONTEXT/2026-09-12/czr_wynn_acquisition_5c6fda9ce84f7684.html',
  'source_sha256': '5c6fda9ce84f768497253f10794654f394757ee6e4bd70ad5ec3c0b8ac8ba782',
  'captured_at': '2026-09-12T22:34:10.997380+00:00'},
 {'source_id': 'czr_horseshoe_launch',
  'source_url': 'https://newsroom.caesars.com/press-releases/press-release-details/2024/Caesars-Entertainment-Launches-its-Newest-Proprietary-iGaming-Platform-Horseshoe-Online-Casino/default.aspx',
  'source_file': 'data/raw/GAMING_CONTEXT/2026-09-12/czr_horseshoe_launch_e03c5b0071b0f25f.html',
  'source_sha256': 'e03c5b0071b0f25fcb4cf0534618523c674d4809c27f452c4da286d087f030c3',
  'captured_at': '2026-09-12T22:34:11.004408+00:00'}]

CZR_GRAND_TRAVERSE = ("William Hill (Grand Traverse Band of Ottawa and Chippewa Indians)",
                     "Caesars/WSOP (Grand Traverse Band of Ottawa and Chippewa Indians)")
CZR_SAULT_CASINO = ("Wynn (Sault Ste. Marie Tribe of Chippewa Indians)",
                   "Caesars Horseshoe (Sault Ste. Marie Tribe of Chippewa Indians)")


def caesars_michigan_monthly(observations):
    """Two specifically reviewed licenses, bounded by first full ownership months.

    The notebook verifies CZR_MAPPING_SOURCES before use. Exactly one source alias
    per required license/month is allowed; no Sault sportsbook is attributed.
    Original native labels and all report references remain in the output.
    """
    output = []
    for vertical, since in [(SPORTS, "2021-05"), (CASINO, "2024-07")]:
        contract = CONTRACTS["MI", vertical]
        selected = observations[observations.state_code.eq("MI") & observations.vertical.eq(vertical)
                                & observations.channel.eq("online") & observations.frequency.eq("monthly")]
        aliases = [CZR_GRAND_TRAVERSE] + ([CZR_SAULT_CASINO] if vertical == CASINO else [])
        for (start, end), group in selected.groupby(["period_start", "period_end"], dropna=False):
            native = [sorted(set(group.loc[group.row_type.eq("operator") & group.operator.isin(names), "operator"])) for names in aliases]
            for metric, label in contract["metrics"].items():
                item = dict(state_code="MI", vertical=vertical, metric=metric, native_metric=label,
                            period_start=start, period_end=end, fd_operator=" | ".join(name for names in native for name in names),
                            status="eligible", reason="", source_refs=_refs(group))
                try:
                    month = pd.Period(start, freq="M")
                    if start != str(month.start_time.date()) or end != str(month.end_time.date()):
                        raise ValueError("not_one_complete_calendar_month")
                    if str(month) < since:
                        raise ValueError("before_reviewed_full_ownership_month")
                    if any(len(names) != 1 for names in native):
                        raise ValueError("missing_or_multiple_aliases_for_caesars_license")
                    values = [_month_values(group, dict(contract, operator=names[0]), metric) for names in native]
                    amount, market, total = sum(v[0] for v in values), values[0][1], values[0][2]
                    share, share_status = _share(amount, market, handle=metric == "handle")
                    item.update(fd_amount=amount, market_amount=market, operator_sum=total,
                                reconciliation_difference=total-market, fd_share_pct=share, share_status=share_status)
                except (ValueError, TypeError) as exc:
                    item.update(status="excluded", reason=str(exc), share_status="excluded")
                output.append(item)
    return pd.DataFrame(output, columns=MONTHLY_COLUMNS)


def _company_columns(frame):
    return frame.rename(columns={column: column.replace("fd_", "company_") for column in frame.columns})


def _direction(value, up="higher", down="lower"):
    if pd.isna(value):
        return "unavailable"
    return up if value > 1e-9 else down if value < -1e-9 else "unchanged"


def operator_scope():
    """Explicit identities and omissions; ticker labels do not establish full-company coverage."""
    return pd.DataFrame([
        dict(company=company, state_code=state, vertical=vertical,
             native_operator=identities.get((state, vertical)),
             mapping_status="native_brand_only" if (state, vertical) in identities else "unmapped",
             scope=SCOPE_NOTES[company])
        for company, identities in NATIVE_OPERATORS.items() for state, vertical in CONTRACTS
    ] + [dict(company=company, state_code="NY", vertical=SPORTS, native_operator=operator,
              mapping_status="weekly_native_brand_only", scope=SCOPE_NOTES[company])
         for company, operator in NY_OPERATORS.items()])


def ny_weekly_panel(observations, *, weeks=8):
    """Latest expected Monday-Sunday weeks; missing weeks stay visible, no proration.

    NY publishes separate operator and statewide workbooks. Revisions must agree
    within each native identity; the full operator population must reconcile.
    A state's source-wide workbook capture does not supply historical PIT status.
    """
    if isinstance(weeks, bool) or not isinstance(weeks, int) or not 1 <= weeks <= 52:
        raise ValueError("weeks must be an integer between 1 and 52")
    selected = observations[observations.state_code.eq("NY") & observations.vertical.eq(SPORTS)
                            & observations.channel.eq("online") & observations.frequency.eq("weekly")]
    columns = ["company", "native_operator", "period_start", "period_end", "metric", "native_metric",
               "company_amount", "market_amount", "company_share_pct", "share_status", "status", "reason", "source_refs"]
    if selected.empty:
        return pd.DataFrame(columns=columns)
    latest = pd.Timestamp(selected.period_end.max())
    ends = [(latest - pd.Timedelta(days=7*i)).date().isoformat() for i in reversed(range(weeks))]
    output = []
    for end in ends:
        group = selected[selected.period_end.eq(end)]
        start = (pd.Timestamp(end) - pd.Timedelta(days=6)).date().isoformat()
        for company, operator in NY_OPERATORS.items():
            contract = dict(operator=operator, operator_status="ok", labels={"GGR"},
                            identity_error="missing_or_ambiguous_native_operator_identity")
            for metric, label in [("handle", "NY: Handle"), ("gross_revenue", "NY: cash-basis GGR")]:
                item = dict(company=company, native_operator=operator, period_start=start, period_end=end,
                            metric=metric, native_metric=label, status="eligible", reason="", source_refs=_refs(group))
                try:
                    if group.empty:
                        raise ValueError("missing_week")
                    if pd.Timestamp(end).dayofweek != 6 or not group.period_start.eq(start).all():
                        raise ValueError("not_one_complete_monday_sunday_week")
                    amount, market, _ = _month_values(group, contract, metric, common_source=False)
                    share, status = _share(amount, market, handle=metric == "handle")
                    item.update(company_amount=amount, market_amount=market, company_share_pct=share, share_status=status)
                except ValueError as exc:
                    item.update(status="excluded", reason=str(exc), share_status="excluded")
                output.append(item)
    return pd.DataFrame(output, columns=columns)


def build_industry_tables(observations, *, quarter, through_month, ny_weeks=8):
    """Reuse the same native accounting/calendars for each explicit operator.

    State demand is computed independently of the selected brands. Every month
    in the requested quarter-to-date window and its prior-year counterpart must
    qualify before growth, share change or hold change is displayed.
    """
    if not re.fullmatch(r"\d{4}Q[1-4]", str(quarter)) or not re.fullmatch(r"\d{4}-\d{2}", str(through_month)):
        raise ValueError("Use an explicit YYYYQn quarter and YYYY-MM through_month")
    monthly, quarterly, holds = [], [], []
    for company, operators in NATIVE_OPERATORS.items():
        direct = {key: value for key, value in operators.items() if not (company == "CZR" and key[0] == "MI")}
        m = build_monthly_scorecard(observations, operators=direct)
        if company == "CZR":
            m = pd.concat([m, caesars_michigan_monthly(observations)], ignore_index=True)
        q = build_quarterly_scorecard(m, quarter=quarter, through_month=through_month)
        q["mapping_status"] = ["native_brand_only" if (row.state_code, row.vertical) in operators else "unmapped"
                               for row in q.itertuples()]
        q.loc[q.mapping_status.eq("unmapped"), "status"] = "unmapped_company_scope"
        h = sportsbook_hold(q)
        monthly.append(_company_columns(m).assign(company=company))
        quarterly.append(_company_columns(q).assign(company=company))
        holds.append(_company_columns(h).assign(company=company))
    m, q, h = [pd.concat(items, ignore_index=True) for items in (monthly, quarterly, holds)]
    q["amount_direction"] = q.company_growth_pct.map(_direction)
    q["share_direction"] = q.share_change_pp.map(lambda value: _direction(value, "gaining", "losing"))
    h["hold_direction"] = h.hold_change_pp.map(_direction)
    # None means the independently verified printed market, not an absent brand.
    market_monthly = build_monthly_scorecard(observations, operators={key: None for key in CONTRACTS})
    market_q = build_quarterly_scorecard(market_monthly, quarter=quarter, through_month=through_month)
    market = market_q.drop(columns=[c for c in market_q if "fd_" in c or c in {"share_change_pp", "growth_status", "share_status", "prior_share_status"}])
    market["direction"] = market.market_growth_pct.map(_direction)
    market_hold = sportsbook_hold(market_q).rename(columns={"fd_hold_pct": "market_hold_pct", "prior_fd_hold_pct": "prior_market_hold_pct"})
    return dict(monthly=m, quarterly=q, market=market, market_monthly=market_monthly,
                demand=market[market.metric.eq("handle")].copy(),
                competition=q[q.vertical.eq(SPORTS) & q.metric.eq("handle")].copy(),
                hold=h, market_hold=market_hold,
                casino=q[q.vertical.eq(CASINO)].copy(),
                weekly=ny_weekly_panel(observations, weeks=ny_weeks))


def coverage_table(observations, coverage, *, capture_at, age_warning_days=90):
    """Report-period age is an analyst attention threshold, not a missed deadline."""
    counts = observations.groupby(["state_code", "vertical"], as_index=False).agg(
        observations=("operator", "size"), first_period=("period_start", "min"),
        latest_observation=("period_end", "max"), source_versions=("source_sha256", "nunique"))
    result = coverage.merge(counts, on=["state_code", "vertical"], how="outer")
    capture = pd.Timestamp(capture_at).tz_convert("UTC").tz_localize(None).normalize()
    result["period_age_days"] = (capture - pd.to_datetime(result.latest_observation)).dt.days
    result["attention"] = result.period_age_days.gt(age_warning_days) | result.observations.isna() | ~result.status.isin(["ok", "recent_only"])
    return result


def business_update(tables, legal_events=None):
    """Plain metric-specific observations and interpretations, with no composite score."""
    output = []
    for row in tables["demand"].itertuples():
        observed = (f"{row.state_code} official online handle {row.market_growth_pct:+.1f}% YoY ({row.expected_months})."
                    if row.status == "comparable" and pd.notna(row.market_growth_pct)
                    else f"{row.state_code} handle comparison unavailable; missing/excluded: {row.missing_or_excluded_months or 'nonpositive baseline'}.")
        output.append(dict(subject=f"Industry / {row.state_code} sportsbook", what_changed=observed,
            interpretation=f"Measured wagering demand is {row.direction}; handle is separate from gross win and customer counts.",
            uncertainty=f"{row.expected_window_months} of 3 quarter months; same months in two years, limited to this state. Higher YoY activity alone does not prove acceleration or durable improvement.",
            next_check="Next official month and same-month prior year; inspect excluded source rows."))
    casino_market = tables["market"].query("vertical == @CASINO and metric == 'gross_revenue'")
    for row in casino_market.itertuples():
        text = (f"MI casino Gross Receipts {row.market_growth_pct:+.1f}% YoY ({row.expected_months})."
                if pd.notna(row.market_growth_pct) else "MI casino Gross Receipts comparison unavailable.")
        output.append(dict(subject="Industry / MI casino", what_changed=text,
            interpretation=f"Reported casino gross receipts are {row.direction}; casino is separate from sportsbook demand.",
            uncertainty="Gross Receipts differ from Adjusted Gross and issuer net revenue.", next_check="Next MI internet gaming workbook and adjustments."))
    for company in NATIVE_OPERATORS:
        parts = []
        for row in tables["competition"].query("company == @company").itertuples():
            parts.append(f"{row.state_code} handle share {row.share_change_pp:+.2f} pp YoY ({row.share_direction})"
                         if pd.notna(row.share_change_pp) else f"{row.state_code} company comparison not yet supported" if row.mapping_status == "unmapped"
                         else f"{row.state_code} handle-share comparison unavailable: missing or excluded months")
        for row in tables["casino"].query("company == @company and metric == 'gross_revenue'").itertuples():
            parts.append(f"MI casino Gross Receipts {row.company_growth_pct:+.1f}% YoY; share {row.share_change_pp:+.2f} pp"
                         if pd.notna(row.company_growth_pct) and pd.notna(row.share_change_pp) else "Michigan company casino comparison not yet supported" if row.mapping_status == "unmapped"
                         else "MI casino comparison unavailable: missing/excluded months or nonpositive baseline")
        hold = tables["hold"].query("company == @company")
        hold_text = "; ".join(f"{row.state_code} gross hold {row.hold_change_pp:+.2f} pp" for row in hold.itertuples() if pd.notna(row.hold_change_pp))
        output.append(dict(subject=company, what_changed="; ".join(parts) + ".",
            interpretation=(hold_text + ". " if hold_text else "") + "Share changes describe competitive position. Gross hold can move independently of wagering demand; its causes are not identified here.",
            uncertainty=SCOPE_NOTES[company] + " State evidence does not quantify company EBITDA, retention or valuation.",
            next_check="Next state releases, same-period share/hold, promotions and issuer disclosures; keep state differences visible."))
    count = 0 if legal_events is None else len(legal_events)
    output.append(dict(subject="Law and regulation", what_changed=f"{count} retained events reviewed separately from operating metrics.",
        interpretation="An enacted baseline is not a new deterioration. Proposals and interim court rulings retain their stated status and jurisdiction.",
        uncertainty="This is a small event watchlist; current legal coverage is not exhaustive. Statutory cost is not an estimated EBITDA change.",
        next_check="Recheck listed primary sources and pending effective dates/dockets; retain changed source bytes as a new capture."))
    return pd.DataFrame(output)


def research_note(update, *, quarter, through_month, capture_at, database_sha256, sources, legal_events=None):
    """A reusable Markdown note; explicit window, capture and drilldown accompany prose."""
    lines = [f"# Gambling industry research — {quarter} through {through_month}", "",
             f"Operating-data capture: {capture_at}.",
             "Current retained evidence; capture time does not reconstruct historical public availability.", ""]
    if "supporting_evidence" in update:
        lines[0] = f"# Monthly gambling fundamentals — through {through_month}"
        lines.extend([f"Twelve monthly reads: {pd.Period(through_month, freq='M')-11} through {through_month}. Latest 3M: {update.window.iloc[0]}.",
                      "Prior-read changes use the same capture. Acceleration compares latest 3M YoY with preceding non-overlapping 3M YoY; no composite rating is calculated.", ""])
    for row in update.itertuples():
        lines.append(f"## {row.subject}")
        if hasattr(row, "supporting_evidence"):
            lines.extend([f"**Supporting evidence:** {row.supporting_evidence}", f"**Contrary evidence:** {row.contrary_evidence}"])
        lines.extend([f"**What changed:** {row.what_changed}",
                      f"**Interpretation:** {row.interpretation}", f"**Uncertainty:** {row.uncertainty}",
                      f"**Next check:** {row.next_check}", ""])
    if legal_events is not None:
        lines.extend(["## Legal evidence", ""])
        for row in legal_events.itertuples():
            lines.extend([f"- {row.event_id} ({row.status}; status as of {row.status_as_of}; effective {row.effective_date or 'unknown'}): {row.observed_fact}",
                          f"  Interpretation: {row.business_interpretation}. Limits: {row.limitations}. Recheck: {row.next_check}.",
                          f"  [{row.jurisdiction} source]({row.source_url})."])
    lines.extend(["", "## Source and integrity appendix", "", f"Database SHA256: `{database_sha256}`.", "", "### Operating sources", ""])
    for row in sources.itertuples():
        lines.append(f"- [{row.source_file}]({row.source_url}) — `{row.source_sha256}`")
    if legal_events is not None:
        lines.extend(["", "### Legal sources", ""])
        for row in legal_events.itertuples():
            lines.append(f"- {row.event_id}: [{row.source_file}]({row.source_url}) — `{row.source_sha256}`; captured {row.captured_at}.")
    return "\n".join(lines) + "\n"


def export_research_note(note, destination):
    """Optional explicit write to a NEW dated Markdown path, never an overwrite."""
    destination = Path(destination)
    if not destination.is_absolute() or destination.suffix.lower() != ".md" or not re.search(r"\d{4}-?\d{2}-?\d{2}", destination.name):
        raise ValueError("Choose an absolute dated .md filename in an existing directory")
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(note)
    return destination
