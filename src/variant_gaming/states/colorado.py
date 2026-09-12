"""Colorado internet net sports betting proceeds from the state library archive."""

import re
from urllib.parse import urljoin

import pandas as pd
import pdfplumber
from variant_gaming.common import read_transcribed_report
from bs4 import BeautifulSoup

from variant_gaming.common import collect_reports, http_get, month_period, parse_money

LANDING_URL = "https://spl.cde.state.co.us/artemis/revserials/rev14111internet/"


def discover_reports(html):
    """Use government-retained copies when the regulator host refuses downloads."""
    return list(dict.fromkeys(urljoin(LANDING_URL, a["href"])
                for a in BeautifulSoup(html, "html.parser").select("a[href]")
                if re.search(r"rev14111\d{6}internet\.pdf$", a["href"])))


def parse_report(path):
    """Select Internet in the first statewide row; the second section is cumulative."""
    transcribed = read_transcribed_report(path)
    if transcribed is not None:
        return transcribed
    with pdfplumber.open(path) as pdf:
        text = pdf.pages[0].extract_text() or ""
    if "Statewide Summary Retail Online Total" in text:
        return parse_summary(text)
    monthly = text.split("Annual Sports Betting Proceeds")[0]
    date = re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", monthly)
    total = re.search(r"Statewide\s+([^\n]+)", monthly)
    if not date or not total or "Internet" not in monthly or "NSBP" not in monthly:
        raise ValueError("Colorado monthly internet proceeds table missing")
    values = re.findall(r"-?\$[\d,]+\.\d{2}", total.group(1))
    if len(values) != 6:
        raise ValueError("Colorado statewide row requires six amounts")
    period = pd.to_datetime(date.group())
    start, end = month_period(period.year, period.month)
    return pd.DataFrame([{
        "operator": "STATEWIDE", "row_type": "official_statewide_total", "channel": "online",
        "frequency": "monthly", "period_start": start, "period_end": end,
        "net_proceeds": parse_money(values[1]), "tax": parse_money(values[4]),
        "reported_revenue_name": "Net Sports Betting Proceeds (NSBP)", "report_status": "reported",
    }])


def parse_summary(text):
    """Some archived files contain the richer retail/online summary instead."""
    date = re.search(r"\b([A-Z][a-z]+) (20\d{2})\b", text)
    if not date:
        raise ValueError("Colorado summary month missing")
    period = pd.to_datetime(" ".join(date.groups()), format="%B %Y")
    values = {}
    for label, column in [(r"Total GGR\*?", "gross_revenue"), (r"Total NSBP\*\*", "net_proceeds"),
                          ("Total Taxes", "tax"), ("Total", "handle")]:
        line = re.search(r"^" + label + r"\s+(\$[^\n]+)", text, re.M)
        amounts = re.findall(r"\$\s*([\d,.()\-]+)", line.group(1)) if line else []
        if len(amounts) != (6 if column == "handle" else 3):
            raise ValueError(f"Colorado summary {column} changed")
        values[column] = parse_money(amounts[1])
        if column == "handle" and abs(values[column] - parse_money(amounts[4]) - values["gross_revenue"]) > 0.1:
            raise ValueError("Colorado online GGR does not reconcile to wagers less payments")
    start, end = month_period(period.year, period.month)
    return pd.DataFrame([{"operator": "STATEWIDE", "row_type": "official_statewide_total",
        "channel": "online", "frequency": "monthly", "period_start": start, "period_end": end,
        **values, "reported_revenue_name": "Gross Gaming Revenue / Net Sports Betting Proceeds",
        "report_status": "subject_to_revision"}])


def collect_history(root=None, db_path=None):
    return collect_reports(state_code="CO", jurisdiction="Colorado",
                           vertical="online_sports_betting", landing_url=LANDING_URL,
                           urls=discover_reports(http_get(LANDING_URL).text),
                           parse_report=parse_report, root=root, db_path=db_path)
