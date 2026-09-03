"""Pennsylvania PGCB collectors for online sports wagering and interactive gaming."""

from __future__ import annotations

import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin

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

LANDING_URL = "https://gamingcontrolboard.pa.gov/news-and-transparency/revenue"
STATE_CODE = "PA"
JURISDICTION = "Pennsylvania"

SPORTS_VERTICAL = "online_sports_betting"
CASINO_VERTICAL = "online_casino"

SPORTS_REPORTED_REVENUE = "Gross Revenue (Taxable)"
CASINO_REPORTED_REVENUE = "Gross Revenue"

FY_FILTER_PARAM = "field_gaming_revenue_fiscal_year_target_id"

# Drupal taxonomy term ids discovered on the revenue landing page.
FY_TERM_IDS: list[tuple[int, str]] = [
    (292, "FY 2026/2027"),
    (285, "FY 2025/2026"),
    (252, "FY 2024/2025"),
    (115, "FY 2023/2024"),
    (50, "FY 2022/2023"),
    (49, "FY 2021/2022"),
    (110, "FY 2020/2021"),
    (114, "FY 2019/2020"),
    (116, "FY 2018/2019"),
]

MONTH_HEADER_RE = re.compile(
    r"^(" + "|".join(MONTH_NAMES) + r")\s+(20\d{2})$",
    re.IGNORECASE,
)


def _cell_text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).casefold()


def fy_filter_url(term_id: int) -> str:
    return f"{LANDING_URL}?{FY_FILTER_PARAM}={term_id}"


def classify_pa_excel_link(url: str, link_text: str = "") -> str | None:
    """Return 'sports', 'interactive', or None."""
    blob = f"{url} {link_text}".casefold()
    if "fantasy" in blob:
        return None
    if "sports" in blob and "wager" in blob:
        return "sports"
    if "interactive" in blob and "gaming" in blob:
        return "interactive"
    return None


def discover_fy_workbook_links(html: str, base_url: str = LANDING_URL) -> dict[str, dict]:
    """
    Find Sports Wagering and Interactive Gaming Excel links on a FY-filtered page.
    Returns {"sports": {url, filename}, "interactive": {...}} when present.
    """
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not re.search(r"\.xlsx($|\?)", href, re.IGNORECASE):
            continue
        text = anchor.get_text(" ", strip=True)
        kind = classify_pa_excel_link(href, text)
        if kind is None:
            continue
        url = urljoin(base_url, href)
        filename = url.rsplit("/", 1)[-1].split("?")[0]
        # Prefer first match per kind for a given FY page.
        found.setdefault(kind, {"url": url, "filename": filename, "link_text": text})
    return found


def discover_all_fy_links(session: requests.Session | None = None) -> list[dict]:
    """
    Walk FY filter query params and collect sports + interactive workbook URLs.
    Each item: {fy_label, fy_term_id, kind, url, filename}
    """
    sess = session or requests.Session()
    results: list[dict] = []
    seen_urls: set[str] = set()

    # Current (unfiltered) page first — usually latest FY.
    landing = http_get(LANDING_URL, session=sess)
    for kind, item in discover_fy_workbook_links(landing.text, LANDING_URL).items():
        if item["url"] in seen_urls:
            continue
        seen_urls.add(item["url"])
        results.append(
            {
                "fy_label": "current",
                "fy_term_id": None,
                "kind": kind,
                "url": item["url"],
                "filename": item["filename"],
            }
        )

    for term_id, label in FY_TERM_IDS:
        page_url = fy_filter_url(term_id)
        page = http_get(page_url, session=sess)
        for kind, item in discover_fy_workbook_links(page.text, page_url).items():
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            results.append(
                {
                    "fy_label": label,
                    "fy_term_id": term_id,
                    "kind": kind,
                    "url": item["url"],
                    "filename": item["filename"],
                }
            )
    return results


def parse_month_columns(header_row: pd.Series) -> list[tuple[int, int, int]]:
    """Return list of (column_index, year, month) for month headers; skip FY total."""
    months: list[tuple[int, int, int]] = []
    for col_index, value in enumerate(header_row.tolist()):
        text = _cell_text(value)
        match = MONTH_HEADER_RE.match(text)
        if not match:
            continue
        month = month_name_to_num(match.group(1).title())
        year = int(match.group(2))
        months.append((col_index, year, month))
    return months


def find_header_row(frame: pd.DataFrame) -> int:
    for row_index in range(min(10, len(frame))):
        if parse_month_columns(frame.iloc[row_index]):
            return row_index
    raise ValueError("Could not find month header row in PA workbook")


def _is_operator_header(label: str) -> bool:
    norm = _normalize_label(label)
    if not norm:
        return False
    # GRAND TOTAL is an operator-like header (mapped to STATEWIDE); do not exclude it.
    if norm in {
        "total sports wagering",
        "retail sports wagering",
        "online sports wagering",
        "interactive slots",
        "interactive tables",
        "banking tables",
        "banking tables 2",
        "non-banking tables (poker)",
        "non-banking tables (poker) 3",
        "handle*",
        "handle",
        "revenue",
        "promotional credits",
        "gross revenue (taxable)",
        "gross revenue",
        "wagers received",
        "amount won",
        "state tax due (34%)",
        "state tax (34%)",
        "state tax (14%)",
        "local share assessment (2%)",
        "local share assessment (5%)",
        "cfa county grants (13%)",
        "revenue (rake & tournament fees)",
    }:
        return False
    if norm.startswith("monthly sports") or norm.startswith("monthly interactive"):
        return False
    if "footnotes" in norm:
        return False
    # Metric-like rows with percentages
    if re.search(r"\(\d+%\)$", norm):
        return False
    # Section labels that include trailing footnote digits
    if re.sub(r"\s+\d+$", "", norm).strip() in {
        "banking tables",
        "non-banking tables (poker)",
    }:
        return False
    return True


def parse_sports_online_sections(frame: pd.DataFrame) -> pd.DataFrame:
    """
    Extract Online Sports Wagering metric rows for each operator × month.

    Metrics: Handle*, Gross Revenue (Taxable), State Tax Due (34%), Local Share Assessment (2%).
    """
    header_row = find_header_row(frame)
    months = parse_month_columns(frame.iloc[header_row])
    if not months:
        raise ValueError("No month columns in sports workbook")

    records: list[dict] = []
    current_operator: str | None = None
    in_online = False
    metrics: dict[str, dict[tuple[int, int], float | None]] = {}

    def flush_online():
        nonlocal metrics, current_operator, in_online
        if not current_operator or not metrics:
            metrics = {}
            in_online = False
            return
        for col_index, year, month in months:
            handle = metrics.get("handle", {}).get((year, month))
            taxable = metrics.get("taxable", {}).get((year, month))
            state_tax = metrics.get("state_tax", {}).get((year, month))
            local_tax = metrics.get("local_tax", {}).get((year, month))
            if handle is None and taxable is None and state_tax is None:
                continue
            # Skip FY template placeholder months (explicit zeros, no activity).
            if (handle or 0) == 0 and (taxable or 0) == 0 and (state_tax or 0) == 0 and (local_tax or 0) == 0:
                continue
            tax = None
            if state_tax is not None or local_tax is not None:
                tax = (state_tax or 0.0) + (local_tax or 0.0)
            period_start, period_end = month_period(year, month)
            is_total = _normalize_label(current_operator) == "grand total"
            records.append(
                {
                    "operator": "STATEWIDE" if is_total else current_operator.strip(),
                    "row_type": "official_statewide_total" if is_total else "operator",
                    "period_start": period_start,
                    "period_end": period_end,
                    "year": year,
                    "month": month,
                    "handle": handle,
                    "taxable_revenue": taxable,
                    "tax": tax,
                }
            )
        metrics = {}
        in_online = False

    for row_index in range(header_row + 1, len(frame)):
        label = _cell_text(frame.iloc[row_index, 0])
        if not label:
            continue
        norm = _normalize_label(label)

        if norm == "online sports wagering":
            flush_online()
            in_online = True
            metrics = {}
            continue
        if norm in {"retail sports wagering", "total sports wagering"}:
            flush_online()
            continue
        if _is_operator_header(label) and not in_online:
            flush_online()
            current_operator = label
            continue
        if _is_operator_header(label) and in_online:
            # Next operator starts
            flush_online()
            current_operator = label
            continue

        if not in_online or current_operator is None:
            continue

        key = None
        if norm in {"handle*", "handle"}:
            key = "handle"
        elif norm == "gross revenue (taxable)":
            key = "taxable"
        elif norm.startswith("state tax due"):
            key = "state_tax"
        elif norm.startswith("local share assessment"):
            key = "local_tax"
        if key is None:
            continue

        bucket = metrics.setdefault(key, {})
        for col_index, year, month in months:
            bucket[(year, month)] = parse_money(frame.iloc[row_index, col_index])

    flush_online()
    return pd.DataFrame.from_records(records)


def parse_interactive_operator_sections(frame: pd.DataFrame) -> pd.DataFrame:
    """
    Sum Interactive Gaming Gross Revenue (and taxes / wagers) per operator × month.

    Product lines under each operator are rolled into one online_casino row.
    """
    header_row = find_header_row(frame)
    months = parse_month_columns(frame.iloc[header_row])
    if not months:
        raise ValueError("No month columns in interactive workbook")

    records: list[dict] = []
    current_operator: str | None = None
    # Accumulators: (year, month) -> sums
    gross: dict[tuple[int, int], float] = {}
    handle: dict[tuple[int, int], float] = {}
    tax: dict[tuple[int, int], float] = {}

    def flush():
        nonlocal current_operator, gross, handle, tax
        if not current_operator:
            gross, handle, tax = {}, {}, {}
            return
        keys = set(gross) | set(handle) | set(tax)
        for year, month in sorted(keys):
            g = gross.get((year, month))
            h = handle.get((year, month))
            t = tax.get((year, month))
            if g is None and h is None and t is None:
                continue
            # Skip FY template placeholder months (explicit zeros, no activity).
            if (g or 0) == 0 and (h or 0) == 0 and (t or 0) == 0:
                continue
            period_start, period_end = month_period(year, month)
            is_total = _normalize_label(current_operator) == "grand total"
            records.append(
                {
                    "operator": "STATEWIDE" if is_total else current_operator.strip(),
                    "row_type": "official_statewide_total" if is_total else "operator",
                    "period_start": period_start,
                    "period_end": period_end,
                    "year": year,
                    "month": month,
                    "handle": h,
                    "gross_revenue": g,
                    "tax": t,
                }
            )
        current_operator = None
        gross, handle, tax = {}, {}, {}

    for row_index in range(header_row + 1, len(frame)):
        label = _cell_text(frame.iloc[row_index, 0])
        if not label:
            continue
        norm = _normalize_label(label)

        # Strip footnote digits from labels like 'Banking Tables 2'
        norm_base = re.sub(r"\s+\d+$", "", norm).strip()

        if _is_operator_header(label):
            flush()
            current_operator = label
            continue

        if current_operator is None:
            continue

        metric = None
        if norm in {"gross revenue"} or norm_base == "gross revenue":
            metric = "gross"
        elif norm in {"wagers received"}:
            metric = "handle"
        elif norm.startswith("state tax"):
            metric = "tax"
        elif norm.startswith("revenue (rake"):
            metric = "gross"
        if metric is None:
            continue

        for col_index, year, month in months:
            amount = parse_money(frame.iloc[row_index, col_index])
            if amount is None:
                continue
            key = (year, month)
            if metric == "gross":
                gross[key] = gross.get(key, 0.0) + amount
            elif metric == "handle":
                handle[key] = handle.get(key, 0.0) + amount
            elif metric == "tax":
                tax[key] = tax.get(key, 0.0) + amount

    flush()
    return pd.DataFrame.from_records(records)


def build_sports_normalized(
    parsed: pd.DataFrame,
    *,
    source_url: str,
    source_file: str,
    source_sha256: str,
    retrieved_at: datetime,
) -> pd.DataFrame:
    if parsed.empty:
        return pd.DataFrame()
    rows = parsed.copy()
    rows = rows.assign(
        jurisdiction=JURISDICTION,
        state_code=STATE_CODE,
        vertical=SPORTS_VERTICAL,
        channel="online",
        frequency="monthly",
        gross_revenue=pd.NA,
        adjusted_revenue=pd.NA,
        net_proceeds=pd.NA,
        reported_revenue_name=SPORTS_REPORTED_REVENUE,
        source_url=source_url,
        source_file=source_file,
        source_sha256=source_sha256,
        retrieved_at_utc=retrieved_at.isoformat(),
        report_status="ok",
    )
    return rows


def build_casino_normalized(
    parsed: pd.DataFrame,
    *,
    source_url: str,
    source_file: str,
    source_sha256: str,
    retrieved_at: datetime,
) -> pd.DataFrame:
    if parsed.empty:
        return pd.DataFrame()
    rows = parsed.copy()
    rows = rows.assign(
        jurisdiction=JURISDICTION,
        state_code=STATE_CODE,
        vertical=CASINO_VERTICAL,
        channel="online",
        frequency="monthly",
        adjusted_revenue=pd.NA,
        taxable_revenue=pd.NA,
        net_proceeds=pd.NA,
        reported_revenue_name=CASINO_REPORTED_REVENUE,
        source_url=source_url,
        source_file=source_file,
        source_sha256=source_sha256,
        retrieved_at_utc=retrieved_at.isoformat(),
        report_status="ok",
    )
    return rows


def parse_sports_workbook(content: bytes | Path) -> pd.DataFrame:
    excel = pd.ExcelFile(content if isinstance(content, Path) else BytesIO(content))
    # Prefer first non-footnote sheet
    sheet = next((n for n in excel.sheet_names if "footnote" not in n.casefold()), excel.sheet_names[0])
    frame = pd.read_excel(excel, sheet_name=sheet, header=None)
    return parse_sports_online_sections(frame)


def parse_interactive_workbook(content: bytes | Path) -> pd.DataFrame:
    excel = pd.ExcelFile(content if isinstance(content, Path) else BytesIO(content))
    sheet = next((n for n in excel.sheet_names if "footnote" not in n.casefold()), excel.sheet_names[0])
    frame = pd.read_excel(excel, sheet_name=sheet, header=None)
    return parse_interactive_operator_sections(frame)


def _upsert_vertical(
    connection,
    frame: pd.DataFrame,
    *,
    vertical: str,
    downloaded: int,
    failures: list[str],
    official_reason: str,
) -> None:
    if frame.empty:
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": vertical,
                "status": "failed",
                "reason": "; ".join(failures) or "No rows",
                "official_url": LANDING_URL,
                "available_frequency": "monthly",
                "earliest_period": None,
                "latest_period": None,
                "downloaded_file_count": downloaded,
                "normalized_row_count": 0,
                "last_retrieval_utc": utc_now().isoformat(),
            },
        )
        return
    upsert_coverage(
        connection,
        {
            "state_code": STATE_CODE,
            "vertical": vertical,
            "status": "ok" if not failures else "partial",
            "reason": "; ".join(failures[:5]) if failures else official_reason,
            "official_url": LANDING_URL,
            "available_frequency": "monthly",
            "earliest_period": frame["period_start"].min(),
            "latest_period": frame["period_end"].max(),
            "downloaded_file_count": downloaded,
            "normalized_row_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM gaming_results WHERE state_code=? AND vertical=?",
                    (STATE_CODE, vertical),
                ).fetchone()[0]
            ),
            "last_retrieval_utc": utc_now().isoformat(),
        },
    )


def collect_history(
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
    verticals: tuple[str, ...] = (SPORTS_VERTICAL, CASINO_VERTICAL),
) -> pd.DataFrame:
    """
    Download all discoverable FY sports + interactive Excels, parse monthly columns,
    upsert both verticals. Returns concatenated frame for requested verticals.
    """
    root = root or project_root()
    retrieved_at = utc_now()
    sess = session or requests.Session()
    links = discover_all_fy_links(session=sess)

    db_path = db_path or default_db_path(root)
    connection = connect(db_path)
    migrate_legacy_table(connection)
    ensure_schema(connection)

    sports_frames: list[pd.DataFrame] = []
    casino_frames: list[pd.DataFrame] = []
    sports_failures: list[str] = []
    casino_failures: list[str] = []
    sports_downloaded = 0
    casino_downloaded = 0

    for link in links:
        kind = link["kind"]
        label = f"{link['fy_label']}:{link['filename']}"
        try:
            response = http_get(link["url"], session=sess)
            content = response.content
            if not content.startswith(b"PK"):
                raise RuntimeError("Not an XLSX/ZIP payload")
            path = save_raw_bytes(
                root,
                STATE_CODE,
                content,
                link["filename"],
                retrieved_at=retrieved_at,
            )
            rel = str(path.relative_to(root)).replace("\\", "/")
            digest = sha256_bytes(content)
            source_url = response.url or link["url"]

            if kind == "sports" and SPORTS_VERTICAL in verticals:
                parsed = parse_sports_workbook(content)
                # Drop all-null future months already handled; also drop months with no data
                frame = build_sports_normalized(
                    parsed,
                    source_url=source_url,
                    source_file=rel,
                    source_sha256=digest,
                    retrieved_at=retrieved_at,
                )
                if frame.empty:
                    sports_failures.append(f"{label}: no online sports rows")
                    continue
                upsert_gaming_results(connection, frame)
                sports_frames.append(frame)
                sports_downloaded += 1
                print(f"OK PA sports {link['fy_label']}: {len(frame)} rows")
            elif kind == "interactive" and CASINO_VERTICAL in verticals:
                parsed = parse_interactive_workbook(content)
                frame = build_casino_normalized(
                    parsed,
                    source_url=source_url,
                    source_file=rel,
                    source_sha256=digest,
                    retrieved_at=retrieved_at,
                )
                if frame.empty:
                    casino_failures.append(f"{label}: no interactive rows")
                    continue
                upsert_gaming_results(connection, frame)
                casino_frames.append(frame)
                casino_downloaded += 1
                print(f"OK PA interactive {link['fy_label']}: {len(frame)} rows")
        except Exception as exc:  # noqa: BLE001
            msg = f"{label}: {exc}"
            if kind == "sports":
                sports_failures.append(msg)
            else:
                casino_failures.append(msg)
            print(f"SKIP {msg}")

    sports_result = (
        pd.concat(sports_frames, ignore_index=True).sort_values(["period_start", "operator"]).reset_index(drop=True)
        if sports_frames
        else pd.DataFrame()
    )
    casino_result = (
        pd.concat(casino_frames, ignore_index=True).sort_values(["period_start", "operator"]).reset_index(drop=True)
        if casino_frames
        else pd.DataFrame()
    )

    if SPORTS_VERTICAL in verticals:
        _upsert_vertical(
            connection,
            sports_result,
            vertical=SPORTS_VERTICAL,
            downloaded=sports_downloaded,
            failures=sports_failures,
            official_reason="Collected PGCB Online Sports Wagering monthly columns (Gross Revenue (Taxable))",
        )
    if CASINO_VERTICAL in verticals:
        _upsert_vertical(
            connection,
            casino_result,
            vertical=CASINO_VERTICAL,
            downloaded=casino_downloaded,
            failures=casino_failures,
            official_reason="Collected PGCB Interactive Gaming monthly Gross Revenue (online casino)",
        )

    connection.close()
    parts = []
    if SPORTS_VERTICAL in verticals and not sports_result.empty:
        parts.append(sports_result)
    if CASINO_VERTICAL in verticals and not casino_result.empty:
        parts.append(casino_result)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def collect_sports_history(**kwargs) -> pd.DataFrame:
    return collect_history(verticals=(SPORTS_VERTICAL,), **kwargs)


def collect_casino_history(**kwargs) -> pd.DataFrame:
    return collect_history(verticals=(CASINO_VERTICAL,), **kwargs)
