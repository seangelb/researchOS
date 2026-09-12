"""Iowa Racing and Gaming Commission sports-wagering collectors (internet only)."""

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

LANDING_URL = "https://irgc.iowa.gov/publications-reports/sports-wagering-revenue"
ARCHIVE_URL = "https://irgc.iowa.gov/publications-reports/sports-wagering-revenue/archived-sports-revenue"
STATE_CODE = "IA"
JURISDICTION = "Iowa"
VERTICAL = "online_sports_betting"
REPORTED_REVENUE_NAME = "INTERNET NET RECEIPTS"

MONEY_TOKEN_RE = re.compile(r"^\(?\$?-?[\d,]+(?:\.\d{2})?\)?$")
PERIOD_RE = re.compile(
    r"SPORTS WAGERING REVENUE REPORT\s*[-–]+\s*"
    r"(?:FYTD\s+\d{4}|(?:FY|FISCAL YEAR)\s+\d{4}|"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2}))",
    re.IGNORECASE,
)
OPERATOR_PAGE_RE = re.compile(r"BY OPERATOR", re.IGNORECASE)
AMEND_PAGE_RE = re.compile(r"Amendments to published", re.IGNORECASE)
FYTD_OR_ANNUAL_RE = re.compile(r"REPORT\s*[-–]+\s*(FYTD|FY|FISCAL YEAR)\s+\d{4}", re.IGNORECASE)
INTERNET_NET = "internet net receipts"
INTERNET_HANDLE = "internet handle"


def _cell_text(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def discover_current_month_links(html: str, base_url: str = LANDING_URL) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        text = _cell_text(anchor.get_text(" ", strip=True))
        href = anchor["href"].strip()
        absolute = urljoin(base_url, href)
        blob = f"{text} {absolute}".casefold()
        if "sports wagering revenue" not in blob:
            continue
        if "archiv" in blob:
            continue
        if "/media/" not in absolute.casefold() and "download" not in absolute.casefold():
            continue
        year = month = None
        match = re.search(
            r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})",
            text,
            re.IGNORECASE,
        )
        if match:
            month = month_name_to_num(match.group(1).title())
            year = int(match.group(2))
        filename = unquote(absolute.rsplit("/", 1)[-1].split("?", 1)[0])
        if year and month:
            filename = f"{MONTH_NAMES[month - 1]}{year}.pdf"
        found.setdefault(
            absolute,
            {
                "url": absolute,
                "filename": filename or "iowa_sports.pdf",
                "year": year,
                "month": month,
                "kind": "monthly",
                "link_text": text,
            },
        )
    return sorted(found.values(), key=lambda item: (item["year"] or 0, item["month"] or 0))


def discover_archive_fy_links(html: str, base_url: str = ARCHIVE_URL) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        text = _cell_text(anchor.get_text(" ", strip=True))
        href = anchor["href"].strip()
        absolute = urljoin(base_url, href)
        if "fiscal year" not in text.casefold() or "sports" not in text.casefold():
            continue
        year_match = re.search(r"(20\d{2})", text)
        year = int(year_match.group(1)) if year_match else None
        filename = f"IA_FY{year}_sports_wagering.pdf" if year else "iowa_fy_sports.pdf"
        found.setdefault(
            absolute,
            {
                "url": absolute,
                "filename": filename,
                "year": year,
                "month": None,
                "kind": "fy_archive",
                "link_text": text,
            },
        )
    return sorted(found.values(), key=lambda item: (item["year"] or 0, item["filename"]))


def _cluster_rows(words: list[dict], tol: float = 3.5) -> list[dict]:
    rows: list[dict] = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if rows and abs(word["top"] - rows[-1]["top"]) <= tol:
            rows[-1]["words"].append(word)
        else:
            rows.append({"top": word["top"], "words": [word]})
    return rows


def _row_text(row: dict) -> str:
    return " ".join(w["text"] for w in row["words"])


def _money_words(row: dict) -> list[dict]:
    return [w for w in row["words"] if MONEY_TOKEN_RE.match(w["text"])]


def _assign_names(header_rows: list[dict], money_xs: list[float]) -> list[str]:
    if not money_xs:
        return []
    bounds: list[tuple[float, float]] = []
    for i, x in enumerate(money_xs):
        left = (money_xs[i - 1] + x) / 2 if i else x - 80
        right = (x + money_xs[i + 1]) / 2 if i + 1 < len(money_xs) else x + 80
        bounds.append((left, right))
    names: list[str] = []
    for left, right in bounds:
        parts: list[str] = []
        for row in header_rows:
            for word in row["words"]:
                if MONEY_TOKEN_RE.match(word["text"]):
                    continue
                mid = (word["x0"] + word["x1"]) / 2
                if left <= mid < right:
                    parts.append(word["text"])
        name = _cell_text(" ".join(parts))
        name = re.sub(r"^[0-9]+", "", name).strip()
        names.append(name or "Unknown")
    return names


def parse_ia_casino_page(page) -> pd.DataFrame:
    """Parse INTERNET rows for casino licensees + Totals from a monthly revenue page."""
    words = page.extract_words() or []
    rows = _cluster_rows(words)
    blocks: list[dict] = []
    current_headers: list[dict] = []
    current_metrics: list[dict] = []

    def flush():
        nonlocal current_headers, current_metrics
        if current_metrics:
            blocks.append({"headers": current_headers, "metrics": current_metrics})
        current_headers = []
        current_metrics = []

    for row in rows:
        text = _row_text(row)
        norm = text.casefold()
        if OPERATOR_PAGE_RE.search(text) or AMEND_PAGE_RE.search(text):
            continue
        if INTERNET_NET in norm or INTERNET_HANDLE in norm or norm.startswith("state tax"):
            current_metrics.append(row)
            continue
        if "sports wagering" in norm and ("net receipts" in norm or "handle" in norm or "payouts" in norm):
            current_metrics.append(row)
            continue
        if "retail net" in norm or "retail handle" in norm or "retail payouts" in norm:
            current_metrics.append(row)
            continue
        if _money_words(row):
            continue
        if current_metrics:
            flush()
        if text.strip() and not text.casefold().startswith("sports wagering revenue report"):
            current_headers.append(row)
    flush()

    records: list[dict] = []
    for block in blocks:
        metric_map: dict[str, list[tuple[float, float | None]]] = {}
        money_xs: list[float] = []
        for row in block["metrics"]:
            moneys = _money_words(row)
            if not moneys:
                continue
            label = _cell_text(
                " ".join(w["text"] for w in row["words"] if not MONEY_TOKEN_RE.match(w["text"]))
            ).casefold()
            values = [(w["x0"], parse_money(w["text"])) for w in moneys]
            metric_map[label] = values
            if INTERNET_NET in label or not money_xs:
                money_xs = [x for x, _ in values]
        if not money_xs:
            continue
        names = _assign_names(block["headers"], money_xs)
        net_vals = dict(metric_map.get(INTERNET_NET, []))
        handle_vals = dict(metric_map.get(INTERNET_HANDLE, []))
        # Keys are x0 floats; align by index instead.
        internet_net = [v for _, v in metric_map.get(INTERNET_NET, [])]
        internet_handle = [v for _, v in metric_map.get(INTERNET_HANDLE, [])]
        if not internet_net and not internet_handle:
            continue
        n = max(len(internet_net), len(internet_handle), len(names))
        for i in range(n):
            name = names[i] if i < len(names) else f"Column {i + 1}"
            net = internet_net[i] if i < len(internet_net) else None
            handle = internet_handle[i] if i < len(internet_handle) else None
            if net is None and handle is None:
                continue
            is_total = name.casefold().rstrip("s") in {"total"} or name.casefold() == "totals"
            records.append(
                {
                    "operator": "STATEWIDE" if is_total else name,
                    "row_type": "official_statewide_total" if is_total else "operator",
                    "handle": handle,
                    "net_proceeds": net,
                }
            )
    return pd.DataFrame.from_records(records)


def page_period(text: str) -> tuple[int, int] | None:
    if OPERATOR_PAGE_RE.search(text) or AMEND_PAGE_RE.search(text):
        return None
    if FYTD_OR_ANNUAL_RE.search(text) and not re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+20\d{2}",
        text,
        re.IGNORECASE,
    ):
        return None
    match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return int(match.group(2)), month_name_to_num(match.group(1).title())


def parse_ia_pdf(content: bytes | Path) -> pd.DataFrame:
    """Parse monthly casino INTERNET rows; skip FYTD, amendments, and by-operator pages."""
    source = content if isinstance(content, Path) else BytesIO(content)
    frames: list[pd.DataFrame] = []
    with pdfplumber.open(source) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            period = page_period(text)
            if period is None:
                continue
            if OPERATOR_PAGE_RE.search(text):
                continue
            parsed = parse_ia_casino_page(page)
            if parsed.empty:
                continue
            year, month = period
            period_start, period_end = month_period(year, month)
            parsed = parsed.assign(period_start=period_start, period_end=period_end, year=year, month=month)
            frames.append(parsed)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def build_normalized_rows(
    parsed: pd.DataFrame,
    *,
    source_url: str,
    source_file: str,
    source_sha256: str,
    retrieved_at: datetime,
) -> pd.DataFrame:
    if parsed is None or parsed.empty:
        return pd.DataFrame()
    rows = parsed.copy()
    rows = rows.assign(
        jurisdiction=JURISDICTION,
        state_code=STATE_CODE,
        vertical=VERTICAL,
        channel="online",
        frequency="monthly",
        gross_revenue=pd.NA,
        adjusted_revenue=pd.NA,
        taxable_revenue=pd.NA,
        tax=pd.NA,
        reported_revenue_name=REPORTED_REVENUE_NAME,
        source_url=source_url,
        source_file=source_file,
        source_sha256=source_sha256,
        retrieved_at_utc=retrieved_at.isoformat(),
        report_status="ok",
    )
    return rows.drop(columns=[c for c in ("year", "month") if c in rows.columns])


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
        print(f"BLOCKED IA: {blocked}")
        return pd.DataFrame()
    landing.raise_for_status()
    save_raw_bytes(root, STATE_CODE, landing.content, "ia_sports_landing.html", retrieved_at=retrieved_at)

    links = discover_current_month_links(landing.text, LANDING_URL)
    archive = http_get(ARCHIVE_URL, session=sess)
    save_raw_bytes(root, STATE_CODE, archive.content, "ia_sports_archive.html", retrieved_at=retrieved_at)
    links.extend(discover_archive_fy_links(archive.text, ARCHIVE_URL))

    all_frames: list[pd.DataFrame] = []
    failures: list[str] = []
    downloaded = 2

    for link in links:
        try:
            response = http_get(link["url"], session=sess)
            content = response.content
            if not content.startswith(b"%PDF"):
                raise RuntimeError("Not a PDF payload")
            filename = link["filename"]
            cd = response.headers.get("content-disposition", "")
            cd_match = re.search(r'filename="?([^";]+)"?', cd)
            if cd_match:
                filename = unquote(cd_match.group(1))
            path = save_raw_bytes(root, STATE_CODE, content, filename, retrieved_at=retrieved_at)
            downloaded += 1
            parsed = parse_ia_pdf(content)
            if parsed.empty:
                failures.append(f"{filename}: no internet rows")
                print(f"SKIP IA {filename}: no internet rows")
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            frame = build_normalized_rows(
                parsed,
                source_url=response.url or link["url"],
                source_file=rel,
                source_sha256=sha256_bytes(content),
                retrieved_at=retrieved_at,
            )
            upsert_gaming_results(connection, frame)
            all_frames.append(frame)
            print(f"OK IA {filename}: {len(frame)} internet rows")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{link['url']}: {exc}")
            print(f"SKIP IA {link['filename']}: {exc}")

    result = (
        pd.concat(all_frames, ignore_index=True).sort_values(["period_start", "operator"]).reset_index(drop=True)
        if all_frames
        else pd.DataFrame()
    )
    upsert_coverage(
        connection,
        {
            "state_code": STATE_CODE,
            "vertical": VERTICAL,
            "status": "ok" if all_frames and not failures else ("failed" if not all_frames else "partial"),
            "reason": (
                "; ".join(failures[:5])
                if failures
                else "Collected IRGC INTERNET NET RECEIPTS by casino licensee (retail excluded)"
            ),
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
