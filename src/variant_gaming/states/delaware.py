"""Delaware Lottery sports-lottery and iGaming monthly net-proceeds collectors."""

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

SPORTS_LANDING_URL = "https://delottery.com/Sports-Lottery/Monthly-Net-Proceeds"
IGAMING_LANDING_URL = "https://www.delottery.com/More/iGaming/Monthly-Net-Proceeds"
STATE_CODE = "DE"
JURISDICTION = "Delaware"
SPORTS_VERTICAL = "online_sports_betting"
CASINO_VERTICAL = "online_casino"

SPORTS_BLOCK_REASON = (
    "Official tables split casino sportsbooks vs Sports Lottery retailers, "
    "not online vs retail; not normalized as channel=online"
)

ONLINE_HEADER_HINTS = ("online", "internet", "mobile", "iGaming", "i-gaming")
RETAIL_HEADER_HINTS = ("retailer", "retailers")

FY_MONTH_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"[\s\-]+(\d{2}|\d{4})$",
    re.IGNORECASE,
)
CLASSIC_MONTH_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r",?\s+(\d{4})",
    re.IGNORECASE,
)
ACCOUNTING_MONTH_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{2,4})\s*[-–]\s*(\d{1,2})/(\d{1,2})/(\d{2,4})"
)


def _cell_text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def _norm(text: str) -> str:
    return _cell_text(text).casefold()


def _year_from_two_digit(year: int) -> int:
    if year < 100:
        return 2000 + year
    return year


def html_table_matrix(table) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [_cell_text(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        rows.append(cells)
    return rows


def _looks_online_header(text: str) -> bool:
    blob = _norm(text)
    return any(hint in blob for hint in ("online", "internet", "mobile"))


def sports_headers_have_online_split(headers: list[str]) -> bool:
    """True only when a column is explicitly online/internet/mobile (not retailers)."""
    for header in headers:
        blob = _norm(header)
        if not blob:
            continue
        if any(h in blob for h in RETAIL_HEADER_HINTS):
            continue
        if _looks_online_header(header):
            return True
    return False


def discover_year_pages(html: str, landing_url: str, *, kind: str) -> list[dict]:
    """
    kind: 'sports' | 'igaming'
    Returns [{url, filename, year_hint, fy, link_text}].
    """
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        text = _cell_text(anchor.get_text(" ", strip=True))
        absolute = urljoin(landing_url, href)
        blob = f"{text} {absolute}".casefold()
        if kind == "sports":
            if "sports-lottery" not in blob and "sportsbooks" not in blob:
                continue
            if "monthly-proceeds" not in blob and "monthly-distribution" not in blob:
                continue
            if "/retailers/" in blob or "track-data" in blob:
                continue
        else:
            if "igaming" not in blob and "i-gaming" not in blob:
                continue
            if "monthly-proceeds" not in blob and "monthly-distribution" not in blob:
                continue
            if "sports-lottery" in blob:
                continue
        year_hint = None
        fy = "financial-year" in blob or blob.rstrip("/").endswith(
            tuple(f"/20{y}" for y in range(10, 40))
        )
        year_match = re.search(r"(20\d{2})(?:-and-prior)?/?$", absolute.rstrip("/"), re.IGNORECASE)
        if year_match:
            year_hint = int(year_match.group(1))
        elif re.search(r"20\d{2}", text):
            year_hint = int(re.search(r"(20\d{2})", text).group(1))
        filename = absolute.rstrip("/").rsplit("/", 1)[-1] + ".html"
        if year_hint is None and "prior" not in blob:
            continue
        found.setdefault(
            absolute,
            {
                "url": absolute,
                "filename": filename,
                "year_hint": year_hint,
                "fy": "financial-year" in absolute.casefold() or "fy " in text.casefold(),
                "link_text": text,
            },
        )
    return sorted(
        found.values(),
        key=lambda item: (item["year_hint"] or 0, item["filename"]),
    )


def _parse_fy_month_label(label: str) -> tuple[int, int] | None:
    text = _cell_text(label)
    match = FY_MONTH_RE.match(text.replace("—", "-"))
    if not match:
        match = CLASSIC_MONTH_RE.search(text)
        if not match:
            return None
        return int(match.group(2)), month_name_to_num(match.group(1).title())
    year = _year_from_two_digit(int(match.group(2)))
    return year, month_name_to_num(match.group(1).title())


def parse_igaming_fy_stacked(matrix: list[list[str]]) -> pd.DataFrame:
    """FY pages: month label in col0, stacked product metrics, casinos as columns."""
    if not matrix:
        return pd.DataFrame()
    header = matrix[0]
    casinos: list[tuple[int, str]] = []
    for idx, cell in enumerate(header):
        name = _cell_text(cell)
        if idx == 0 or not name:
            continue
        if _norm(name) in {"month ending", "month"}:
            continue
        casinos.append((idx, name))
    if not casinos:
        return pd.DataFrame()

    records: list[dict] = []
    current_month: tuple[int, int] | None = None
    metrics: dict[str, dict[str, float | None]] = {}
    section: str | None = None

    def flush():
        nonlocal metrics, current_month
        if current_month is None or not metrics:
            metrics = {}
            return
        year, month = current_month
        period_start, period_end = month_period(year, month)
        for casino in [name for _, name in casinos]:
            bucket = metrics.get(casino, {})
            tables_bets = bucket.get("tables_bets")
            video_bets = bucket.get("video_bets")
            handle = None
            if tables_bets is not None or video_bets is not None:
                handle = (tables_bets or 0.0) + (video_bets or 0.0)
            net = bucket.get("total_net")
            if net is None:
                parts = [
                    bucket.get("tables_net"),
                    bucket.get("video_net"),
                    bucket.get("poker"),
                ]
                if any(v is not None for v in parts):
                    net = sum(v or 0.0 for v in parts)
            if handle is None and net is None:
                continue
            is_total = _norm(casino) == "total"
            records.append(
                {
                    "operator": "STATEWIDE" if is_total else casino,
                    "row_type": "official_statewide_total" if is_total else "operator",
                    "period_start": period_start,
                    "period_end": period_end,
                    "handle": handle,
                    "net_proceeds": net,
                    "reported_revenue_name": "Total Net Gaming Revenue",
                }
            )
        metrics = {}

    for row in matrix[1:]:
        if not row:
            continue
        label = _cell_text(row[0] if row else "")
        metric = _cell_text(row[1] if len(row) > 1 else "")
        if _norm(label).startswith("fiscal year") or _norm(metric).startswith("fiscal year"):
            flush()
            current_month = None
            continue
        month_parsed = _parse_fy_month_label(label)
        if month_parsed:
            flush()
            current_month = month_parsed
            section = None
            metric_label = metric
        else:
            metric_label = label or metric
        if current_month is None:
            continue
        key = None
        norm = _norm(metric_label)
        if "tables bets" in norm:
            section = "tables"
            key = "tables_bets"
        elif "video bets" in norm:
            section = "video"
            key = "video_bets"
        elif "tables" in norm and "win" in norm:
            key = "tables_win"
        elif "video" in norm and "win" in norm:
            key = "video_win"
        elif "total net gaming revenue" in norm:
            key = "total_net"
        elif "net gaming revenue" in norm:
            key = "tables_net" if section != "video" else "video_net"
        elif "poker" in norm:
            key = "poker"
        if key is None:
            continue
        for col_index, casino in casinos:
            amount = parse_money(row[col_index] if col_index < len(row) else None)
            metrics.setdefault(casino, {})[key] = amount

    flush()
    return pd.DataFrame.from_records(records)


def parse_igaming_classic_tables(soup: BeautifulSoup) -> pd.DataFrame:
    """Pre-FY pages: one HTML table per calendar month with Amount Played / Won / Net."""
    records: list[dict] = []
    for table in soup.find_all("table"):
        matrix = html_table_matrix(table)
        if not matrix:
            continue
        month_label = " ".join(_cell_text(c) for c in matrix[0])
        parsed = CLASSIC_MONTH_RE.search(month_label)
        if not parsed and len(matrix) > 1:
            parsed = CLASSIC_MONTH_RE.search(" ".join(_cell_text(c) for c in matrix[1]))
        if not parsed:
            continue
        year, month = int(parsed.group(2)), month_name_to_num(parsed.group(1).title())
        period_start, period_end = month_period(year, month)

        casinos: list[str] = []
        # Header row with casino names (usually row 1).
        for row in matrix[:4]:
            names = [_cell_text(c) for c in row if _cell_text(c)]
            candidate = [
                n
                for n in names
                if n.casefold()
                not in {
                    "amount played",
                    "amount won",
                    "net",
                    month_label.casefold(),
                    parsed.group(1).casefold(),
                }
                and not CLASSIC_MONTH_RE.search(n)
            ]
            if len(candidate) >= 2:
                casinos = candidate
                break
        if not casinos:
            continue

        total_row = None
        for row in matrix:
            first = _cell_text(row[0] if row else "")
            if _norm(first) in {"number of registrations", "# registrations"}:
                break
            moneys = []
            for cell in row:
                try:
                    moneys.append(parse_money(cell))
                except ValueError:
                    moneys.append(None)
            if sum(v is not None for v in moneys) >= 3 and _norm(first) not in {
                "table games",
                "video lottery",
                "poker rake & fee",
                "poker",
            }:
                total_row = row
        if total_row is None:
            continue

        values = [_cell_text(c) for c in total_row]
        # Pattern: [label?] then repeating Played, Won, Net per casino (including statewide first).
        amounts = [parse_money(v) for v in values]
        numeric = [v for v in amounts if v is not None]
        # Expect 3 values per casino column.
        if len(numeric) < 3:
            continue
        groups = len(numeric) // 3
        names = casinos
        if len(names) == groups - 1:
            names = ["Total"] + names
        elif len(names) > groups:
            names = names[:groups]
        elif len(names) < groups:
            names = names + [f"Column {i}" for i in range(len(names), groups)]
        for i, name in enumerate(names):
            played = numeric[i * 3]
            net = numeric[i * 3 + 2]
            is_total = _norm(name) in {"total", "january", "february", "march", "april",
                                       "may", "june", "july", "august", "september",
                                       "october", "november", "december"} or i == 0 and groups == len(casinos) + 1
            # First triplet is statewide when header is Month + casinos.
            if i == 0 and len(casinos) == groups - 1:
                is_total = True
                name = "STATEWIDE"
            records.append(
                {
                    "operator": "STATEWIDE" if is_total else name,
                    "row_type": "official_statewide_total" if is_total else "operator",
                    "period_start": period_start,
                    "period_end": period_end,
                    "handle": played,
                    "net_proceeds": net,
                    "reported_revenue_name": "Net",
                }
            )
    return pd.DataFrame.from_records(records)


def parse_igaming_html(html: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "html.parser")
    frames: list[pd.DataFrame] = []
    for table in soup.find_all("table"):
        matrix = html_table_matrix(table)
        if not matrix:
            continue
        blob = " ".join(_cell_text(c) for row in matrix[:8] for c in row).casefold()
        if "igaming tables bets" in blob or "total net gaming revenue" in blob or "net gaming revenue" in blob:
            parsed = parse_igaming_fy_stacked(matrix)
            if not parsed.empty:
                frames.append(parsed)
    classic = parse_igaming_classic_tables(soup)
    if not classic.empty:
        frames.append(classic)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def parse_sports_html(html: str) -> tuple[pd.DataFrame, dict]:
    """
    Parse sports-lottery net-proceeds tables.

    Returns rows only when an explicit online/internet/mobile column exists.
    Retailers are never treated as online.
    """
    soup = BeautifulSoup(html, "html.parser")
    meta = {"has_online_split": False, "headers": []}
    records: list[dict] = []
    for table in soup.find_all("table"):
        matrix = html_table_matrix(table)
        if not matrix:
            continue
        header = matrix[0]
        meta["headers"] = [_cell_text(c) for c in header if _cell_text(c)]
        if not sports_headers_have_online_split(header):
            continue
        meta["has_online_split"] = True
        online_cols = [
            i
            for i, cell in enumerate(header)
            if _looks_online_header(cell) and not any(h in _norm(cell) for h in RETAIL_HEADER_HINTS)
        ]
        if not online_cols:
            continue
        current_period = None
        for row in matrix[1:]:
            label = _cell_text(row[0] if row else "")
            metric = _cell_text(row[1] if len(row) > 1 else "")
            acc = ACCOUNTING_MONTH_RE.search(label)
            if acc:
                y1 = _year_from_two_digit(int(acc.group(3)))
                current_period = (
                    f"{y1:04d}-{int(acc.group(1)):02d}-{int(acc.group(2)):02d}",
                    f"{_year_from_two_digit(int(acc.group(6))):04d}-"
                    f"{int(acc.group(4)):02d}-{int(acc.group(5)):02d}",
                )
            month_ended = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", label)
            if current_period is None and month_ended and "month" in _norm(" ".join(header)):
                y = _year_from_two_digit(int(month_ended.group(3)))
                m = int(month_ended.group(1))
                current_period = month_period(y, m)
            if current_period is None:
                continue
            if _norm(metric) not in {"net proceeds", "sports sales"} and _norm(label) not in {
                "net proceeds",
                "sports sales",
            }:
                continue
            metric_name = metric or label
            for col in online_cols:
                amount = parse_money(row[col] if col < len(row) else None)
                name = _cell_text(header[col])
                is_total = _norm(name) == "total"
                rec = {
                    "operator": "STATEWIDE" if is_total else name,
                    "row_type": "official_statewide_total" if is_total else "operator",
                    "period_start": current_period[0],
                    "period_end": current_period[1],
                    "handle": amount if "sales" in _norm(metric_name) else None,
                    "net_proceeds": amount if "net proceeds" in _norm(metric_name) else None,
                    "reported_revenue_name": "Net Proceeds",
                }
                records.append(rec)
    return pd.DataFrame.from_records(records), meta


def build_normalized_rows(
    parsed: pd.DataFrame,
    *,
    vertical: str,
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
        vertical=vertical,
        channel="online",
        frequency="monthly",
        gross_revenue=pd.NA if "gross_revenue" not in rows.columns else rows.get("gross_revenue"),
        adjusted_revenue=pd.NA,
        taxable_revenue=pd.NA,
        tax=pd.NA if "tax" not in rows.columns else rows.get("tax"),
        source_url=source_url,
        source_file=source_file,
        source_sha256=source_sha256,
        retrieved_at_utc=retrieved_at.isoformat(),
        report_status="ok",
    )
    if "gross_revenue" not in parsed.columns:
        rows["gross_revenue"] = pd.NA
    if "handle" not in rows.columns:
        rows["handle"] = pd.NA
    if "net_proceeds" not in rows.columns:
        rows["net_proceeds"] = pd.NA
    if "reported_revenue_name" not in rows.columns:
        rows["reported_revenue_name"] = "Net Proceeds"
    return rows


def _coverage(
    connection,
    *,
    vertical: str,
    official_url: str,
    status: str,
    reason: str,
    frame: pd.DataFrame,
    downloaded: int,
) -> None:
    upsert_coverage(
        connection,
        {
            "state_code": STATE_CODE,
            "vertical": vertical,
            "status": status,
            "reason": reason,
            "official_url": official_url,
            "available_frequency": "monthly",
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


def collect_sports_history(
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Download sports-lottery archives. Upsert only if online vs retail is explicit."""
    root = root or project_root()
    retrieved_at = utc_now()
    sess = session or requests.Session()
    db_path = db_path or default_db_path(root)
    connection = connect(db_path)
    migrate_legacy_table(connection)
    ensure_schema(connection)

    landing = http_get_unchecked(SPORTS_LANDING_URL, session=sess)
    blocked = http_block_reason(landing)
    if blocked:
        _coverage(
            connection,
            vertical=SPORTS_VERTICAL,
            official_url=SPORTS_LANDING_URL,
            status="blocked",
            reason=blocked,
            frame=pd.DataFrame(),
            downloaded=0,
        )
        connection.close()
        print(f"BLOCKED DE sports: {blocked}")
        return pd.DataFrame()
    landing.raise_for_status()

    pages = discover_year_pages(landing.text, SPORTS_LANDING_URL, kind="sports")
    landing_path = save_raw_bytes(
        root, STATE_CODE, landing.content, "de_sports_landing.html", retrieved_at=retrieved_at
    )
    downloaded = 1
    all_frames: list[pd.DataFrame] = []
    saw_online = False
    failures: list[str] = []

    for page in pages:
        try:
            response = http_get(page["url"], session=sess)
            path = save_raw_bytes(
                root, STATE_CODE, response.content, page["filename"], retrieved_at=retrieved_at
            )
            downloaded += 1
            parsed, meta = parse_sports_html(response.text)
            if meta.get("has_online_split"):
                saw_online = True
            if parsed.empty:
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            frame = build_normalized_rows(
                parsed,
                vertical=SPORTS_VERTICAL,
                source_url=page["url"],
                source_file=rel,
                source_sha256=sha256_bytes(response.content),
                retrieved_at=retrieved_at,
            )
            upsert_gaming_results(connection, frame)
            all_frames.append(frame)
            print(f"OK DE sports {page['link_text']}: {len(frame)} online rows")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{page['url']}: {exc}")
            print(f"SKIP DE sports {page['filename']}: {exc}")

    result = (
        pd.concat(all_frames, ignore_index=True).sort_values(["period_start", "operator"]).reset_index(drop=True)
        if all_frames
        else pd.DataFrame()
    )
    if not saw_online:
        _coverage(
            connection,
            vertical=SPORTS_VERTICAL,
            official_url=SPORTS_LANDING_URL,
            status="blocked",
            reason=SPORTS_BLOCK_REASON,
            frame=result,
            downloaded=downloaded,
        )
        connection.close()
        print(f"BLOCKED DE sports: {SPORTS_BLOCK_REASON} (saved {downloaded} files)")
        return result

    _coverage(
        connection,
        vertical=SPORTS_VERTICAL,
        official_url=SPORTS_LANDING_URL,
        status="ok" if not failures else "partial",
        reason="; ".join(failures[:5]) if failures else "Collected DE online sports-lottery columns",
        frame=result,
        downloaded=downloaded,
    )
    connection.close()
    return result


def collect_casino_history(
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

    landing = http_get_unchecked(IGAMING_LANDING_URL, session=sess)
    blocked = http_block_reason(landing)
    if blocked:
        _coverage(
            connection,
            vertical=CASINO_VERTICAL,
            official_url=IGAMING_LANDING_URL,
            status="blocked",
            reason=blocked,
            frame=pd.DataFrame(),
            downloaded=0,
        )
        connection.close()
        print(f"BLOCKED DE iGaming: {blocked}")
        return pd.DataFrame()
    landing.raise_for_status()

    pages = discover_year_pages(landing.text, IGAMING_LANDING_URL, kind="igaming")
    save_raw_bytes(
        root, STATE_CODE, landing.content, "de_igaming_landing.html", retrieved_at=retrieved_at
    )
    downloaded = 1
    all_frames: list[pd.DataFrame] = []
    failures: list[str] = []

    for page in pages:
        try:
            response = http_get(page["url"], session=sess)
            path = save_raw_bytes(
                root, STATE_CODE, response.content, page["filename"], retrieved_at=retrieved_at
            )
            downloaded += 1
            parsed = parse_igaming_html(response.text)
            if parsed.empty:
                failures.append(f"{page['filename']}: no iGaming rows")
                print(f"SKIP DE iGaming {page['filename']}: no rows")
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            frame = build_normalized_rows(
                parsed,
                vertical=CASINO_VERTICAL,
                source_url=page["url"],
                source_file=rel,
                source_sha256=sha256_bytes(response.content),
                retrieved_at=retrieved_at,
            )
            upsert_gaming_results(connection, frame)
            all_frames.append(frame)
            print(f"OK DE iGaming {page['link_text']}: {len(frame)} rows")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{page['url']}: {exc}")
            print(f"SKIP DE iGaming {page['filename']}: {exc}")

    result = (
        pd.concat(all_frames, ignore_index=True).sort_values(["period_start", "operator"]).reset_index(drop=True)
        if all_frames
        else pd.DataFrame()
    )
    _coverage(
        connection,
        vertical=CASINO_VERTICAL,
        official_url=IGAMING_LANDING_URL,
        status=("ok" if all_frames and not failures else "failed" if not all_frames else "partial"),
        reason=(
            "; ".join(failures[:5])
            if failures
            else "Collected DE iGaming monthly net proceeds / Total Net Gaming Revenue"
        ),
        frame=result,
        downloaded=downloaded,
    )
    connection.close()
    return result


def collect_history(
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
    vertical: str = SPORTS_VERTICAL,
) -> pd.DataFrame:
    if vertical == CASINO_VERTICAL:
        return collect_casino_history(root=root, db_path=db_path, session=session)
    return collect_sports_history(root=root, db_path=db_path, session=session)
