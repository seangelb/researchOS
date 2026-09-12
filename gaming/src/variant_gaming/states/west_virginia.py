"""West Virginia Lottery weekly sports-wagering (mobile) and iGaming collectors."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, unquote
from zipfile import ZipFile

import pandas as pd
import requests
from bs4 import BeautifulSoup

from variant_gaming.common import (
    http_block_reason,
    http_get,
    http_get_unchecked,
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

LANDING_URL = "https://business.wvlottery.com/resourcesPayments"
STATE_CODE = "WV"
JURISDICTION = "West Virginia"
SPORTS_VERTICAL = "online_sports_betting"
CASINO_VERTICAL = "online_casino"
SPORTS_REPORTED = "Total Taxable Receipts"
CASINO_REPORTED = "Revenue"

SPORTS_ZIP_HINTS = ("sports wagering weekly", "sports_wagering.zip", "sports wagering.zip")
IGAMING_ZIP_HINTS = ("igaming weekly", "i-gaming.zip", "i_gaming.zip", "igaming.zip")


def _cell_text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def _norm(text: str) -> str:
    return _cell_text(text).casefold()


def discover_weekly_zip_links(html: str, base_url: str = LANDING_URL) -> dict[str, dict]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        text = _cell_text(anchor.get_text(" ", strip=True))
        href = anchor["href"].strip()
        absolute = urljoin(base_url, href)
        blob = f"{text} {absolute}".casefold()
        if ".zip" not in blob:
            continue
        kind = None
        if any(h in blob for h in SPORTS_ZIP_HINTS) or (
            "sports" in blob and "wager" in blob and "weekly" in blob
        ):
            kind = "sports"
        elif any(h in blob for h in IGAMING_ZIP_HINTS) or (
            ("igaming" in blob or "i-gaming" in blob or "i_gaming" in blob) and "weekly" in blob
        ):
            kind = "igaming"
        if kind is None:
            continue
        filename = unquote(absolute.rsplit("/", 1)[-1].split("?", 1)[0])
        found[kind] = {
            "url": absolute,
            "filename": filename or f"wv_{kind}.zip",
            "link_text": text,
            "kind": kind,
        }
    return found


def _to_week_end(value) -> pd.Timestamp | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = _cell_text(value).replace("*", "").strip()
    if not text or text.casefold().startswith("fy") or text.casefold().startswith("fiscal"):
        return None
    ts = pd.to_datetime(text, errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts).normalize()


def _week_bounds(week_end: pd.Timestamp, asterisk: bool) -> tuple[str, str]:
    end = week_end.date()
    if asterisk:
        # WV FY starts July 1; starred first week is a short stub.
        start = end.replace(month=7, day=1) if end.month == 7 else end - timedelta(days=3)
        if start.month != 7:
            start = end - timedelta(days=3)
    else:
        start = (week_end - pd.Timedelta(days=6)).date()
    return start.isoformat(), end.isoformat()


def _metric_from_label(label: str) -> str | None:
    if "gross tickets" in label:
        return "handle"
    if "taxable" in label:
        return "taxable"
    if "privilege" in label:
        return "tax"
    return None


def _section_from_label(label: str) -> str | None:
    if label.startswith("mobile") or label == "mobile":
        return "mobile"
    if label.startswith("retail") or label == "retail":
        return "retail"
    if label.startswith("total") or label == "total":
        return "total"
    return None


def _header_positions(frame: pd.DataFrame) -> dict[str, dict[str, int]]:
    """
    Locate Retail / Mobile / Total blocks and metric columns on a sports sheet.

    FY21–FY22 use a two-row header (Retail | Mobile | Total, then Gross Tickets…).
    FY23+ collapse that into one row (Mobile Gross Tickets Written, Mobile Taxable Receipts).
    """
    # Combined single-row headers (FY23+).
    for idx in range(min(12, len(frame))):
        combined: dict[str, dict[str, int]] = {}
        for col, raw in enumerate(frame.iloc[idx].tolist()):
            label = _norm(raw)
            if not label:
                continue
            section = _section_from_label(label)
            metric = _metric_from_label(label)
            if section is None or metric is None:
                continue
            combined.setdefault(section, {})[metric] = col
        if combined.get("mobile", {}).get("taxable") is not None:
            return combined

    sections: dict[str, dict[str, int]] = {}
    section_row = None
    metric_row = None
    for idx in range(min(12, len(frame))):
        values = [_norm(v) for v in frame.iloc[idx].tolist()]
        if "mobile" in values or "retail" in values:
            section_row = idx
        if any("taxable" in v for v in values) or any("gross tickets" in v for v in values):
            metric_row = idx
            break
    if section_row is None or metric_row is None:
        return sections
    current = None
    for col in range(frame.shape[1]):
        section_label = _norm(frame.iloc[section_row, col])
        if section_label in {"retail", "mobile", "total"}:
            current = section_label
            sections.setdefault(current, {})
        metric = _norm(frame.iloc[metric_row, col]) if pd.notna(frame.iloc[metric_row, col]) else ""
        if current is None or not metric:
            continue
        mapped = _metric_from_label(metric)
        if mapped:
            sections[current][mapped] = col
    # Privilege tax sits in the Total block; do not attach combined tax to mobile.
    return sections


def parse_sports_mobile_sheet(frame: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
    sections = _header_positions(frame)
    mobile = sections.get("mobile")
    if not mobile or "taxable" not in mobile:
        return pd.DataFrame()
    handle_col = mobile.get("handle")
    taxable_col = mobile["taxable"]
    records: list[dict] = []
    start_row = 0
    for idx in range(min(12, len(frame))):
        if any("taxable" in _norm(v) for v in frame.iloc[idx].tolist()):
            start_row = idx + 1
            break
    is_total_sheet = _norm(sheet_name) == "total"
    for idx in range(start_row, len(frame)):
        raw = frame.iloc[idx, 0]
        asterisk = "*" in _cell_text(raw)
        week_end = _to_week_end(raw)
        if week_end is None:
            continue
        handle = parse_money(frame.iloc[idx, handle_col] if handle_col is not None else None)
        taxable = parse_money(frame.iloc[idx, taxable_col])
        if handle is None and taxable is None:
            continue
        period_start, period_end = _week_bounds(week_end, asterisk)
        records.append(
            {
                "operator": "STATEWIDE" if is_total_sheet else sheet_name.strip(),
                "row_type": "official_statewide_total" if is_total_sheet else "operator",
                "period_start": period_start,
                "period_end": period_end,
                "handle": handle,
                "taxable_revenue": taxable,
            }
        )
    return pd.DataFrame.from_records(records)


def parse_igaming_sheet(frame: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
    header_row = None
    cols: dict[str, int] = {}
    for idx in range(min(15, len(frame))):
        values = [_norm(v) for v in frame.iloc[idx].tolist()]
        if "wagers" in values and any("revenue" == v or v.startswith("revenue") for v in values):
            header_row = idx
            for col, label in enumerate(values):
                if label == "wagers":
                    cols["handle"] = col
                elif label == "revenue" or label.startswith("revenue"):
                    cols["revenue"] = col
                elif "privilege" in label:
                    cols["tax"] = col
            break
    if header_row is None or "revenue" not in cols:
        return pd.DataFrame()
    is_total_sheet = _norm(sheet_name) == "total"
    records: list[dict] = []
    for idx in range(header_row + 1, len(frame)):
        raw = frame.iloc[idx, 0]
        asterisk = "*" in _cell_text(raw)
        week_end = _to_week_end(raw)
        if week_end is None:
            continue
        handle = parse_money(frame.iloc[idx, cols["handle"]]) if "handle" in cols else None
        revenue = parse_money(frame.iloc[idx, cols["revenue"]])
        tax = parse_money(frame.iloc[idx, cols["tax"]]) if "tax" in cols else None
        if handle is None and revenue is None:
            continue
        period_start, period_end = _week_bounds(week_end, asterisk)
        records.append(
            {
                "operator": "STATEWIDE" if is_total_sheet else sheet_name.strip(),
                "row_type": "official_statewide_total" if is_total_sheet else "operator",
                "period_start": period_start,
                "period_end": period_end,
                "handle": handle,
                "gross_revenue": revenue,
                "tax": tax,
            }
        )
    return pd.DataFrame.from_records(records)


def parse_sports_workbook(content: bytes | Path) -> pd.DataFrame:
    excel = pd.ExcelFile(content if isinstance(content, Path) else BytesIO(content))
    frames = []
    for name in excel.sheet_names:
        frame = pd.read_excel(excel, sheet_name=name, header=None)
        parsed = parse_sports_mobile_sheet(frame, name)
        if not parsed.empty:
            frames.append(parsed)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def parse_igaming_workbook(content: bytes | Path) -> pd.DataFrame:
    excel = pd.ExcelFile(content if isinstance(content, Path) else BytesIO(content))
    frames = []
    for name in excel.sheet_names:
        frame = pd.read_excel(excel, sheet_name=name, header=None)
        parsed = parse_igaming_sheet(frame, name)
        if not parsed.empty:
            frames.append(parsed)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


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
    return parsed.assign(
        jurisdiction=JURISDICTION,
        state_code=STATE_CODE,
        vertical=SPORTS_VERTICAL,
        channel="online",
        frequency="weekly",
        gross_revenue=pd.NA,
        adjusted_revenue=pd.NA,
        net_proceeds=pd.NA,
        tax=pd.NA,
        reported_revenue_name=SPORTS_REPORTED,
        source_url=source_url,
        source_file=source_file,
        source_sha256=source_sha256,
        retrieved_at_utc=retrieved_at.isoformat(),
        report_status="ok",
    )


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
    return parsed.assign(
        jurisdiction=JURISDICTION,
        state_code=STATE_CODE,
        vertical=CASINO_VERTICAL,
        channel="online",
        frequency="weekly",
        adjusted_revenue=pd.NA,
        taxable_revenue=pd.NA,
        net_proceeds=pd.NA,
        reported_revenue_name=CASINO_REPORTED,
        source_url=source_url,
        source_file=source_file,
        source_sha256=source_sha256,
        retrieved_at_utc=retrieved_at.isoformat(),
        report_status="ok",
    )


def _parse_zip_members(content: bytes, kind: str) -> list[tuple[str, bytes, pd.DataFrame]]:
    results = []
    with ZipFile(BytesIO(content)) as zf:
        for name in zf.namelist():
            if name.endswith("/") or not name.casefold().endswith((".xlsx", ".xls")):
                continue
            data = zf.read(name)
            if kind == "sports":
                parsed = parse_sports_workbook(data)
            else:
                parsed = parse_igaming_workbook(data)
            results.append((name, data, parsed))
    return results


def _upsert_vertical(connection, frame, *, vertical, downloaded, failures, reason, official_url=LANDING_URL):
    upsert_coverage(
        connection,
        {
            "state_code": STATE_CODE,
            "vertical": vertical,
            "status": "ok" if (not frame.empty and not failures) else (
                "failed" if frame.empty else "partial"
            ),
            "reason": "; ".join(failures[:5]) if failures else reason,
            "official_url": official_url,
            "available_frequency": "weekly",
            "earliest_period": None if frame.empty else frame["period_start"].min(),
            "latest_period": None if frame.empty else frame["period_end"].max(),
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
        for vertical in verticals:
            _upsert_vertical(
                connection,
                pd.DataFrame(),
                vertical=vertical,
                downloaded=0,
                failures=[blocked],
                reason=blocked,
            )
        connection.close()
        print(f"BLOCKED WV: {blocked}")
        return pd.DataFrame()
    landing.raise_for_status()
    save_raw_bytes(root, STATE_CODE, landing.content, "wv_resources_payments.html", retrieved_at=retrieved_at)
    zips = discover_weekly_zip_links(landing.text, LANDING_URL)

    sports_frames: list[pd.DataFrame] = []
    casino_frames: list[pd.DataFrame] = []
    sports_failures: list[str] = []
    casino_failures: list[str] = []
    sports_downloaded = 0
    casino_downloaded = 0

    mapping = []
    if SPORTS_VERTICAL in verticals:
        mapping.append(("sports", SPORTS_VERTICAL))
    if CASINO_VERTICAL in verticals:
        mapping.append(("igaming", CASINO_VERTICAL))

    for kind, vertical in mapping:
        link = zips.get(kind)
        if link is None:
            msg = f"No {kind} weekly zip on {LANDING_URL}"
            if kind == "sports":
                sports_failures.append(msg)
            else:
                casino_failures.append(msg)
            print(f"SKIP WV {kind}: {msg}")
            continue
        try:
            response = http_get(link["url"], session=sess)
            content = response.content
            if not content.startswith(b"PK"):
                raise RuntimeError("Not a ZIP payload")
            zip_path = save_raw_bytes(
                root, STATE_CODE, content, link["filename"], retrieved_at=retrieved_at
            )
            members = _parse_zip_members(content, kind)
            for member_name, member_bytes, parsed in members:
                member_path = save_raw_bytes(
                    root,
                    STATE_CODE,
                    member_bytes,
                    Path(member_name).name,
                    retrieved_at=retrieved_at,
                )
                if kind == "sports":
                    sports_downloaded += 1
                else:
                    casino_downloaded += 1
                if parsed.empty:
                    msg = f"{member_name}: no weekly rows"
                    if kind == "sports":
                        sports_failures.append(msg)
                    else:
                        casino_failures.append(msg)
                    print(f"SKIP WV {kind} {Path(member_name).name}: no weekly rows")
                    continue
                rel = str(member_path.relative_to(root)).replace("\\", "/")
                if kind == "sports":
                    frame = build_sports_normalized(
                        parsed,
                        source_url=link["url"],
                        source_file=rel,
                        source_sha256=sha256_bytes(member_bytes),
                        retrieved_at=retrieved_at,
                    )
                    upsert_gaming_results(connection, frame)
                    sports_frames.append(frame)
                    print(f"OK WV sports {Path(member_name).name}: {len(frame)} rows")
                else:
                    frame = build_casino_normalized(
                        parsed,
                        source_url=link["url"],
                        source_file=rel,
                        source_sha256=sha256_bytes(member_bytes),
                        retrieved_at=retrieved_at,
                    )
                    upsert_gaming_results(connection, frame)
                    casino_frames.append(frame)
                    print(f"OK WV iGaming {Path(member_name).name}: {len(frame)} rows")
            _ = zip_path
        except Exception as exc:  # noqa: BLE001
            msg = f"{link['url']}: {exc}"
            if kind == "sports":
                sports_failures.append(msg)
            else:
                casino_failures.append(msg)
            print(f"SKIP WV {kind}: {exc}")

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
            reason="Collected WV weekly Mobile Total Taxable Receipts (retail excluded)",
        )
    if CASINO_VERTICAL in verticals:
        _upsert_vertical(
            connection,
            casino_result,
            vertical=CASINO_VERTICAL,
            downloaded=casino_downloaded,
            failures=casino_failures,
            reason="Collected WV weekly iGaming Revenue (online only)",
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
