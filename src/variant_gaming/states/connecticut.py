"""Connecticut DCP online sports + casino collectors via data.ct.gov source CSV."""

from __future__ import annotations

from datetime import datetime
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from variant_gaming.common import (
    http_get,
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

LANDING_URL = "https://portal.ct.gov/dcp/gaming-division/gaming/gaming-revenue-and-statistics"
# Dashboard embeds are charts; collect the underlying selected-data tables (source CSV).
SPORTS_SOURCE_DATASET = "xf6g-659c"  # Selected Online Sport Wagering Data
CASINO_SOURCE_DATASET = "imqd-at3c"  # Selected Online Casino Gaming Data
SPORTS_DASHBOARD_ID = "2b8v-xwd3"
CASINO_DASHBOARD_ID = "se2q-sh9j"

STATE_CODE = "CT"
JURISDICTION = "Connecticut"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def source_csv_url(dataset_id: str) -> str:
    return f"https://data.ct.gov/api/views/{dataset_id}/rows.csv?accessType=DOWNLOAD"


def download_source_csv(dataset_id: str, session: requests.Session | None = None) -> str:
    response = http_get(source_csv_url(dataset_id), session=session, headers=BROWSER_HEADERS)
    text = response.text
    if not text.strip() or "Licensee" not in text:
        raise RuntimeError(f"CT source CSV empty or unexpected for dataset {dataset_id}")
    return text


def _month_from_ending(value) -> tuple[int, int] | None:
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return int(ts.year), int(ts.month)


def parse_sports_csv(text: str) -> pd.DataFrame:
    raw = pd.read_csv(StringIO(text))
    records: list[dict] = []
    for row in raw.to_dict(orient="records"):
        period = _month_from_ending(row.get("Month Ending"))
        if period is None:
            continue
        operator = str(row.get("Licensee") or "").strip()
        if not operator:
            continue
        records.append(
            {
                "operator": operator,
                "year": period[0],
                "month": period[1],
                "handle": parse_money(row.get("Wagers")),
                "gross_revenue": parse_money(row.get("Total Gross Gaming Revenue")),
                "tax": parse_money(row.get("Payment (7)")),
            }
        )
    if not records:
        raise ValueError("No CT online sports source rows parsed")
    return pd.DataFrame(records)


def parse_casino_csv(text: str) -> pd.DataFrame:
    raw = pd.read_csv(StringIO(text))
    records: list[dict] = []
    for row in raw.to_dict(orient="records"):
        period = _month_from_ending(row.get("Month Ending"))
        if period is None:
            continue
        operator = str(row.get("Licensee") or "").strip()
        if not operator:
            continue
        records.append(
            {
                "operator": operator,
                "year": period[0],
                "month": period[1],
                "handle": parse_money(row.get("Wagers")),
                "gross_revenue": parse_money(row.get("Total Gross Gaming Revenue")),
                "tax": parse_money(row.get("Payment (5)")),
            }
        )
    if not records:
        raise ValueError("No CT online casino source rows parsed")
    return pd.DataFrame(records)


def build_normalized_rows(
    parsed: pd.DataFrame,
    *,
    vertical: str,
    source_url: str,
    source_file: str,
    source_sha256: str,
    retrieved_at: datetime,
    reported_revenue_name: str = "Total Gross Gaming Revenue",
) -> pd.DataFrame:
    rows = []
    for record in parsed.to_dict(orient="records"):
        period_start, period_end = month_period(int(record["year"]), int(record["month"]))
        rows.append(
            {
                "jurisdiction": JURISDICTION,
                "state_code": STATE_CODE,
                "vertical": vertical,
                "channel": "online",
                "operator": record["operator"],
                "row_type": "operator",
                "period_start": period_start,
                "period_end": period_end,
                "frequency": "monthly",
                "handle": record.get("handle"),
                "gross_revenue": record.get("gross_revenue"),
                "adjusted_revenue": None,
                "taxable_revenue": None,
                "net_proceeds": None,
                "tax": record.get("tax"),
                "reported_revenue_name": reported_revenue_name,
                "source_url": source_url,
                "source_file": source_file,
                "source_sha256": source_sha256,
                "retrieved_at_utc": retrieved_at.isoformat(),
                "report_status": "ok",
            }
        )
    # Operator rows only. Statewide sums are computed later for validation/consolidation.
    return pd.DataFrame(rows)


def derived_totals_match_operator_sums(connection) -> tuple[bool, int, pd.DataFrame]:
    """Return (matches, derived_count, comparison) for existing CT derived rows."""
    derived = pd.read_sql_query(
        """
        SELECT vertical, period_start, period_end, handle, gross_revenue, tax
        FROM gaming_results
        WHERE state_code='CT' AND row_type='derived_statewide_total'
        """,
        connection,
    )
    if derived.empty:
        return True, 0, derived
    operators = pd.read_sql_query(
        """
        SELECT vertical, period_start, period_end,
               SUM(handle) AS handle, SUM(gross_revenue) AS gross_revenue, SUM(tax) AS tax
        FROM gaming_results
        WHERE state_code='CT' AND row_type='operator'
        GROUP BY vertical, period_start, period_end
        """,
        connection,
    )
    merged = derived.merge(
        operators,
        on=["vertical", "period_start", "period_end"],
        how="left",
        suffixes=("_derived", "_operators"),
    )
    for col in ["handle", "gross_revenue", "tax"]:
        merged[f"{col}_diff"] = (
            pd.to_numeric(merged[f"{col}_derived"], errors="coerce")
            - pd.to_numeric(merged[f"{col}_operators"], errors="coerce")
        )
    matches = bool((merged[["handle_diff", "gross_revenue_diff", "tax_diff"]].abs().fillna(1) < 0.02).all().all())
    return matches, int(len(derived)), merged


def purge_derived_statewide_totals(connection) -> int:
    count = connection.execute(
        "SELECT COUNT(*) FROM gaming_results WHERE state_code='CT' AND row_type='derived_statewide_total'"
    ).fetchone()[0]
    connection.execute(
        "DELETE FROM gaming_results WHERE state_code='CT' AND row_type='derived_statewide_total'"
    )
    connection.commit()
    return int(count)


def _collect_vertical(
    *,
    vertical: str,
    dataset_id: str,
    dashboard_id: str,
    parse_fn,
    filename: str,
    root: Path,
    connection,
    session: requests.Session,
    retrieved_at: datetime,
) -> pd.DataFrame:
    matches, derived_count, _comparison = derived_totals_match_operator_sums(connection)
    if derived_count and not matches:
        raise RuntimeError(
            f"Refusing to delete {derived_count} CT derived statewide rows; "
            "they do not match retained operator sums"
        )
    if derived_count:
        purge_derived_statewide_totals(connection)

    text = download_source_csv(dataset_id, session=session)
    path = save_raw_text(root, STATE_CODE, text, filename, retrieved_at=retrieved_at)
    parsed = parse_fn(text)
    source_url = source_csv_url(dataset_id)
    normalized = build_normalized_rows(
        parsed,
        vertical=vertical,
        source_url=source_url,
        source_file=str(path.relative_to(root)).replace("\\", "/"),
        source_sha256=sha256_bytes(text.encode("utf-8")),
        retrieved_at=retrieved_at,
    )
    upsert_gaming_results(connection, normalized)
    upsert_coverage(
        connection,
        {
            "state_code": STATE_CODE,
            "vertical": vertical,
            "status": "ok",
            "reason": (
                f"data.ct.gov source CSV {dataset_id} (dashboard {dashboard_id} is chart-only; "
                "not scraped as pixels)"
            ),
            "official_url": LANDING_URL,
            "available_frequency": "monthly",
            "earliest_period": normalized["period_start"].min(),
            "latest_period": normalized["period_end"].max(),
            "downloaded_file_count": 1,
            "normalized_row_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM gaming_results WHERE state_code=? AND vertical=?",
                    (STATE_CODE, vertical),
                ).fetchone()[0]
            ),
            "last_retrieval_utc": utc_now().isoformat(),
        },
    )
    return normalized


def collect_sports_history(
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    root = root or project_root()
    retrieved_at = utc_now()
    sess = session or requests.Session()
    db_path = db_path or default_db_path(root)
    connection = connect(db_path)
    migrate_legacy_table(connection)
    ensure_schema(connection)
    try:
        result = _collect_vertical(
            vertical="online_sports_betting",
            dataset_id=SPORTS_SOURCE_DATASET,
            dashboard_id=SPORTS_DASHBOARD_ID,
            parse_fn=parse_sports_csv,
            filename="ct_online_sports_source.csv",
            root=root,
            connection=connection,
            session=sess,
            retrieved_at=retrieved_at,
        )
        connection.close()
        return result
    except Exception as exc:  # noqa: BLE001
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": "online_sports_betting",
                "status": "blocked_or_failed",
                "reason": f"Source-data download failed: {exc}",
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
        raise


def collect_casino_history(
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    root = root or project_root()
    retrieved_at = utc_now()
    sess = session or requests.Session()
    db_path = db_path or default_db_path(root)
    connection = connect(db_path)
    migrate_legacy_table(connection)
    ensure_schema(connection)
    try:
        result = _collect_vertical(
            vertical="online_casino",
            dataset_id=CASINO_SOURCE_DATASET,
            dashboard_id=CASINO_DASHBOARD_ID,
            parse_fn=parse_casino_csv,
            filename="ct_online_casino_source.csv",
            root=root,
            connection=connection,
            session=sess,
            retrieved_at=retrieved_at,
        )
        connection.close()
        return result
    except Exception as exc:  # noqa: BLE001
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": "online_casino",
                "status": "blocked_or_failed",
                "reason": f"Source-data download failed: {exc}",
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
        raise


def collect_history(
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Collect both CT online sports and online casino source CSVs."""
    sports = collect_sports_history(root=root, db_path=db_path, session=session)
    casino = collect_casino_history(root=root, db_path=db_path, session=session)
    return pd.concat([sports, casino], ignore_index=True)
