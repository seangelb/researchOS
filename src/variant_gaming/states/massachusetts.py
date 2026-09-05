"""Massachusetts Category 3 online sportsbook PDF collector."""

from __future__ import annotations

import re
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, unquote

import pandas as pd
import pdfplumber
import requests
from bs4 import BeautifulSoup

from variant_gaming.common import (
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

LANDING_URL = "https://massgaming.com/regulations/revenue/"
ARCHIVE_URL = "https://massgaming.com/regulations/revenue/revenue-report-archives/"
STATE_CODE = "MA"
JURISDICTION = "Massachusetts"
VERTICAL = "online_sports_betting"
# gross_revenue stores Accrual Win; taxable_revenue stores Taxable Gaming Revenue.
REPORTED_REVENUE_NAME = (
    "Accrual Win (gross_revenue); Taxable Gaming Revenue (taxable_revenue)"
)
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

# Archive typo (2026-06): the June link currently serves May 2026 bytes.
URL_CORRECTIONS: dict[tuple[int, int], str] = {
    (2026, 6): "https://massgaming.com/wp-content/uploads/MGC-Revenue-Report-June-2026.pdf",
}

# Online sportsbook launch: March 2023. Only February 2023 may have zero online rows.
ONLINE_START = (2023, 3)
PRE_LAUNCH_EMPTY_OK = (2023, 2)

RETAIL_OPERATORS = {
    "Encore Boston Harbor",
    "MGM Springfield",
    "Plainridge Park Casino",
}
SKIP_LABELS = {"Total Retail", "Total Online", "Total", "ONLINE LICENSEE", "RETAIL LICENSEE"}

MONEY_FIELD = (
    r"(?:"
    r"\(\$[\d,]+\.\d{2}\)"
    r"|-\$[\d,]+\.\d{2}"
    r"|\$-[\d,]+\.\d{2}"
    r"|\$[\d,]+\.\d{2}"
    r")"
)
MONEY_ROW_RE = re.compile(
    rf"^{MONEY_FIELD}\s+{MONEY_FIELD}\s+(?P<hold>-?[\d.]+%)\s+{MONEY_FIELD}\s+{MONEY_FIELD}$"
)
INLINE_ROW_RE = re.compile(
    rf"^(?P<operator>[A-Za-z][A-Za-z0-9' &.]+?)\s+{MONEY_FIELD}\s+{MONEY_FIELD}\s+"
    rf"(?P<hold>-?[\d.]+%)\s+{MONEY_FIELD}\s+{MONEY_FIELD}$"
)
PERIOD_HEADING_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(20\d{2})\s+Revenue Report\b",
    re.I,
)
MONTH_FROM_TEXT_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b",
    re.I,
)


def _month_from_text(text: str) -> int | None:
    match = MONTH_FROM_TEXT_RE.search(text)
    return month_name_to_num(match.group(1).title()) if match else None


def _is_consolidated_report(url: str, link_text: str) -> bool:
    blob = f"{link_text} {url}".lower()
    if not url.lower().endswith(".pdf"):
        return False
    if any(
        token in blob
        for token in (
            "cat. 1",
            "cat. 3",
            "category 1",
            "category 3",
            "daily fantasy",
            "encore-boston-harbor-cat",
            "mgm-springfield-cat",
            "plainridge-park-casino-cat",
            "sports-wagering-revenue-report",
            "revenue-encore-boston-harbor",
            "revenue-mgm-springfield",
            "revenue-plainridge-park-casino",
        )
    ):
        return False
    return any(token in blob for token in ("revenue", "rev-report", "mgc-"))


def discover_archive_reports(html: str, base_url: str = ARCHIVE_URL) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    current_year: int | None = None
    reports: dict[tuple[int, int], dict] = {}
    for element in soup.find_all(["h2", "h3", "h4", "p", "a"]):
        if element.name in {"h2", "h3", "h4", "p"}:
            text = element.get_text(" ", strip=True)
            year_match = re.search(r"\b(20\d{2})\b", text)
            if year_match and len(text) <= 24:
                current_year = int(year_match.group(1))
            continue
        href = element.get("href", "").strip()
        if not href:
            continue
        link_text = " ".join(element.get_text(" ", strip=True).split())
        url = urljoin(base_url, href)
        if not _is_consolidated_report(url, link_text):
            continue
        month = _month_from_text(link_text) or _month_from_text(url)
        year = current_year or _year_from_text(url)
        if month is None or year is None:
            continue
        key = (year, month)
        filename = unquote(url.rsplit("/", 1)[-1])
        reports[key] = {
            "url": url,
            "filename": filename,
            "link_text": link_text,
            "expected_year": year,
            "expected_month": month,
        }
    return sorted(reports.values(), key=lambda item: (item["expected_year"], item["expected_month"]))


def _year_from_text(text: str) -> int | None:
    matches = re.findall(r"(20\d{2})", text)
    return int(matches[-1]) if matches else None


def apply_url_corrections(reports: list[dict]) -> list[dict]:
    corrected: dict[tuple[int, int], dict] = {}
    for report in reports:
        key = (report["expected_year"], report["expected_month"])
        url = URL_CORRECTIONS.get(key, report["url"])
        filename = unquote(url.rsplit("/", 1)[-1])
        corrected[key] = {**report, "url": url, "filename": filename}
    return sorted(corrected.values(), key=lambda item: (item["expected_year"], item["expected_month"]))


def discover_all_reports(session: requests.Session | None = None) -> list[dict]:
    sess = session or requests.Session()
    landing = http_get(LANDING_URL, session=sess, headers=BROWSER_HEADERS)
    landing.raise_for_status()
    archive = http_get(ARCHIVE_URL, session=sess, headers=BROWSER_HEADERS)
    archive.raise_for_status()
    by_period: dict[tuple[int, int], dict] = {}
    for report in discover_archive_reports(archive.text):
        key = (report["expected_year"], report["expected_month"])
        by_period[key] = report
    soup = BeautifulSoup(landing.text, "html.parser")
    for anchor in soup.find_all("a", href=True):
        url = urljoin(LANDING_URL, anchor["href"].strip())
        link_text = " ".join(anchor.get_text(" ", strip=True).split())
        if not _is_consolidated_report(url, link_text):
            continue
        month = _month_from_text(link_text) or _month_from_text(url)
        year = _year_from_text(url)
        if month is None or year is None:
            continue
        key = (year, month)
        if key not in by_period:
            by_period[key] = {
                "url": url,
                "filename": unquote(url.rsplit("/", 1)[-1]),
                "link_text": link_text,
                "expected_year": year,
                "expected_month": month,
            }
    return apply_url_corrections(list(by_period.values()))


def money_fields_from_row(line: str) -> tuple[float, float, float, float] | None:
    if not MONEY_ROW_RE.match(line):
        return None
    tokens = re.findall(MONEY_FIELD, line)
    if len(tokens) != 4:
        return None
    values = [parse_money(token) for token in tokens]
    if any(value is None for value in values):
        return None
    return values[0], values[1], values[2], values[3]


def parse_inline_operator_row(line: str) -> tuple[str, tuple[float, float, float, float]] | None:
    match = INLINE_ROW_RE.match(line.strip())
    if not match:
        return None
    operator = match.group("operator").strip()
    tokens = re.findall(MONEY_FIELD, line)
    if len(tokens) != 4:
        return None
    values = [parse_money(token) for token in tokens]
    if any(value is None for value in values):
        return None
    return operator, (values[0], values[1], values[2], values[3])


def parse_report_period(text: str) -> tuple[int, int]:
    match = PERIOD_HEADING_RE.search(text)
    if not match:
        raise ValueError("Could not find Massachusetts report month/year heading")
    return int(match.group(2)), month_name_to_num(match.group(1).title())


def find_online_operator_text(pdf: pdfplumber.PDF) -> str:
    for page in pdf.pages:
        text = page.extract_text() or ""
        if not any(line.strip().upper() == "ONLINE LICENSEE" for line in text.splitlines()):
            continue
        upper = text.upper()
        if "MONTH OVER MONTH" in upper or "YEAR OVER YEAR" in upper:
            continue
        return text
    return ""


def parse_online_section(text: str) -> tuple[list[dict], dict | None]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    in_online = False
    operators: list[dict] = []
    total_online: dict | None = None

    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if line.upper() == "ONLINE LICENSEE":
            in_online = True
            idx += 1
            continue
        if not in_online:
            idx += 1
            continue
        if line.lower() == "total" and idx > 0:
            amounts = money_fields_from_row(lines[idx - 1])
            if amounts is not None:
                total_online = {
                    "wagers_settled": amounts[0],
                    "accrual_win": amounts[1],
                    "taxable_revenue": amounts[2],
                    "tax_collected": amounts[3],
                }
            break

        inline = parse_inline_operator_row(line)
        if inline is not None:
            operator, amounts = inline
            if operator not in RETAIL_OPERATORS and operator not in SKIP_LABELS:
                operators.append(
                    {
                        "operator": operator,
                        "wagers_settled": amounts[0],
                        "accrual_win": amounts[1],
                        "taxable_revenue": amounts[2],
                        "tax_collected": amounts[3],
                    }
                )
            idx += 1
            continue

        amounts = money_fields_from_row(line)
        if amounts is not None and idx + 1 < len(lines):
            operator = lines[idx + 1]
            if operator in RETAIL_OPERATORS or operator in SKIP_LABELS or operator.lower() == "total":
                idx += 1
                continue
            operators.append(
                {
                    "operator": operator,
                    "wagers_settled": amounts[0],
                    "accrual_win": amounts[1],
                    "taxable_revenue": amounts[2],
                    "tax_collected": amounts[3],
                }
            )
            idx += 2
            continue
        idx += 1

    return operators, total_online


# Revenue and tax reconcile to the cent; handle totals can be up to $1 below operator sums.
RECONCILE_TOLERANCE = {
    "wagers_settled": 1.00,
    "accrual_win": 0.01,
    "taxable_revenue": 0.01,
    "tax_collected": 0.01,
}


def reconcile_operators(operators: list[dict], total_online: dict) -> None:
    frame = pd.DataFrame(operators)
    for field in ("wagers_settled", "accrual_win", "taxable_revenue", "tax_collected"):
        operator_sum = round(float(frame[field].sum()), 2)
        official = round(float(total_online[field]), 2)
        if abs(operator_sum - official) > RECONCILE_TOLERANCE[field]:
            raise ValueError(
                f"Online total mismatch for {field}: operators={operator_sum} official={official}"
            )


def missing_reporting_months(
    discovered_periods: list[tuple[int, int]],
    parsed_periods: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Return months from March 2023 through the latest discovery that did not parse."""
    if not discovered_periods:
        return []
    latest_year, latest_month = max(discovered_periods)
    if (latest_year, latest_month) < ONLINE_START:
        return []
    expected: list[tuple[int, int]] = []
    current = date(ONLINE_START[0], ONLINE_START[1], 1)
    end = date(latest_year, latest_month, 1)
    while current <= end:
        expected.append((current.year, current.month))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    parsed = set(parsed_periods)
    return [period for period in expected if period not in parsed]


def coverage_bounds_from_rows(frame: pd.DataFrame) -> tuple[str | None, str | None]:
    """Earliest period_start and latest period_end from parsed operator rows."""
    if frame.empty:
        return None, None
    return str(frame["period_start"].min()), str(frame["period_end"].max())


def parse_revenue_pdf(
    content: bytes,
    *,
    expected_year: int | None = None,
    expected_month: int | None = None,
) -> tuple[list[dict], dict | None, tuple[int, int]]:
    with pdfplumber.open(BytesIO(content)) as pdf:
        full_text = "\n".join((page.extract_text() or "") for page in pdf.pages)
        online_text = find_online_operator_text(pdf)
    year, month = parse_report_period(full_text)
    if expected_year is not None and (year, month) != (expected_year, expected_month):
        raise ValueError(
            f"PDF heading {year}-{month:02d} does not match expected {expected_year}-{expected_month:02d}"
        )
    # Only February 2023 may lack online operators (pre-launch).
    allow_empty = (year, month) == PRE_LAUNCH_EMPTY_OK
    if not online_text:
        if allow_empty:
            return [], None, (year, month)
        raise ValueError(
            f"Missing ONLINE LICENSEE section for {year}-{month:02d} "
            "(empty online rows are only allowed for February 2023)"
        )
    operators, total_online = parse_online_section(online_text)
    if not operators:
        if allow_empty:
            return [], total_online, (year, month)
        raise ValueError(
            f"No Category 3 online operators parsed for {year}-{month:02d} "
            "(empty online rows are only allowed for February 2023)"
        )
    if total_online is None:
        raise ValueError("Missing Total Online control row")
    reconcile_operators(operators, total_online)
    return operators, total_online, (year, month)


def build_normalized_rows(
    operators: list[dict],
    *,
    year: int,
    month: int,
    source_url: str,
    source_file: str,
    source_sha256: str,
    retrieved_at: datetime,
    total_online: dict | None = None,
) -> pd.DataFrame:
    period_start, period_end = month_period(year, month)
    rows = []
    for operator in operators:
        rows.append(
            {
                "jurisdiction": JURISDICTION,
                "state_code": STATE_CODE,
                "vertical": VERTICAL,
                "channel": "online",
                "operator": operator["operator"],
                "row_type": "operator",
                "period_start": period_start,
                "period_end": period_end,
                "frequency": "monthly",
                "handle": operator["wagers_settled"],
                "gross_revenue": operator["accrual_win"],
                "adjusted_revenue": None,
                "taxable_revenue": operator["taxable_revenue"],
                "net_proceeds": None,
                "tax": operator["tax_collected"],
                "reported_revenue_name": REPORTED_REVENUE_NAME,
                "source_url": source_url,
                "source_file": source_file,
                "source_sha256": source_sha256,
                "retrieved_at_utc": retrieved_at.isoformat(),
                "report_status": "ok",
            }
        )
    if total_online is not None:
        # Retain the regulator's printed Total Online control row as-is.
        rows.append(
            {
                "jurisdiction": JURISDICTION,
                "state_code": STATE_CODE,
                "vertical": VERTICAL,
                "channel": "online",
                "operator": "STATEWIDE",
                "row_type": "official_statewide_total",
                "period_start": period_start,
                "period_end": period_end,
                "frequency": "monthly",
                "handle": total_online["wagers_settled"],
                "gross_revenue": total_online["accrual_win"],
                "adjusted_revenue": None,
                "taxable_revenue": total_online["taxable_revenue"],
                "net_proceeds": None,
                "tax": total_online["tax_collected"],
                "reported_revenue_name": REPORTED_REVENUE_NAME,
                "source_url": source_url,
                "source_file": source_file,
                "source_sha256": source_sha256,
                "retrieved_at_utc": retrieved_at.isoformat(),
                "report_status": "reconciled_printed_total",
            }
        )
    return pd.DataFrame(rows)


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

    failures: list[str] = []
    frames: list[pd.DataFrame] = []
    downloaded = 0
    parsed_months: list[tuple[int, int]] = []

    try:
        reports = discover_all_reports(session=sess)
    except Exception as exc:  # noqa: BLE001
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": VERTICAL,
                "status": "failed",
                "reason": str(exc),
                "official_url": ARCHIVE_URL,
                "available_frequency": "monthly",
                "earliest_period": None,
                "latest_period": None,
                "downloaded_file_count": 0,
                "normalized_row_count": 0,
                "last_retrieval_utc": retrieved_at.isoformat(),
            },
        )
        connection.close()
        raise

    for report in reports:
        try:
            response = http_get(report["url"], session=sess, headers=BROWSER_HEADERS)
            response.raise_for_status()
            content = response.content
            if not content.startswith(b"%PDF"):
                raise ValueError("Downloaded bytes do not look like a PDF")
            path = save_raw_bytes(root, STATE_CODE, content, report["filename"], retrieved_at=retrieved_at)
            operators, total_online, (year, month) = parse_revenue_pdf(
                content,
                expected_year=report["expected_year"],
                expected_month=report["expected_month"],
            )
            downloaded += 1
            if operators:
                frame = build_normalized_rows(
                    operators,
                    year=year,
                    month=month,
                    source_url=report["url"],
                    source_file=str(path.relative_to(root)).replace("\\", "/"),
                    source_sha256=sha256_bytes(content),
                    retrieved_at=retrieved_at,
                    total_online=total_online,
                )
                frames.append(frame)
                parsed_months.append((year, month))
                print(f"OK MA {year}-{month:02d}: {len(operators)} operators + printed Total Online")
            else:
                # Only reachable for February 2023 (pre-launch empty online section).
                print(f"OK MA {year}-{month:02d}: no online operator detail (pre-launch)")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{report['filename']}: {exc}")
            print(f"SKIP {report['filename']}: {exc}")

    discovered_periods = [(r["expected_year"], r["expected_month"]) for r in reports]
    missing_months = missing_reporting_months(discovered_periods, parsed_months)
    gap_notes = [f"missing report {year}-{month:02d}" for year, month in missing_months]
    coverage_issues = failures + gap_notes

    if frames:
        result = pd.concat(frames, ignore_index=True)
        upsert_gaming_results(connection, result)
        row_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM gaming_results WHERE state_code=? AND vertical=?",
                (STATE_CODE, VERTICAL),
            ).fetchone()[0]
        )
        earliest_period, latest_period = coverage_bounds_from_rows(result)
        upsert_coverage(
            connection,
            {
                "state_code": STATE_CODE,
                "vertical": VERTICAL,
                "status": "ok" if not coverage_issues else "partial",
                "reason": (
                    "; ".join(coverage_issues[:8])
                    if coverage_issues
                    else "MGC consolidated monthly PDFs: Category 3 operator rows with taxable revenue"
                ),
                "official_url": ARCHIVE_URL,
                "available_frequency": "monthly",
                "earliest_period": earliest_period,
                "latest_period": latest_period,
                "downloaded_file_count": downloaded,
                "normalized_row_count": row_count,
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
            "reason": "; ".join(coverage_issues) or "No Massachusetts PDFs collected",
            "official_url": ARCHIVE_URL,
            "available_frequency": "monthly",
            "earliest_period": None,
            "latest_period": None,
            "downloaded_file_count": downloaded,
            "normalized_row_count": 0,
            "last_retrieval_utc": utc_now().isoformat(),
        },
    )
    connection.close()
    return pd.DataFrame()
