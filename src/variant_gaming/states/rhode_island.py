"""Rhode Island accrual sportsbook and iGaming reports, with retail excluded."""

import re
from functools import partial
from urllib.parse import urljoin

import pandas as pd
import pdfplumber

from variant_gaming.common import collect_reports, http_get, month_period, parse_money

LANDING_URL = "https://www.rilot.com/en-us/about-us/financials.html"


def discover_reports(html, vertical):
    """Report links also occur inside commented modal HTML."""
    name = "(?:SportsBookSummary|SportsbookWebsiteData)" if vertical == "online_sports_betting" else "iGamingWebsiteData"
    paths = re.findall(r'(/content/dam/[^"<>\s]*' + name + r'[^"<>\s]*\.pdf)', html, re.I)
    return list(dict.fromkeys(urljoin(LANDING_URL, path) for path in paths if "FY2019" not in path))


def parse_report(path, vertical="online_sports_betting"):
    """Read online sports or combined iSlots/iTables; exclude fiscal-year totals."""
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        text = page.extract_text() or ""
    sports = vertical == "online_sports_betting"
    required = "Online (Mobile)" if sports else "iGaming Revenue"
    if required not in text or "(Accrual)" not in text:
        raise ValueError(f"Missing Rhode Island {required} heading")
    rows = []
    for line in text.splitlines():
        # Each facility repeats the month/year. This avoids drifting numeric positions.
        parts = re.split(r"\b([A-Z][a-z]{2})\s+(\d{2})\b", line)
        if len(parts) != (13 if sports else 10):
            continue
        month, year, numbers = parts[7:10]
        # The selected group is online sports (third of four) or combined casino (third of three).
        if numbers.count("$") == 3:
            values = [value.replace(" ", "") for value in numbers.split("$")[1:]]
        else:
            # Some older PDFs separate the first digit from its comma-grouped amount.
            numbers = re.sub(r"\b(\d)\s+(?=\d{1,3},|,)", r"\1", numbers)
            values = re.findall(r"\(?\s*-?\d[\d,]*\s*\)?|(?<!\w)-(?!\w)", numbers)
        values = [parse_money(value) for value in values]
        if all(value is None for value in values):
            continue
        if len(values) != 3:
            raise ValueError(f"Unrecognized Rhode Island monthly values: {numbers}")
        handle, prizes, revenue = values
        if all(value is not None for value in values) and abs(handle - prizes - revenue) > 2:
            raise ValueError("Rhode Island wagers minus prizes does not reconcile")
        period = pd.to_datetime(f"{month} 20{year}", format="%b %Y")
        start, end = month_period(period.year, period.month)
        rows.append({
            "operator": "STATEWIDE", "row_type": "official_statewide_total",
            "channel": "online", "frequency": "monthly",
            "period_start": start, "period_end": end, "handle": handle,
            "gross_revenue": revenue,
            "reported_revenue_name": "Book Revenue" if sports else "Net Gaming Revenue (NGR)",
            "report_status": "unaudited_unadjusted",
        })
    # RI calls casino win NGR, but defines it as wagers less prizes before expenses.
    return pd.DataFrame(rows)


def collect_history(vertical, root=None, db_path=None):
    urls = discover_reports(http_get(LANDING_URL).text, vertical)
    return collect_reports(state_code="RI", jurisdiction="Rhode Island", vertical=vertical,
                           landing_url=LANDING_URL, urls=urls,
                           parse_report=partial(parse_report, vertical=vertical),
                           root=root, db_path=db_path)


def collect_sports_history(root=None, db_path=None):
    return collect_history("online_sports_betting", root, db_path)


def collect_casino_history(root=None, db_path=None):
    return collect_history("online_casino", root, db_path)
