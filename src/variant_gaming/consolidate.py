"""Build consolidated CSV exports from gaming_results + source_coverage."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from variant_gaming.common import project_root
from variant_gaming.storage import connect, default_db_path, ensure_schema, migrate_legacy_table

ONLINE_CHANNELS = {"online"}
PRIMARY_VERTICALS = {"online_sports_betting", "online_casino"}


def revenue_basis(row: pd.Series) -> str | None:
    """Pick a single labeled revenue basis; never mix definitions silently."""
    if pd.notna(row.get("gross_revenue")):
        return "GGR"
    if pd.notna(row.get("adjusted_revenue")):
        return "AGR"
    if pd.notna(row.get("taxable_revenue")):
        return "taxable_revenue"
    if pd.notna(row.get("net_proceeds")):
        return "net_proceeds"
    return None


def revenue_amount(row: pd.Series) -> float | None:
    basis = revenue_basis(row)
    if basis == "GGR":
        return float(row["gross_revenue"])
    if basis == "AGR":
        return float(row["adjusted_revenue"])
    if basis == "taxable_revenue":
        return float(row["taxable_revenue"])
    if basis == "net_proceeds":
        return float(row["net_proceeds"])
    return None


def dedupe_latest_observation(results: pd.DataFrame) -> pd.DataFrame:
    """
    Keep the latest retrieval for each logical business key.
    Amendments (different source_sha256) are preserved in gaming_results.csv,
    but state/operator exports should not double-count the same period.
    """
    if results.empty:
        return results
    frame = results.copy()
    frame["_retrieved_sort"] = pd.to_datetime(frame["retrieved_at_utc"], utc=True, errors="coerce")
    business_key = [
        "state_code",
        "vertical",
        "channel",
        "operator",
        "row_type",
        "period_start",
        "period_end",
    ]
    frame = frame.sort_values("_retrieved_sort")
    frame = frame.drop_duplicates(business_key, keep="last")
    return frame.drop(columns=["_retrieved_sort"])


def build_state_period_revenue(results: pd.DataFrame) -> pd.DataFrame:
    """
    One nonduplicated state-period-vertical-channel result.
    Prefer official statewide totals; else sum complete operator detail.
    """
    frame = dedupe_latest_observation(results)
    frame = frame[
        frame["vertical"].isin(PRIMARY_VERTICALS)
        & frame["channel"].isin(ONLINE_CHANNELS | {"combined", "location_based_mobile"})
    ].copy()
    if frame.empty:
        return pd.DataFrame()

    frame["revenue_basis"] = frame.apply(revenue_basis, axis=1)
    frame["revenue"] = frame.apply(revenue_amount, axis=1)

    rows: list[dict] = []
    keys = ["state_code", "vertical", "channel", "period_start", "period_end", "frequency"]
    for key, group in frame.groupby(keys, dropna=False):
        statewide = group[group["row_type"] == "official_statewide_total"]
        # Prefer one official statewide total when present (already deduped to latest observation).
        chosen = None
        source = None
        if not statewide.empty:
            chosen = statewide.iloc[-1]
            source = "official_statewide_total"
        else:
            ops = group[group["row_type"] == "operator"]
            if ops.empty:
                continue
            # Aggregate only when operators share the same revenue_basis (or all null)
            bases = set(ops["revenue_basis"].dropna().unique().tolist())
            if len(bases) > 1:
                # Do not silently sum different definitions
                for basis in sorted(bases):
                    subset = ops[ops["revenue_basis"] == basis]
                    rows.append(
                        {
                            "state_code": key[0],
                            "jurisdiction": subset["jurisdiction"].iloc[0],
                            "vertical": key[1],
                            "channel": key[2],
                            "period_start": key[3],
                            "period_end": key[4],
                            "frequency": key[5],
                            "handle": float(pd.to_numeric(subset["handle"], errors="coerce").sum())
                            if subset["handle"].notna().any()
                            else None,
                            "revenue": float(pd.to_numeric(subset["revenue"], errors="coerce").sum()),
                            "revenue_basis": basis,
                            "reported_revenue_name": subset["reported_revenue_name"].dropna().iloc[0]
                            if subset["reported_revenue_name"].notna().any()
                            else None,
                            "aggregation_source": "operator_sum_split_by_basis",
                            "operator_count": int(len(subset)),
                        }
                    )
                continue
            chosen_basis = next(iter(bases)) if bases else None
            rows.append(
                {
                    "state_code": key[0],
                    "jurisdiction": ops["jurisdiction"].iloc[0],
                    "vertical": key[1],
                    "channel": key[2],
                    "period_start": key[3],
                    "period_end": key[4],
                    "frequency": key[5],
                    "handle": float(pd.to_numeric(ops["handle"], errors="coerce").sum())
                    if ops["handle"].notna().any()
                    else None,
                    "revenue": float(pd.to_numeric(ops["revenue"], errors="coerce").sum())
                    if ops["revenue"].notna().any()
                    else None,
                    "revenue_basis": chosen_basis,
                    "reported_revenue_name": ops["reported_revenue_name"].dropna().iloc[0]
                    if ops["reported_revenue_name"].notna().any()
                    else None,
                    "aggregation_source": "operator_sum",
                    "operator_count": int(len(ops)),
                }
            )
            continue

        rows.append(
            {
                "state_code": key[0],
                "jurisdiction": chosen["jurisdiction"],
                "vertical": key[1],
                "channel": key[2],
                "period_start": key[3],
                "period_end": key[4],
                "frequency": key[5],
                "handle": float(chosen["handle"]) if pd.notna(chosen.get("handle")) else None,
                "revenue": float(chosen["revenue"]) if pd.notna(chosen.get("revenue")) else None,
                "revenue_basis": chosen["revenue_basis"],
                "reported_revenue_name": chosen.get("reported_revenue_name"),
                "aggregation_source": source,
                "operator_count": int((group["row_type"] == "operator").sum()),
            }
        )
    return pd.DataFrame(rows)


def build_operator_revenue(results: pd.DataFrame) -> pd.DataFrame:
    ops = dedupe_latest_observation(results)
    ops = ops[
        (ops["row_type"] == "operator")
        & ops["vertical"].isin(PRIMARY_VERTICALS)
        & ops["channel"].isin(ONLINE_CHANNELS | {"combined", "location_based_mobile"})
    ].copy()
    if ops.empty:
        return ops
    ops["revenue_basis"] = ops.apply(revenue_basis, axis=1)
    ops["revenue"] = ops.apply(revenue_amount, axis=1)
    cols = [
        "jurisdiction",
        "state_code",
        "vertical",
        "channel",
        "operator",
        "period_start",
        "period_end",
        "frequency",
        "handle",
        "gross_revenue",
        "adjusted_revenue",
        "taxable_revenue",
        "net_proceeds",
        "tax",
        "revenue",
        "revenue_basis",
        "reported_revenue_name",
        "source_url",
        "source_sha256",
        "retrieved_at_utc",
        "report_status",
    ]
    return ops[cols].sort_values(["state_code", "vertical", "period_start", "operator"])


def build_source_coverage_export(connection) -> pd.DataFrame:
    inventory = pd.read_csv(project_root() / "config" / "state_gaming_source_inventory.csv")
    try:
        coverage = pd.read_sql_query("SELECT * FROM source_coverage", connection)
    except Exception:
        coverage = pd.DataFrame()

    if coverage.empty:
        out = inventory.rename(
            columns={
                "official_landing_url": "official_url",
                "status_note": "reason",
            }
        )
        out["status"] = "not_collected"
        out["available_frequency"] = None
        out["earliest_period"] = None
        out["latest_period"] = None
        out["downloaded_file_count"] = 0
        out["normalized_row_count"] = 0
        out["last_retrieval_utc"] = None
        return out[
            [
                "state_code",
                "vertical",
                "status",
                "reason",
                "official_url",
                "available_frequency",
                "earliest_period",
                "latest_period",
                "downloaded_file_count",
                "normalized_row_count",
                "last_retrieval_utc",
            ]
        ]

    merged = inventory.merge(coverage, on=["state_code", "vertical"], how="left", suffixes=("_inv", ""))
    merged["status"] = merged["status"].fillna("not_collected")
    merged["reason"] = merged["reason"].fillna(merged.get("status_note"))
    merged["official_url"] = merged["official_url"].fillna(merged["official_landing_url"])
    for col in [
        "available_frequency",
        "earliest_period",
        "latest_period",
        "last_retrieval_utc",
    ]:
        if col not in merged:
            merged[col] = None
    for col in ["downloaded_file_count", "normalized_row_count"]:
        merged[col] = merged[col].fillna(0).astype(int)
    return merged[
        [
            "state_code",
            "vertical",
            "status",
            "reason",
            "official_url",
            "available_frequency",
            "earliest_period",
            "latest_period",
            "downloaded_file_count",
            "normalized_row_count",
            "last_retrieval_utc",
        ]
    ].sort_values(["state_code", "vertical"])


def export_all(root: Path | None = None) -> dict[str, Path]:
    root = root or project_root()
    processed = root / "data" / "processed"
    processed.mkdir(parents=True, exist_ok=True)

    conn = connect(default_db_path(root))
    migrate_legacy_table(conn)
    ensure_schema(conn)

    results = pd.read_sql_query("SELECT * FROM gaming_results", conn)
    results_path = processed / "gaming_results.csv"
    results.to_csv(results_path, index=False)

    state_period = build_state_period_revenue(results)
    state_period_path = processed / "state_period_revenue.csv"
    state_period.to_csv(state_period_path, index=False)

    operator = build_operator_revenue(results)
    operator_path = processed / "operator_revenue.csv"
    operator.to_csv(operator_path, index=False)

    coverage = build_source_coverage_export(conn)
    coverage_path = processed / "source_coverage.csv"
    coverage.to_csv(coverage_path, index=False)

    conn.close()
    return {
        "gaming_results": results_path,
        "state_period_revenue": state_period_path,
        "operator_revenue": operator_path,
        "source_coverage": coverage_path,
    }


if __name__ == "__main__":
    paths = export_all()
    for name, path in paths.items():
        print(name, path, path.stat().st_size)
