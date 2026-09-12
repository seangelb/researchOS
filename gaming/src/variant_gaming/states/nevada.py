"""Nevada statewide mobile win, reported in thousands; handle is not estimated."""

import re
from urllib.parse import urljoin

import pandas as pd
import pdfplumber
from variant_gaming.common import read_transcribed_report
from bs4 import BeautifulSoup

from variant_gaming.common import collect_reports, http_get, month_period, parse_money

LANDING_URL = "https://www.gaming.nv.gov/about-us/gaming-revenue-information-gri/"


def discover_reports(html):
    """Mobile detail starts in January 2020; older combined sports pools are excluded."""
    urls = []
    for a in BeautifulSoup(html, "html.parser").select("a[href]"):
        label = a.get_text(" ", strip=True)
        year = re.search(r"20\d{2}", label)
        if label.startswith("GRI ") and year and int(year.group()) >= 2020:
            urls.append(urljoin(LANDING_URL, a["href"]))
    return list(dict.fromkeys(urls))


def parse_report(path):
    """Select the statewide page and current month, not regions or trailing periods."""
    transcribed = read_transcribed_report(path)
    if transcribed is not None:
        return transcribed
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages[:6]:
            text = page.extract_text() or ""
            if "Statewide - All Nonrestricted Locations" in text and "Sports - Mobile" in text:
                break
        else:
            raise ValueError("Nevada statewide mobile detail missing")
    date = re.search(r"Current Month - ([A-Za-z]+)-(20\d{2})", text)
    mobile = re.search(r"Sports - Mobile\s+(\d+)\s+(\(?[\d,]+\)?)", text)
    if not date or not mobile or "thousands" not in text:
        raise ValueError("Unrecognized Nevada period, mobile win, or units")
    period = pd.to_datetime(" ".join(date.groups()), format="%B %Y")
    start, end = month_period(period.year, period.month)
    return pd.DataFrame([{
        "operator": "STATEWIDE", "row_type": "official_statewide_total",
        "channel": "online", "frequency": "monthly",
        "period_start": start, "period_end": end,
        "gross_revenue": parse_money(mobile.group(2)) * 1000,
        "reported_revenue_name": "Sports - Mobile Win Amount",
        "report_status": "subject_to_revision",
    }])


def collect_history(root=None, db_path=None):
    urls = discover_reports(http_get(LANDING_URL).text)
    return collect_reports(state_code="NV", jurisdiction="Nevada",
                           vertical="online_sports_betting", landing_url=LANDING_URL,
                           urls=urls, parse_report=parse_report, root=root, db_path=db_path)
