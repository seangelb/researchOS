"""New Hampshire Lottery sports-betting summary PDF collectors (mobile channel)."""

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

LANDING_URL = "https://nhlottery.com/About-Us/Financial-Reports"
STATE_CODE = "NH"
JURISDICTION = "New Hampshire"
VERTICAL = "online_sports_betting"
REPORTED_REVENUE_NAME = "GGR"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

MONTH_MAP = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def discover_summary_links(html: str, base_url: str = LANDING_URL) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        text = " ".join(anchor.get_text(" ", strip=True).split())
        absolute = urljoin(base_url, href)
        low = absolute.lower()
        if "sports_betting_summary" not in low and "sports-betting-summary" not in low:
            continue
        if not low.endswith(".pdf"):
            continue
        filename = unquote(absolute.rsplit("/", 1)[-1].split("?", 1)[0])
        fy = None
        m = re.search(r"fy\s*(\d{2,4})", filename + " " + text, re.I)
        if m:
            raw = m.group(1)
            fy = int(raw) if len(raw) == 4 else 2000 + int(raw)
        found[absolute] = {"url": absolute, "filename": filename, "link_text": text, "fy": fy}
    return sorted(found.values(), key=lambda item: item["fy"] or 0)


def _extract_mobile_amounts(segment: str) -> list[float]:
    """Extract first three money values from the Mobile column group."""
    text = segment.replace("$", " ")
    # Repair PDF digit splits: "4 4,191,929" -> "44,191,929", "6 4,609,513" -> "64,609,513"
    text = re.sub(r"\b(\d)\s+(\d{1,3}(?:,\d{3})+)\b", r"\1\2", text)
    text = re.sub(r"\b(\d)\s+(\d{4,})\b", r"\1\2", text)
    values: list[float] = []
    for token in re.findall(r"\d[\d,]*\.?\d*", text):
        value = parse_money(token)
        if value is not None:
            values.append(value)
        if len(values) >= 3:
            break
    return values


def parse_summary_pdf(content: bytes) -> pd.DataFrame:
    with pdfplumber.open(BytesIO(content)) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)

    fy_match = re.search(r"FY\s*(20\d{2})", text, re.I)
    if not fy_match:
        raise ValueError("NH sports summary missing FY label")
    fy_end = int(fy_match.group(1))

    records: list[dict] = []
    month_re = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-(\d{2})\b", re.I)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        month_match = month_re.match(line)
        if not month_match:
            continue
        mon = MONTH_MAP[month_match.group(1).lower()]
        year = 2000 + int(month_match.group(2))
        rest = line[month_match.end() :]
        second = re.search(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{2}\b",
            rest,
            re.I,
        )
        mobile_segment = rest[: second.start()] if second else rest
        values = _extract_mobile_amounts(mobile_segment)
        if len(values) < 3:
            continue
        handle, ggr, tax = values[0], values[1], values[2]
        if handle == 0 and ggr == 0:
            continue
        records.append(
            {
                "year": year,
                "month": mon,
                "handle": handle,
                "gross_revenue": ggr,
                "tax": tax,
                "fy_end": fy_end,
            }
        )

    dedup: dict[tuple[int, int], dict] = {}
    for record in records:
        key = (record["year"], record["month"])
        dedup.setdefault(key, record)
    if not dedup:
        raise ValueError("No NH mobile sports monthly rows parsed")
    return pd.DataFrame(dedup.values()).sort_values(["year", "month"]).reset_index(drop=True)


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
    by_period: dict[tuple[int, int], pd.DataFrame] = {}
    downloaded = 0

    try:
        html = http_get(LANDING_URL, session=sess, headers=BROWSER_HEADERS).text
        links = discover_summary_links(html)
        if not links:
            raise RuntimeError("No NH Sports Betting Summary PDF links found")
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
            parsed = parse_summary_pdf(content)
            normalized = build_normalized_rows(
                parsed,
                source_url=link["url"],
                source_file=str(path.relative_to(root)).replace("\\", "/"),
                source_sha256=sha256_bytes(content),
                retrieved_at=retrieved_at,
            )
            for _, row in normalized.iterrows():
                key = (int(str(row["period_start"])[:4]), int(str(row["period_start"])[5:7]))
                by_period[key] = pd.DataFrame([row])
            downloaded += 1
            print(f"OK NH FY{link.get('fy')}: {len(normalized)} rows")
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
                    else "NH Sports Betting Summary PDF mobile Handle/GGR/State Rev Share"
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
            "reason": "; ".join(failures) or "No NH summaries collected",
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
