"""Michigan MGCB internet sports betting and internet gaming collectors."""

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

LANDING_URL = (
    "https://www.michigan.gov/mgcb/detroit-casinos/resources/"
    "revenues-and-wagering-tax-information"
)
STATE_CODE = "MI"
JURISDICTION = "Michigan"

SPORTS_VERTICAL = "online_sports_betting"
CASINO_VERTICAL = "online_casino"
SPORTS_REPORTED_REVENUE_NAME = "Adjusted Gross"
CASINO_REPORTED_REVENUE_NAME = "Adjusted Gross"

NOTE_SUFFIX_RE = re.compile(r"\s*NOTE\s*\d+\s*$", re.IGNORECASE)
YEAR_IN_TEXT_RE = re.compile(r"(20\d{2})")


def _clean_name(value) -> str:
    text = " ".join(str(value).split()).strip()
    text = NOTE_SUFFIX_RE.sub("", text).strip()
    return text


def _norm_header(value) -> str:
    text = " ".join(str(value).replace("\n", " ").split()).strip().casefold()
    return re.sub(r"\s+", " ", text)


def discover_michigan_workbook_links(
    html: str,
    *,
    vertical: str,
    landing_url: str = LANDING_URL,
) -> list[dict]:
    """
    Discover annual Excel workbooks for internet sports betting or internet gaming.

    vertical: online_sports_betting | online_casino
    """
    if vertical == SPORTS_VERTICAL:
        needles = ("internet sports betting", "internet-sports-betting")
    elif vertical == CASINO_VERTICAL:
        needles = ("internet gaming", "internet-gaming")
    else:
        raise ValueError(f"Unsupported Michigan vertical: {vertical}")

    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        href = anchor["href"].strip()
        absolute = urljoin(landing_url, href)
        blob = f"{text} {absolute}".casefold()
        if not any(n in blob for n in needles):
            continue
        if "excel" not in text.casefold() and ".xls" not in absolute.casefold():
            continue
        # Exclude retail sports betting workbooks.
        if "retail" in blob:
            continue
        year_hint = None
        year_match = YEAR_IN_TEXT_RE.search(text) or YEAR_IN_TEXT_RE.search(absolute)
        if year_match:
            year_hint = int(year_match.group(1))
        filename = unquote(absolute.rsplit("/", 1)[-1].split("?", 1)[0])
        found.setdefault(
            absolute,
            {
                "url": absolute,
                "link_text": text,
                "filename": filename,
                "year_hint": year_hint,
                "vertical": vertical,
            },
        )
    return sorted(
        found.values(),
        key=lambda item: (item["year_hint"] or 0, item["filename"].lower()),
    )


def _header_row_index(frame: pd.DataFrame) -> int:
    for idx in range(min(12, len(frame))):
        values = [_norm_header(v) for v in frame.iloc[idx].tolist() if pd.notna(v)]
        if not values:
            continue
        joined = " | ".join(values)
        if "month" in values[0] or values[0] == "month":
            if any("adjusted gross" in v for v in values) or any(
                "gross" in v and "receipt" in v for v in values
            ):
                return idx
        if "total handle" in joined and "adjusted gross" in joined:
            return idx
    raise ValueError("Could not locate Michigan metric header row")


def _operator_row_index(frame: pd.DataFrame, header_row: int) -> int:
    for idx in range(header_row):
        first = _norm_header(frame.iloc[idx, 0]) if pd.notna(frame.iloc[idx, 0]) else ""
        if first in {"operators", "operator"}:
            return idx
    # Fall back to first non-empty label row above header.
    for idx in range(header_row):
        if frame.iloc[idx].notna().sum() > 3:
            return idx
    raise ValueError("Could not locate Michigan operators row")


def _platform_row_index(frame: pd.DataFrame, header_row: int) -> int | None:
    for idx in range(header_row):
        first = _norm_header(frame.iloc[idx, 0]) if pd.notna(frame.iloc[idx, 0]) else ""
        if "platform" in first:
            return idx
    return None


def _classify_metric(header: str) -> str | None:
    h = _norm_header(header)
    if not h or h == "month":
        return None
    if "city wagering" in h or "municipal" in h or "governing body" in h:
        return None
    if "total handle" == h or h == "handle":
        return "handle"
    if "adjusted gross" in h:
        return "adjusted_revenue"
    if "gross" in h and "receipt" in h:
        return "gross_revenue"
    if "tax" in h or "payment" in h:
        return "tax"
    return None


def _is_statewide_operator(name: str) -> bool:
    text = name.casefold()
    return text.startswith("all internet") or text in {
        "all operators",
        "statewide",
        "all internet sports betting operators",
        "all internet gaming operators",
    }


def _sheet_year(sheet_name: str, year_hint: int | None) -> int | None:
    match = YEAR_IN_TEXT_RE.search(sheet_name)
    if match:
        return int(match.group(1))
    return year_hint


def parse_michigan_wide_sheet(
    frame: pd.DataFrame,
    *,
    vertical: str,
    sheet_name: str,
    year_hint: int | None = None,
) -> pd.DataFrame:
    """
    Reshape one wide Michigan annual sheet into long monthly operator rows.

    Sports: Handle / Gross Receipts / Adjusted Gross / tax
    Gaming: Gross Receipts / Adjusted Gross / tax (no handle)
    """
    year = _sheet_year(sheet_name, year_hint)
    if year is None:
        raise ValueError(f"Could not resolve calendar year for sheet {sheet_name!r}")

    header_row = _header_row_index(frame)
    operator_row = _operator_row_index(frame, header_row)
    platform_row = _platform_row_index(frame, header_row)
    headers = frame.iloc[header_row]
    operators = frame.iloc[operator_row]
    platforms = frame.iloc[platform_row] if platform_row is not None else None

    # Build operator blocks: contiguous columns belonging to one operator.
    blocks: list[dict] = []
    col = 1
    while col < frame.shape[1]:
        header = headers.iloc[col] if col < len(headers) else None
        metric = _classify_metric(header) if pd.notna(header) else None
        if metric is None:
            col += 1
            continue

        op_name = ""
        if pd.notna(operators.iloc[col]):
            op_name = _clean_name(operators.iloc[col])
        platform_name = ""
        if platforms is not None and pd.notna(platforms.iloc[col]):
            platform_name = _clean_name(platforms.iloc[col])

        # Walk forward while metrics continue and no new operator label appears.
        metrics: dict[str, int] = {metric: col}
        end = col + 1
        while end < frame.shape[1]:
            next_header = headers.iloc[end]
            next_metric = _classify_metric(next_header) if pd.notna(next_header) else None
            if next_metric is None:
                break
            if pd.notna(operators.iloc[end]) and _clean_name(operators.iloc[end]):
                # New operator block starts.
                break
            # Avoid swallowing the next block's first metric if operator name is sparse.
            if next_metric in metrics:
                break
            metrics[next_metric] = end
            end += 1

        if not op_name and platform_name:
            op_name = platform_name
        if not op_name:
            col = end
            continue

        display_name = platform_name or op_name
        if _is_statewide_operator(op_name) or _is_statewide_operator(display_name):
            row_type = "official_statewide_total"
            operator = "STATEWIDE"
        else:
            row_type = "operator"
            # Prefer platform/brand when present; keep licensed operator in parentheses.
            if platform_name and platform_name.casefold() != op_name.casefold():
                operator = f"{platform_name} ({op_name})"
            else:
                operator = display_name or op_name

        blocks.append(
            {
                "operator": operator,
                "row_type": row_type,
                "metrics": metrics,
            }
        )
        col = end

    if not blocks:
        raise ValueError(f"No operator blocks parsed on sheet {sheet_name!r}")

    records: list[dict] = []
    for row_idx in range(header_row + 1, len(frame)):
        month_label = frame.iloc[row_idx, 0]
        if pd.isna(month_label):
            continue
        month_text = str(month_label).strip()
        if month_text.casefold() in {"total", "nan"} or month_text.casefold().startswith("note"):
            continue
        try:
            month_num = month_name_to_num(month_text.title())
        except ValueError:
            continue

        for block in blocks:
            metrics = block["metrics"]
            handle = (
                parse_money(frame.iloc[row_idx, metrics["handle"]])
                if "handle" in metrics
                else None
            )
            gross = (
                parse_money(frame.iloc[row_idx, metrics["gross_revenue"]])
                if "gross_revenue" in metrics
                else None
            )
            adjusted = (
                parse_money(frame.iloc[row_idx, metrics["adjusted_revenue"]])
                if "adjusted_revenue" in metrics
                else None
            )
            tax = parse_money(frame.iloc[row_idx, metrics["tax"]]) if "tax" in metrics else None

            # Skip fully empty month cells (operator not yet live).
            if all(v is None for v in (handle, gross, adjusted, tax)):
                continue

            period_start, period_end = month_period(year, month_num)
            records.append(
                {
                    "operator": block["operator"],
                    "row_type": block["row_type"],
                    "period_start": period_start,
                    "period_end": period_end,
                    "handle": handle,
                    "gross_revenue": gross,
                    "adjusted_revenue": adjusted,
                    "tax": tax,
                    "month_name": MONTH_NAMES[month_num - 1],
                    "year": year,
                }
            )

    if not records:
        raise ValueError(f"No monthly rows parsed from sheet {sheet_name!r}")
    return pd.DataFrame(records)


def parse_michigan_workbook(
    content: bytes | Path,
    *,
    vertical: str,
    year_hint: int | None = None,
) -> pd.DataFrame:
    """Parse the sheet matching year_hint (or all year-named sheets) into long rows."""
    if isinstance(content, Path):
        excel_file = pd.ExcelFile(content)
    else:
        excel_file = pd.ExcelFile(BytesIO(content))

    frames: list[pd.DataFrame] = []
    for sheet_name in excel_file.sheet_names:
        sheet_year = _sheet_year(sheet_name, None)
        if year_hint is not None and sheet_year is not None and sheet_year != year_hint:
            continue
        frame = pd.read_excel(excel_file, sheet_name=sheet_name, header=None)
        try:
            parsed = parse_michigan_wide_sheet(
                frame,
                vertical=vertical,
                sheet_name=sheet_name,
                year_hint=year_hint or sheet_year,
            )
        except ValueError:
            continue
        frames.append(parsed)

    if not frames:
        raise ValueError("No Michigan sheets produced monthly rows")
    return pd.concat(frames, ignore_index=True)


def build_normalized_rows(
    parsed: pd.DataFrame,
    *,
    vertical: str,
    source_url: str,
    source_file: str,
    source_sha256: str,
    retrieved_at: datetime,
) -> pd.DataFrame:
    reported = (
        SPORTS_REPORTED_REVENUE_NAME
        if vertical == SPORTS_VERTICAL
        else CASINO_REPORTED_REVENUE_NAME
    )
    rows = parsed.copy()
    rows = rows.assign(
        jurisdiction=JURISDICTION,
        state_code=STATE_CODE,
        vertical=vertical,
        channel="online",
        frequency="monthly",
        taxable_revenue=pd.NA,
        net_proceeds=pd.NA,
        reported_revenue_name=reported,
        source_url=source_url,
        source_file=source_file,
        source_sha256=source_sha256,
        retrieved_at_utc=retrieved_at.isoformat(),
        report_status="ok",
    )
    return rows.drop(columns=[c for c in ("month_name", "year") if c in rows.columns])


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
    vertical = link["vertical"]

    response = http_get(link["url"], session=sess)
    content = response.content
    if not content.startswith(b"PK"):
        raise RuntimeError(f"Expected XLSX from {link['url']}")

    path = save_raw_bytes(
        root,
        STATE_CODE,
        content,
        link["filename"] or f"michigan_{vertical}.xlsx",
        retrieved_at=retrieved_at,
    )
    parsed = parse_michigan_workbook(
        content,
        vertical=vertical,
        year_hint=link.get("year_hint"),
    )
    rel = str(path.relative_to(root)).replace("\\", "/")
    return build_normalized_rows(
        parsed,
        vertical=vertical,
        source_url=response.url or link["url"],
        source_file=rel,
        source_sha256=sha256_bytes(content),
        retrieved_at=retrieved_at,
    )


def _collect_vertical_history(
    vertical: str,
    *,
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    root = root or project_root()
    retrieved_at = utc_now()
    sess = session or requests.Session()

    landing_html = http_get(LANDING_URL, session=sess).text
    links = discover_michigan_workbook_links(landing_html, vertical=vertical)
    if not links:
        raise RuntimeError(f"No Michigan workbook links found for {vertical}")

    db_path = db_path or default_db_path(root)
    connection = connect(db_path)
    migrate_legacy_table(connection)
    ensure_schema(connection)

    all_frames: list[pd.DataFrame] = []
    failures: list[str] = []
    downloaded = 0

    for link in links:
        label = link.get("link_text") or link["filename"]
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
            print(f"OK {vertical} {label}: {len(frame)} rows")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{label}: {exc}")
            print(f"SKIP {label}: {exc}")

    if all_frames:
        result = pd.concat(all_frames, ignore_index=True)
        result = result.sort_values(
            ["period_start", "row_type", "operator"]
        ).reset_index(drop=True)
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": vertical,
                "status": "ok" if not failures else "partial",
                "reason": (
                    "; ".join(failures[:5])
                    if failures
                    else f"Collected Michigan {vertical} monthly rows from annual Excel"
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
                        (STATE_CODE, vertical),
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
            "vertical": vertical,
            "status": "failed",
            "reason": "; ".join(failures) or "No Michigan workbooks collected",
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


def collect_history(
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
    vertical: str = SPORTS_VERTICAL,
) -> pd.DataFrame:
    """Collect one Michigan vertical (default: online_sports_betting)."""
    return _collect_vertical_history(
        vertical, root=root, db_path=db_path, session=session
    )


def collect_sports_history(**kwargs) -> pd.DataFrame:
    return collect_history(vertical=SPORTS_VERTICAL, **kwargs)


def collect_casino_history(**kwargs) -> pd.DataFrame:
    return collect_history(vertical=CASINO_VERTICAL, **kwargs)
