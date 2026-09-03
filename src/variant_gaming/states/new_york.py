"""New York Gaming Commission weekly online sports-wagering collectors (Handle + GGR)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

from variant_gaming.common import (
    http_get,
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

LANDING_URL = "https://gaming.ny.gov/revenue-reports"
CENTS = Decimal("0.01")
REPORTED_REVENUE_NAME = "GGR"


# --- Landing-page discovery -------------------------------------------------


def is_statewide_sports_weekly_excel(link_text: str) -> bool:
    """Visible-text test: statewide + sports wagering + weekly + excel."""
    text = " ".join(link_text.split()).casefold()
    return (
        "statewide" in text
        and "sports wagering" in text
        and "weekly" in text
        and "excel" in text
    )


def is_weekly_excel_link(link_text: str) -> bool:
    text = " ".join(link_text.split()).casefold()
    return "weekly" in text and "excel" in text


def discover_ny_sports_workbook_links(
    html: str, landing_url: str = LANDING_URL
) -> dict:
    """
    Find statewide + operator weekly Excel links under the Sports Wagering section.
    Returns {"statewide": {url, link_text}, "operators": [{name, url, link_text}, ...]}.
    """
    soup = BeautifulSoup(html, "html.parser")

    statewide_matches: list[tuple[str, str]] = []
    operators: list[dict[str, str]] = []

    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        row_name = cells[0].get_text(" ", strip=True).strip()
        if not row_name or row_name.casefold() == "name":
            continue

        previous_h2 = row.find_previous("h2")
        if previous_h2 is None:
            continue
        if previous_h2.get_text(" ", strip=True).casefold() != "sports wagering":
            continue

        if row_name.casefold() == "statewide":
            for anchor in row.find_all("a", href=True):
                visible = " ".join(anchor.get_text(" ", strip=True).split())
                if is_statewide_sports_weekly_excel(visible):
                    absolute_url = urljoin(landing_url, anchor["href"])
                    statewide_matches.append((visible, absolute_url))
            continue

        weekly_excel_links: list[tuple[str, str]] = []
        for anchor in row.find_all("a", href=True):
            visible = " ".join(anchor.get_text(" ", strip=True).split())
            if is_weekly_excel_link(visible):
                weekly_excel_links.append((visible, urljoin(landing_url, anchor["href"])))

        if len(weekly_excel_links) != 1:
            raise RuntimeError(
                f"Expected exactly one weekly Excel link for operator {row_name!r}; "
                f"found {len(weekly_excel_links)}: {weekly_excel_links}"
            )
        link_text, discovered_url = weekly_excel_links[0]
        operators.append(
            {
                "source_operator_name": row_name,
                "link_visible_text": link_text,
                "discovered_url": discovered_url,
            }
        )

    if len(statewide_matches) != 1:
        raise RuntimeError(
            "Expected exactly one Statewide Sports Wagering Weekly Excel link; "
            f"found {len(statewide_matches)}: {statewide_matches}"
        )

    names = [op["source_operator_name"] for op in operators]
    if len(names) != len(set(names)):
        raise RuntimeError(f"Duplicate operator names discovered: {names}")

    link_text, url = statewide_matches[0]
    return {
        "statewide": {"link_visible_text": link_text, "discovered_url": url},
        "operators": operators,
    }


def operator_slug(source_operator_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", source_operator_name.casefold()).strip("-")
    if not slug:
        raise ValueError(f"Could not derive slug from {source_operator_name!r}")
    return slug


# --- Excel cell helpers (same shape for statewide and operator workbooks) ---


def to_week_ending_date(value) -> pd.Timestamp | None:
    if pd.isna(value) or value is None or value == "":
        return None
    timestamp = pd.to_datetime(value, errors="raise")
    return pd.Timestamp(timestamp).normalize()


def to_money_decimal(value) -> Decimal | None:
    """Convert a cell to Decimal dollars quantized to cents. Negatives allowed."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, Decimal):
        return value.quantize(CENTS, rounding=ROUND_HALF_UP)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Decimal(str(value)).quantize(CENTS, rounding=ROUND_HALF_UP)
    text = str(value).strip()
    if text == "" or text.casefold() in {"nan", "nat", "none"}:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    text = text.replace("$", "").replace(",", "").strip()
    try:
        return Decimal(text).quantize(CENTS, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError(f"Cannot parse money value {value!r}") from exc


def find_header_row(frame: pd.DataFrame) -> int:
    for row_index, row in frame.iterrows():
        texts = [str(v).strip().casefold() for v in row.tolist() if pd.notna(v)]
        if (
            any(t == "week-ending" for t in texts)
            and any(t == "handle" for t in texts)
            and any(t == "ggr" for t in texts)
        ):
            return int(row_index)
    raise ValueError("Could not find a header row with Week-Ending, Handle, and GGR.")


def column_index_for_label(header_row: pd.Series, label: str) -> int:
    target = label.casefold()
    for column_index, value in header_row.items():
        if pd.notna(value) and str(value).strip().casefold() == target:
            return int(column_index)
    raise ValueError(f"Header row is missing column label {label!r}: {header_row.tolist()}")


def find_total_row(frame: pd.DataFrame, start_row: int) -> int:
    for row_index in range(start_row, len(frame)):
        texts = [
            str(v).strip().casefold()
            for v in frame.loc[row_index].tolist()
            if pd.notna(v)
        ]
        if "total" in texts:
            return row_index
    raise ValueError("Could not find a Total row after the header.")


def parse_fiscal_year_label(sheet_name: str, frame: pd.DataFrame) -> str:
    """Prefer an explicit 'Fiscal Year YYYY/YYYY' title; else use the sheet name."""
    for value in frame.iloc[:, 0].tolist():
        if pd.isna(value):
            continue
        text = str(value).strip()
        if text.casefold().startswith("fiscal year"):
            return text.replace("Fiscal Year", "FY").strip()
    return sheet_name.strip()


def quantized_sum_from_raw(values) -> Decimal:
    """Sum raw numeric cells, then quantize once — mirrors Excel Total intent."""
    total = Decimal("0")
    for value in values:
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except TypeError:
            pass
        total += Decimal(str(value))
    return total.quantize(CENTS, rounding=ROUND_HALF_UP)


def week_period(week_ending) -> tuple[str, str]:
    """Keep weeks as weeks: period_end = week-ending date; period_start = 6 days earlier."""
    end = pd.Timestamp(week_ending).normalize().date()
    start = end - timedelta(days=6)
    return start.isoformat(), end.isoformat()


# --- Sheet / workbook parsers -----------------------------------------------


def parse_fy_sheet(
    sheet_name: str,
    frame: pd.DataFrame,
    *,
    skip_zero_placeholders: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """
    Parse one fiscal-year sheet into completed weekly rows + reconciliation metadata.
    Columns: fiscal_year, week_ending, handle_usd, ggr_usd (Decimal).
    """
    header_row = find_header_row(frame)
    header = frame.loc[header_row]
    week_col = column_index_for_label(header, "Week-Ending")
    handle_col = column_index_for_label(header, "Handle")
    ggr_col = column_index_for_label(header, "GGR")
    total_row = find_total_row(frame, header_row + 1)

    body = frame.loc[header_row + 1 : total_row - 1]
    total_series = frame.loc[total_row]
    fiscal_year = parse_fiscal_year_label(sheet_name, frame)

    records = []
    raw_handles = []
    raw_ggrs = []

    for _, row in body.iterrows():
        week_ending = to_week_ending_date(row.iloc[week_col])
        handle_raw = row.iloc[handle_col]
        ggr_raw = row.iloc[ggr_col]
        handle = to_money_decimal(handle_raw)
        ggr = to_money_decimal(ggr_raw)

        if week_ending is not None and handle is None and ggr is None:
            continue
        if skip_zero_placeholders and week_ending is not None and handle is None and ggr == Decimal("0.00"):
            continue
        if week_ending is None and handle is None and ggr is None:
            continue
        if week_ending is None or handle is None or ggr is None:
            raise ValueError(
                f"Malformed row in {sheet_name!r}: "
                f"week_ending={week_ending}, handle={handle}, ggr={ggr}, raw={row.tolist()}"
            )
        if skip_zero_placeholders and handle == Decimal("0.00") and ggr == Decimal("0.00"):
            continue
        if handle <= Decimal("0.00"):
            raise ValueError(
                f"Handle must be positive in {sheet_name!r} for {week_ending.date()}: {handle}"
            )

        records.append(
            {
                "fiscal_year": fiscal_year,
                "week_ending": week_ending.date(),
                "handle_usd": handle,
                "ggr_usd": ggr,
            }
        )
        raw_handles.append(handle_raw)
        raw_ggrs.append(ggr_raw)

    reported_handle = to_money_decimal(total_series.iloc[handle_col])
    reported_ggr = to_money_decimal(total_series.iloc[ggr_col])
    if reported_handle is None or reported_ggr is None:
        raise ValueError(f"Total row missing handle/GGR on sheet {sheet_name!r}")

    meta = {
        "sheet_name": sheet_name,
        "fiscal_year": fiscal_year,
        "completed_weeks": len(records),
        "reported_handle_total": reported_handle,
        "reported_ggr_total": reported_ggr,
        "reconciled_handle_total": quantized_sum_from_raw(raw_handles),
        "reconciled_ggr_total": quantized_sum_from_raw(raw_ggrs),
    }
    if meta["reconciled_handle_total"] != meta["reported_handle_total"]:
        raise ValueError(
            f"Handle total mismatch on {sheet_name!r}: "
            f"{meta['reconciled_handle_total']} != {meta['reported_handle_total']}"
        )
    if meta["reconciled_ggr_total"] != meta["reported_ggr_total"]:
        raise ValueError(
            f"GGR total mismatch on {sheet_name!r}: "
            f"{meta['reconciled_ggr_total']} != {meta['reported_ggr_total']}"
        )

    return pd.DataFrame.from_records(records), meta


def parse_ny_workbook(
    content: bytes | Path,
    *,
    skip_zero_placeholders: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Parse all FY sheets in a NY weekly sports Excel workbook.
    Returns (weekly_rows, reconciliation_by_sheet).
    """
    if isinstance(content, Path):
        excel_file = pd.ExcelFile(content)
    else:
        excel_file = pd.ExcelFile(BytesIO(content))

    weekly_frames: list[pd.DataFrame] = []
    reconciliation_rows: list[dict] = []

    for sheet_name in excel_file.sheet_names:
        frame = pd.read_excel(excel_file, sheet_name=sheet_name, header=None)
        weekly, meta = parse_fy_sheet(
            sheet_name, frame, skip_zero_placeholders=skip_zero_placeholders
        )
        if not weekly.empty:
            weekly_frames.append(weekly)
        reconciliation_rows.append(meta)

    if not weekly_frames:
        raise ValueError("No weekly rows parsed from workbook")

    weekly = pd.concat(weekly_frames, ignore_index=True)
    weekly = weekly.sort_values(["week_ending", "fiscal_year"]).reset_index(drop=True)
    # One economic week can appear on only one FY sheet for storage; keep first.
    weekly = weekly.drop_duplicates(subset=["week_ending"], keep="first").reset_index(drop=True)
    reconciliation = pd.DataFrame(reconciliation_rows)
    return weekly, reconciliation


# --- Normalize into gaming_results shape ------------------------------------


def _empty_money_cols(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.assign(
        adjusted_revenue=pd.NA,
        taxable_revenue=pd.NA,
        net_proceeds=pd.NA,
        tax=pd.NA,
    )


def build_normalized_rows(
    weekly: pd.DataFrame,
    *,
    operator: str,
    row_type: str,
    source_url: str,
    source_file: str,
    source_sha256: str,
    retrieved_at: datetime,
) -> pd.DataFrame:
    """Map parsed Handle/GGR weeks into storage columns. GGR -> gross_revenue."""
    if weekly.empty:
        return pd.DataFrame()

    periods = weekly["week_ending"].map(week_period)
    rows = pd.DataFrame(
        {
            "jurisdiction": "New York",
            "state_code": "NY",
            "vertical": "online_sports_betting",
            "channel": "online",
            "operator": operator,
            "row_type": row_type,
            "period_start": [p[0] for p in periods],
            "period_end": [p[1] for p in periods],
            "frequency": "weekly",
            "handle": weekly["handle_usd"].map(float).values,
            "gross_revenue": weekly["ggr_usd"].map(float).values,
            "reported_revenue_name": REPORTED_REVENUE_NAME,
            "source_url": source_url,
            "source_file": source_file,
            "source_sha256": source_sha256,
            "retrieved_at_utc": retrieved_at.isoformat(),
            "report_status": "ok",
        }
    )
    return _empty_money_cols(rows)


def download_workbook(url: str, session: requests.Session | None = None) -> tuple[bytes, str]:
    """GET a workbook URL (may redirect). Returns (bytes, final_url)."""
    response = http_get(url, session=session)
    content = response.content
    if not content:
        raise RuntimeError(f"Empty workbook download from {url}")
    # XLSX is a ZIP; reject obvious HTML error pages.
    if not content.startswith(b"PK"):
        raise RuntimeError(f"Workbook from {url} is not an XLSX/ZIP signature")
    return content, response.url


# --- Collect ----------------------------------------------------------------


def collect_history(
    root: Path | None = None,
    db_path: Path | None = None,
    include_operators: bool = True,
) -> pd.DataFrame:
    """
    Discover NY weekly Excel links, download statewide (+ operators), parse, upsert.
    Each workbook already contains the full published weekly history — no month loop.
    """
    root = root or project_root()
    retrieved_at = utc_now()
    session = requests.Session()

    landing = http_get(LANDING_URL, session=session)
    discovery = discover_ny_sports_workbook_links(landing.text, LANDING_URL)

    db_path = db_path or default_db_path(root)
    connection = connect(db_path)
    migrate_legacy_table(connection)
    ensure_schema(connection)

    all_frames: list[pd.DataFrame] = []
    downloaded = 0
    failures: list[str] = []

    # --- Statewide ---
    sw = discovery["statewide"]
    try:
        content, final_url = download_workbook(sw["discovered_url"], session=session)
        path = save_raw_bytes(
            root,
            "NY",
            content,
            "ny_statewide_sports_wagering_weekly.xlsx",
            retrieved_at=retrieved_at,
        )
        digest = sha256_bytes(content)
        weekly, _meta = parse_ny_workbook(content, skip_zero_placeholders=False)
        rel = str(path.relative_to(root)).replace("\\", "/")
        normalized = build_normalized_rows(
            weekly,
            operator="STATEWIDE",
            row_type="official_statewide_total",
            source_url=final_url or sw["discovered_url"],
            source_file=rel,
            source_sha256=digest,
            retrieved_at=retrieved_at,
        )
        upsert_gaming_results(connection, normalized)
        all_frames.append(normalized)
        downloaded += 1
        print(f"OK STATEWIDE: {len(normalized)} weekly rows -> {path.name}")
    except Exception as exc:  # noqa: BLE001 - still try operators
        failures.append(f"STATEWIDE: {exc}")
        print(f"FAIL STATEWIDE: {exc}")

    # --- Operators ---
    if include_operators:
        for item in discovery["operators"]:
            name = item["source_operator_name"]
            slug = operator_slug(name)
            try:
                content, final_url = download_workbook(item["discovered_url"], session=session)
                path = save_raw_bytes(
                    root,
                    "NY",
                    content,
                    f"ny_{slug}_sports_wagering_weekly.xlsx",
                    retrieved_at=retrieved_at,
                )
                digest = sha256_bytes(content)
                weekly, _meta = parse_ny_workbook(content, skip_zero_placeholders=True)
                rel = str(path.relative_to(root)).replace("\\", "/")
                normalized = build_normalized_rows(
                    weekly,
                    operator=name,
                    row_type="operator",
                    source_url=final_url or item["discovered_url"],
                    source_file=rel,
                    source_sha256=digest,
                    retrieved_at=retrieved_at,
                )
                upsert_gaming_results(connection, normalized)
                all_frames.append(normalized)
                downloaded += 1
                print(f"OK {name}: {len(normalized)} weekly rows -> {path.name}")
            except Exception as exc:  # noqa: BLE001 - continue other operators
                failures.append(f"{name}: {exc}")
                print(f"SKIP {name}: {exc}")

    if all_frames:
        result = pd.concat(all_frames, ignore_index=True)
        upsert_coverage(
            connection,
            {
                "state_code": "NY",
                "vertical": "online_sports_betting",
                "status": "ok" if not failures else "partial",
                "reason": (
                    "; ".join(failures[:5])
                    if failures
                    else "Collected statewide + operator weekly Handle/GGR (online)"
                ),
                "official_url": LANDING_URL,
                "available_frequency": "weekly",
                "earliest_period": result["period_start"].min(),
                "latest_period": result["period_end"].max(),
                "downloaded_file_count": downloaded,
                "normalized_row_count": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM gaming_results "
                        "WHERE state_code='NY' AND vertical='online_sports_betting'"
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
            "state_code": "NY",
            "vertical": "online_sports_betting",
            "status": "failed",
            "reason": "; ".join(failures) or "No NY workbooks collected",
            "official_url": LANDING_URL,
            "available_frequency": "weekly",
            "earliest_period": None,
            "latest_period": None,
            "downloaded_file_count": 0,
            "normalized_row_count": 0,
            "last_retrieval_utc": utc_now().isoformat(),
        },
    )
    connection.close()
    return pd.DataFrame()
