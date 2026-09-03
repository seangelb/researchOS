"""New Jersey DGE monthly sports-wagering and internet-gaming collectors."""

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

SPORTS_LANDING_URL = (
    "https://www.njoag.gov/about/divisions-and-offices/division-of-gaming-enforcement-home/"
    "financial-and-statistical-information/monthly-sports-wagering-revenue-reports/"
)
IGR_LANDING_URL = (
    "https://www.njoag.gov/about/divisions-and-offices/division-of-gaming-enforcement-home/"
    "financial-and-statistical-information/monthly-internet-gross-revenue-reports/"
)
STATE_CODE = "NJ"
JURISDICTION = "New Jersey"
SPORTS_VERTICAL = "online_sports_betting"
CASINO_VERTICAL = "online_casino"

SPORTS_GGR_LABELS = (
    "Monthly Online Sportsbook Gross Revenue",
    "Monthly Online Sports Wagering Gross Revenue",
    "Monthly Internet Sports Wagering Gross Revenue",
    "Current Month Internet Sports Wagering Gross Revenue",
)
SPORTS_TAX_LABELS = (
    "Monthly Tax on Online Sportsbook Gross Revenue",
    "Monthly 13% Tax on Internet Sports Wagering Gross Revenue",
    "Total Internet Sports Wagering Tax Payment for this Month",
    "Total Internet Sports Wagering Tax Required for this Month",
    "Total Internet Sports Wagering Tax Obligation for this Month",
)
SPORTS_SKIN_GGR_LABELS = (
    "Monthly Online Sportsbook Gross Revenue",
    "Monthly Online Sports Wagering Gross Revenue",
)
MONTH_RE = re.compile(
    r"FOR THE MONTH OF\s+([A-Za-z]+)(?:\s|,)+?(20\d{2})",
    re.IGNORECASE,
)
PERIOD_RE = re.compile(
    r"FOR THE PERIOD OF\s+([A-Za-z]+).*?(20\d{2})",
    re.IGNORECASE,
)
MONEY_TOKEN_RE = re.compile(
    r"\(?\$?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?|\(?\$?-?\d+(?:\.\d+)?\)?"
)


def _cell_text(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def repair_pdf_line(text: str) -> str:
    """Join DGE skin-detail numbers split by spaces ('1 3,429,459' -> '13,429,459')."""
    text = _cell_text(text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"(\d)\s+,", r"\1,", text)
    text = re.sub(r",\s+(\d)", r",\1", text)
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"(\d)\s+(\d{1,2},\d{3})", r"\1\2", text)
    return text


def money_tokens(text: str) -> list[float | None]:
    repaired = repair_pdf_line(text)
    tokens: list[float | None] = []
    for match in re.finditer(r"-|\(?\$?-?\d[\d,.]*(?:\)?)", repaired):
        tok = match.group(0)
        if tok == "-" or tok in {"$-", "($-"}:
            tokens.append(None)
            continue
        try:
            tokens.append(parse_money(tok))
        except ValueError:
            continue
    return tokens


def repair_pdf_number(token: str) -> float | None:
    values = money_tokens(token)
    if not values:
        return None
    return values[0]


def discover_monthly_pdf_links(html: str, base_url: str, *, kind: str) -> list[dict]:
    """kind: 'sports' (SWRTaxReturns) or 'igr' (IGRTaxReturns). Skip amendments lists."""
    soup = BeautifulSoup(html, "html.parser")
    needle = "swrtaxreturns" if kind == "sports" else "igrtaxreturns"
    found: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        text = _cell_text(anchor.get_text(" ", strip=True))
        absolute = urljoin(base_url, href)
        blob = f"{text} {absolute}".casefold()
        if not absolute.casefold().endswith(".pdf"):
            continue
        if needle not in blob:
            continue
        if "amend" in blob:
            continue
        filename = unquote(absolute.rsplit("/", 1)[-1].split("?", 1)[0])
        year = month = None
        match = re.search(
            r"(January|February|March|April|May|June|July|August|September|October|November|December)(20\d{2})\.pdf$",
            filename,
            re.IGNORECASE,
        )
        if match:
            month = month_name_to_num(match.group(1).title())
            year = int(match.group(2))
        found.setdefault(
            absolute,
            {
                "url": absolute,
                "filename": filename,
                "year": year,
                "month": month,
                "kind": kind,
                "link_text": text,
            },
        )
    return sorted(
        found.values(),
        key=lambda item: (item["year"] or 0, item["month"] or 0, item["filename"]),
    )


def _page_period(text: str) -> tuple[int, int] | None:
    match = MONTH_RE.search(text) or PERIOD_RE.search(text)
    if not match:
        return None
    month_name = match.group(1).title()
    if month_name not in MONTH_NAMES:
        return None
    return int(match.group(2)), month_name_to_num(month_name)


def _entity_name(lines: list[str]) -> str:
    parts: list[str] = []
    for line in lines:
        text = _cell_text(line)
        if not text:
            continue
        upper = text.upper()
        if upper.startswith("MONTHLY") or upper.startswith("FOR THE MONTH") or upper.startswith("FOR THE PERIOD"):
            break
        if "TAX RETURN" in upper or "GROSS REVENUE" in upper or "SKIN DETAIL" in upper:
            break
        if upper.startswith("LINE ") or upper.startswith("SPORTSBOOK") or upper.startswith("ONLINE "):
            break
        parts.append(text)
        if len(parts) >= 2:
            break
    return " ".join(parts).strip() or "Unknown"


def _classify_nj_page(text: str) -> str | None:
    upper = text.upper()
    if "SKIN DETAIL" in upper:
        if "INTERNET GAMING" in upper or "INTERNET GROSS" in upper:
            return "igr_skin"
        if "ONLINE SPORTS" in upper or "SPORTSBOOK" in upper or "SPORTS WAGERING" in upper:
            return "sports_skin"
        return "sports_skin"
    if "SPORTS WAGERING TAX RETURN" in upper:
        return "sports_tax"
    if "INTERNET GAMING GROSS REVENUE" in upper or "INTERNET GROSS REVENUE" in upper:
        return "igr_tax"
    return None


def _line_values(text: str, line_no: str, label_fragment: str) -> list[float | None]:
    """Return money tokens on the matching line after the label."""
    pattern = re.compile(
        rf"^{line_no}\s+{re.escape(label_fragment)}(.*)$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        # Allow wrapped labels: match fragment anywhere then take rest of line.
        for raw_line in text.splitlines():
            if raw_line.strip().startswith(line_no) and label_fragment.casefold() in raw_line.casefold():
                idx = raw_line.casefold().find(label_fragment.casefold())
                rest = raw_line[idx + len(label_fragment) :]
                return money_tokens(rest)
        return []
    return money_tokens(match.group(1))


def _first_amount(text: str, labels: tuple[str, ...]) -> tuple[float | None, str | None]:
    for label in labels:
        for raw_line in text.splitlines():
            if label.casefold() in raw_line.casefold():
                idx = raw_line.casefold().find(label.casefold())
                rest = raw_line[idx + len(label) :]
                values = [v for v in money_tokens(rest) if v is not None]
                if not values:
                    values = [v for v in money_tokens(raw_line) if v is not None]
                if values:
                    return values[-1], label
    return None, None


def _skin_brands(text: str) -> list[str]:
    lines = [_cell_text(ln) for ln in text.splitlines() if _cell_text(ln)]
    for i, line in enumerate(lines):
        if line.casefold() == "line description" or line.casefold().startswith("line type of game"):
            # Brands are on the previous non-empty line.
            if i == 0:
                return []
            header = lines[i - 1]
            if header.casefold().startswith("gross revenue") or header.casefold() == "win":
                header = lines[i - 2] if i >= 2 else header
            parts = header.replace("Gross Revenue", "").split()
            # Keep multi-word brands by splitting on likely boundaries is hard; use the
            # pdfplumber table when available. Fallback: split on two-or-more spaces already collapsed.
            return [p for p in re.split(r"\s{2,}", header) if p and p.casefold() != "win"]
    return []


def _skin_brands_from_table(page) -> list[str]:
    tables = page.extract_tables() or []
    for table in tables:
        if not table:
            continue
        header = table[0]
        names = []
        for cell in header[2:]:
            text = _cell_text(cell).replace("\n", " ")
            text = re.sub(r"\bGross Revenue\b", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\bWin\b", "", text, flags=re.IGNORECASE)
            text = _cell_text(text)
            if not text or text.casefold() == "total":
                if text.casefold() == "total":
                    names.append("Total")
                continue
            names.append(text)
        if names:
            return names
    return []


def parse_sports_tax_page(text: str) -> dict | None:
    amount, label = _first_amount(text, SPORTS_GGR_LABELS)
    if amount is None and label is None:
        return None
    tax, _tax_label = _first_amount(text, SPORTS_TAX_LABELS)
    taxable, _ = _first_amount(text, ("Monthly Taxable Online Sportsbook Gross Revenue",))
    lines = text.splitlines()
    return {
        "operator": _entity_name(lines),
        "row_type": "operator",
        "gross_revenue": amount,
        "taxable_revenue": taxable,
        "tax": tax,
        "reported_revenue_name": label or SPORTS_GGR_LABELS[0],
        "is_skin": False,
    }


def parse_sports_skin_page(page, text: str) -> list[dict]:
    brands = _skin_brands_from_table(page) or _skin_brands(text)
    values: list[float | None] = []
    ggr_label = SPORTS_SKIN_GGR_LABELS[0]
    for raw_line in text.splitlines():
        for label in SPORTS_SKIN_GGR_LABELS:
            if label in raw_line:
                rest = raw_line.split(label, 1)[-1]
                values = money_tokens(rest)
                ggr_label = label
                break
        if values:
            break
    if not brands or not values:
        return []
    # Drop trailing Total column if present.
    if brands and brands[-1].casefold() == "total":
        brands = brands[:-1]
        if values:
            values = values[:-1]
    entity = _entity_name(text.splitlines())
    records = []
    for brand, amount in zip(brands, values):
        if amount is None or amount == 0:
            continue
        operator = brand
        if operator.casefold() == entity.casefold():
            operator = brand
        records.append(
            {
                "operator": operator,
                "row_type": "operator",
                "gross_revenue": amount,
                "taxable_revenue": None,
                "tax": None,
                "reported_revenue_name": ggr_label,
                "is_skin": True,
                "casino": entity,
            }
        )
    return records


def parse_igr_tax_page(text: str) -> dict | None:
    amount = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if re.match(r"^3\s+Total\b", stripped, re.IGNORECASE):
            tokens = money_tokens(stripped)
            numeric = [v for v in tokens if v is not None]
            if numeric:
                amount = numeric[-1]
            break
    if amount is None:
        return None
    tax, _ = _first_amount(text, ("Total Tax Required for this Month",))
    return {
        "operator": _entity_name(text.splitlines()),
        "row_type": "operator",
        "gross_revenue": amount,
        "tax": tax,
        "reported_revenue_name": "Internet Gaming Win",
        "is_skin": False,
    }


def parse_igr_skin_page(page, text: str) -> list[dict]:
    brands = _skin_brands_from_table(page)
    if not brands:
        return []
    values: list[float | None] = []
    for raw_line in text.splitlines():
        if re.match(r"^3\s+Total\b", raw_line.strip(), re.IGNORECASE):
            rest = re.sub(r"^3\s+Total", "", raw_line.strip(), flags=re.IGNORECASE)
            values = money_tokens(rest)
            break
    if brands and brands[-1].casefold() == "total":
        brands = brands[:-1]
        if values:
            values = values[:-1]
    records = []
    for brand, amount in zip(brands, values):
        if amount is None or amount == 0:
            continue
        records.append(
            {
                "operator": brand,
                "row_type": "operator",
                "gross_revenue": amount,
                "tax": None,
                "reported_revenue_name": "Internet Gaming Win",
                "is_skin": True,
            }
        )
    return records


def parse_nj_pdf(content: bytes | Path, *, vertical: str) -> pd.DataFrame:
    source = content if isinstance(content, Path) else BytesIO(content)
    tax_rows: list[dict] = []
    skin_rows: list[dict] = []
    period = None
    with pdfplumber.open(source) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            page_period = _page_period(text)
            if page_period:
                period = page_period
            kind = _classify_nj_page(text)
            if kind is None:
                continue
            if vertical == SPORTS_VERTICAL:
                if kind == "sports_tax":
                    row = parse_sports_tax_page(text)
                    if row:
                        tax_rows.append(row)
                elif kind == "sports_skin":
                    skin_rows.extend(parse_sports_skin_page(page, text))
            else:
                if kind == "igr_tax":
                    row = parse_igr_tax_page(text)
                    if row:
                        tax_rows.append(row)
                elif kind == "igr_skin":
                    skin_rows.extend(parse_igr_skin_page(page, text))
    chosen = skin_rows if skin_rows else tax_rows
    if not chosen or period is None:
        return pd.DataFrame()
    year, month = period
    period_start, period_end = month_period(year, month)
    frame = pd.DataFrame.from_records(chosen)
    frame = frame.assign(period_start=period_start, period_end=period_end)
    # Deduplicate identical brand names by summing GGR (same PDF, same brand, two casinos).
    if "gross_revenue" in frame.columns:
        grouped = (
            frame.groupby(["operator", "row_type", "period_start", "period_end", "reported_revenue_name"], dropna=False)
            .agg(
                {
                    "gross_revenue": "sum",
                    **({"tax": "sum"} if "tax" in frame.columns else {}),
                    **({"taxable_revenue": "sum"} if "taxable_revenue" in frame.columns else {}),
                }
            )
            .reset_index()
        )
        frame = grouped
    return frame


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
        handle=pd.NA,
        adjusted_revenue=pd.NA,
        net_proceeds=pd.NA,
        source_url=source_url,
        source_file=source_file,
        source_sha256=source_sha256,
        retrieved_at_utc=retrieved_at.isoformat(),
        report_status="ok",
    )
    if "taxable_revenue" not in rows.columns:
        rows["taxable_revenue"] = pd.NA
    if "tax" not in rows.columns:
        rows["tax"] = pd.NA
    if "gross_revenue" not in rows.columns:
        rows["gross_revenue"] = pd.NA
    extra = [c for c in ("is_skin", "casino") if c in rows.columns]
    return rows.drop(columns=extra)


def _collect_kind(
    *,
    kind: str,
    vertical: str,
    landing_url: str,
    root: Path,
    db_path: Path,
    session: requests.Session,
) -> pd.DataFrame:
    retrieved_at = utc_now()
    connection = connect(db_path)
    migrate_legacy_table(connection)
    ensure_schema(connection)

    landing = http_get_unchecked(landing_url, session=session)
    blocked = http_block_reason(landing)
    if blocked:
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": vertical,
                "status": "blocked",
                "reason": blocked,
                "official_url": landing_url,
                "available_frequency": "monthly",
                "earliest_period": None,
                "latest_period": None,
                "downloaded_file_count": 0,
                "normalized_row_count": 0,
                "last_retrieval_utc": utc_now().isoformat(),
            },
        )
        connection.close()
        print(f"BLOCKED NJ {kind}: {blocked}", flush=True)
        return pd.DataFrame()
    landing.raise_for_status()
    save_raw_bytes(
        root,
        STATE_CODE,
        landing.content,
        f"nj_{kind}_landing.html",
        retrieved_at=retrieved_at,
    )
    links = discover_monthly_pdf_links(landing.text, landing_url, kind=kind)
    all_frames: list[pd.DataFrame] = []
    failures: list[str] = []
    downloaded = 1

    for link in links:
        try:
            response = http_get_unchecked(link["url"], session=session)
            if response.status_code == 404:
                continue
            blocked_file = http_block_reason(response)
            if blocked_file:
                failures.append(blocked_file)
                continue
            response.raise_for_status()
            content = response.content
            if not content.startswith(b"%PDF"):
                raise RuntimeError("Not a PDF payload")
            path = save_raw_bytes(
                root, STATE_CODE, content, link["filename"], retrieved_at=retrieved_at
            )
            downloaded += 1
            parsed = parse_nj_pdf(content, vertical=vertical)
            if parsed.empty:
                failures.append(f"{link['filename']}: no online rows")
                print(f"SKIP NJ {kind} {link['filename']}: no online rows", flush=True)
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            frame = build_normalized_rows(
                parsed,
                vertical=vertical,
                source_url=response.url or link["url"],
                source_file=rel,
                source_sha256=sha256_bytes(content),
                retrieved_at=retrieved_at,
            )
            upsert_gaming_results(connection, frame)
            all_frames.append(frame)
            print(f"OK NJ {kind} {link['filename']}: {len(frame)} rows", flush=True)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{link['filename']}: {exc}")
            print(f"SKIP NJ {kind} {link['filename']}: {exc}", flush=True)

    result = (
        pd.concat(all_frames, ignore_index=True).sort_values(["period_start", "operator"]).reset_index(drop=True)
        if all_frames
        else pd.DataFrame()
    )
    reason = (
        "; ".join(failures[:5])
        if failures
        else (
            "Collected DGE online sportsbook GGR by brand"
            if kind == "sports"
            else "Collected DGE Internet Gaming Win by brand"
        )
    )
    upsert_coverage(
        connection,
        {
            "state_code": STATE_CODE,
            "vertical": vertical,
            "status": "ok" if all_frames and not failures else ("failed" if not all_frames else "partial"),
            "reason": reason,
            "official_url": landing_url,
            "available_frequency": "monthly",
            "earliest_period": None if result.empty else result["period_start"].min(),
            "latest_period": None if result.empty else result["period_end"].max(),
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
    connection.close()
    return result


def collect_sports_history(
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    root = root or project_root()
    return _collect_kind(
        kind="sports",
        vertical=SPORTS_VERTICAL,
        landing_url=SPORTS_LANDING_URL,
        root=root,
        db_path=db_path or default_db_path(root),
        session=session or requests.Session(),
    )


def collect_casino_history(
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    root = root or project_root()
    return _collect_kind(
        kind="igr",
        vertical=CASINO_VERTICAL,
        landing_url=IGR_LANDING_URL,
        root=root,
        db_path=db_path or default_db_path(root),
        session=session or requests.Session(),
    )


def collect_history(
    root: Path | None = None,
    db_path: Path | None = None,
    session: requests.Session | None = None,
    vertical: str = SPORTS_VERTICAL,
) -> pd.DataFrame:
    if vertical == CASINO_VERTICAL:
        return collect_casino_history(root=root, db_path=db_path, session=session)
    return collect_sports_history(root=root, db_path=db_path, session=session)
