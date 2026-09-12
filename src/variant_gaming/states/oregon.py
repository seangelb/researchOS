"""Oregon Lottery monthly sports figures from published commission statements."""

import re
from urllib.parse import urljoin

import pandas as pd
import pdfplumber
from bs4 import BeautifulSoup

from variant_gaming.common import collect_reports, http_get, month_period, parse_money

LANDING_URL = "https://www.oregonlottery.org/about/how-we-operate/commission-and-director-info/"


def discover_reports():
    """The public meeting archive is rolling; older files may need a records request."""
    soup = BeautifulSoup(http_get(LANDING_URL).text, "html.parser")
    meetings = list(dict.fromkeys(urljoin(LANDING_URL, a["href"])
                    for a in soup.select("a[href]") if "/commission-meeting-" in a["href"]))
    urls = []
    for url in meetings:
        page = BeautifulSoup(http_get(url).text, "html.parser")
        urls.extend(urljoin(url, a["href"]) for a in page.select("a[href]")
                    if "financial statements" in (a.get_text(" ", strip=True) + " " + a["href"].replace("-", " ")).lower()
                    and a["href"].lower().endswith(".pdf"))
    return list(dict.fromkeys(urls))


def parse_report(path):
    """Select monthly actual dollars, excluding budgets, summaries in thousands, and YTD."""
    with pdfplumber.open(path) as pdf:
        texts = [page.extract_text() or "" for page in pdf.pages[:5]]
    text = next((t for t in texts if "Operating Statement" in t
                 and "For the month ending" in t), "")
    match = re.search(r"For the month ending ([A-Za-z]+ \d{1,2}, 20\d{2})", text)
    if not match or "Traditional Video Sports" not in text or "(in thousands)" in text:
        raise ValueError("Oregon monthly actual statement or sports column missing")
    period = pd.to_datetime(match.group(1))
    amounts = {}
    for label in ["Sports Wagering (Gross Receipts)", "Prizes", "Net Revenue"]:
        line = next((line for line in text.splitlines() if line.startswith(label + " ")), "")
        line = re.sub(r"\b(\d)\s+(?=\d{1,3},|,)", r"\1", line)
        values = re.findall(r"\(?[\d,]+\)?", line[len(label):].replace("$", ""))
        if len(values) < 3:
            raise ValueError(f"Oregon actual values missing: {label}")
        amounts[label] = parse_money(values[0 if label.startswith("Sports") else 2])
    handle = amounts["Sports Wagering (Gross Receipts)"]
    revenue = amounts["Net Revenue"]
    if abs(handle + amounts["Prizes"] - revenue) > 2:
        raise ValueError("Oregon sports receipts less prizes does not reconcile")
    start, end = month_period(period.year, period.month)
    return pd.DataFrame([{
        "operator": "STATEWIDE", "row_type": "official_statewide_total", "channel": "online",
        "frequency": "monthly", "period_start": start, "period_end": end,
        "handle": handle, "gross_revenue": revenue, "reported_revenue_name": "Net Revenue (Sports)",
        "report_status": "draft_unaudited_non_gaap" if "Draft" in text else "reported_non_gaap",
    }])


def collect_history(root=None, db_path=None):
    return collect_reports(state_code="OR", jurisdiction="Oregon",
                           vertical="online_sports_betting", landing_url=LANDING_URL,
                           urls=discover_reports(), parse_report=parse_report, root=root, db_path=db_path)
