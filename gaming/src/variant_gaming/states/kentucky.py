"""Hash-pinned online tables from KHRG's public image meeting packets.

The six Q2 tables are AI-checked transcriptions, not analyst approval. Original
licensees, winnings, federal excise, pages and native labels remain in the CSV.
Only reported AGR, handle and tax enter normalized storage; GGR stays missing.
"""

from contextlib import closing
from pathlib import Path

import pandas as pd

from variant_gaming.common import (
    collect_reports, project_root, read_transcribed_report, sha256_bytes,
)

LANDING_URL = "https://khrc.ky.gov/newstatic_info.aspx/utils/newstatic_Info.aspx?menuid=80&static_ID=722"
REPORT_URL = "https://dcg.ky.gov/Documents/2025-06-24%20Meeting%20Materials%20PUBLIC.pdf"
LEGACY_SHA256 = "7f5d45a445bf4f13b90f2e63792eff205d9c9c5030db821313ea993e81b91641"
PACKETS = {
    "1212d5f940cf94cde825af308e894b2645baaaf13936388951b41c02ed6cba16": (
        "https://khrc.ky.gov/Documents/August%202025%20Public%20Meeting%20Materials.pdf",
        {"2025-04-01": (145, 153), "2025-05-01": (146, 154), "2025-06-01": (147, 155)},
    ),
    "4ed25614f397f74d68bc69ee97bc739baaec8b1e397ae435f8bb863dbf8270fd": (
        "https://khrc.ky.gov/Documents/20260609%20Board%20Meeting%20Materials%20-%20Public.pdf",
        {"2026-04-01": (89, 115)},
    ),
    "fb021bb81ff28d99b4a2b1f01c45bf51cde440d4e59fedc1b00d162f510b0326": (
        "https://khrc.ky.gov/Documents/20260811%20Board%20Meeting%20Materials%20-%20Public.pdf",
        {"2026-05-01": (47, 50), "2026-06-01": (48, 51)},
    ),
}
MONEY_COLUMNS = ["handle", "winnings", "federal_excise_tax", "adjusted_revenue", "tax"]
TRANSCRIPTION_STATUS = "ai_checked_manual_transcription_unreviewed"
OPERATOR_LICENSEES = {
    "DraftKings": "Cumberland Run", "Circa": "Kentucky Downs", "Fanatics": "Oak Grove",
    "Caesars": "Red Mile", "bet365": "Sandy's", "BetMGM": "Sandy's", "Fanduel": "Turfway Park",
}


def validate_transcriptions(rows):
    """Check the six complete Online tables without changing printed amounts.

    For whole-dollar values, four independently rounded components permit an
    AGR identity difference of at most $2. Summing n operators to the separately
    rounded total permits at most $(n + 1)/2, for each printed metric.
    """
    expected = {(digest, period) for digest, (_, periods) in PACKETS.items() for period in periods}
    actual = set(zip(rows.source_sha256, rows.period_start))
    if actual != expected:
        raise ValueError("Kentucky transcription packet/period coverage changed")
    if rows.duplicated(["source_sha256", "period_start", "operator"]).any():
        raise ValueError("Kentucky duplicate operator transcription")
    for (digest, period), group in rows.groupby(["source_sha256", "period_start"]):
        url, periods = PACKETS[digest]
        physical, printed = periods[period]
        expected_operators = dict(OPERATOR_LICENSEES)
        expected_operators["Penn Sports Inter..." if period.startswith("2025") else "Penn Sports Interactive, LLC"] = "Ellis Park"
        if period.startswith("2026"):
            expected_operators["Prime"] = "Churchill Downs"
        expected_operators["STATEWIDE"] = "Grand Total"
        if dict(zip(group.operator, group.native_licensee)) != expected_operators:
            raise ValueError("Kentucky complete native operator/licensee census changed")
        expected_name = "Adjusted Gross Revenue" if period.startswith("2025") else "Approximate AGR"
        metadata = {
            "source_url": url, "physical_pdf_page": physical, "printed_page": printed,
            "channel": "online", "frequency": "monthly", "rounding_unit_usd": 1,
            "period_end": str(pd.Period(period[:7]).end_time.date()),
            "reported_revenue_name": expected_name, "report_status": TRANSCRIPTION_STATUS,
        }
        if any(not group[column].eq(value).all() for column, value in metadata.items()):
            raise ValueError("Kentucky transcription source, period, precision or status changed")
        totals = group[group.row_type == "official_statewide_total"]
        operators = group[group.row_type == "operator"]
        if len(totals) != 1 or totals.operator.iloc[0] != "STATEWIDE" or len(operators) != len(group) - 1:
            raise ValueError("Kentucky online total/operator row types changed")
        native = group.native_operator.where(group.operator != "STATEWIDE", "STATEWIDE")
        if not native.eq(group.operator).all() or totals.native_operator.iloc[0] != "Grand Total":
            raise ValueError("Kentucky native operator label changed")
        money = group[MONEY_COLUMNS].apply(pd.to_numeric, errors="raise")
        if (money.isna().any().any() or money.abs().eq(float("inf")).any().any()
                or not ((money % 1) == 0).all().all()):
            raise ValueError("Kentucky amounts must be complete finite printed whole dollars")
        difference = money.handle - money.winnings - money.federal_excise_tax - money.adjusted_revenue
        if difference.abs().gt(2).any():
            raise ValueError("Kentucky AGR identity exceeds whole-dollar rounding bound")
        residual = operators[MONEY_COLUMNS].sum() - totals.iloc[0][MONEY_COLUMNS]
        if residual.abs().gt((len(operators) + 1) / 2).any():
            raise ValueError("Kentucky operator sum exceeds whole-dollar rounding bound")
    return rows


def load_transcriptions():
    """Read the explicit source manifest; never guess values from new images."""
    return validate_transcriptions(pd.read_csv(project_root() / "config/kentucky_online_transcriptions.csv"))


def parse_report(path):
    digest = sha256_bytes(Path(path).read_bytes())
    rows = load_transcriptions()
    matched = rows[rows.source_sha256 == digest].copy()
    if not matched.empty:
        return matched
    if digest != LEGACY_SHA256:
        raise ValueError("Kentucky image packet needs a checked transcription; source bytes changed")
    rows = read_transcribed_report(path)  # Keep the two legacy March-April 2025 rows.
    if rows is None:
        raise ValueError("Kentucky image packet needs a checked transcription; source bytes changed")
    return rows


def collect_history(root=None, db_path=None):
    from variant_gaming.storage import connect, default_db_path, upsert_coverage

    root = root or project_root()
    db_path = db_path or default_db_path(root)
    result = collect_reports(
        state_code="KY", jurisdiction="Kentucky", vertical="online_sports_betting",
        landing_url=LANDING_URL, urls=[REPORT_URL, *(packet[0] for packet in PACKETS.values())],
        parse_report=parse_report, root=root, db_path=db_path,
    )
    if not result.empty:
        with closing(connect(db_path)) as connection:
            coverage = dict(connection.execute(
                "SELECT * FROM source_coverage WHERE state_code='KY' AND vertical='online_sports_betting'"
            ).fetchone())
            coverage.update(status="partial", reason=coverage["reason"] +
                "; bounded manual transcription: legacy March-April 2025 statewide rows and six Q2 "
                "2025/2026 Online tables only; other months and newer packets need source review. "
                "AI-checked, not analyst approved; AGR is not GGR.")
            upsert_coverage(connection, coverage)
    return result
