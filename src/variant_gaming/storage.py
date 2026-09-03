"""SQLite storage for normalized gaming_results with safe upserts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS gaming_results (
    jurisdiction TEXT NOT NULL,
    state_code TEXT NOT NULL,
    vertical TEXT NOT NULL,
    channel TEXT NOT NULL,
    operator TEXT NOT NULL,
    row_type TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    frequency TEXT NOT NULL,
    handle REAL,
    gross_revenue REAL,
    adjusted_revenue REAL,
    taxable_revenue REAL,
    net_proceeds REAL,
    tax REAL,
    reported_revenue_name TEXT,
    source_url TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    retrieved_at_utc TEXT NOT NULL,
    report_status TEXT NOT NULL,
    PRIMARY KEY (
        state_code,
        vertical,
        channel,
        operator,
        row_type,
        period_start,
        period_end,
        source_sha256
    )
);

CREATE TABLE IF NOT EXISTS source_coverage (
    state_code TEXT NOT NULL,
    vertical TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    official_url TEXT,
    available_frequency TEXT,
    earliest_period TEXT,
    latest_period TEXT,
    downloaded_file_count INTEGER,
    normalized_row_count INTEGER,
    last_retrieval_utc TEXT,
    PRIMARY KEY (state_code, vertical)
);
"""

ALLOWED_VERTICALS = frozenset({"online_sports_betting", "online_casino"})
ALLOWED_CHANNELS = frozenset({"online", "combined", "location_based_mobile"})
ALLOWED_ROW_TYPES = frozenset({"operator", "official_statewide_total"})
REQUIRED_IDENTIFYING = [
    "jurisdiction",
    "state_code",
    "vertical",
    "channel",
    "operator",
    "row_type",
    "period_start",
    "period_end",
]

RESULT_COLUMNS = [
    "jurisdiction",
    "state_code",
    "vertical",
    "channel",
    "operator",
    "row_type",
    "period_start",
    "period_end",
    "frequency",
    "handle",
    "gross_revenue",
    "adjusted_revenue",
    "taxable_revenue",
    "net_proceeds",
    "tax",
    "reported_revenue_name",
    "source_url",
    "source_file",
    "source_sha256",
    "retrieved_at_utc",
    "report_status",
]

UPSERT_SQL = """
INSERT INTO gaming_results (
    jurisdiction, state_code, vertical, channel, operator, row_type,
    period_start, period_end, frequency,
    handle, gross_revenue, adjusted_revenue, taxable_revenue, net_proceeds, tax,
    reported_revenue_name, source_url, source_file, source_sha256,
    retrieved_at_utc, report_status
) VALUES (
    :jurisdiction, :state_code, :vertical, :channel, :operator, :row_type,
    :period_start, :period_end, :frequency,
    :handle, :gross_revenue, :adjusted_revenue, :taxable_revenue, :net_proceeds, :tax,
    :reported_revenue_name, :source_url, :source_file, :source_sha256,
    :retrieved_at_utc, :report_status
)
ON CONFLICT (
    state_code, vertical, channel, operator, row_type,
    period_start, period_end, source_sha256
) DO UPDATE SET
    jurisdiction = excluded.jurisdiction,
    frequency = excluded.frequency,
    handle = excluded.handle,
    gross_revenue = excluded.gross_revenue,
    adjusted_revenue = excluded.adjusted_revenue,
    taxable_revenue = excluded.taxable_revenue,
    net_proceeds = excluded.net_proceeds,
    tax = excluded.tax,
    reported_revenue_name = excluded.reported_revenue_name,
    source_url = excluded.source_url,
    source_file = excluded.source_file,
    retrieved_at_utc = excluded.retrieved_at_utc,
    report_status = excluded.report_status
"""

COVERAGE_UPSERT_SQL = """
INSERT INTO source_coverage (
    state_code, vertical, status, reason, official_url, available_frequency,
    earliest_period, latest_period, downloaded_file_count, normalized_row_count,
    last_retrieval_utc
) VALUES (
    :state_code, :vertical, :status, :reason, :official_url, :available_frequency,
    :earliest_period, :latest_period, :downloaded_file_count, :normalized_row_count,
    :last_retrieval_utc
)
ON CONFLICT (state_code, vertical) DO UPDATE SET
    status = excluded.status,
    reason = excluded.reason,
    official_url = excluded.official_url,
    available_frequency = excluded.available_frequency,
    earliest_period = excluded.earliest_period,
    latest_period = excluded.latest_period,
    downloaded_file_count = excluded.downloaded_file_count,
    normalized_row_count = excluded.normalized_row_count,
    last_retrieval_utc = excluded.last_retrieval_utc
"""


def default_db_path(root: Path) -> Path:
    return root / "data" / "gaming.sqlite"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)
    connection.commit()


def migrate_legacy_table(connection: sqlite3.Connection) -> None:
    """
    If an older gaming_results table exists without the new columns,
    rename it aside and recreate the normalized schema.
    """
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='gaming_results'"
    ).fetchone()
    if row is None:
        ensure_schema(connection)
        return

    columns = {
        info[1]
        for info in connection.execute("PRAGMA table_info(gaming_results)").fetchall()
    }
    required = {"row_type", "frequency", "source_sha256", "taxable_revenue", "net_proceeds", "report_status"}
    if required.issubset(columns):
        ensure_schema(connection)
        return

    connection.execute("ALTER TABLE gaming_results RENAME TO gaming_results_legacy")
    ensure_schema(connection)
    connection.commit()


def validate_gaming_results_frame(frame: pd.DataFrame) -> None:
    """Reject missing identifiers and values the primary table does not allow."""
    missing_cols = [c for c in REQUIRED_IDENTIFYING if c not in frame.columns]
    if missing_cols:
        raise ValueError(f"gaming_results missing identifying columns: {missing_cols}")
    for col in REQUIRED_IDENTIFYING:
        blank = frame[col].isna() | frame[col].astype(str).str.strip().isin({"", "None", "nan"})
        if bool(blank.any()):
            raise ValueError(f"Missing required identifying field: {col}")
    checks = (
        ("vertical", ALLOWED_VERTICALS),
        ("channel", ALLOWED_CHANNELS),
        ("row_type", ALLOWED_ROW_TYPES),
    )
    for col, allowed in checks:
        values = set(frame[col].astype(str))
        bad = sorted(values - allowed)
        if bad:
            raise ValueError(f"Disallowed {col} values: {bad}")


def _row_to_params(row: dict) -> dict:
    params = {}
    for col in RESULT_COLUMNS:
        value = row.get(col)
        if value is None or (isinstance(value, float) and pd.isna(value)):
            params[col] = None
        elif hasattr(value, "item"):
            params[col] = value.item()
        else:
            params[col] = value
    return params


def upsert_gaming_results(connection: sqlite3.Connection, frame: pd.DataFrame) -> int:
    """Insert/update rows for one collector run. Never drops other states."""
    if frame is None or frame.empty:
        return 0
    missing = [c for c in RESULT_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"gaming_results missing columns: {missing}")

    ensure_schema(connection)
    validate_gaming_results_frame(frame)
    records = frame[RESULT_COLUMNS].to_dict(orient="records")
    with connection:
        connection.executemany(UPSERT_SQL, [_row_to_params(r) for r in records])
    return len(records)


def upsert_coverage(connection: sqlite3.Connection, coverage: dict) -> None:
    ensure_schema(connection)
    with connection:
        connection.execute(COVERAGE_UPSERT_SQL, coverage)


def read_gaming_results(connection: sqlite3.Connection, where: str = "", params: tuple = ()) -> pd.DataFrame:
    sql = "SELECT * FROM gaming_results"
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY state_code, vertical, period_start, channel, operator, row_type"
    return pd.read_sql_query(sql, connection, params=params)


def export_csv(connection: sqlite3.Connection, path: Path, sql: str, params: tuple = ()) -> pd.DataFrame:
    frame = pd.read_sql_query(sql, connection, params=params)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return frame


def count_by_state(connection: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT state_code, vertical, channel, COUNT(*) AS n
        FROM gaming_results
        GROUP BY state_code, vertical, channel
        ORDER BY state_code, vertical, channel
        """,
        connection,
    )
