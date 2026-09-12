"""Small, explicit MA/NY recent refresh. Returns a report log, not observations.

MA selects the latest discovered months; NY downloads the current workbooks,
whose fiscal-year sheets retain weekly history. State modules still own parsing.
Calling collect_recent explicitly enables downloads and writes to its destination.
"""

from pathlib import Path

import pandas as pd
import requests

from variant_gaming.common import http_get, project_root, save_raw_bytes, sha256_bytes, utc_now
from variant_gaming.states import massachusetts as ma, new_york as ny
from variant_gaming.storage import connect, ensure_schema, upsert_gaming_results, validate_gaming_results_frame

RECENT_SOURCES = {("MA", "online_sports_betting"), ("NY", "online_sports_betting")}
LOG_COLUMNS = [
    "state_code", "vertical", "report", "period_start", "period_end", "source_url",
    "download_status", "bytes_changed", "parsed_rows", "stored_rows", "existing_rows",
    "validation", "reason", "source_file", "source_sha256",
]


def discover_recent_reports(state_code, *, report_limit=2, include_operators=True, session=None):
    """Discover MA's latest N months or NY's current statewide/operator workbooks.

    Only index pages are fetched here. NY's reporting dates are learned by parsing
    the workbook, not inferred from the capture date or URL.
    """
    if state_code == "MA":
        reports = ma.discover_all_reports(session=session)
        return [dict(item, report=f"{item['expected_year']}-{item['expected_month']:02d}")
                for item in reports[-report_limit:]]
    if state_code != "NY":
        raise ValueError("Recent updates support only MA and NY online sports betting")
    landing = http_get(ny.LANDING_URL, session=session)
    discovery = ny.discover_ny_sports_workbook_links(landing.text, ny.LANDING_URL)
    reports = [{"report": "STATEWIDE", "url": discovery["statewide"]["discovered_url"],
                "filename": "ny_statewide_sports_wagering_weekly.xlsx"}]
    if include_operators:
        reports.extend({"report": item["source_operator_name"], "url": item["discovered_url"],
                        "filename": f"ny_{ny.operator_slug(item['source_operator_name'])}_sports_wagering_weekly.xlsx"}
                       for item in discovery["operators"])
    return reports


def _stored_report(connection, state_code, report):
    sql = "SELECT * FROM gaming_results WHERE state_code=? AND vertical='online_sports_betting'"
    if state_code == "MA":
        sql += " AND period_start=?"
        identity = report["report"] + "-01"
    else:
        sql += " AND operator=?"
        identity = report["report"]
    return pd.read_sql_query(sql, connection, params=(state_code, identity))


def _retained_path(root, state_code, digest, stored):
    # Check the content, not just a hash-looking filename. Reuse earlier captures.
    candidates = [root / str(name) for name in stored.source_file.drop_duplicates()]
    candidates.extend(sorted((root / "data" / "raw" / state_code).glob(f"*/{digest}_*")))
    for path in dict.fromkeys(candidates):
        if path.is_file() and sha256_bytes(path.read_bytes()) == digest:
            return path
    return None


def _parse_report(state_code, report, content, provenance):
    if state_code == "MA":
        operators, total, (year, month) = ma.parse_revenue_pdf(
            content, expected_year=report["expected_year"], expected_month=report["expected_month"])
        return ma.build_normalized_rows(operators, year=year, month=month, total_online=total, **provenance)
    weekly, _reconciliation = ny.parse_ny_workbook(
        content, skip_zero_placeholders=report["report"] != "STATEWIDE")
    return ny.build_normalized_rows(
        weekly, operator=report["report"],
        row_type="official_statewide_total" if report["report"] == "STATEWIDE" else "operator",
        **provenance)


def _validate_report(frame):
    """Reject malformed keys/dates before a report's atomic upsert; USD may be negative."""
    if frame.empty:
        raise ValueError("No supported observations parsed")
    validate_gaming_results_frame(frame)
    keys = ["state_code", "vertical", "channel", "operator", "row_type", "period_start", "period_end"]
    if frame.duplicated(keys).any():
        raise ValueError("Duplicate observation keys within report")
    start = pd.to_datetime(frame.period_start, errors="raise")
    end = pd.to_datetime(frame.period_end, errors="raise")
    if start.isna().any() or end.isna().any() or (start > end).any():
        raise ValueError("Invalid reporting dates")
    for column in ["handle", "gross_revenue"]:
        values = pd.to_numeric(frame[column], errors="raise")
        if values.isna().any() or values.isin([float("inf"), -float("inf")]).any():
            raise ValueError(f"Missing or non-finite {column}")


def collect_recent(state_code, vertical="online_sports_betting", *, root=None, db_path,
                   report_limit=2, include_operators=True, force_reparse=False, session=None):
    """Download recent reports and return one log row per report (counts, dates, sources).

    db_path is required so the write destination is always explicit. bytes_changed
    is unknown on a first download; otherwise it compares with stored versions of
    this report. stored_rows counts upserted rows, not net new rows. An unchanged
    download skips parsing only when identical bytes AND a complete prior ingestion
    are retained. One small receipt table stores each successful report's row count;
    legacy versions without a receipt are parsed once to establish that count.
    force_reparse deliberately updates parsed values for that same source hash;
    use a separately reviewed candidate database for historical parser replays.

    Changed hashes remain separate observations. This log describes this run;
    it neither renews approvals nor replaces full-history source_coverage records.
    """
    state_code = state_code.upper()
    if (state_code, vertical) not in RECENT_SOURCES:
        raise ValueError("Recent updates support only MA and NY online sports betting; choose history explicitly")
    if not isinstance(report_limit, int) or isinstance(report_limit, bool) or report_limit < 1:
        raise ValueError("report_limit must be a positive integer")
    root = Path(root or project_root())
    sess = session or requests.Session()
    own_session = session is None
    connection = None
    attempts = []
    base = dict(state_code=state_code, vertical=vertical, report="discovery", period_start=None,
                period_end=None, source_url=ma.ARCHIVE_URL if state_code == "MA" else ny.LANDING_URL,
                download_status="not_attempted", bytes_changed=None, parsed_rows=0, stored_rows=0,
                existing_rows=0, validation="not_run", reason="", source_file="", source_sha256="")
    try:
        try:
            reports = discover_recent_reports(state_code, report_limit=report_limit,
                                             include_operators=include_operators, session=sess)
            if not reports:
                raise ValueError("No recent reports discovered")
        except Exception as exc:
            return pd.DataFrame([{**base, "reason": f"Discovery failed: {exc}"}], columns=LOG_COLUMNS)
        connection = connect(Path(db_path))
        ensure_schema(connection)
        connection.execute("""CREATE TABLE IF NOT EXISTS recent_ingestions (
            state_code TEXT, vertical TEXT, report TEXT, source_sha256 TEXT, row_count INTEGER,
            PRIMARY KEY (state_code, vertical, report, source_sha256))""")
        for report in reports:
            attempt = {**base, "report": report["report"], "source_url": report["url"]}
            if state_code == "MA":
                start = pd.Timestamp(report["expected_year"], report["expected_month"], 1)
                attempt.update(period_start=start.date().isoformat(),
                               period_end=(start + pd.offsets.MonthEnd()).date().isoformat())
            try:
                response = http_get(report["url"], session=sess,
                                    headers=ma.BROWSER_HEADERS if state_code == "MA" else {})
                content = response.content
                if not content:
                    raise ValueError("Empty report download")
                attempt["download_status"] = "downloaded"
                digest = sha256_bytes(content)
                attempt["source_sha256"] = digest
                previous = _stored_report(connection, state_code, report)
                identical = previous[previous.source_sha256 == digest]
                receipt_key = (state_code, vertical, report["report"], digest)
                receipt = connection.execute(
                    "SELECT row_count FROM recent_ingestions WHERE state_code=? AND vertical=? AND report=? AND source_sha256=?",
                    receipt_key).fetchone()
                attempt["bytes_changed"] = None if previous.empty else identical.empty
                attempt["existing_rows"] = len(identical)
                retained = _retained_path(root, state_code, digest, identical)
                was_retained = retained is not None
                captured = utc_now()
                path = retained or save_raw_bytes(root, state_code, content, report["filename"], captured)
                attempt["source_file"] = path.relative_to(root).as_posix()
                complete = receipt is not None and receipt[0] == len(identical) and len(identical) > 0
                if was_retained and complete and not force_reparse:
                    attempt.update(validation="previously_ingested", reason="Unchanged bytes already retained and ingested",
                                   period_start=identical.period_start.min(), period_end=identical.period_end.max())
                else:
                    frame = _parse_report(state_code, report, content, dict(
                        source_url=report["url"], source_file=attempt["source_file"],
                        source_sha256=digest, retrieved_at=captured))
                    attempt["parsed_rows"] = len(frame)
                    _validate_report(frame)
                    attempt.update(period_start=frame.period_start.min(), period_end=frame.period_end.max())
                    attempt["stored_rows"] = upsert_gaming_results(connection, frame)
                    with connection:
                        connection.execute("INSERT OR REPLACE INTO recent_ingestions VALUES (?, ?, ?, ?, ?)",
                                           (*receipt_key, len(frame)))
                    attempt.update(validation="passed", reason="Forced reparse" if force_reparse else "")
            except Exception as exc:
                if attempt["download_status"] == "not_attempted":
                    attempt["download_status"] = "failed"
                else:
                    attempt["validation"] = "failed"
                attempt["reason"] = str(exc)
            attempts.append(attempt)
        return pd.DataFrame(attempts, columns=LOG_COLUMNS)
    finally:
        if connection is not None:
            connection.close()
        if own_session:
            sess.close()
