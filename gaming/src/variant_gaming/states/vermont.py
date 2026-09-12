"""Vermont monthly adjusted sports revenue and contractual state revenue share."""

import re
from urllib.parse import urljoin

import pandas as pd
import pdfplumber
from variant_gaming.common import read_transcribed_report
from bs4 import BeautifulSoup

from variant_gaming.common import collect_reports, http_get, month_period, parse_money

LANDING_URL = "https://liquorandlottery.vermont.gov/monthly-reports"


def discover_reports():
    """Follow the official year pages and their linked summary PDFs."""
    soup = BeautifulSoup(http_get(LANDING_URL).text, "html.parser")
    years = list(dict.fromkeys(urljoin(LANDING_URL, a["href"])
                 for a in soup.select("a[href]")
                 if re.search(r"monthly-sports-wagering-reports-20\d{2}$", a["href"])))
    urls = []
    for year_url in years:
        page = BeautifulSoup(http_get(year_url).text, "html.parser")
        urls.extend(urljoin(year_url, a["href"]) for a in page.select("a[href]")
                    if a["href"].lower().endswith(".pdf"))
        for a in page.select('a[href*="/document/sports-wagering-executive-report-"]'):
            document_url = urljoin(year_url, a["href"])
            document = BeautifulSoup(http_get(document_url).text, "html.parser")
            urls.extend(urljoin(document_url, link["href"]) for link in document.select("a[href]")
                        if link["href"].lower().endswith(".pdf"))
    return list(dict.fromkeys(urls))


def parse_report(path):
    """Keep AGR separate from gross win; reported deductions include several items."""
    transcribed = read_transcribed_report(path)
    if transcribed is not None:
        return transcribed
    with pdfplumber.open(path) as pdf:
        text = re.sub(r"\s+", " ", pdf.pages[0].extract_text() or "")
    match = re.search(r"MONTH\s+([A-Za-z]+)\s+(20\d{2})", text)
    if not match or "DLL SPORTS WAGERING SUMMARY REPORT" not in text:
        raise ValueError("Vermont monthly report heading missing")
    period = pd.to_datetime(" ".join(match.groups()).title(), format="%B %Y")
    labels = {"handle": "Total Handle Receipts", "adjusted_revenue": "Adjusted Gross Sports Wagering Revenue",
              "tax": "Vermont Revenue Share", "payouts": "Less: Winning Payouts",
              "deductions": "Less: Resettlements, Voids, Tax, Promo"}
    amounts = {}
    for column, label in labels.items():
        value = re.search(re.escape(label) + r"\s+(\(?\$[\d,.]+\)?)", text)
        amounts[column] = parse_money(value.group(1)) if value else None
    if amounts["handle"] is None or amounts["adjusted_revenue"] is None:
        raise ValueError("Vermont handle or AGR missing from the expected summary")
    if all(amounts[key] is not None for key in ["handle", "payouts", "deductions", "adjusted_revenue"]):
        calculated = amounts["handle"] - amounts["payouts"] - amounts["deductions"]
        if abs(calculated - amounts["adjusted_revenue"]) > 2:
            raise ValueError("Vermont AGR does not reconcile to its printed deductions")
    start, end = month_period(period.year, period.month)
    return pd.DataFrame([{
        "operator": "STATEWIDE", "row_type": "official_statewide_total",
        "channel": "online", "frequency": "monthly", "period_start": start, "period_end": end,
        "handle": amounts["handle"], "adjusted_revenue": amounts["adjusted_revenue"],
        "tax": amounts["tax"], "reported_revenue_name": "Adjusted Gross Sports Wagering Revenue",
        "report_status": "reported",
    }])


def collect_history(root=None, db_path=None):
    return collect_reports(state_code="VT", jurisdiction="Vermont",
                           vertical="online_sports_betting", landing_url=LANDING_URL,
                           urls=discover_reports(), parse_report=parse_report, root=root, db_path=db_path)
