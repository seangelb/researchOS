"""Ohio Casino Control Commission sports-gaming Excel collectors (online Type A)."""

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
    MONTH_NAMES,
    http_get,
    month_name_to_num,
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

LANDING_URL = "https://casinocontrol.ohio.gov/about/revenue-reports"
STATE_CODE = "OH"
JURISDICTION = "Ohio"
VERTICAL = "online_sports_betting"
REPORTED_REVENUE_NAME = "Revenue"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

YEAR_IN_NAME_RE = re.compile(r"(20\d{2})")
MONTH_SHEET_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)$",
    re.I,
)


def discover_sports_excel_links(html: str, base_url: str = LANDING_URL) -> list[dict]:
    """Find Ohio Sports Gaming calendar-year Excel workbooks."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        text = " ".join(anchor.get_text(" ", strip=True).split())
        absolute = urljoin(base_url, href)
        low = absolute.lower()
        text_low = text.lower()
        if not (low.endswith((".xlsx", ".xls")) or "xlsx" in low):
            continue
        if "sport" not in low and "sport" not in text_low:
            continue
        # Exclude casino-only workbooks.
        if "casino" in low and "sport" not in low:
            continue
        filename = unquote(absolute.rsplit("/", 1)[-1].split("?", 1)[0])
        year_hint = None
        year_match = YEAR_IN_NAME_RE.search(filename) or YEAR_IN_NAME_RE.search(text)
        if year_match:
            year_hint = int(year_match.group(1))
        found[absolute] = {
            "url": absolute,
            "filename": filename or f"ohio_sports_{year_hint or 'unknown'}.xlsx",
            "link_text": text,
            "year_hint": year_hint,
        }
    return sorted(
        found.values(),
        key=lambda item: (item["year_hint"] or 0, item["filename"]),
        reverse=True,
    )


def _find_header_row(frame: pd.DataFrame) -> int | None:
    for idx in range(min(20, len(frame))):
        values = [str(v).strip().lower() if pd.notna(v) else "" for v in frame.iloc[idx].tolist()]
        joined = " | ".join(values)
        if "online proprietor" in joined or (
            "total gross receipts" in joined and "taxable revenue" in joined
        ):
            return idx
    return None


def parse_month_sheet(frame: pd.DataFrame, *, year: int, month: int) -> pd.DataFrame:
    """Parse one monthly sheet for Type A Online operator + subtotal rows."""
    header_row = _find_header_row(frame)
    if header_row is None:
        raise ValueError(f"No online sports header for {year}-{month:02d}")

    records: list[dict] = []
    in_online = False
    for row_idx in range(header_row + 1, len(frame)):
        row = frame.iloc[row_idx]
        label = "" if pd.isna(row.iloc[0]) else str(row.iloc[0]).strip()
        if not label:
            # blank separator often ends a section
            if in_online and records:
                break
            continue
        low = label.lower()
        if "type a proprietors" in low and "online" in low:
            in_online = True
            continue
        if "type a proprietors" in low and "retail" in low:
            break
        if "type b" in low or "type c" in low:
            break
        if not in_online and "online proprietor" in str(frame.iloc[header_row, 0]).lower():
            in_online = True

        if not in_online:
            continue

        handle = parse_money(row.iloc[1] if len(row) > 1 else None)
        revenue = parse_money(row.iloc[5] if len(row) > 5 else None)
        taxable = parse_money(row.iloc[6] if len(row) > 6 else None)
        if handle is None and revenue is None and taxable is None:
            continue

        if low.startswith("subtotal type a online"):
            records.append(
                {
                    "operator": "STATEWIDE",
                    "row_type": "official_statewide_total",
                    "year": year,
                    "month": month,
                    "handle": handle,
                    "gross_revenue": revenue,
                    "taxable_revenue": taxable,
                }
            )
            break

        if low.startswith("subtotal") or low.startswith("total"):
            continue

        records.append(
            {
                "operator": re.sub(r"\s+", " ", label),
                "row_type": "operator",
                "year": year,
                "month": month,
                "handle": handle,
                "gross_revenue": revenue,
                "taxable_revenue": taxable,
            }
        )

    if not records:
        raise ValueError(f"No Type A Online rows for {year}-{month:02d}")
    return pd.DataFrame(records)


def parse_sports_workbook(content: bytes, *, year_hint: int | None = None) -> pd.DataFrame:
    xl = pd.ExcelFile(BytesIO(content))
    year = year_hint
    if year is None:
        for name in xl.sheet_names:
            m = YEAR_IN_NAME_RE.search(str(name))
            if m:
                year = int(m.group(1))
                break
    if year is None:
        raise ValueError("Could not determine calendar year for Ohio sports workbook")

    frames: list[pd.DataFrame] = []
    for sheet_name in xl.sheet_names:
        if not MONTH_SHEET_RE.match(str(sheet_name).strip()):
            continue
        month = month_name_to_num(str(sheet_name).strip().title())
        frame = pd.read_excel(BytesIO(content), sheet_name=sheet_name, header=None)
        try:
            parsed = parse_month_sheet(frame, year=year, month=month)
        except ValueError:
            continue
        frames.append(parsed)
    if not frames:
        raise ValueError("No Ohio monthly online sports sheets parsed")
    return pd.concat(frames, ignore_index=True)


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
                "operator": record["operator"],
                "row_type": record["row_type"],
                "period_start": period_start,
                "period_end": period_end,
                "frequency": "monthly",
                "handle": record.get("handle"),
                "gross_revenue": record.get("gross_revenue"),
                "adjusted_revenue": None,
                "taxable_revenue": record.get("taxable_revenue"),
                "net_proceeds": None,
                "tax": None,
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
    root = root or project_root()
    retrieved_at = utc_now()
    sess = session or requests.Session()
    db_path = db_path or default_db_path(root)
    connection = connect(db_path)
    migrate_legacy_table(connection)
    ensure_schema(connection)

    failures: list[str] = []
    all_frames: list[pd.DataFrame] = []
    downloaded = 0

    try:
        html = http_get(LANDING_URL, session=sess, headers=BROWSER_HEADERS).text
        links = discover_sports_excel_links(html)
        if not links:
            raise RuntimeError("No Ohio Sports Gaming Excel links found")
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

    # Keep one workbook per calendar year (newest link first already).
    seen_years: set[int] = set()
    for link in links:
        year = link.get("year_hint")
        if year is not None and year in seen_years:
            continue
        try:
            content = http_get(link["url"], session=sess, headers=BROWSER_HEADERS).content
            path = save_raw_bytes(root, STATE_CODE, content, link["filename"], retrieved_at=retrieved_at)
            parsed = parse_sports_workbook(content, year_hint=year)
            if year is None and not parsed.empty:
                year = int(parsed["year"].iloc[0])
            if year is not None:
                seen_years.add(year)
            normalized = build_normalized_rows(
                parsed,
                source_url=link["url"],
                source_file=str(path.relative_to(root)).replace("\\", "/"),
                source_sha256=sha256_bytes(content),
                retrieved_at=retrieved_at,
            )
            upsert_gaming_results(connection, normalized)
            all_frames.append(normalized)
            downloaded += 1
            print(f"OK OH {year}: {len(normalized)} rows ({link['filename']})")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{link['filename']}: {exc}")
            print(f"SKIP {link['filename']}: {exc}")

    if all_frames:
        result = pd.concat(all_frames, ignore_index=True)
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": VERTICAL,
                "status": "ok" if not failures else "partial",
                "reason": (
                    "; ".join(failures[:5])
                    if failures
                    else "OCC Type A Online sports gaming Excel: Gross Receipts + Revenue + Taxable Revenue"
                ),
                "official_url": LANDING_URL,
                "available_frequency": "monthly",
                "earliest_period": result["period_start"].min(),
                "latest_period": result["period_end"].max(),
                "downloaded_file_count": downloaded,
                "normalized_row_count": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM gaming_results WHERE state_code=? AND vertical=?",
                        (STATE_CODE, VERTICAL),
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
            "state_code": STATE_CODE,
            "vertical": VERTICAL,
            "status": "failed",
            "reason": "; ".join(failures) or "No Ohio sports workbooks collected",
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
