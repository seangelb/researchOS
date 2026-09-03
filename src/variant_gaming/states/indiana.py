"""Indiana IGC monthly sports-wagering collectors (online brands from SW Details)."""

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

LANDING_URL = "https://www.in.gov/igc/publications/monthly-revenue/"
ARCHIVE_URL = "https://www.in.gov/igc/publications/archived-monthly-revenue-reports"
VERTICAL = "online_sports_betting"
STATE_CODE = "IN"
JURISDICTION = "Indiana"

# Prefer Taxable AGR when unambiguously attributable; else Gross Receipts from SW Details.
REPORTED_TAXABLE_AGR = "Taxable AGR"
REPORTED_GROSS_RECEIPTS = "Gross Receipts"

PERIOD_IN_TITLE_RE = re.compile(
    r"As reported for\s+([A-Za-z]+)\s+(\d{4})",
    re.IGNORECASE,
)
PERIOD_IN_FILENAME_RE = re.compile(r"(20\d{2})-(\d{2})-Revenue", re.IGNORECASE)
YEAR_PAGE_RE = re.compile(r"20\d{2}")
SKIP_ROW_LABELS = {
    "retail",
    "adjustments",
    "taxable agr",
    "taxable agr*",
    "handle",
    "gross receipts",
}
SECTION_HEADERS = {
    "northern licensees",
    "southern licensees",
    "southern licenees",  # spelling in source workbooks
    "racino licensees",
    "racino licenees",
}
DOMAIN_RE = re.compile(r"\.[a-z]{2,}(/|$|\s|$)", re.IGNORECASE)


def _cell_text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).casefold()


def is_online_brand_label(label: str) -> bool:
    """Online brands look like 'AS - Sportsbook.DraftKings.com' (domain present)."""
    text = label.strip()
    if not text:
        return False
    norm = _normalize_label(text)
    if norm in SKIP_ROW_LABELS or norm in SECTION_HEADERS:
        return False
    if norm.startswith("indiana gaming") or "as reported for" in norm:
        return False
    if norm == "retail" or norm.startswith("wc "):
        # Walk-up / OTB locations under racinos are not online brands.
        return False
    return bool(DOMAIN_RE.search(text)) or ".com" in text.casefold()


def discover_year_archive_urls(html: str, base_url: str = ARCHIVE_URL) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        text = anchor.get_text(" ", strip=True)
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        if "archived-monthly-revenue-reports" in absolute and YEAR_PAGE_RE.search(text):
            year_match = YEAR_PAGE_RE.search(text)
            if year_match and int(year_match.group(0)) >= 2019:
                seen.add(absolute)
                urls.append(absolute)
        elif re.search(r"igc-monthly-revenue-reports-20\d{2}", absolute):
            seen.add(absolute)
            urls.append(absolute)
        elif re.search(r"archived-monthly-revenue-reports-20\d{2}", absolute):
            year = int(re.search(r"20\d{2}", absolute).group(0))
            if year >= 2019:
                seen.add(absolute)
                urls.append(absolute)
    return sorted(set(urls))


def discover_xlsx_links(html: str, base_url: str) -> list[dict]:
    """Return unique monthly revenue XLSX links: {url, filename, year, month}."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not re.search(r"\.xlsx($|\?)", href, re.IGNORECASE):
            continue
        if "Revenue" not in href and "revenue" not in href:
            continue
        url = urljoin(base_url, href)
        filename = url.rsplit("/", 1)[-1].split("?")[0]
        year = month = None
        match = PERIOD_IN_FILENAME_RE.search(filename)
        if match:
            year, month = int(match.group(1)), int(match.group(2))
        found.setdefault(
            url,
            {"url": url, "filename": filename, "year": year, "month": month},
        )
    return sorted(
        found.values(),
        key=lambda item: (item["year"] or 0, item["month"] or 0, item["filename"]),
    )


def discover_all_workbook_links(session: requests.Session | None = None) -> list[dict]:
    sess = session or requests.Session()
    landing = http_get(LANDING_URL, session=sess)
    links = discover_xlsx_links(landing.text, LANDING_URL)

    archive = http_get(ARCHIVE_URL, session=sess)
    for year_url in discover_year_archive_urls(archive.text, archive.url):
        page = http_get(year_url, session=sess)
        links.extend(discover_xlsx_links(page.text, year_url))

    # Dedupe by URL, prefer entries with parsed period.
    by_url: dict[str, dict] = {}
    for item in links:
        by_url[item["url"]] = item
    return sorted(
        by_url.values(),
        key=lambda item: (item["year"] or 0, item["month"] or 0, item["filename"]),
    )


def resolve_sheet_name(sheet_names: list[str], *candidates: str) -> str | None:
    lowered = {name.casefold(): name for name in sheet_names}
    for candidate in candidates:
        if candidate.casefold() in lowered:
            return lowered[candidate.casefold()]
    # Soft match: contains SW Tax / SW Detail / Sheet7 / Sheet8
    for name in sheet_names:
        norm = name.casefold()
        for candidate in candidates:
            if candidate.casefold() in norm:
                return name
    return None


def parse_period_from_workbook(excel: pd.ExcelFile, filename: str) -> tuple[int, int]:
    match = PERIOD_IN_FILENAME_RE.search(filename)
    if match:
        return int(match.group(1)), int(match.group(2))

    for sheet_name in excel.sheet_names:
        frame = pd.read_excel(excel, sheet_name=sheet_name, header=None)
        for value in frame.to_numpy().ravel()[:80]:
            text = _cell_text(value)
            period = PERIOD_IN_TITLE_RE.search(text)
            if period:
                month_name = period.group(1).title()
                if month_name in MONTH_NAMES:
                    return int(period.group(2)), MONTH_NAMES.index(month_name) + 1
    raise ValueError(f"Could not resolve period for {filename}")


def parse_sw_tax_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """
    Parse sheet 7: licensee Handle / Taxable AGR / Tax (combined online+retail).
    Returns columns: licensee, handle, taxable_agr, tax
    """
    header_row = None
    handle_col = taxable_col = tax_col = None
    for row_index in range(min(15, len(frame))):
        row = frame.iloc[row_index]
        labels = {_normalize_label(_cell_text(v)): idx for idx, v in enumerate(row.tolist()) if _cell_text(v)}
        handle_keys = [k for k in labels if k == "handle"]
        taxable_keys = [k for k in labels if k.startswith("taxable agr")]
        tax_keys = [
            k
            for k in labels
            if k in {"tax", "sports wagering tax"} or k.startswith("sports wagering tax")
        ]
        if handle_keys and taxable_keys and tax_keys:
            header_row = row_index
            handle_col = labels[handle_keys[0]]
            taxable_col = labels[taxable_keys[0]]
            tax_col = labels[tax_keys[0]]
            break
    if header_row is None:
        return pd.DataFrame(
            columns=["licensee", "handle", "taxable_agr", "tax", "is_total"]
        )

    def _safe_money(value):
        try:
            return parse_money(value)
        except (TypeError, ValueError):
            return None

    records = []
    for row_index in range(header_row + 1, len(frame)):
        row = frame.iloc[row_index]
        label = None
        for value in row.tolist():
            text = _cell_text(value)
            if text:
                label = text
                break
        if not label:
            continue
        norm = _normalize_label(label)
        if norm.startswith("*") or "adjusted gross" in norm or "reported amounts" in norm:
            continue
        if (
            "handle by sport" in norm
            or norm in {"month", "ytd", "sport"}
            or norm.startswith("monthly sports")
            or norm.startswith("state wide handle")
        ):
            break
        handle = _safe_money(row.iloc[handle_col] if handle_col < len(row) else None)
        taxable = _safe_money(row.iloc[taxable_col] if taxable_col < len(row) else None)
        tax = _safe_money(row.iloc[tax_col] if tax_col < len(row) else None)
        if handle is None and taxable is None and tax is None:
            continue
        is_total = norm == "total"
        records.append(
            {
                "licensee": label if not is_total else "TOTAL",
                "handle": handle,
                "taxable_agr": taxable,
                "tax": tax,
                "is_total": is_total,
            }
        )
        if is_total:
            break
    return pd.DataFrame.from_records(records)


def _iter_detail_blocks(frame: pd.DataFrame):
    """
    Sheet 8 lays out three licensee columns: (name, handle, blank, gross) repeating.
    Yield (licensee, rows_of_(label, handle, gross_receipts)).
    """
    col_starts = [0, 5, 10]
    for start in col_starts:
        name_col = start
        handle_col = start + 1
        gross_col = start + 3
        if gross_col >= frame.shape[1]:
            continue
        current_licensee = None
        block_rows: list[tuple[str, float | None, float | None]] = []

        for row_index in range(len(frame)):
            label = _cell_text(frame.iloc[row_index, name_col])
            handle_header = _normalize_label(_cell_text(frame.iloc[row_index, handle_col]))
            gross_header = _normalize_label(_cell_text(frame.iloc[row_index, gross_col]))

            if label and handle_header == "handle" and gross_header.startswith("gross receipt"):
                if current_licensee and block_rows:
                    yield current_licensee, list(block_rows)
                current_licensee = label
                block_rows = []
                continue

            if not current_licensee:
                continue
            if not label:
                if block_rows and all(
                    _cell_text(frame.iloc[row_index, c]) == ""
                    for c in range(name_col, min(name_col + 4, frame.shape[1]))
                ):
                    yield current_licensee, list(block_rows)
                    current_licensee = None
                    block_rows = []
                continue

            try:
                handle = parse_money(frame.iloc[row_index, handle_col])
            except (TypeError, ValueError):
                handle = None
            try:
                gross = parse_money(frame.iloc[row_index, gross_col])
            except (TypeError, ValueError):
                gross = None
            block_rows.append((label, handle, gross))

        if current_licensee and block_rows:
            yield current_licensee, list(block_rows)

def parse_sw_details_online_brands(frame: pd.DataFrame) -> pd.DataFrame:
    """
    Parse sheet 8 online brand rows (exclude Retail / Adjustments / Taxable AGR / OTB).

    Returns operator, licensee, handle, gross_revenue, taxable_agr (licensee block),
    retail_handle, retail_gross.
    """
    records = []
    for licensee, rows in _iter_detail_blocks(frame):
        taxable_agr = None
        retail_handle = 0.0
        retail_gross = 0.0
        retail_seen = False
        brands: list[dict] = []
        for label, handle, gross in rows:
            norm = _normalize_label(label)
            if norm.startswith("taxable agr"):
                taxable_agr = gross
                continue
            if norm == "retail" or norm.startswith("wc "):
                retail_seen = True
                if handle is not None:
                    retail_handle += handle
                if gross is not None:
                    retail_gross += gross
                continue
            if norm == "adjustments":
                continue
            if is_online_brand_label(label):
                brands.append(
                    {
                        "operator": label,
                        "licensee": licensee,
                        "handle": handle,
                        "gross_revenue": gross,
                    }
                )
        for brand in brands:
            brand["taxable_agr_licensee"] = taxable_agr
            brand["retail_handle"] = retail_handle if retail_seen else 0.0
            brand["retail_gross"] = retail_gross if retail_seen else 0.0
            brand["online_brand_count"] = len(brands)
            records.append(brand)
    return pd.DataFrame.from_records(records)


def build_normalized_rows(
    brands: pd.DataFrame,
    tax_summary: pd.DataFrame,
    *,
    year: int,
    month: int,
    source_url: str,
    source_file: str,
    source_sha256: str,
    retrieved_at: datetime,
) -> pd.DataFrame:
    period_start, period_end = month_period(year, month)
    rows: list[dict] = []

    tax_by_licensee = {}
    if tax_summary is not None and not tax_summary.empty:
        for _, row in tax_summary[~tax_summary["is_total"]].iterrows():
            tax_by_licensee[_normalize_label(row["licensee"])] = row

    for _, brand in brands.iterrows():
        handle = brand.get("handle")
        gross = brand.get("gross_revenue")
        taxable = None
        tax = None
        reported = REPORTED_GROSS_RECEIPTS
        # Prefer Taxable AGR only when this licensee has a single online brand and no retail.
        retail_handle = float(brand.get("retail_handle") or 0)
        online_count = int(brand.get("online_brand_count") or 0)
        if online_count == 1 and retail_handle == 0:
            lic = tax_by_licensee.get(_normalize_label(str(brand["licensee"])))
            if lic is not None and lic.get("taxable_agr") is not None:
                taxable = float(lic["taxable_agr"])
                tax = float(lic["tax"]) if lic.get("tax") is not None else None
                reported = REPORTED_TAXABLE_AGR
            elif brand.get("taxable_agr_licensee") is not None:
                taxable = float(brand["taxable_agr_licensee"])
                reported = REPORTED_TAXABLE_AGR

        rows.append(
            {
                "jurisdiction": JURISDICTION,
                "state_code": STATE_CODE,
                "vertical": VERTICAL,
                "channel": "online",
                "operator": brand["operator"],
                "row_type": "operator",
                "period_start": period_start,
                "period_end": period_end,
                "frequency": "monthly",
                "handle": handle,
                "gross_revenue": gross,
                "adjusted_revenue": None,
                "taxable_revenue": taxable,
                "net_proceeds": None,
                "tax": tax,
                "reported_revenue_name": reported,
                "source_url": source_url,
                "source_file": source_file,
                "source_sha256": source_sha256,
                "retrieved_at_utc": retrieved_at.isoformat(),
                "report_status": "ok",
            }
        )

    if not rows:
        return pd.DataFrame()

    operators = pd.DataFrame(rows)
    # Derived statewide from online brands only.
    statewide_handle = float(pd.to_numeric(operators["handle"], errors="coerce").sum())
    statewide_gross = float(pd.to_numeric(operators["gross_revenue"], errors="coerce").sum())
    statewide_taxable = None
    statewide_tax = None
    reported = REPORTED_GROSS_RECEIPTS
    status = "derived_from_operator_sum"

    total_row = None
    if tax_summary is not None and not tax_summary.empty:
        totals = tax_summary[tax_summary["is_total"]]
        if not totals.empty:
            total_row = totals.iloc[0]
    retail_total = float(pd.to_numeric(brands["retail_handle"], errors="coerce").fillna(0).sum()) if "retail_handle" in brands else 0.0
    if total_row is not None and retail_total == 0:
        # Taxable AGR is statewide online-only this month.
        statewide_taxable = float(total_row["taxable_agr"]) if total_row["taxable_agr"] is not None else None
        statewide_tax = float(total_row["tax"]) if total_row["tax"] is not None else None
        reported = REPORTED_TAXABLE_AGR
        status = "ok"

    operators = pd.concat(
        [
            operators,
            pd.DataFrame(
                [
                    {
                        "jurisdiction": JURISDICTION,
                        "state_code": STATE_CODE,
                        "vertical": VERTICAL,
                        "channel": "online",
                        "operator": "STATEWIDE",
                        "row_type": "official_statewide_total" if status == "ok" else "official_statewide_total",
                        "period_start": period_start,
                        "period_end": period_end,
                        "frequency": "monthly",
                        "handle": statewide_handle,
                        "gross_revenue": statewide_gross,
                        "adjusted_revenue": None,
                        "taxable_revenue": statewide_taxable,
                        "net_proceeds": None,
                        "tax": statewide_tax,
                        "reported_revenue_name": reported,
                        "source_url": source_url,
                        "source_file": source_file,
                        "source_sha256": source_sha256,
                        "retrieved_at_utc": retrieved_at.isoformat(),
                        "report_status": status,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    return operators


def parse_workbook(
    content: bytes | Path, filename: str = "workbook.xlsx"
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Parse an IGC monthly revenue workbook into brands, tax summary, and meta."""
    if isinstance(content, Path):
        raw = content.read_bytes()
        excel = pd.ExcelFile(content)
        filename = content.name
    else:
        raw = content
        excel = pd.ExcelFile(BytesIO(content))

    year, month = parse_period_from_workbook(excel, filename)
    details_name = resolve_sheet_name(excel.sheet_names, "8 SW Details", "Sheet8", "SW Details")
    summary_name = resolve_sheet_name(excel.sheet_names, "7 SW Tax Summary", "Sheet7", "SW Tax Summary")
    if details_name is None:
        raise ValueError(f"No SW Details sheet in {filename}; sheets={excel.sheet_names}")

    details = pd.read_excel(excel, sheet_name=details_name, header=None)
    brands = parse_sw_details_online_brands(details)
    tax_summary = pd.DataFrame()
    if summary_name is not None:
        tax_summary = parse_sw_tax_summary(pd.read_excel(excel, sheet_name=summary_name, header=None))

    meta = {
        "year": year,
        "month": month,
        "details_sheet": details_name,
        "summary_sheet": summary_name,
        "online_brand_rows": int(len(brands)),
        "content_sha256": sha256_bytes(raw),
    }
    return brands, tax_summary, meta


def collect_workbook_bytes(
    content: bytes,
    *,
    filename: str,
    source_url: str,
    root: Path | None = None,
    retrieved_at: datetime | None = None,
) -> pd.DataFrame:
    root = root or project_root()
    retrieved_at = retrieved_at or utc_now()
    brands, tax_summary, meta = parse_workbook(content, filename=filename)
    if brands.empty:
        return pd.DataFrame()

    path = save_raw_bytes(root, STATE_CODE, content, filename, retrieved_at=retrieved_at)
    rel = str(path.relative_to(root)).replace("\\", "/")
    digest = sha256_bytes(content)
    return build_normalized_rows(
        brands,
        tax_summary,
        year=meta["year"],
        month=meta["month"],
        source_url=source_url,
        source_file=rel,
        source_sha256=digest,
        retrieved_at=retrieved_at,
    )


def collect_history(
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
    min_year: int = 2019,
) -> pd.DataFrame:
    """Discover monthly IGC Excels, parse online sports brands, upsert."""
    root = root or project_root()
    retrieved_at = utc_now()
    sess = session or requests.Session()

    links = discover_all_workbook_links(session=sess)
    links = [item for item in links if (item["year"] is None or item["year"] >= min_year)]
    if not links:
        raise RuntimeError(f"No Indiana revenue XLSX links found from {LANDING_URL}")

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
            response = http_get(link["url"], session=sess)
            content = response.content
            if not content.startswith(b"PK"):
                raise RuntimeError("Not an XLSX/ZIP payload")
            frame = collect_workbook_bytes(
                content,
                filename=label,
                source_url=response.url or link["url"],
                root=root,
                retrieved_at=retrieved_at,
            )
            if frame.empty:
                failures.append(f"{label}: no online brand rows")
                print(f"SKIP {label}: no online brand rows")
                continue
            upsert_gaming_results(connection, frame)
            all_frames.append(frame)
            downloaded += 1
            print(f"OK {frame['period_start'].iloc[0]}: {len(frame)} rows <- {label}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{label}: {exc}")
            print(f"SKIP {label}: {exc}")

    if all_frames:
        result = pd.concat(all_frames, ignore_index=True)
        result = result.sort_values(["period_start", "operator"]).reset_index(drop=True)
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": VERTICAL,
                "status": "ok" if not failures else "partial",
                "reason": (
                    "; ".join(failures[:5])
                    if failures
                    else "Collected IGC SW Details online brands + Tax Summary when attributable"
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
            "reason": "; ".join(failures) or "No Indiana months collected",
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
