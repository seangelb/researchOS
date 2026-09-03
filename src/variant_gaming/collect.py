"""Explicit mapping of inventory keys to collector callables."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pandas as pd

from variant_gaming.states import (
    connecticut,
    delaware,
    district_of_columbia,
    illinois,
    indiana,
    iowa,
    louisiana,
    maryland,
    massachusetts,
    michigan,
    missouri,
    new_hampshire,
    new_jersey,
    new_york,
    north_carolina,
    ohio,
    pennsylvania,
    tennessee,
    wave_coverage,
    west_virginia,
)

# Keys are (state_code, vertical)
Collector = Callable[..., pd.DataFrame]

COLLECTORS: dict[tuple[str, str], Collector] = {
    ("IL", "online_sports_betting"): illinois.collect_history,
    ("IN", "online_sports_betting"): indiana.collect_history,
    ("MD", "online_sports_betting"): maryland.collect_history,
    ("MI", "online_sports_betting"): michigan.collect_sports_history,
    ("MI", "online_casino"): michigan.collect_casino_history,
    ("MO", "online_sports_betting"): missouri.collect_history,
    ("NY", "online_sports_betting"): new_york.collect_history,
    ("PA", "online_sports_betting"): pennsylvania.collect_sports_history,
    ("PA", "online_casino"): pennsylvania.collect_casino_history,
    ("TN", "online_sports_betting"): tennessee.collect_history,
    # Wave 2 collectors
    ("DC", "online_sports_betting"): district_of_columbia.collect_history,
    ("DE", "online_casino"): delaware.collect_casino_history,
    ("DE", "online_sports_betting"): delaware.collect_sports_history,
    ("IA", "online_sports_betting"): iowa.collect_history,
    ("NJ", "online_casino"): new_jersey.collect_casino_history,
    ("NJ", "online_sports_betting"): new_jersey.collect_sports_history,
    ("WV", "online_casino"): west_virginia.collect_casino_history,
    ("WV", "online_sports_betting"): west_virginia.collect_sports_history,
    # Wave 3–4 collectors with real upserts
    ("LA", "online_sports_betting"): louisiana.collect_history,
    ("OH", "online_sports_betting"): ohio.collect_history,
    ("CT", "online_sports_betting"): connecticut.collect_sports_history,
    ("CT", "online_casino"): connecticut.collect_casino_history,
    ("NC", "online_sports_betting"): north_carolina.collect_history,
    ("NH", "online_sports_betting"): new_hampshire.collect_history,
    ("MA", "online_sports_betting"): massachusetts.collect_history,
    # Wave 3–5 coverage / special-case recorders (no invented revenue rows)
    ("AZ", "online_sports_betting"): wave_coverage.collect_arizona_coverage,
    ("CO", "online_sports_betting"): wave_coverage.collect_colorado_coverage,
    ("KS", "online_sports_betting"): wave_coverage.collect_kansas_coverage,
    ("KY", "online_sports_betting"): wave_coverage.collect_kentucky_coverage,
    ("ME", "online_sports_betting"): wave_coverage.collect_maine_sports_coverage,
    ("ME", "online_casino"): wave_coverage.collect_maine_casino_coverage,
    ("RI", "online_sports_betting"): wave_coverage.collect_rhode_island_sports_coverage,
    ("RI", "online_casino"): wave_coverage.collect_rhode_island_casino_coverage,
    ("VT", "online_sports_betting"): wave_coverage.collect_vermont_coverage,
    ("VA", "online_sports_betting"): wave_coverage.collect_virginia_coverage,
    ("WY", "online_sports_betting"): wave_coverage.collect_wyoming_coverage,
    ("FL", "online_sports_betting"): wave_coverage.collect_florida_coverage,
    ("AR", "online_sports_betting"): wave_coverage.collect_arkansas_coverage,
    ("NV", "online_sports_betting"): wave_coverage.collect_nevada_coverage,
    ("MS", "on_premises_mobile_sports_betting"): wave_coverage.collect_mississippi_coverage,
    ("MT", "location_based_mobile_sports_betting"): wave_coverage.collect_montana_coverage,
    ("OR", "online_sports_betting"): wave_coverage.collect_oregon_coverage,
}


def run_collector(state_code: str, vertical: str, **kwargs) -> pd.DataFrame:
    key = (state_code.upper(), vertical)
    if key not in COLLECTORS:
        raise KeyError(f"No collector registered for {key}")
    return COLLECTORS[key](**kwargs)


def inventory_collector_order(root: Path | None = None) -> list[tuple[int, str, str]]:
    """Return (wave, state_code, vertical) in inventory wave order."""
    from variant_gaming.common import project_root

    root = root or project_root()
    inventory = pd.read_csv(root / "config" / "state_gaming_source_inventory.csv")
    inventory = inventory.sort_values(["recommended_wave", "state_code", "vertical"])
    order: list[tuple[int, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in inventory.itertuples(index=False):
        key = (str(row.state_code).upper(), str(row.vertical))
        if key in COLLECTORS and key not in seen:
            order.append((int(row.recommended_wave), key[0], key[1]))
            seen.add(key)
    for key in COLLECTORS:
        if key not in seen:
            order.append((99, key[0], key[1]))
    return order


def inventory_official_url(state_code: str, vertical: str, root: Path | None = None) -> str:
    """Look up the official landing URL for one inventory row."""
    from variant_gaming.common import project_root

    root = root or project_root()
    inventory = pd.read_csv(root / "config" / "state_gaming_source_inventory.csv")
    match = inventory[
        (inventory["state_code"].astype(str).str.upper() == state_code.upper())
        & (inventory["vertical"].astype(str) == vertical)
    ]
    if match.empty:
        return ""
    return str(match.iloc[0]["official_landing_url"])


def run_all_collectors(root: Path | None = None, db_path: Path | None = None) -> pd.DataFrame:
    """
    Run every mapped collector one at a time, in inventory wave order.

    Distinguishes this-run success (`run_status`) from stored dataset coverage
    (`coverage_status`). A raised exception always yields run_status=failed even
    when older coverage remains ok. The database is never replaced.
    """
    from variant_gaming.common import project_root, utc_now
    from variant_gaming.coverage import record_coverage
    from variant_gaming.storage import connect, default_db_path, ensure_schema, migrate_legacy_table

    root = root or project_root()
    db_path = db_path or default_db_path(root)
    setup = connect(db_path)
    migrate_legacy_table(setup)
    ensure_schema(setup)
    setup.close()

    def _coverage(state_code: str, vertical: str):
        conn = connect(db_path)
        try:
            return conn.execute(
                "SELECT status, reason FROM source_coverage WHERE state_code=? AND vertical=?",
                (state_code, vertical),
            ).fetchone()
        finally:
            conn.close()

    def _db_rows(state_code: str, vertical: str) -> int:
        conn = connect(db_path)
        try:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM gaming_results WHERE state_code=? AND vertical=?",
                    (state_code, vertical),
                ).fetchone()[0]
            )
        finally:
            conn.close()

    rows: list[dict] = []
    for wave, state_code, vertical in inventory_collector_order(root):
        print(f"Wave {wave}: {state_code} {vertical}", flush=True)
        started = utc_now()
        returned_rows = 0
        run_status = "failed"
        run_error = None
        coverage_status = None
        coverage_reason = None

        try:
            frame = COLLECTORS[(state_code, vertical)](root=root, db_path=db_path)
            if frame is None:
                frame = pd.DataFrame()
            returned_rows = int(len(frame))
            run_status = "completed"
            coverage = _coverage(state_code, vertical)
            if coverage is not None:
                coverage_status = coverage["status"]
                coverage_reason = coverage["reason"]
            else:
                coverage_status = "ok" if returned_rows else "no_rows"
                coverage_reason = None
            print(
                f"  run={run_status} coverage={coverage_status}: {returned_rows} rows returned",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 — continue other states
            run_status = "failed"
            run_error = str(exc)
            coverage_before = _coverage(state_code, vertical)
            if coverage_before is None:
                official_url = inventory_official_url(state_code, vertical, root=root)
                record_coverage(
                    state_code=state_code,
                    vertical=vertical,
                    status="failed",
                    reason=run_error,
                    official_url=official_url,
                    root=root,
                    db_path=db_path,
                )
                coverage_after = _coverage(state_code, vertical)
                coverage_status = coverage_after["status"] if coverage_after else "failed"
                coverage_reason = coverage_after["reason"] if coverage_after else run_error
            else:
                # Keep previously collected dataset coverage visible; do not overwrite run failure.
                coverage_status = coverage_before["status"]
                coverage_reason = coverage_before["reason"]
            print(
                f"  run={run_status} coverage={coverage_status}: {run_error}",
                flush=True,
            )

        rows.append(
            {
                "wave": wave,
                "state_code": state_code,
                "vertical": vertical,
                "run_status": run_status,
                "coverage_status": coverage_status,
                "coverage_reason": coverage_reason,
                "run_error": run_error,
                "returned_rows": returned_rows,
                "database_rows": _db_rows(state_code, vertical),
                "finished_at_utc": utc_now().isoformat(),
                "started_at_utc": started.isoformat(),
            }
        )

    return pd.DataFrame(rows)


if __name__ == "__main__":
    summary = run_all_collectors()
    print(summary.to_string(index=False))
