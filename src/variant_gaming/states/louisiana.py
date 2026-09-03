"""Louisiana LSP mobile sportsbook collectors (statewide Excel)."""

from __future__ import annotations

import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, unquote

import pandas as pd
import requests
from bs4 import BeautifulSoup

from variant_gaming.common import (
    http_get,
    month_period,
    parse_money,
    project_root,
    save_raw_bytes,
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

LANDING_URL = (
    "https://lsp.org/about/leadershipsections/bureau-of-investigations/"
    "gaming-enforcement-division/gaming-revenue-reports/"
)
INVENTORY_URL = "https://lgcb.dps.louisiana.gov/reports/"
STATE_CODE = "LA"
JURISDICTION = "Louisiana"
VERTICAL = "online_sports_betting"
REPORTED_REVENUE_NAME = "Net Proceeds"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def discover_mobile_excel_links(html: str, base_url: str = LANDING_URL) -> list[dict]:
    """Find Sportsbook - Mobile Excel workbook links on the LSP revenue page."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        text = " ".join(anchor.get_text(" ", strip=True).split())
        absolute = urljoin(base_url, href)
        low = absolute.lower()
        if not low.endswith((".xlsx", ".xls")):
            continue
        if "mobile" not in low and "mobile" not in text.lower():
            continue
        if "sb" not in low and "sports" not in text.lower() and "sportsbook" not in text.lower():
            continue
        filename = unquote(absolute.rsplit("/", 1)[-1].split("?", 1)[0])
        found[absolute] = {"url": absolute, "filename": filename, "link_text": text}
    # Prefer newest filename lexical / page order last seen
    return sorted(found.values(), key=lambda item: item["filename"], reverse=True)


def _is_month_timestamp(value) -> bool:
    return isinstance(value, (datetime, pd.Timestamp)) or (
        isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value.strip())
    )


def _to_timestamp(value) -> pd.Timestamp | None:
    if isinstance(value, pd.Timestamp):
        return value
    if isinstance(value, datetime):
        return pd.Timestamp(value)
    try:
        ts = pd.to_datetime(value, errors="coerce")
    except Exception:  # noqa: BLE001
        return None
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts)


def parse_mobile_workbook(content: bytes) -> pd.DataFrame:
    """
    Parse FY sheets for monthly statewide mobile sports rows.

    Columns (after header): date, wagers written, promo deduct, net proceeds, taxes paid.
    Placeholder months use -1 / blank and are skipped.
    """
    xl = pd.ExcelFile(BytesIO(content))
    records: list[dict] = []
    seen: set[tuple[int, int]] = set()

    # Prefer FY## history sheets only (Current mixes placeholders for future months).
    sheet_names = [s for s in xl.sheet_names if re.match(r"^FY\d{2}$", str(s), re.I)]
    if not sheet_names:
        sheet_names = [s for s in xl.sheet_names if str(s).lower() == "current"]

    for sheet_name in sheet_names:
        frame = pd.read_excel(BytesIO(content), sheet_name=sheet_name, header=None)
        for row_idx in range(len(frame)):
            cell0 = frame.iloc[row_idx, 0]
            ts = _to_timestamp(cell0) if _is_month_timestamp(cell0) or isinstance(cell0, (datetime, pd.Timestamp)) else None
            if ts is None:
                continue
            handle = parse_money(frame.iloc[row_idx, 2] if frame.shape[1] > 2 else None)
            net_proceeds = parse_money(frame.iloc[row_idx, 4] if frame.shape[1] > 4 else None)
            tax = parse_money(frame.iloc[row_idx, 5] if frame.shape[1] > 5 else None)
            # Skip FY placeholders (-1) and empty future months.
            if handle is None or handle < 0:
                continue
            if net_proceeds is not None and net_proceeds < 0 and handle == 0:
                continue
            key = (int(ts.year), int(ts.month))
            if key in seen:
                continue
            seen.add(key)
            records.append(
                {
                    "year": key[0],
                    "month": key[1],
                    "handle": handle,
                    "net_proceeds": net_proceeds,
                    "tax": tax,
                    "sheet_name": sheet_name,
                }
            )

    if not records:
        raise ValueError("No Louisiana mobile sports monthly rows parsed")
    return pd.DataFrame(records).sort_values(["year", "month"]).reset_index(drop=True)


def build_normalized_rows(
    parsed: pd.DataFrame,
    *,
    source_url: str,
    source_file: str,
    source_sha256: str,
    retrieved_at: datetime,
) -> pd.DataFrame:
    rows = []
    for record in parsed.to_dict(orient="records"):
        period_start, period_end = month_period(int(record["year"]), int(record["month"]))
        rows.append(
            {
                "jurisdiction": JURISDICTION,
                "state_code": STATE_CODE,
                "vertical": VERTICAL,
                "channel": "online",
                "operator": "STATEWIDE",
                "row_type": "official_statewide_total",
                "period_start": period_start,
                "period_end": period_end,
                "frequency": "monthly",
                "handle": record.get("handle"),
                "gross_revenue": None,
                "adjusted_revenue": None,
                "taxable_revenue": None,
                "net_proceeds": record.get("net_proceeds"),
                "tax": record.get("tax"),
                "reported_revenue_name": REPORTED_REVENUE_NAME,
                "source_url": source_url,
                "source_file": source_file,
                "source_sha256": source_sha256,
                "retrieved_at_utc": retrieved_at.isoformat(),
                "report_status": "ok",
            }
        )
    return pd.DataFrame(rows)


def collect_history(
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Download the latest LSP mobile sports Excel and upsert statewide monthly rows."""
    root = root or project_root()
    retrieved_at = utc_now()
    sess = session or requests.Session()
    db_path = db_path or default_db_path(root)
    connection = connect(db_path)
    migrate_legacy_table(connection)
    ensure_schema(connection)

    try:
        html = http_get(LANDING_URL, session=sess, headers=BROWSER_HEADERS).text
        links = discover_mobile_excel_links(html)
        if not links:
            raise RuntimeError("No Sportsbook Mobile Excel links found on LSP revenue page")
        # Prefer the most recent monthly file; each workbook carries FY history sheets.
        link = links[0]
        content = http_get(link["url"], session=sess, headers=BROWSER_HEADERS).content
        path = save_raw_bytes(root, STATE_CODE, content, link["filename"], retrieved_at=retrieved_at)
        parsed = parse_mobile_workbook(content)
        normalized = build_normalized_rows(
            parsed,
            source_url=link["url"],
            source_file=str(path.relative_to(root)).replace("\\", "/"),
            source_sha256=sha256_bytes(content),
            retrieved_at=retrieved_at,
        )
        upsert_gaming_results(connection, normalized)
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": VERTICAL,
                "status": "ok",
                "reason": "LSP Sportsbook Mobile Excel: Wagers Written + Net Proceeds + Taxes Paid",
                "official_url": LANDING_URL,
                "available_frequency": "monthly",
                "earliest_period": normalized["period_start"].min(),
                "latest_period": normalized["period_end"].max(),
                "downloaded_file_count": 1,
                "normalized_row_count": int(len(normalized)),
                "last_retrieval_utc": utc_now().isoformat(),
            },
        )
        connection.close()
        return normalized
    except Exception as exc:  # noqa: BLE001
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": VERTICAL,
                "status": "failed",
                "reason": str(exc),
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
