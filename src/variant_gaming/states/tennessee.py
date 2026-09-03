"""Tennessee SWAC online sports-wagering collectors (statewide handle + privilege tax)."""

from __future__ import annotations

import csv
import re
import time
from datetime import datetime
from io import StringIO
from pathlib import Path
from urllib.parse import unquote, urljoin

import pandas as pd
import requests

from variant_gaming.common import (
    MONTH_NAMES,
    http_get,
    month_name_to_num,
    month_period,
    parse_money,
    project_root,
    save_raw_bytes,
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

LANDING_URL = "https://www.tn.gov/swac/reports"
VERTICAL = "online_sports_betting"
STATE_CODE = "TN"
JURISDICTION = "Tennessee"

# tn.gov sometimes resets TLS handshakes for non-browser User-Agents (Vary: User-Agent).
TN_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Official statewide CSVs publish handle + privilege tax only (no GGR/AGR/win column).
REPORTED_REVENUE_NAME = "none (handle + privilege tax only)"
REPORT_STATUS_LIMITED = "limited_metrics_handle_and_tax_only"

TITLE_PERIOD_RE = re.compile(
    r"MONTHLY\s+SPORTS\s+GAMING\s+REPORT:\s*([A-Za-z]+)\s+(\d{4})",
    re.IGNORECASE,
)
RANGE_PERIOD_RE = re.compile(
    r"([A-Za-z]+)\s+\d{1,2}\s*[-–]\s*[A-Za-z]+\s+\d{1,2}",
    re.IGNORECASE,
)
MONTH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z])(" + "|".join(MONTH_NAMES) + r")(?![A-Za-z])",
    re.IGNORECASE,
)
YEAR_IN_PATH_RE = re.compile(r"/report/(20\d{2})/", re.IGNORECASE)
CSV_HREF_RE = re.compile(r'href=["\']([^"\']+\.csv[^"\']*)["\']', re.IGNORECASE)


def _normalize_label(text: str) -> str:
    """Strip bullets / punctuation so '• Gross Handle' matches cleanly."""
    cleaned = re.sub(r"[^\w\s]", " ", str(text))
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def discover_csv_links(html: str, base_url: str = LANDING_URL) -> list[dict]:
    """
    Discover monthly CSV links from the SWAC reports page (including year sections).

    Returns list of dicts: {url, filename, year_hint, month_hint, unaudited}.
    """
    found: dict[str, dict] = {}
    for match in CSV_HREF_RE.finditer(html):
        href = match.group(1).strip()
        url = urljoin(base_url, href)
        filename = unquote(url.rsplit("/", 1)[-1])
        year_hint = None
        year_match = YEAR_IN_PATH_RE.search(url)
        if year_match:
            year_hint = int(year_match.group(1))
        month_hint = None
        month_match = MONTH_TOKEN_RE.search(filename)
        if month_match:
            month_hint = month_name_to_num(month_match.group(1).title())
        unaudited = "(ua)" in filename.lower() or "_ua" in filename.lower()
        # Prefer first occurrence; page order is newest-first within years.
        found.setdefault(
            url,
            {
                "url": url,
                "filename": filename,
                "year_hint": year_hint,
                "month_hint": month_hint,
                "unaudited": unaudited,
            },
        )
    # Stable sort: year then month when known, else filename.
    return sorted(
        found.values(),
        key=lambda item: (
            item["year_hint"] or 0,
            item["month_hint"] or 0,
            item["filename"].lower(),
        ),
    )


def _tn_http_get(url: str, session: requests.Session | None = None) -> requests.Response:
    """GET with TN-friendly headers and short retries for intermittent TLS resets."""
    last_exc: Exception | None = None
    for attempt in range(5):
        try:
            return http_get(url, session=session, headers=TN_HTTP_HEADERS)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            time.sleep(1.25 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def fetch_reports_html(session: requests.Session | None = None) -> str:
    response = _tn_http_get(LANDING_URL, session=session)
    return response.text


def decode_csv_bytes(content: bytes) -> str:
    """SWAC CSVs are usually UTF-8; older exports may use Windows-1252 / NBSP bytes."""
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8-sig", errors="replace")


def download_csv_bytes(url: str, session: requests.Session | None = None) -> bytes:
    response = _tn_http_get(url, session=session)
    return response.content


def parse_period_from_csv_text(csv_text: str) -> tuple[int | None, int | None]:
    """Return (year, month) from the report title or date-range line when present."""
    for line in csv_text.splitlines()[:12]:
        title = TITLE_PERIOD_RE.search(line)
        if title:
            return int(title.group(2)), month_name_to_num(title.group(1).title())
        ranged = RANGE_PERIOD_RE.search(line)
        if ranged:
            # Year usually only in the title; month from range start.
            return None, month_name_to_num(ranged.group(1).title())
    return None, None


def resolve_period(
    csv_text: str,
    *,
    year_hint: int | None = None,
    month_hint: int | None = None,
) -> tuple[int, int]:
    year_from_text, month_from_text = parse_period_from_csv_text(csv_text)
    year = year_from_text or year_hint
    month = month_from_text or month_hint
    if year is None or month is None:
        raise ValueError(
            f"Could not resolve report period (year={year}, month={month}, "
            f"hints year={year_hint} month={month_hint})"
        )
    return year, month


def parse_statewide_monthly_csv(csv_text: str) -> dict:
    """
    Parse a SWAC statewide monthly CSV.

    Official columns (label in first cell, amount in second):
      - Gross Wagers
      - Adjustments
      - Gross Handle  -> handle
      - Privilege Tax Assessed -> tax

    There is no published GGR/AGR/win field in these CSVs (or matching PDF labels).
    """
    reader = csv.reader(StringIO(csv_text))
    metrics: dict[str, float | None] = {
        "gross_wagers": None,
        "adjustments": None,
        "handle": None,
        "tax": None,
    }

    for row in reader:
        if not row:
            continue
        cells = [c.strip() for c in row if str(c).strip() != ""]
        if not cells:
            continue
        label = _normalize_label(cells[0])
        if not label:
            continue
        amount = parse_money(cells[1]) if len(cells) > 1 else None

        if "gross wagers" in label:
            metrics["gross_wagers"] = amount
        elif label == "adjustments" or label.endswith(" adjustments"):
            metrics["adjustments"] = amount
        elif "gross handle" in label:
            metrics["handle"] = amount
        elif "privilege tax" in label:
            metrics["tax"] = amount

    if metrics["handle"] is None and metrics["tax"] is None:
        raise ValueError("Statewide CSV missing Gross Handle and Privilege Tax Assessed")

    return metrics


def build_normalized_row(
    metrics: dict,
    *,
    year: int,
    month: int,
    source_file: str,
    source_sha256: str,
    retrieved_at: datetime,
    unaudited: bool = False,
) -> dict:
    period_start, period_end = month_period(year, month)
    status = REPORT_STATUS_LIMITED
    if unaudited:
        status = f"{status}; unaudited"
    return {
        "jurisdiction": JURISDICTION,
        "state_code": STATE_CODE,
        "vertical": VERTICAL,
        "channel": "online",
        "operator": "STATEWIDE",
        "row_type": "official_statewide_total",
        "period_start": period_start,
        "period_end": period_end,
        "frequency": "monthly",
        "handle": metrics.get("handle"),
        "gross_revenue": None,
        "adjusted_revenue": None,
        "taxable_revenue": None,
        "net_proceeds": None,
        "tax": metrics.get("tax"),
        "reported_revenue_name": REPORTED_REVENUE_NAME,
        "source_url": LANDING_URL,
        "source_file": source_file,
        "source_sha256": source_sha256,
        "retrieved_at_utc": retrieved_at.isoformat(),
        "report_status": status,
    }


def collect_csv_link(
    link: dict,
    *,
    root: Path | None = None,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
) -> pd.DataFrame:
    """Download one CSV, save raw bytes, return a one-row normalized DataFrame."""
    root = root or project_root()
    retrieved_at = retrieved_at or utc_now()
    content = download_csv_bytes(link["url"], session=session)
    path = save_raw_bytes(
        root,
        STATE_CODE,
        content,
        link["filename"],
        retrieved_at=retrieved_at,
    )
    text = decode_csv_bytes(content)
    metrics = parse_statewide_monthly_csv(text)
    year, month = resolve_period(
        text,
        year_hint=link.get("year_hint"),
        month_hint=link.get("month_hint"),
    )
    rel = str(path.relative_to(root)).replace("\\", "/")
    sha = path.name.split("_", 1)[0]
    row = build_normalized_row(
        metrics,
        year=year,
        month=month,
        source_file=rel,
        source_sha256=sha,
        retrieved_at=retrieved_at,
        unaudited=bool(link.get("unaudited")),
    )
    return pd.DataFrame([row])


def collect_history(
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """
    Discover all monthly CSVs on the SWAC reports page, download/parse each,
    upsert into SQLite, and refresh source_coverage for TN online sports betting.
    """
    root = root or project_root()
    retrieved_at = utc_now()
    sess = session or requests.Session()

    html = fetch_reports_html(session=sess)
    links = discover_csv_links(html)
    if not links:
        raise RuntimeError(f"No CSV links found on {LANDING_URL}")

    db_path = db_path or default_db_path(root)
    connection = connect(db_path)
    migrate_legacy_table(connection)
    ensure_schema(connection)

    all_frames: list[pd.DataFrame] = []
    failures: list[str] = []
    downloaded = 0

    for link in links:
        label = link["filename"]
        try:
            frame = collect_csv_link(
                link,
                root=root,
                session=sess,
                retrieved_at=retrieved_at,
            )
            upsert_gaming_results(connection, frame)
            all_frames.append(frame)
            downloaded += 1
            period = frame["period_start"].iloc[0]
            print(f"OK {period}: {label}")
        except Exception as exc:  # noqa: BLE001 - continue other files
            failures.append(f"{label}: {exc}")
            print(f"SKIP {label}: {exc}")

    if all_frames:
        result = pd.concat(all_frames, ignore_index=True)
        # Prefer earliest/latest by period, not discovery order.
        result = result.sort_values(["period_start", "period_end"]).reset_index(drop=True)
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": VERTICAL,
                "status": "ok" if not failures else "partial",
                "reason": (
                    "; ".join(failures[:5])
                    if failures
                    else (
                        "Collected SWAC statewide monthly CSVs "
                        "(Gross Handle + Privilege Tax; no GGR/AGR published)"
                    )
                ),
                "official_url": LANDING_URL,
                "available_frequency": "monthly",
                "earliest_period": result["period_start"].min(),
                "latest_period": result["period_end"].max(),
                "downloaded_file_count": downloaded,
                "normalized_row_count": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM gaming_results "
                        "WHERE state_code=? AND vertical=?",
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
            "reason": "; ".join(failures) or "No CSVs collected",
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
