"""Kansas online settled wagers and reported net revenue; never substitute GGR."""

import re
from urllib.parse import urljoin

import pandas as pd
import pdfplumber
from variant_gaming.common import read_transcribed_report
from bs4 import BeautifulSoup

from variant_gaming.common import collect_reports, http_get, month_period, parse_money

LANDING_URL = "https://www.kslottery.gov/publications/sports-monthly-detail-breakdown/"


def read_unruled_table(text):
    """Some 2025 PDFs have no usable cell borders; their eight fields remain explicit."""
    rows = [["Casino", "Provider", "Settled", "Prizes", "Promotions", "Excise",
             "Carryover", "Net Revenues", "Carryover", "State Share"]]
    money = r"\(?\s*\$?\s*-?\d[\d, ]*(?:\.\d+)?\)?|(?<!\S)-(?!\S)"
    for line in text.splitlines():
        if not re.match(r"Boot Hill|Kansas Star|Hollywood|KS Crossing|Kansas Crossing|Subtotal", line):
            continue
        start = re.search(r"\s(?=\(?\s*(?:\$|\d))", line)
        if not start:
            continue
        label = line[:start.start()].strip()
        amounts = re.findall(money, line[start.end():])
        if len(amounts) != 8:
            raise ValueError(f"Kansas expects eight numeric fields: {line}")
        rows.append([label, ""] + [value.replace(" ", "") for value in amounts])
    return rows


def discover_reports(html):
    """Use the monthly detail archive, not the summary that masks some losses."""
    soup = BeautifulSoup(html, "html.parser")
    return list(dict.fromkeys(
        urljoin(LANDING_URL, a["href"]) for a in soup.select("a[href]")
        if re.search(r"sports-wagering-monthly-detail-.*\.pdf", a["href"], re.I)
    ))


def parse_report(path):
    """Read only the current-month table; page two is cumulative fiscal-year data."""
    transcribed = read_transcribed_report(path)
    if transcribed is not None:
        return transcribed
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        # Four 2025 exports have negligible rotation that is misread as vertical text.
        for char in page.chars:
            if abs(char["matrix"][1]) < 1e-5 and abs(char["matrix"][2]) < 1e-5:
                char["upright"] = True
        text = page.extract_text() or ""
        match = re.search(r"Sports Wagering Revenues\s+([A-Za-z]+ 20\d{2})", text)
        if not match or "Fiscal Year Through" in text:
            raise ValueError("Missing Kansas current-month heading")
        period = pd.to_datetime(match.group(1), format="%B %Y")
        table = page.extract_table()
    if not table or "Settled" not in str(table[0]):
        table = read_unruled_table(text)
    if "Carryover" not in str(table[0]):
        raise ValueError("Unrecognized Kansas detail columns")
    old_format = len(table[0]) == 9
    rows, online = [], False
    for cells in table[1:]:
        label = " ".join(str(c or "") for c in cells[:2]).replace("\n", " ").strip()
        if "Subtotal" in label and "Retail" in label:
            online = True
            continue
        if not online or not label:
            continue
        if old_format:
            cells = cells[:6] + [None] + cells[6:]
        if len(cells) < 10:
            raise ValueError("Kansas detail requires ten columns")
        # Some PDFs split the final State Share cell at an extra vertical rule.
        cells = cells[:9] + ["".join(str(c or "") for c in cells[9:])]
        is_total = "Subtotal" in label and "Online" in label
        start, end = month_period(period.year, period.month)
        rows.append({
            "operator": "STATEWIDE" if is_total else label,
            "row_type": "official_statewide_total" if is_total else "operator",
            "channel": "online", "frequency": "monthly",
            "period_start": start, "period_end": end,
            "handle": parse_money(cells[2]), "net_proceeds": parse_money(cells[7]),
            "tax": parse_money(cells[9]), "reported_revenue_name": "Net Revenues",
            "report_status": "unaudited",
        })
        if is_total:
            break
    frame = pd.DataFrame(rows)
    if frame.empty or not is_total:
        raise ValueError("Kansas online subtotal missing")
    # Published whole-dollar cells can differ by rounding across providers.
    for metric in ["handle", "net_proceeds", "tax"]:
        values = frame.iloc[:-1][metric]
        total = frame.iloc[-1][metric]
        if values.notna().all() and pd.notna(total) and abs(values.sum() - total) > len(values):
            raise ValueError(f"Kansas online {metric} does not reconcile")
    return frame


def collect_history(root=None, db_path=None):
    urls = discover_reports(http_get(LANDING_URL).text)
    return collect_reports(state_code="KS", jurisdiction="Kansas",
                           vertical="online_sports_betting", landing_url=LANDING_URL,
                           urls=urls, parse_report=parse_report, root=root, db_path=db_path)
