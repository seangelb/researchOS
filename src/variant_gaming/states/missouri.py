"""Missouri Gaming Commission online (mobile) sports-wagering collectors."""

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

LANDING_URL = "https://www.mgc.dps.mo.gov/SportsWagering/sw_financials/rb_SWFin_main.html"
STATE_CODE = "MO"
JURISDICTION = "Missouri"
VERTICAL = "online_sports_betting"
REPORTED_REVENUE_NAME = "TAXABLE AGR"
MOBILE_SHEET_NAMES = {"MONTHLY STATS MOBILE", "MONTHLY STATS - MOBILE"}

MONTH_ENDED_RE = re.compile(
    r"MONTH\s+ENDED:\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})",
    re.IGNORECASE,
)
PATH_MONTH_RE = re.compile(
    r"/(?:FY\d+_SWFinReport/)?(\d{2})_([A-Za-z]{3})/",
    re.IGNORECASE,
)


def _read_excel_frames(content: bytes, filename: str) -> tuple[pd.ExcelFile | None, dict[str, pd.DataFrame]]:
    """
    Load workbook sheets. Prefer pandas/openpyxl for xlsx; use xlrd 1.x directly for legacy .xls
    because modern pandas requires xlrd>=2 which dropped .xls support.
    """
    lower = filename.casefold()
    if lower.endswith(".xlsx") or content.startswith(b"PK"):
        excel_file = pd.ExcelFile(BytesIO(content), engine="openpyxl")
        return excel_file, {
            name: pd.read_excel(excel_file, sheet_name=name, header=None)
            for name in excel_file.sheet_names
        }

    if lower.endswith(".xls"):
        try:
            import xlrd  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Reading Missouri .xls requires xlrd==1.2.0 (pip install xlrd==1.2.0)"
            ) from exc

        book = xlrd.open_workbook(file_contents=content)
        frames: dict[str, pd.DataFrame] = {}
        for sheet in book.sheets():
            rows = []
            for r in range(sheet.nrows):
                converted = []
                for c in range(sheet.ncols):
                    cell = sheet.cell(r, c)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        parts = xlrd.xldate_as_tuple(cell.value, book.datemode)
                        converted.append(pd.Timestamp(year=parts[0], month=parts[1], day=parts[2]))
                    else:
                        converted.append(cell.value)
                rows.append(converted)
            frames[sheet.name] = pd.DataFrame(rows)
        return None, frames

    raise ValueError(f"Unsupported Excel extension for {filename}")


def discover_monthly_financial_links(
    html: str, base_url: str = LANDING_URL
) -> list[dict]:
    """Prefer Monthly Financials Excel links (not Revenue Detail)."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        absolute = urljoin(base_url, href)
        filename = unquote(absolute.rsplit("/", 1)[-1].split("?", 1)[0])
        blob = f"{filename} {absolute}".casefold()
        if "monthly financial" not in blob:
            continue
        if not (blob.endswith(".xlsx") or blob.endswith(".xls") or ".xls" in blob):
            continue
        if "revenue detail" in blob:
            continue
        year_hint = None
        month_hint = None
        # Paths look like .../FY26_SWFinReport/06_Jun/...
        path_match = re.search(
            r"FY(\d{2})_SWFinReport/(\d{2})_[A-Za-z]{3}/",
            absolute,
            re.IGNORECASE,
        )
        if path_match:
            fy_two = int(path_match.group(1))
            month_hint = int(path_match.group(2))
            # Missouri FY ends in June; FY26 = Jul 2025 - Jun 2026.
            fy_end_year = 2000 + fy_two
            if month_hint >= 7:
                year_hint = fy_end_year - 1
            else:
                year_hint = fy_end_year
        found.setdefault(
            absolute,
            {
                "url": absolute,
                "filename": filename,
                "year_hint": year_hint,
                "month_hint": month_hint,
            },
        )
    return sorted(
        found.values(),
        key=lambda item: (item["year_hint"] or 0, item["month_hint"] or 0, item["filename"]),
    )


def parse_month_ended(frame: pd.DataFrame) -> tuple[int, int]:
    for value in frame.iloc[:, 0].tolist()[:8]:
        if pd.isna(value):
            continue
        match = MONTH_ENDED_RE.search(str(value))
        if match:
            month_name = match.group(1).title()
            year = int(match.group(3))
            month = pd.Timestamp(f"1 {month_name} {year}").month
            return year, month
    raise ValueError("Could not parse MONTH ENDED from Missouri workbook")


def _to_month_start(value) -> pd.Timestamp | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    # xlrd may return Excel serial dates as floats.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value > 20000:  # plausible Excel serial
            try:
                import xlrd  # type: ignore

                date_tuple = xlrd.xldate_as_tuple(float(value), datemode=0)
                return pd.Timestamp(
                    year=date_tuple[0], month=date_tuple[1], day=1
                )
            except Exception:  # noqa: BLE001
                pass
        # Avoid treating wagers counts as dates.
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts).normalize().replace(day=1)


def parse_mobile_monthly_financials(
    content: bytes | Path,
    *,
    filename: str = "workbook.xlsx",
    report_month_only: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """
    Parse MONTHLY STATS MOBILE sheet.

    TAXABLE AGR -> taxable_revenue
    HANDLE -> handle
    WAGERING TAX -> tax
    """
    if isinstance(content, Path):
        filename = content.name
        content = content.read_bytes()

    _excel_file, sheets = _read_excel_frames(content, filename)
    sheet_name = next(
        (name for name in sheets if name.strip().upper() in MOBILE_SHEET_NAMES),
        next(
            (
                name
                for name in sheets
                if "mobile" in name.casefold() and "monthly" in name.casefold()
            ),
            None,
        ),
    )
    if sheet_name is None:
        raise ValueError(f"No MOBILE monthly sheet in {filename}; sheets={list(sheets)}")

    frame = sheets[sheet_name]
    report_year, report_month = parse_month_ended(frame)

    # Locate header row with LICENSEE / HANDLE / TAXABLE AGR
    header_row = None
    for idx in range(min(15, len(frame))):
        texts = [
            str(v).strip().casefold()
            for v in frame.iloc[idx].tolist()
            if pd.notna(v) and str(v).strip() != ""
        ]
        if "licensee" in texts and any("taxable" in t for t in texts) and any(
            t == "handle" or t.startswith("handle") for t in texts
        ):
            header_row = idx
            break
    if header_row is None:
        raise ValueError("Could not find LICENSEE/HANDLE/TAXABLE AGR header row")

    header = frame.iloc[header_row]
    col_map: dict[str, int] = {}
    for col_idx, value in header.items():
        if pd.isna(value) or str(value).strip() == "":
            continue
        label = str(value).strip().casefold()
        if label == "licensee":
            col_map["licensee"] = int(col_idx)
        elif label in {"mo/yr", "month", "mo / yr"}:
            col_map["month"] = int(col_idx)
        elif label == "handle" or label.startswith("handle"):
            col_map["handle"] = int(col_idx)
        elif "taxable" in label and "agr" in label:
            col_map["taxable"] = int(col_idx)
        elif "wagering tax" in label or label == "tax":
            col_map["tax"] = int(col_idx)

    # Some workbooks split headers across two rows (NUMBER OF / LICENSEE).
    if "handle" not in col_map or "taxable" not in col_map:
        # Use positional defaults from published layout: 0 licensee, 1 mo/yr, 3 handle, 5 taxable, 6 tax
        col_map.setdefault("licensee", 0)
        col_map.setdefault("month", 1)
        col_map.setdefault("handle", 3)
        col_map.setdefault("taxable", 5)
        col_map.setdefault("tax", 6)

    records: list[dict] = []
    current_operator: str | None = None

    for row_idx in range(header_row + 1, len(frame)):
        row = frame.iloc[row_idx]
        raw_licensee = row.iloc[col_map.get("licensee", 0)] if len(row) else None
        licensee = "" if pd.isna(raw_licensee) else str(raw_licensee).strip()

        if licensee.upper().startswith("NOTE"):
            break

        month_val = row.iloc[col_map["month"]] if "month" in col_map and len(row) > col_map["month"] else None
        month_start = _to_month_start(month_val)

        # Carry forward operator when subsequent month rows leave licensee blank.
        if licensee and "MOBILE" in licensee.upper() and "TOTAL" not in licensee.upper():
            current_operator = re.sub(r"\s+", " ", licensee).strip()
            if month_start is None:
                continue
        elif licensee.upper().startswith("STATE TOTALS MTD"):
            # Statewide total rows omit MO/YR: LICENSEE, WAGERS, HANDLE, DEDUCTIONS, TAXABLE AGR, TAX
            year, month = report_year, report_month
            handle_col = 2
            taxable_col = 4
            tax_col = 5
            records.append(
                {
                    "operator": "STATEWIDE",
                    "row_type": "official_statewide_total",
                    "year": year,
                    "month": month,
                    "handle": parse_money(row.iloc[handle_col] if len(row) > handle_col else None),
                    "taxable_revenue": parse_money(
                        row.iloc[taxable_col] if len(row) > taxable_col else None
                    ),
                    "tax": parse_money(row.iloc[tax_col] if len(row) > tax_col else None),
                }
            )
            continue
        elif licensee and "TOTAL" in licensee.upper():
            continue
        elif not licensee and current_operator and month_start is not None:
            pass
        else:
            continue

        if current_operator is None or month_start is None:
            continue

        year, month = int(month_start.year), int(month_start.month)
        if report_month_only and (year, month) != (report_year, report_month):
            continue

        records.append(
            {
                "operator": current_operator,
                "row_type": "operator",
                "year": year,
                "month": month,
                "handle": parse_money(row.iloc[col_map["handle"]]),
                "taxable_revenue": parse_money(row.iloc[col_map["taxable"]]),
                "tax": parse_money(row.iloc[col_map["tax"]]),
            }
        )

    if not records:
        raise ValueError(f"No MOBILE rows parsed for report month {report_year}-{report_month:02d}")

    meta = {
        "sheet_name": sheet_name,
        "report_year": report_year,
        "report_month": report_month,
    }
    return pd.DataFrame(records), meta


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
                "gross_revenue": None,
                "adjusted_revenue": None,
                "taxable_revenue": record.get("taxable_revenue"),
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


def collect_workbook(
    link: dict,
    *,
    root: Path | None = None,
    session: requests.Session | None = None,
    retrieved_at: datetime | None = None,
) -> pd.DataFrame:
    root = root or project_root()
    retrieved_at = retrieved_at or utc_now()
    sess = session or requests.Session()

    response = http_get(link["url"], session=sess)
    content = response.content
    filename = link.get("filename") or "mo_monthly_financials.xlsx"
    path = save_raw_bytes(root, STATE_CODE, content, filename, retrieved_at=retrieved_at)
    parsed, _meta = parse_mobile_monthly_financials(
        content,
        filename=filename,
        report_month_only=True,
    )
    rel = str(path.relative_to(root)).replace("\\", "/")
    return build_normalized_rows(
        parsed,
        source_url=response.url or link["url"],
        source_file=rel,
        source_sha256=sha256_bytes(content),
        retrieved_at=retrieved_at,
    )


def collect_history(
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Discover Monthly Financials Excel files, parse MOBILE rows, upsert."""
    root = root or project_root()
    retrieved_at = utc_now()
    sess = session or requests.Session()

    html = http_get(LANDING_URL, session=sess).text
    links = discover_monthly_financial_links(html)
    if not links:
        raise RuntimeError(f"No Missouri Monthly Financials Excel links on {LANDING_URL}")

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
            frame = collect_workbook(
                link,
                root=root,
                session=sess,
                retrieved_at=retrieved_at,
            )
            upsert_gaming_results(connection, frame)
            all_frames.append(frame)
            downloaded += 1
            print(f"OK {frame['period_start'].iloc[0]}: {len(frame)} rows ({label})")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{label}: {exc}")
            print(f"SKIP {label}: {exc}")

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
                    else "Collected MO MOBILE TAXABLE AGR + HANDLE + WAGERING TAX"
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
            "reason": "; ".join(failures) or "No Missouri workbooks collected",
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
