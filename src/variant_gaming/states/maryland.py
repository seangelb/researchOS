"""Maryland Lottery and Gaming online (mobile) sports-wagering collectors."""

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

LANDING_URL = "https://www.mdgaming.com/maryland-sports-wagering/revenue-reports/"
ALL_REPORTS_URL = (
    "https://www.mdgaming.com/maryland-sports-wagering/revenue-reports/all-financial-reports/"
)
STATE_CODE = "MD"
JURISDICTION = "Maryland"
VERTICAL = "online_sports_betting"
REPORTED_REVENUE_NAME = "Taxable Win"

EXCEL_LINK_RE = re.compile(r"sports wagering data.*excel", re.IGNORECASE)
MONTH_ENDED_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{4})",
    re.IGNORECASE,
)


def discover_release_links(html: str, base_url: str) -> list[dict]:
    """Find monthly news-release URLs from a revenue-reports listing page."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        text = " ".join(anchor.get_text(" ", strip=True).split())
        absolute = urljoin(base_url, href)
        if not any(title in absolute.casefold() for title in
                   ("sports-wagering-contributes", "sports-wagering-sets-new-benchmark")):
            continue
        found.setdefault(
            absolute,
            {"url": absolute, "link_text": text},
        )
    return sorted(found.values(), key=lambda item: item["url"])


def discover_all_release_links(
    session: requests.Session | None = None,
) -> list[dict]:
    """Follow the archive's Older Entries links, including the mobile launch reports."""
    sess = session or requests.Session()
    found: dict[str, dict] = {}
    pending = [LANDING_URL, ALL_REPORTS_URL]
    visited = set()
    while pending:
        page_url = pending.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)
        html = http_get(page_url, session=sess).text
        for item in discover_release_links(html, page_url):
            found.setdefault(item["url"], item)
        for anchor in BeautifulSoup(html, "html.parser").select("a[href]"):
            if "older entries" in anchor.get_text(" ", strip=True).casefold():
                pending.append(urljoin(page_url, anchor["href"]))
    return sorted(found.values(), key=lambda item: item["url"])


def find_report_download(html: str, base_url: str) -> dict:
    """
    Two-hop: on a monthly release page, find the
    Sports Wagering Data workbook, or its accessible PDF replacement.
    """
    soup = BeautifulSoup(html, "html.parser")
    matches: list[dict] = []
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        href = anchor["href"].strip()
        absolute = urljoin(base_url, href)
        if EXCEL_LINK_RE.search(text) or (
            absolute.casefold().endswith((".xlsx", ".xls", ".pdf"))
            and "sports" in absolute.casefold()
            and "wagering" in absolute.casefold()
        ):
            matches.append(
                {
                    "url": absolute,
                    "link_text": text,
                    "filename": unquote(absolute.rsplit("/", 1)[-1].split("?", 1)[0]),
                }
            )
    if not matches:
        raise RuntimeError("No Sports Wagering Data workbook or accessible PDF found")
    # Prefer explicit Excel-download wording when multiple anchors exist.
    preferred = [m for m in matches if EXCEL_LINK_RE.search(m["link_text"])]
    return preferred[0] if preferred else matches[0]


def _cell_text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _is_fytd_marker(value) -> bool:
    return _cell_text(value).casefold() == "fytd"


def _section_label(value) -> str | None:
    text = _cell_text(value).casefold()
    if text == "mobile":
        return "mobile"
    if text == "retail":
        return "retail"
    if "combined statewide" in text:
        return "combined"
    return None


def _is_total_mobile(value) -> bool:
    # Early workbooks call the subtotal within MOBILE "Combined".
    return _cell_text(value).casefold() in {"total mobile", "combined"}


def _to_timestamp(value) -> pd.Timestamp | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return pd.Timestamp(value).normalize()
    text = _cell_text(value)
    if not text or text.casefold() in {"fytd", "month", "nan"}:
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts).normalize()


def parse_mobile_sports_workbook(content: bytes | Path) -> tuple[pd.DataFrame, dict]:
    """
    Parse licensee MOBILE rows from a Maryland monthly sports-wagering workbook.

    Maps:
      Taxable Win -> taxable_revenue
      Handle -> handle
      Contributions to the State -> tax

    Excludes RETAIL and COMBINED sections from the returned frame.
    """
    if isinstance(content, Path):
        excel_file = pd.ExcelFile(content)
    else:
        excel_file = pd.ExcelFile(BytesIO(content))

    # Prefer the main data sheet (not Instructions / Bets By Sport).
    sheet_name = next(
        (name for name in excel_file.sheet_names if "sw data" in name.casefold()),
        next(
            (
                name
                for name in excel_file.sheet_names
                if name.casefold() not in {"instructions", "bets by sport"}
            ),
            excel_file.sheet_names[0],
        ),
    )
    frame = pd.read_excel(excel_file, sheet_name=sheet_name, header=None)

    period_date = None
    for value in frame.iloc[:, 0].tolist()[:5]:
        ts = _to_timestamp(value)
        if ts is not None:
            period_date = ts
            break
    if period_date is None:
        # Fall back to first dated month cell under MOBILE.
        for _, row in frame.iterrows():
            ts = _to_timestamp(row.iloc[1] if len(row) > 1 else None)
            if ts is not None:
                period_date = ts
                break
    if period_date is None:
        raise ValueError("Could not determine report month from Maryland workbook")

    year, month = int(period_date.year), int(period_date.month)
    section: str | None = None
    records: list[dict] = []

    for _, row in frame.iterrows():
        label = _section_label(row.iloc[0] if len(row) else None)
        if label is not None:
            section = label
            continue
        if section != "mobile":
            continue

        licensee = _cell_text(row.iloc[0] if len(row) else None)
        if not licensee or licensee.casefold() in {"licensee", "nan"}:
            continue
        if _is_fytd_marker(row.iloc[1] if len(row) > 1 else None):
            continue
        # Header sub-row with metric names has Month/FYTD in col0 empty and labels in other cols.
        if licensee.casefold() in {"fytd"}:
            continue

        month_cell = row.iloc[1] if len(row) > 1 else None
        if _is_fytd_marker(month_cell):
            continue
        # Month data rows have a date in column 1; skip blank / note rows.
        if _to_timestamp(month_cell) is None and not _is_total_mobile(licensee):
            # TOTALS without date still accepted when labeled Total Mobile.
            continue

        handle = parse_money(row.iloc[2] if len(row) > 2 else None)
        taxable = parse_money(row.iloc[7] if len(row) > 7 else None)
        tax = parse_money(row.iloc[8] if len(row) > 8 else None)

        if _is_total_mobile(licensee):
            records.append(
                {
                    "operator": "STATEWIDE",
                    "row_type": "official_statewide_total",
                    "handle": handle,
                    "taxable_revenue": taxable,
                    "tax": tax,
                }
            )
            continue

        records.append(
            {
                "operator": licensee,
                "row_type": "operator",
                "handle": handle,
                "taxable_revenue": taxable,
                "tax": tax,
            }
        )

    if not records:
        raise ValueError("No MOBILE licensee rows found in Maryland workbook")

    parsed = pd.DataFrame(records)
    operators = parsed[parsed.row_type == "operator"]
    totals = parsed[parsed.row_type == "official_statewide_total"]
    if len(totals) != 1:
        raise ValueError("Expected one Maryland mobile subtotal")
    # A tax-base subtotal can exclude licensee losses; do not force it to sum.
    for column in ["handle", "tax"]:
        total = totals.iloc[0][column]
        if pd.notna(total) and operators[column].notna().all():
            if abs(operators[column].sum() - total) > 1:
                raise ValueError(f"Maryland mobile {column} does not reconcile")

    meta = {
        "sheet_name": sheet_name,
        "year": year,
        "month": month,
        "period_start": month_period(year, month)[0],
        "period_end": month_period(year, month)[1],
    }
    return parsed, meta


def parse_mobile_sports_pdf(path: Path) -> tuple[pd.DataFrame, dict]:
    """The February 2026 release supplies the same mobile table as an accessible PDF."""
    records = []
    period = None
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if not re.search(r"^MOBILE$", text, re.M):
                continue
            match = MONTH_ENDED_RE.search(text)
            if not match:
                raise ValueError("Maryland mobile PDF month missing")
            period = pd.to_datetime(match.group())
            for table in page.extract_tables():
                for row in table:
                    if len(row) != 10 or row[1] != period.strftime("%B") or not row[0]:
                        continue
                    is_total = _is_total_mobile(row[0])
                    records.append({"operator": "STATEWIDE" if is_total else " ".join(row[0].split()),
                        "row_type": "official_statewide_total" if is_total else "operator",
                        "handle": parse_money(row[2]), "taxable_revenue": parse_money(row[7]),
                        "tax": parse_money(row[8])})
    if not records:
        raise ValueError("Maryland PDF has no supported mobile rows")
    rows = pd.DataFrame(records)
    operators = rows[rows.row_type == "operator"]
    totals = rows[rows.row_type == "official_statewide_total"]
    if len(totals) != 1 or operators.empty:
        raise ValueError("Maryland PDF mobile subtotal or licensees missing")
    for column in ["handle", "tax"]:
        if operators[column].notna().all() and pd.notna(totals.iloc[0][column]):
            if abs(operators[column].sum() - totals.iloc[0][column]) > 1:
                raise ValueError(f"Maryland PDF {column} does not reconcile")
    start, end = month_period(period.year, period.month)
    return rows, dict(year=period.year, month=period.month, period_start=start, period_end=end)


def build_normalized_rows(
    parsed: pd.DataFrame,
    *,
    year: int,
    month: int,
    source_url: str,
    source_file: str,
    source_sha256: str,
    retrieved_at: datetime,
) -> pd.DataFrame:
    period_start, period_end = month_period(year, month)
    rows = parsed.copy()
    rows = rows.assign(
        jurisdiction=JURISDICTION,
        state_code=STATE_CODE,
        vertical=VERTICAL,
        channel="online",
        period_start=period_start,
        period_end=period_end,
        frequency="monthly",
        gross_revenue=pd.NA,
        adjusted_revenue=pd.NA,
        net_proceeds=pd.NA,
        reported_revenue_name=REPORTED_REVENUE_NAME,
        source_url=source_url,
        source_file=source_file,
        source_sha256=source_sha256,
        retrieved_at_utc=retrieved_at.isoformat(),
        report_status="ok",
    )
    return rows


def collect_release(
    release: dict,
    *,
    root: Path | None = None,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
) -> pd.DataFrame:
    """Download one monthly release's Excel workbook and return normalized online rows."""
    root = root or project_root()
    retrieved_at = retrieved_at or utc_now()
    sess = session or requests.Session()

    release_html = http_get(release["url"], session=sess).text
    excel = find_report_download(release_html, release["url"])
    content = http_get(excel["url"], session=sess).content
    if not content.startswith((b"PK", b"%PDF")):
        raise RuntimeError(f"Expected workbook or PDF from {excel['url']}")

    path = save_raw_bytes(
        root,
        STATE_CODE,
        content,
        excel["filename"] or "maryland_sports_wagering.xlsx",
        retrieved_at=retrieved_at,
    )
    parsed, meta = (parse_mobile_sports_pdf(path) if content.startswith(b"%PDF")
                    else parse_mobile_sports_workbook(content))
    rel = str(path.relative_to(root)).replace("\\", "/")
    digest = sha256_bytes(content)
    return build_normalized_rows(
        parsed,
        year=meta["year"],
        month=meta["month"],
        source_url=excel["url"],
        source_file=rel,
        source_sha256=digest,
        retrieved_at=retrieved_at,
    )


def collect_history(
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Discover all monthly MD releases, download Excel workbooks, upsert online rows."""
    root = root or project_root()
    retrieved_at = utc_now()
    sess = session or requests.Session()

    releases = discover_all_release_links(session=sess)
    if not releases:
        raise RuntimeError(f"No Maryland release links found on {LANDING_URL}")

    db_path = db_path or default_db_path(root)
    connection = connect(db_path)
    migrate_legacy_table(connection)
    ensure_schema(connection)

    all_frames: list[pd.DataFrame] = []
    failures: list[str] = []
    downloaded = 0

    for release in releases:
        label = release.get("link_text") or release["url"]
        try:
            frame = collect_release(
                release,
                root=root,
                session=sess,
                retrieved_at=retrieved_at,
            )
            upsert_gaming_results(connection, frame)
            all_frames.append(frame)
            downloaded += 1
            print(f"OK {frame['period_start'].iloc[0]}: {len(frame)} rows ({label[:60]})")
        except Exception as exc:  # noqa: BLE001 - continue other months
            failures.append(f"{label}: {exc}")
            print(f"SKIP {label[:80]}: {exc}")

    if all_frames:
        result = pd.concat(all_frames, ignore_index=True)
        result = result.sort_values(["period_start", "row_type", "operator"]).reset_index(drop=True)
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": VERTICAL,
                "status": "ok" if not failures else "partial",
                "reason": (
                    "; ".join(failures[:5])
                    if failures
                    else "Collected MD mobile Taxable Win + Handle (retail excluded)"
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
            "reason": "; ".join(failures) or "No Maryland workbooks collected",
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
