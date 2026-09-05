"""Maine tribal mobile sports receipts; facility sportsbooks are excluded."""

import re
from urllib.parse import urljoin

import pandas as pd
import pdfplumber
from bs4 import BeautifulSoup

from variant_gaming.common import MONTH_NAMES, collect_reports, http_get, month_period

LANDING_URL = "https://www.maine.gov/dps/gcu/sports-wagering/sports-wagering-revenue"


def discover_reports(html):
    """Only the two tribal mobile reporting groups; exclude Oxford and First Tracks."""
    return list(dict.fromkeys(
        urljoin(LANDING_URL, a["href"])
        for a in BeautifulSoup(html, "html.parser").select("a[href]")
        if ".pdf" in a["href"].lower()
        and re.search(r"Passamaquoddy|Penobscot", a["href"], re.I)
    ))


def read_metric_row(page, label, month_headers):
    """Align amounts by their printed column, preserving blank historical months.

    PDF text splits some digits with spaces. Decimal positions identify the
    column even when future months are blank and the YTD column is populated.
    """
    matches = page.search(label, regex=False)
    if len(matches) != 1:
        raise ValueError(f"Maine row missing or repeated: {label}")
    match = matches[0]
    row = page.crop((page.bbox[0], match["top"] - 1, page.bbox[2], match["bottom"] + 1))
    text = row.extract_text(x_tolerance=2) or ""
    amounts = re.findall(r"\(?-?\d[\d, ]*\.\d{2}\)?", text)
    endings = [word for word in row.extract_words(x_tolerance=2)
               if re.search(r"\.\d{2}\)?$", word["text"])]
    if len(amounts) != len(endings):
        raise ValueError(f"Ambiguous Maine amount columns: {label}")
    values = {}
    for amount, word in zip(amounts, endings):
        eligible = [name for name, x in month_headers.items() if x < word["x1"]]
        name = eligible[-1]
        number = amount.replace(" ", "").replace(",", "")
        values[name] = -float(number[1:-1]) if number.startswith("(") else float(number)
    return values


def parse_report(path):
    """Read monthly native adjusted receipts and tax; never ingest the YTD total."""
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        text = page.extract_text() or ""
        if "SPORTS WAGERING - Passamaquoddy" in text:
            operator = "Passamaquoddy"
        elif "SPORTS WAGERING - Penobscot" in text and "Maliseet Micmac" in text:
            operator = "Penobscot Maliseet Micmac"
        else:
            raise ValueError("Not a supported Maine mobile tribal report")
        year = int(re.search(r"20\d{2}", text).group())
        words = page.extract_words()
        headers = {word["text"]: word["x0"] for word in words
                   if word["text"] in MONTH_NAMES + ["Y-T-D"]}
        headers = dict(sorted(headers.items(), key=lambda item: item[1]))
        if len(headers) != 13:
            raise ValueError("Maine requires twelve monthly columns and a YTD column")
        handle = read_metric_row(page, "Gross Event Wagering Receipts", headers)
        revenue = read_metric_row(page, "Adjusted Gross Receipts", headers)
        taxes = read_metric_row(page, "Total Tax revenue Due", headers)
    rows = []
    for month, name in enumerate(MONTH_NAMES, 1):
        if name not in handle and name not in revenue and name not in taxes:
            continue
        start, end = month_period(year, month)
        rows.append({
            "operator": operator, "row_type": "operator", "channel": "online",
            "frequency": "monthly", "period_start": start, "period_end": end,
            "handle": handle.get(name), "adjusted_revenue": revenue.get(name),
            "tax": taxes.get(name), "reported_revenue_name": "Adjusted Gross Receipts",
            "report_status": "reported",
        })
    # YTD is a reconciliation target only. Missing monthly cells stay missing.
    for values in [handle, revenue, taxes]:
        total = values.get("Y-T-D")
        monthly = [value for name, value in values.items() if name != "Y-T-D"]
        if total is not None and monthly and abs(sum(monthly) - total) > 0.15:
            raise ValueError("Maine monthly values do not reconcile to reported YTD")
    return pd.DataFrame(rows)


def collect_history(root=None, db_path=None):
    urls = discover_reports(http_get(LANDING_URL).text)
    return collect_reports(state_code="ME", jurisdiction="Maine",
                           vertical="online_sports_betting", landing_url=LANDING_URL,
                           urls=urls, parse_report=parse_report, root=root, db_path=db_path)
