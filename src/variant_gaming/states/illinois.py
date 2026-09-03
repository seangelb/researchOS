"""Illinois IGB sports-wagering collectors (handle + State AGR)."""

from __future__ import annotations

import re
from datetime import datetime
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from variant_gaming.common import (
    MONTH_NAMES,
    http_post,
    month_period,
    parse_money,
    project_root,
    save_raw_text,
    sha256_bytes,
    utc_now,
)
from variant_gaming.storage import (
    connect,
    default_db_path,
    ensure_schema,
    migrate_legacy_table,
    upsert_coverage,
    upsert_gaming_results,
)

LANDING_URL = "https://igb.illinois.gov/sports-wagering/sports-reports.html"
APPS_URL = "https://igbapps.illinois.gov/SportsReports_AEM.aspx"
CHANNEL_MAP = {
    "Online Wagering": "online",
    "In-Person Wagering": "retail",  # reference only; excluded from primary upsert
}


def _form_tokens(html: str) -> dict[str, str]:
    return {
        "__VIEWSTATE": re.search(r'id="__VIEWSTATE" value="([^"]*)"', html).group(1),
        "__EVENTVALIDATION": re.search(r'id="__EVENTVALIDATION" value="([^"]*)"', html).group(1),
        "__VIEWSTATEGENERATOR": re.search(
            r'id="__VIEWSTATEGENERATOR" value="([^"]*)"', html
        ).group(1),
    }


def download_igb_csv(
    report_family: str,
    report_name: str,
    month_name: str,
    year: int,
    session: requests.Session | None = None,
) -> str:
    """
    report_family: 'cash' (All Wagering Activity) or 'accrual' (Completed Events)
    report_name for cash: 'Sport Detail Report' or 'Detail Report'
    report_name for accrual: 'Tax Summary Report', etc.
    """
    sess = session or requests.Session()
    page = sess.get(APPS_URL, timeout=60, verify=True, headers={"User-Agent": "researchOS-variant-gaming/1.0"})
    page.raise_for_status()
    tokens = _form_tokens(page.text)

    if report_family == "cash":
        search_type = "TypeCash"
        search_cash = report_name
        search_accrual = "Tax Summary Report"
    elif report_family == "accrual":
        search_type = "TypeAccrual"
        search_cash = "Detail Report"
        search_accrual = report_name
    else:
        raise ValueError(f"Unknown report_family: {report_family}")

    form_data = {
        **tokens,
        "SearchType": search_type,
        "SearchCash": search_cash,
        "SearchAccrual": search_accrual,
        "SearchStartMonth": month_name,
        "SearchStartYear": str(year),
        "SearchEndMonth": month_name,
        "SearchEndYear": str(year),
        "ViewType": "ViewCSV",
        "ButtonSearch.x": "10",
        "ButtonSearch.y": "10",
    }
    response = http_post(APPS_URL, data=form_data, session=sess)
    return response.text


def csv_has_marker(text: str, *markers: str) -> bool:
    return all(marker in text for marker in markers)


def find_header_line(text: str, required_substring: str) -> int:
    for index, line in enumerate(text.splitlines()):
        if required_substring in line:
            return index
    raise ValueError(f"Header containing {required_substring!r} not found")


def parse_sport_detail_handle(csv_text: str) -> pd.DataFrame:
    """Return operator/channel handle rows (excludes official Total location rows)."""
    header = find_header_line(csv_text, "Detail Type")
    raw = pd.read_csv(StringIO(csv_text), skiprows=header)
    handle = raw[raw["Detail Type"] == "Handle Details"].copy()
    meta = {"Detail Type", "Location Type", "Licensee"}
    sport_cols = [c for c in handle.columns if c not in meta and not str(c).startswith("Unnamed")]
    for col in sport_cols:
        handle[col] = handle[col].map(parse_money)
    handle["handle"] = handle[sport_cols].sum(axis=1, min_count=1)

    totals = handle[handle["Location Type"] == "Total"][["Licensee", "handle"]].rename(
        columns={"Licensee": "operator", "handle": "official_total_handle"}
    )
    channel = handle[handle["Location Type"].isin(CHANNEL_MAP)].copy()
    channel["channel"] = channel["Location Type"].map(CHANNEL_MAP)
    channel = channel.rename(columns={"Licensee": "operator"})
    channel = channel[["channel", "operator", "handle"]]

    # Reconcile online+retail to official Total per licensee
    summed = channel.groupby("operator", as_index=False)["handle"].sum().rename(
        columns={"handle": "sum_of_channels"}
    )
    check = totals.merge(summed, on="operator", how="outer")
    check["difference"] = check["official_total_handle"] - check["sum_of_channels"]
    max_diff = check["difference"].abs().max()
    if pd.isna(max_diff) or max_diff > 0.02:
        raise ValueError(f"Handle reconciliation failed; max abs diff={max_diff}")

    return channel, check


def parse_tax_summary(csv_text: str) -> pd.DataFrame:
    """Parse Completed Events Tax Summary: State AGR and taxes by licensee/channel."""
    header = find_header_line(csv_text, "State AGR")
    raw = pd.read_csv(StringIO(csv_text), skiprows=header)
    # Drop blank trailing columns
    raw = raw[[c for c in raw.columns if not str(c).startswith("Unnamed")]].copy()

    money_cols = [
        c
        for c in raw.columns
        if c not in {"Location Type", "Licensee"}
    ]
    for col in money_cols:
        raw[col] = raw[col].map(parse_money)

    # Exclude grand-total style rows if present
    frame = raw[raw["Location Type"].isin(CHANNEL_MAP)].copy()
    frame["channel"] = frame["Location Type"].map(CHANNEL_MAP)
    frame = frame.rename(columns={"Licensee": "operator"})

    # Reconcile online + retail AGR to official Total rows per licensee.
    totals = raw[raw["Location Type"] == "Total"][["Licensee", "State AGR"]].rename(
        columns={"Licensee": "operator", "State AGR": "official_total_agr"}
    )
    channel_sums = (
        frame.groupby("operator", as_index=False)["State AGR"]
        .sum()
        .rename(columns={"State AGR": "sum_of_channels"})
    )
    check = totals.merge(channel_sums, on="operator", how="outer")
    check["difference"] = check["official_total_agr"] - check["sum_of_channels"]
    max_diff = check["difference"].abs().max()
    if pd.isna(max_diff) or max_diff > 0.02:
        raise ValueError(f"Tax Summary AGR reconciliation failed; max abs diff={max_diff}")

    # State Tax is the primary tax on State AGR (not interchangeable with Cook County / Sports Wager Tax).
    out = pd.DataFrame(
        {
            "channel": frame["channel"].values,
            "operator": frame["operator"].values,
            "adjusted_revenue": frame["State AGR"].values if "State AGR" in frame.columns else None,
            "tax": frame["State Tax"].values if "State Tax" in frame.columns else None,
        }
    )
    return out


def join_handle_and_revenue(handle_df: pd.DataFrame, tax_df: pd.DataFrame) -> pd.DataFrame:
    merged = handle_df.merge(tax_df, on=["channel", "operator"], how="outer", indicator=True)
    # Keep rows that appear in either report; money fields stay null when missing
    return merged.drop(columns=["_merge"])


def build_normalized_month(
    joined: pd.DataFrame,
    *,
    year: int,
    month: int,
    handle_path: Path,
    tax_path: Path,
    handle_sha: str,
    tax_sha: str,
    retrieved_at: datetime,
    root: Path,
    online_only: bool = True,
) -> pd.DataFrame:
    period_start, period_end = month_period(year, month)
    rows = joined.copy()
    if online_only:
        rows = rows[rows["channel"] == "online"].copy()

    # Combined source identity: both files contribute; primary key uses tax sha when present else handle sha
    # Store both paths in source_file as "handle|tax" and use a composite hash for uniqueness.
    composite_sha = sha256_bytes(f"{handle_sha}|{tax_sha}".encode("utf-8"))
    rel_handle = str(handle_path.relative_to(root)).replace("\\", "/")
    rel_tax = str(tax_path.relative_to(root)).replace("\\", "/")

    rows = rows.assign(
        jurisdiction="Illinois",
        state_code="IL",
        vertical="online_sports_betting",
        row_type="operator",
        period_start=period_start,
        period_end=period_end,
        frequency="monthly",
        gross_revenue=pd.NA,
        taxable_revenue=pd.NA,
        net_proceeds=pd.NA,
        reported_revenue_name="State AGR",
        source_url=LANDING_URL,
        source_file=f"{rel_handle}|{rel_tax}",
        source_sha256=composite_sha,
        retrieved_at_utc=retrieved_at.isoformat(),
        report_status="ok",
    )
    return rows


def validate_online_totals(online_df: pd.DataFrame) -> dict:
    """Return summary stats used by the notebook for display."""
    return {
        "operator_rows": int(len(online_df)),
        "handle_sum": float(pd.to_numeric(online_df["handle"], errors="coerce").sum()),
        "agr_sum": float(pd.to_numeric(online_df["adjusted_revenue"], errors="coerce").sum()),
        "tax_sum": float(pd.to_numeric(online_df["tax"], errors="coerce").sum()),
    }


def normalize_igb_csv_for_hash(text: str) -> str:
    """Drop volatile 'Report Date' preamble so re-downloads of the same month upsert cleanly."""
    lines = []
    for line in text.splitlines():
        if line.startswith('"Report Date:') or line.startswith("Report Date:"):
            continue
        lines.append(line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def collect_month(year: int, month: int, root: Path | None = None, online_only: bool = True) -> pd.DataFrame:
    """Download, parse, and return normalized operator rows for one calendar month."""
    root = root or project_root()
    retrieved_at = utc_now()
    month_name = MONTH_NAMES[month - 1]
    session = requests.Session()

    handle_text = download_igb_csv("cash", "Sport Detail Report", month_name, year, session=session)
    if not csv_has_marker(handle_text, "Detail Type", "Handle Details"):
        raise RuntimeError(f"No Sport Detail CSV for {month_name} {year}")

    tax_text = download_igb_csv("accrual", "Tax Summary Report", month_name, year, session=session)
    if not csv_has_marker(tax_text, "State AGR"):
        raise RuntimeError(f"No Tax Summary CSV for {month_name} {year}")

    handle_text = normalize_igb_csv_for_hash(handle_text)
    tax_text = normalize_igb_csv_for_hash(tax_text)

    handle_path = save_raw_text(
        root,
        "IL",
        handle_text,
        f"il_sport_detail_{year}-{month:02d}.csv",
        retrieved_at=retrieved_at,
    )
    tax_path = save_raw_text(
        root,
        "IL",
        tax_text,
        f"il_tax_summary_{year}-{month:02d}.csv",
        retrieved_at=retrieved_at,
    )
    handle_sha = handle_path.name.split("_", 1)[0]
    tax_sha = tax_path.name.split("_", 1)[0]

    handle_df, _reconcile = parse_sport_detail_handle(handle_text)
    tax_df = parse_tax_summary(tax_text)
    joined = join_handle_and_revenue(handle_df, tax_df)
    normalized = build_normalized_month(
        joined,
        year=year,
        month=month,
        handle_path=handle_path,
        tax_path=tax_path,
        handle_sha=handle_sha,
        tax_sha=tax_sha,
        retrieved_at=retrieved_at,
        root=root,
        online_only=online_only,
    )

    # Add official statewide total row from online operator sums when no official total line exists
    if online_only and not normalized.empty:
        total = {
            "jurisdiction": "Illinois",
            "state_code": "IL",
            "vertical": "online_sports_betting",
            "channel": "online",
            "operator": "STATEWIDE",
            "row_type": "official_statewide_total",
            "period_start": normalized["period_start"].iloc[0],
            "period_end": normalized["period_end"].iloc[0],
            "frequency": "monthly",
            "handle": float(pd.to_numeric(normalized["handle"], errors="coerce").sum()),
            "gross_revenue": None,
            "adjusted_revenue": float(pd.to_numeric(normalized["adjusted_revenue"], errors="coerce").sum()),
            "taxable_revenue": None,
            "net_proceeds": None,
            "tax": float(pd.to_numeric(normalized["tax"], errors="coerce").sum()),
            "reported_revenue_name": "State AGR",
            "source_url": LANDING_URL,
            "source_file": normalized["source_file"].iloc[0],
            "source_sha256": normalized["source_sha256"].iloc[0],
            "retrieved_at_utc": retrieved_at.isoformat(),
            "report_status": "derived_from_operator_sum",
        }
        # IGB tax CSV has no statewide total line; mark as derived sum of operators
        normalized = pd.concat([normalized, pd.DataFrame([total])], ignore_index=True)
        # Prefer report_status ok for operators; statewide is clearly labeled derived
        normalized.loc[normalized["row_type"] == "operator", "report_status"] = "ok"

    return normalized


def iter_months(start_year: int, start_month: int, end_year: int, end_month: int):
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        yield year, month
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def collect_history(
    start_year: int = 2020,
    start_month: int = 1,
    end_year: int | None = None,
    end_month: int | None = None,
    root: Path | None = None,
    db_path: Path | None = None,
) -> pd.DataFrame:
    """
    Collect all available months from start through end (default: through last completed month).
    Skips months that return no CSV. Upserts into SQLite without wiping other states.
    """
    root = root or project_root()
    now = utc_now()
    if end_year is None or end_month is None:
        # last completed calendar month
        if now.month == 1:
            end_year, end_month = now.year - 1, 12
        else:
            end_year, end_month = now.year, now.month - 1

    db_path = db_path or default_db_path(root)
    connection = connect(db_path)
    migrate_legacy_table(connection)
    ensure_schema(connection)

    all_frames: list[pd.DataFrame] = []
    downloaded = 0
    failures: list[str] = []

    for year, month in iter_months(start_year, start_month, end_year, end_month):
        label = f"{MONTH_NAMES[month - 1]} {year}"
        try:
            frame = collect_month(year, month, root=root, online_only=True)
            upsert_gaming_results(connection, frame)
            all_frames.append(frame)
            downloaded += 2  # handle + tax
            print(f"OK {label}: {len(frame)} rows")
        except Exception as exc:  # noqa: BLE001 - continue other months
            failures.append(f"{label}: {exc}")
            print(f"SKIP {label}: {exc}")

    if all_frames:
        result = pd.concat(all_frames, ignore_index=True)
        upsert_coverage(
            connection,
            {
                "state_code": "IL",
                "vertical": "online_sports_betting",
                "status": "ok" if not failures else "partial",
                "reason": "; ".join(failures[:5]) if failures else "Collected Sport Detail handle + Tax Summary State AGR (online only)",
                "official_url": LANDING_URL,
                "available_frequency": "monthly",
                "earliest_period": result["period_start"].min(),
                "latest_period": result["period_end"].max(),
                "downloaded_file_count": downloaded,
                "normalized_row_count": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM gaming_results WHERE state_code='IL' AND vertical='online_sports_betting'"
                    ).fetchone()[0]
                ),
                "last_retrieval_utc": utc_now().isoformat(),
            },
        )
        connection.close()
        return result

    upsert_coverage(
        connection,
        {
            "state_code": "IL",
            "vertical": "online_sports_betting",
            "status": "failed",
            "reason": "; ".join(failures) or "No months collected",
            "official_url": LANDING_URL,
            "available_frequency": "monthly",
            "earliest_period": None,
            "latest_period": None,
            "downloaded_file_count": 0,
            "normalized_row_count": 0,
            "last_retrieval_utc": utc_now().isoformat(),
        },
    )
    connection.close()
    return pd.DataFrame()
