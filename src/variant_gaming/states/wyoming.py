"""Wyoming's regulator-linked monthly online sports wagering tables."""

import re
from urllib.parse import urljoin

import pandas as pd
import pdfplumber
from bs4 import BeautifulSoup

from variant_gaming.common import collect_reports, http_get, month_period, parse_money

LANDING_URL = "https://gaming.wyo.gov/revenue-reports/financial-reports/combined-wagering-activity-reports"
ARCHIVE_URL = "https://gaming.wyo.gov/revenue-reports/historical-revenue-reports"


def discover_reports():
    """Follow dated report links, not unrelated Drive links in site navigation."""
    archive = BeautifulSoup(http_get(ARCHIVE_URL).text, "html.parser")
    pages = [LANDING_URL]
    pages.extend(urljoin(ARCHIVE_URL, a["href"]) for a in archive.select("a[href]")
                 if a.get_text(strip=True).startswith("Combined Wagering Reports")
                 or a["href"].endswith("/historical-revenue-reports/osw"))
    urls = []
    for page_url in dict.fromkeys(pages):
        soup = BeautifulSoup(http_get(page_url).text, "html.parser")
        for a in soup.select("a[href]"):
            if not re.fullmatch(r"[A-Za-z]+\s+20\d{2}", a.get_text(" ", strip=True)):
                continue
            match = re.search(r"drive.google.com/file/d/([^/]+)", a["href"])
            if match:
                urls.append("https://drive.google.com/uc?export=download&id=" + match.group(1))
    return list(dict.fromkeys(urls))


def parse_report(path):
    """Read only online sports; preserve reported gross and taxable revenue separately."""
    with pdfplumber.open(path) as pdf:
        heading = pdf.pages[0].extract_text() or ""
        date = re.search(r"\b([A-Za-z]+)\s+(20\d{2})\b", heading)
        if not date:
            raise ValueError("Wyoming monthly report date missing")
        tables = []
        sports_text = ""
        for page in pdf.pages:
            text = page.extract_text() or ""
            if "ONLINE SPORTS WAGERING" in text.upper():
                sports_text = text
                tables.extend(page.extract_tables())
    table = next((t for t in tables if "Monthly Wagers" in str(t) and "Gross Gaming Revenue" in str(t)), None)
    period = pd.to_datetime(" ".join(date.groups()).title(), format="%B %Y")
    start, end = month_period(period.year, period.month)
    if not table:
        return parse_older_report(sports_text, start, end)
    # July 2025 has an extra empty column before the metric labels.
    if all(not row[0] for row in table):
        table = [row[1:] for row in table]
    metrics = {str(row[0]).replace("\n", " "): row[1:] for row in table[1:]}
    rows = []
    for index, operator in enumerate(table[0][1:]):
        if not operator:
            raise ValueError("Wyoming operator heading missing")
        is_total = operator.strip().lower() == "total"
        values = {}
        for label, column in [("Monthly Wagers", "handle"), ("Gross Gaming Revenue", "gross_revenue"),
                              ("Taxable Gaming Revenue", "taxable_revenue"), ("Tax Due", "tax")]:
            cell = metrics.get(label, [None] * (len(table[0]) - 1))[index]
            values[column] = parse_money(str(cell).replace(" ", "")) if cell is not None else None
        rows.append({
            "operator": "STATEWIDE" if is_total else operator.replace("\n", " ").strip(),
            "row_type": "official_statewide_total" if is_total else "operator",
            "channel": "online", "frequency": "monthly", "period_start": start, "period_end": end,
            **values, "reported_revenue_name": "Gross Gaming Revenue", "report_status": "reported",
        })
    frame = pd.DataFrame(rows)
    totals = frame[frame.row_type == "official_statewide_total"]
    if len(totals) != 1:
        raise ValueError("Wyoming official total missing or repeated")
    for column in ["handle", "gross_revenue", "taxable_revenue", "tax"]:
        values = frame.loc[frame.row_type == "operator", column]
        total = totals.iloc[0][column]
        if values.notna().all() and pd.notna(total) and abs(values.sum() - total) > 0.1:
            raise ValueError(f"Wyoming {column} does not reconcile")
    return frame


def parse_older_report(text, start, end):
    """2021-22 statewide summaries, then 2023-24 operators across rows."""
    base = {"channel": "online", "frequency": "monthly", "period_start": start,
            "period_end": end, "reported_revenue_name": "Gross Gaming Revenue",
            "report_status": "reported"}
    rows = []
    if "Operator" in text and "Monthly" in text and "Cash Payouts" in text:
        operator_values = {}
        for line in text.splitlines():
            if not re.match(r"^(BetMGM|Caesars|DraftKings|FanDuel|Fanatics|Total)\s+\$", line):
                continue
            operator, *cells = line.split("$")
            if len(cells) == 5 and "Tax Due" not in text:
                cells.append("")  # One December 2023 source does not print tax due.
            if len(cells) not in {3, 6}:
                raise ValueError("Wyoming older operator columns changed")
            values = [parse_money(cell.strip()) for cell in cells]
            operator_values.setdefault(operator.strip(), []).extend(values)
        for operator, values in operator_values.items():
            if len(values) != 6:
                raise ValueError("Wyoming older split tables do not match")
            rows.append({**base, "operator": "STATEWIDE" if operator.strip() == "Total" else operator.strip(),
                         "row_type": "official_statewide_total" if operator.strip() == "Total" else "operator",
                         "handle": values[0], "gross_revenue": values[3],
                         "taxable_revenue": values[4], "tax": values[5]})
    elif "Wagering Revenue" in text:
        values = {}
        labels = {"handle": r"(?:Total )?Monthly Wagers", "gross_revenue": "Gross Gaming Revenue",
                  "net_proceeds": r"(?:Total )?Net Sports Wagering Proceeds",
                  "taxable_revenue": "Adjusted Taxable Gaming Revenue",
                  "tax": r"(?:Tax Revenue|Total Taxes)"}
        for column, label in labels.items():
            match = re.search(label + r"\s+\$\s*([\d,.()\-]+)", text)
            values[column] = parse_money(match.group(1)) if match else None
        rows = [{**base, **values, "operator": "STATEWIDE", "row_type": "official_statewide_total"}]
    if not rows or any(row["gross_revenue"] is None or row["handle"] is None for row in rows):
        raise ValueError("Wyoming online sports table missing or changed")
    frame = pd.DataFrame(rows)
    total = frame[frame.operator == "STATEWIDE"]
    if len(total) != 1:
        raise ValueError("Wyoming older report must contain one printed statewide total")
    if len(frame) > 1:
        for column in ["handle", "gross_revenue", "taxable_revenue", "tax"]:
            amounts = frame.loc[frame.row_type == "operator", column]
            if amounts.notna().all() and pd.notna(total.iloc[0][column]):
                if abs(amounts.sum() - total.iloc[0][column]) > 0.1:
                    raise ValueError(f"Wyoming older {column} does not reconcile")
    return frame


def collect_history(root=None, db_path=None):
    return collect_reports(state_code="WY", jurisdiction="Wyoming",
                           vertical="online_sports_betting", landing_url=LANDING_URL,
                           urls=discover_reports(), parse_report=parse_report, root=root, db_path=db_path)
