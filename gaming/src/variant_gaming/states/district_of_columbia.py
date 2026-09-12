"""District of Columbia OLG monthly sports-wagering financials."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

from variant_gaming.common import (
    http_block_reason,
    http_get,
    http_get_unchecked,
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

LANDING_URL = "https://dclottery.com/olg/financials"
STATE_CODE = "DC"
JURISDICTION = "District of Columbia"
VERTICAL = "online_sports_betting"
REPORTED_REVENUE_NAME = "Gross Gaming Revenue (GGR)"

MONTH_PAGE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(20\d{2})",
    re.IGNORECASE,
)
MONTHLY_TOTALS_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(20\d{2})\s+Totals$",
    re.IGNORECASE,
)
ONLINE_LOCATION_RE = re.compile(
    r"mobile|online|internet|app",
    re.IGNORECASE,
)


def _cell_text(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def is_online_location(location: str, license_class: str | None = None) -> bool:
    """Class C and any location labeled mobile/online/app/internet."""
    if ONLINE_LOCATION_RE.search(location or ""):
        return True
    if (license_class or "").strip().casefold() in {"class c", "c"}:
        return True
    return False


def discover_month_links(html: str, base_url: str = LANDING_URL) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        text = _cell_text(anchor.get_text(" ", strip=True))
        absolute = urljoin(base_url, href)
        if "/olg/financials/" not in absolute.casefold():
            continue
        if "page=" in absolute:
            continue
        match = MONTH_PAGE_RE.search(text) or MONTH_PAGE_RE.search(absolute)
        if not match:
            continue
        month = month_name_to_num(match.group(1).title())
        year = int(match.group(2))
        slug = absolute.rstrip("/").rsplit("/", 1)[-1]
        found.setdefault(
            absolute,
            {
                "url": absolute,
                "filename": f"{slug}.html",
                "link_text": text,
                "year": year,
                "month": month,
                "unaudited": "unaudited" in text.casefold() or "unaudited" in absolute.casefold(),
            },
        )
    return sorted(found.values(), key=lambda item: (item["year"], item["month"], item["filename"]))


def discover_all_month_links(session: requests.Session | None = None) -> list[dict]:
    sess = session or requests.Session()
    found: dict[str, dict] = {}
    for page_n in range(0, 60):
        url = LANDING_URL if page_n == 0 else f"{LANDING_URL}?page={page_n}"
        response = http_get_unchecked(url, session=sess)
        blocked = http_block_reason(response)
        if blocked:
            raise RuntimeError(blocked)
        response.raise_for_status()
        page_links = discover_month_links(response.text, url)
        new = 0
        for item in page_links:
            if item["url"] not in found:
                found[item["url"]] = item
                new += 1
        if page_n > 0 and new == 0:
            break
    return sorted(found.values(), key=lambda item: (item["year"], item["month"], item["filename"]))


def parse_online_operator_table(html: str) -> pd.DataFrame:
    """
    Keep the current-month Totals row for operators whose location is online/mobile
    (Class C). Exclude Class A/B physical venues — those reports do not split retail vs online.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return pd.DataFrame()

    records: list[dict] = []
    license_class = None
    operator = None
    location = None

    for tr in table.find_all("tr"):
        cells = [_cell_text(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if not any(cells):
            continue
        first = cells[0]
        if first.casefold() in {"class a", "class b", "class c"}:
            license_class = first
            operator = None
            location = None
            continue
        if first.casefold() == "name of operator":
            continue
        totals = MONTHLY_TOTALS_RE.match(first)
        if totals:
            if operator is None:
                continue
            if not is_online_location(location or "", license_class):
                continue
            year = int(totals.group(2))
            month = month_name_to_num(totals.group(1).title())
            period_start, period_end = month_period(year, month)
            handle = parse_money(cells[3] if len(cells) > 3 else None)
            ggr = parse_money(cells[5] if len(cells) > 5 else None)
            tax = parse_money(cells[6] if len(cells) > 6 else None)
            if handle is None and ggr is None:
                continue
            records.append(
                {
                    "operator": operator,
                    "row_type": "operator",
                    "period_start": period_start,
                    "period_end": period_end,
                    "handle": handle,
                    "gross_revenue": ggr,
                    "tax": tax,
                    "license_class": license_class,
                    "location": location,
                }
            )
            continue
        if "fiscal year" in first.casefold() or "calendar year" in first.casefold():
            continue
        # Operator identity row: name + location, empty metrics.
        if len(cells) >= 2 and first and not MONTHLY_TOTALS_RE.match(first):
            operator = first
            location = cells[1] if len(cells) > 1 else ""

    if not records:
        return pd.DataFrame()
    return pd.DataFrame.from_records(records)


def build_normalized_rows(
    parsed: pd.DataFrame,
    *,
    source_url: str,
    source_file: str,
    source_sha256: str,
    retrieved_at: datetime,
    unaudited: bool = False,
) -> pd.DataFrame:
    if parsed is None or parsed.empty:
        return pd.DataFrame()
    rows = parsed.copy()
    status = "unaudited" if unaudited else "ok"
    rows = rows.assign(
        jurisdiction=JURISDICTION,
        state_code=STATE_CODE,
        vertical=VERTICAL,
        channel="online",
        frequency="monthly",
        adjusted_revenue=pd.NA,
        taxable_revenue=pd.NA,
        net_proceeds=pd.NA,
        reported_revenue_name=REPORTED_REVENUE_NAME,
        source_url=source_url,
        source_file=source_file,
        source_sha256=source_sha256,
        retrieved_at_utc=retrieved_at.isoformat(),
        report_status=status,
    )
    return rows.drop(columns=[c for c in ("license_class", "location") if c in rows.columns])


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

    landing = http_get_unchecked(LANDING_URL, session=sess)
    blocked = http_block_reason(landing)
    if blocked:
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": VERTICAL,
                "status": "blocked",
                "reason": blocked,
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
        print(f"BLOCKED DC: {blocked}")
        return pd.DataFrame()
    landing.raise_for_status()
    save_raw_bytes(root, STATE_CODE, landing.content, "dc_financials_landing.html", retrieved_at=retrieved_at)

    try:
        links = discover_all_month_links(session=sess)
    except RuntimeError as exc:
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": VERTICAL,
                "status": "blocked",
                "reason": str(exc),
                "official_url": LANDING_URL,
                "available_frequency": "monthly",
                "earliest_period": None,
                "latest_period": None,
                "downloaded_file_count": 1,
                "normalized_row_count": 0,
                "last_retrieval_utc": utc_now().isoformat(),
            },
        )
        connection.close()
        print(f"BLOCKED DC: {exc}")
        return pd.DataFrame()

    all_frames: list[pd.DataFrame] = []
    failures: list[str] = []
    downloaded = 1
    skipped_no_online = 0

    for link in links:
        try:
            response = http_get(link["url"], session=sess)
            path = save_raw_bytes(
                root, STATE_CODE, response.content, link["filename"], retrieved_at=retrieved_at
            )
            downloaded += 1
            parsed = parse_online_operator_table(response.text)
            if parsed.empty:
                skipped_no_online += 1
                print(f"SKIP DC {link['link_text']}: no Class C / mobile rows")
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            frame = build_normalized_rows(
                parsed,
                source_url=link["url"],
                source_file=rel,
                source_sha256=sha256_bytes(response.content),
                retrieved_at=retrieved_at,
                unaudited=bool(link.get("unaudited")),
            )
            upsert_gaming_results(connection, frame)
            all_frames.append(frame)
            print(f"OK DC {frame['period_start'].iloc[0]}: {len(frame)} online rows")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{link['filename']}: {exc}")
            print(f"SKIP DC {link['filename']}: {exc}")

    result = (
        pd.concat(all_frames, ignore_index=True).sort_values(["period_start", "operator"]).reset_index(drop=True)
        if all_frames
        else pd.DataFrame()
    )
    reason_parts = []
    if failures:
        reason_parts.append("; ".join(failures[:5]))
    reason_parts.append(
        "Class C / mobile-app locations only; Class A/B physical venues excluded (no online split)"
    )
    if skipped_no_online:
        reason_parts.append(f"{skipped_no_online} months had no online location rows")
    upsert_coverage(
        connection,
        {
            "state_code": STATE_CODE,
            "vertical": VERTICAL,
            "status": "ok" if all_frames and not failures else ("failed" if not all_frames else "partial"),
            "reason": "; ".join(reason_parts),
            "official_url": LANDING_URL,
            "available_frequency": "monthly",
            "earliest_period": None if result.empty else result["period_start"].min(),
            "latest_period": None if result.empty else result["period_end"].max(),
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
