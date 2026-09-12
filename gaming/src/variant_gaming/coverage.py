"""Helpers for recording source_coverage without inventing revenue rows."""

from __future__ import annotations

from pathlib import Path

from variant_gaming.common import project_root, utc_now
from variant_gaming.storage import (
    connect,
    default_db_path,
    ensure_schema,
    migrate_legacy_table,
    upsert_coverage,
)


def record_coverage(
    *,
    state_code: str,
    vertical: str,
    status: str,
    reason: str,
    official_url: str,
    available_frequency: str | None = None,
    earliest_period: str | None = None,
    latest_period: str | None = None,
    downloaded_file_count: int = 0,
    normalized_row_count: int = 0,
    root: Path | None = None,
    db_path: Path | None = None,
) -> dict:
    """Upsert a source_coverage row and return the payload."""
    root = root or project_root()
    db_path = db_path or default_db_path(root)
    connection = connect(db_path)
    migrate_legacy_table(connection)
    ensure_schema(connection)
    payload = {
        "state_code": state_code.upper(),
        "vertical": vertical,
        "status": status,
        "reason": reason,
        "official_url": official_url,
        "available_frequency": available_frequency,
        "earliest_period": earliest_period,
        "latest_period": latest_period,
        "downloaded_file_count": downloaded_file_count,
        "normalized_row_count": normalized_row_count,
        "last_retrieval_utc": utc_now().isoformat(),
    }
    upsert_coverage(connection, payload)
    connection.close()
    return payload
