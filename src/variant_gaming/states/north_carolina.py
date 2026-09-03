"""North Carolina statewide sports-betting monthly PDF collectors."""

from __future__ import annotations

import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, unquote

import pandas as pd
import pdfplumber
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

LANDING_URL = "https://ncgaming.gov/reports/"
STATE_CODE = "NC"
JURISDICTION = "North Carolina"
VERTICAL = "online_sports_betting"
REPORTED_REVENUE_NAME = "Gross Wagering Revenue"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

MONTH_RE = re.compile(
    r"\b(" + "|".join(MONTH_NAMES) + r")\b",
    re.I,
)
MONTH_LABEL_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)(\d)?$",
    re.I,
)
YEAR_RE = re.compile(r"(20\d{2})")
# Example text line:
# July $551,603,216 $14,746,468 $566,349,683 $4,405,348 $498,728,763 $63,215,572 $14,038,810
# Footnoted labels attach the marker to the month: March1 $456,702,632 ...
ROW_RE = re.compile(
    r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)"
    r"(?P<footnote>\d)?"
    r"\s+(?P<values>(?:\$?\s*[\d,]+(?:\.\d+)?\s*){5,})",
    re.I,
)


def normalize_month_label(label: str) -> str | None:
    """Return a month name, accepting an optional numeric footnote marker suffix."""
    text = str(label).strip()
    if not text:
        return None
    match = MONTH_LABEL_RE.fullmatch(text)
    if not match:
        return None
    return match.group(1).title()


def discover_report_links(html: str, base_url: str = LANDING_URL) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        text = " ".join(anchor.get_text(" ", strip=True).split())
        absolute = urljoin(base_url, href)
        low = absolute.lower()
        if not low.endswith(".pdf"):
            continue
        if "revenue" not in low and "sports" not in low and "revenue" not in text.lower():
            continue
        if "rules" in low or "manual" in low:
            continue
        filename = unquote(absolute.rsplit("/", 1)[-1])
        month_hint = None
        year_hint = None
        month_match = MONTH_RE.search(filename) or MONTH_RE.search(text)
        if month_match:
            month_hint = month_name_to_num(month_match.group(1).title())
        year_match = YEAR_RE.search(filename) or YEAR_RE.search(text)
        if year_match:
            year_hint = int(year_match.group(1))
        found[absolute] = {
            "url": absolute,
            "filename": filename,
            "link_text": text,
            "month_hint": month_hint,
            "year_hint": year_hint,
        }
    return sorted(
        found.values(),
        key=lambda item: (item["year_hint"] or 0, item["month_hint"] or 0, item["filename"]),
    )


def _money_tokens(blob: str) -> list[float]:
    tokens = re.findall(r"\$?\s*[\d\s,]+(?:\.\d+)?", blob)
    values = []
    for token in tokens:
        cleaned = re.sub(r"(?<=\d)\s+(?=\d)", "", token)
        value = parse_money(cleaned)
        if value is not None:
            values.append(value)
    return values


def _cell_money(value) -> float | None:
    if value is None:
        return None
    text = str(value)
    text = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    return parse_money(text)


def parse_revenue_pdf(content: bytes, *, month_hint: int | None = None, year_hint: int | None = None) -> list[dict]:
    with pdfplumber.open(BytesIO(content)) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)

    fy = re.search(r"FY\s*(20\d{2})", text, re.I)
    fy_end = int(fy.group(1)) if fy else None

    def year_for_month(month: int) -> int | None:
        # Prefer FY calendar when present (July starts new FY).
        if fy_end is not None:
            return fy_end - 1 if month >= 7 else fy_end
        if year_hint is not None:
            return year_hint
        return None

    records: list[dict] = []

    # Prefer text rows: pdfplumber tables often split the last money columns.
    for match in ROW_RE.finditer(text):
        month_label = match.group("month") + (match.group("footnote") or "")
        month_name = normalize_month_label(month_label)
        if month_name is None:
            continue
        month = month_name_to_num(month_name)
        nums = _money_tokens(match.group("values"))
        if len(nums) < 7:
            continue
        year = year_for_month(month)
        if year is None:
            continue
        records.append(
            {
                "year": year,
                "month": month,
                "handle": nums[2],
                "gross_revenue": nums[5],
                "tax": nums[6],
            }
        )

    if records:
        dedup = {(r["year"], r["month"]): r for r in records}
        return list(dedup.values())

    # Table fallback
    with pdfplumber.open(BytesIO(content)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for row in table:
                    if not row or not row[0]:
                        continue
                    label = str(row[0]).strip()
                    if label.lower() in {"month", "total"}:
                        continue
                    month_name = normalize_month_label(label)
                    if month_name is None:
                        continue
                    month = month_name_to_num(month_name)
                    # Re-join split trailing cells before parsing.
                    joined = " ".join("" if c is None else str(c) for c in row[1:])
                    nums = _money_tokens(joined)
                    if len(nums) < 7:
                        continue
                    year = year_for_month(month)
                    if year is None:
                        continue
                    records.append(
                        {
                            "year": year,
                            "month": month,
                            "handle": nums[2],
                            "gross_revenue": nums[5],
                            "tax": nums[6],
                        }
                    )

    if not records:
        raise ValueError("Could not parse NC sports betting revenue PDF")
    dedup = {(r["year"], r["month"]): r for r in records}
    return list(dedup.values())


def build_normalized_row(
    record: dict,
    *,
    source_url: str,
    source_file: str,
    source_sha256: str,
    retrieved_at: datetime,
) -> pd.DataFrame:
    period_start, period_end = month_period(int(record["year"]), int(record["month"]))
    return pd.DataFrame(
        [
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
                "gross_revenue": record.get("gross_revenue"),
                "adjusted_revenue": None,
                "taxable_revenue": None,
                "net_proceeds": None,
                "tax": record.get("tax"),
                "reported_revenue_name": REPORTED_REVENUE_NAME,
                "source_url": source_url,
                "source_file": source_file,
                "source_sha256": source_sha256,
                "retrieved_at_utc": retrieved_at.isoformat(),
                "report_status": "ok",
            }
        ]
    )


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
    by_period: dict[tuple[int, int], pd.DataFrame] = {}
    downloaded = 0

    try:
        html = http_get(LANDING_URL, session=sess, headers=BROWSER_HEADERS).text
        links = discover_report_links(html)
        if not links:
            raise RuntimeError("No NC sports revenue PDF links found")
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

    for link in links:
        try:
            content = http_get(link["url"], session=sess, headers=BROWSER_HEADERS).content
            path = save_raw_bytes(root, STATE_CODE, content, link["filename"], retrieved_at=retrieved_at)
            records = parse_revenue_pdf(
                content,
                month_hint=link.get("month_hint"),
                year_hint=link.get("year_hint"),
            )
            for record in records:
                frame = build_normalized_row(
                    record,
                    source_url=link["url"],
                    source_file=str(path.relative_to(root)).replace("\\", "/"),
                    source_sha256=sha256_bytes(content),
                    retrieved_at=retrieved_at,
                )
                # Latest file wins for a calendar month (YTD PDFs repeat earlier months).
                key = (record["year"], record["month"])
                by_period[key] = frame
                print(f"OK NC {record['year']}-{record['month']:02d}")
            downloaded += 1
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{link['filename']}: {exc}")
            print(f"SKIP {link['filename']}: {exc}")

    frames = list(by_period.values())
    if frames:
        # Upsert only. Older official revisions (different source hashes) remain;
        # identical hashes stay idempotent via the primary key.
        result = pd.concat(frames, ignore_index=True)
        upsert_gaming_results(connection, result)
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": VERTICAL,
                "status": "ok" if not failures else "partial",
                "reason": (
                    "; ".join(failures[:5])
                    if failures
                    else "NCSLC monthly statewide PDF: Total Wagering + Gross Wagering Revenue + Estimated Tax"
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
            "reason": "; ".join(failures) or "No NC PDFs collected",
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
