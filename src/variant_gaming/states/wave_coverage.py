"""Coverage-first collectors for Wave 3–5 special / blocked / PDF-heavy cases."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from variant_gaming.common import http_get, project_root, utc_now
from variant_gaming.coverage import record_coverage

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def _probe(url: str, session: requests.Session | None = None) -> tuple[int | None, str]:
    sess = session or requests.Session()
    try:
        response = sess.get(url, headers=BROWSER_HEADERS, timeout=60, allow_redirects=True)
        snippet = response.text[:200].lower()
        note = f"HTTP {response.status_code}"
        if response.status_code == 403 or "just a moment" in snippet or "cf-browser-verification" in snippet:
            note += "; Cloudflare/captcha interstitial"
        elif "login" in snippet and "password" in snippet:
            note += "; login wall suspected"
        return response.status_code, note
    except Exception as exc:  # noqa: BLE001
        return None, f"request error: {exc}"


def collect_arizona_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://gaming.az.gov/resources/reports"
    status_code, note = _probe(url)
    record_coverage(
        state_code="AZ",
        vertical="online_sports_betting",
        status="blocked",
        reason=f"Official reports landing blocked for automated clients ({note}). No revenue upsert.",
        official_url=url,
        available_frequency="monthly",
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_colorado_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://sbg.colorado.gov/sports-betting-monthly-reports"
    status_code, note = _probe(url)
    record_coverage(
        state_code="CO",
        vertical="online_sports_betting",
        status="pdf_only_not_yet_parsed",
        reason=(
            f"Official monthly Sports Betting Proceeds PDFs are public ({note}). "
            "Deferred machine parse; no invented rows."
        ),
        official_url=url,
        available_frequency="monthly",
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_kansas_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://www.kslottery.gov/publications/sports-monthly-detail-breakdown/"
    status_code, note = _probe(url)
    record_coverage(
        state_code="KS",
        vertical="online_sports_betting",
        status="pdf_only_not_yet_parsed",
        reason=(
            f"Official monthly detail PDFs separate online/retail ({note}). "
            "Deferred pdfplumber normalization; no invented rows."
        ),
        official_url=url,
        available_frequency="monthly",
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_kentucky_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://khrc.ky.gov/new_docs.aspx?cat=76&menuid=80"
    record_coverage(
        state_code="KY",
        vertical="online_sports_betting",
        status="blocked",
        reason=(
            "Current sports wagering market report is Tableau-only; "
            "per project rules do not scrape chart pixels. No CSV/Excel export discovered."
        ),
        official_url=url,
        available_frequency="monthly",
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_maine_sports_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://www11.maine.gov/dps/gcu/sports-wagering/sports-wagering-revenue"
    status_code, note = _probe(url)
    record_coverage(
        state_code="ME",
        vertical="online_sports_betting",
        status="pdf_only_not_yet_parsed",
        reason=(
            f"Official operator PDF revenue distributions are public ({note}). "
            "PDF text spacing is fragile; deferred upsert."
        ),
        official_url=url,
        available_frequency="monthly",
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_maine_casino_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://www.maine.gov/dps/gcu/I-Gaming"
    record_coverage(
        state_code="ME",
        vertical="online_casino",
        status="legal_not_reporting",
        reason="I-Gaming authorized in law, but no official operating revenue reports found yet.",
        official_url=url,
        available_frequency=None,
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_rhode_island_sports_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://www.rilot.com/en-us/about-us/financials.html"
    record_coverage(
        state_code="RI",
        vertical="online_sports_betting",
        status="pdf_only_not_yet_parsed",
        reason="Official SportsbookSummary FY PDFs linked from financials modal; deferred parse.",
        official_url=url,
        available_frequency="monthly",
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_rhode_island_casino_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://www.rilot.com/en-us/about-us/financials.html"
    record_coverage(
        state_code="RI",
        vertical="online_casino",
        status="pdf_only_not_yet_parsed",
        reason="Official iGamingWebsiteData FY PDFs linked from financials modal; deferred parse.",
        official_url=url,
        available_frequency="monthly",
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_vermont_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://liquorandlottery.vermont.gov/sports-wagering-reports"
    status_code, note = _probe(url)
    record_coverage(
        state_code="VT",
        vertical="online_sports_betting",
        status="pdf_only_not_yet_parsed",
        reason=(
            f"Monthly SW executive summary PDFs are linked from year pages ({note}). "
            "Deferred parse; inventory notes possible 403 to some automated clients."
        ),
        official_url=url,
        available_frequency="monthly",
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_virginia_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://www.valottery.com/about-us/casinosandsportsbetting/sportsbetting"
    status_code, note = _probe(url)
    record_coverage(
        state_code="VA",
        vertical="online_sports_betting",
        status="not_publicly_available",
        reason=(
            f"Landing page has licensing/resources but no clean monthly online sports revenue archive ({note})."
        ),
        official_url=url,
        available_frequency=None,
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_wyoming_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://gaming.wyo.gov/revenue-reports/financial-reports/combined-wagering-activity-reports"
    status_code, note = _probe(url)
    record_coverage(
        state_code="WY",
        vertical="online_sports_betting",
        status="blocked_or_unavailable_export",
        reason=(
            f"Combined wagering activity landing returned no discoverable PDF/Excel export links ({note})."
        ),
        official_url=url,
        available_frequency="monthly",
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_florida_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://flgaming.gov/pmw/statistics/"
    record_coverage(
        state_code="FL",
        vertical="online_sports_betting",
        status="not_publicly_available",
        reason=(
            "No official monthly online sportsbook series on FGCC statistics page "
            "(slots/cardroom/pari-mutuel only). No third-party estimates used."
        ),
        official_url=url,
        available_frequency=None,
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_arkansas_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = (
        "https://www.dfa.arkansas.gov/office/taxes/excise-tax-administration/"
        "miscellaneous-tax/miscellaneous-tax-descriptions/"
    )
    record_coverage(
        state_code="AR",
        vertical="online_sports_betting",
        status="combined_only",
        reason=(
            "Official public landing does not expose an isolated online sports archive; "
            "DFA reporting may combine casino gaming and sports wagering."
        ),
        official_url=url,
        available_frequency=None,
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_nevada_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://www.gaming.nv.gov/about-us/gaming-revenue-information-gri/"
    record_coverage(
        state_code="NV",
        vertical="online_sports_betting",
        status="combined_only",
        reason=(
            "Official GRI monthly PDFs provide sports-pool totals that do not cleanly isolate mobile. "
            "Channel must be treated as combined; not online-only. No online-only upsert."
        ),
        official_url=url,
        available_frequency="monthly",
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_mississippi_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://www.msgamingcommission.com/reports/monthly_reports"
    status_code, note = _probe(url)
    record_coverage(
        state_code="MS",
        vertical="on_premises_mobile_sports_betting",
        status="on_premises_only",
        reason=(
            f"Official sports wagering Excel/PDF exist ({note}), but mobile wagering is "
            "casino-premises limited. Recorded in source_coverage only; not exported into "
            "primary online_* consolidation."
        ),
        official_url=url,
        available_frequency="monthly",
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_montana_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://montanalottery.com/"
    record_coverage(
        state_code="MT",
        vertical="location_based_mobile_sports_betting",
        status="location_based_mobile",
        reason=(
            "Montana sports wagers are tied to sales-agent locations (location_based_mobile). "
            "Archive is news/PDF oriented; kept separate from unrestricted statewide mobile markets."
        ),
        official_url=url,
        available_frequency="weekly",
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_oregon_coverage(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    url = "https://www.oregonlottery.org/annual-report-2025/"
    status_code, note = _probe(url)
    record_coverage(
        state_code="OR",
        vertical="online_sports_betting",
        status="annual_only",
        reason=(
            f"Official public reporting is mainly annual/summary rather than a clean monthly archive ({note}). "
            "Annual frequency recorded as acceptable coverage."
        ),
        official_url=url,
        available_frequency="annual",
        root=root,
        db_path=db_path,
    )
    return pd.DataFrame()


def collect_all_coverage(root: Path | None = None, db_path: Path | None = None) -> None:
    """Record coverage for all Wave 3–5 non-upsert special/blocked rows."""
    collectors = [
        collect_arizona_coverage,
        collect_colorado_coverage,
        collect_kansas_coverage,
        collect_kentucky_coverage,
        collect_maine_sports_coverage,
        collect_maine_casino_coverage,
        collect_rhode_island_sports_coverage,
        collect_rhode_island_casino_coverage,
        collect_vermont_coverage,
        collect_virginia_coverage,
        collect_wyoming_coverage,
        collect_florida_coverage,
        collect_arkansas_coverage,
        collect_nevada_coverage,
        collect_mississippi_coverage,
        collect_montana_coverage,
        collect_oregon_coverage,
    ]
    for fn in collectors:
        fn(root=root, db_path=db_path)
        print(f"coverage OK: {fn.__name__}")
