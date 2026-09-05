"""Build consolidated CSV exports from gaming_results + source_coverage."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from variant_gaming.common import project_root
from variant_gaming.storage import connect_readonly, default_db_path

ONLINE_CHANNELS = {"online"}
PRIMARY_VERTICALS = {"online_sports_betting", "online_casino"}
# Evidence that an official_statewide_total came from a printed control row,
# not a collector-calculated operator sum. Required for IL/IN acceptance.
PRINTED_OFFICIAL_PROVENANCE = frozenset(
    {
        "printed_statewide_total",
        "reconciled_printed_total",
        "reported_printed_total",
    }
)


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


MONEY_COLUMNS = ["handle", "gross_revenue", "adjusted_revenue", "taxable_revenue", "net_proceeds", "tax"]
REVENUE_LABELS = {
    "gross_revenue": "GGR",
    "adjusted_revenue": "AGR",
    "taxable_revenue": "taxable_revenue",
    "net_proceeds": "net_proceeds",
    "handle": "handle",
    "tax": "tax",
}
BUSINESS_KEY = [
    "state_code", "vertical", "channel", "operator", "row_type", "period_start", "period_end",
]
SOURCE_COLUMNS = ["source_url", "source_file", "source_sha256"]


def _money_values_agree(series: pd.Series) -> bool:
    """True when values are all null, or all present within one printed cent."""
    values = pd.to_numeric(series, errors="coerce")
    if values.isna().all():
        return True
    if values.isna().any():
        return False
    cents = (values.astype(float) * 100).round()
    return float(cents.max() - cents.min()) <= 1

def dedupe_latest_observation(results: pd.DataFrame) -> pd.DataFrame:
    """Collapse equal observations; flag conflicting reports without choosing a winner.

    The existing function name is retained for callers. Retrieval order does
    not establish revision authority. Equal copies use the earliest capture
    for display, with every source reference retained. When copies disagree,
    only the disagreeing money fields are cleared; agreeing fields stay visible
    so a one-cent taxable difference does not blank matching GGR. Inspect the
    original rows in gaming_results for the full conflict.
    """
    frame = results.copy()
    frame["observation_status"] = "single_source"
    if frame.empty:
        return frame
    frame = frame.sort_values(["retrieved_at_utc", "source_sha256", "source_file"])
    repeated = frame.duplicated(BUSINESS_KEY, keep=False)
    unique_rows = frame.loc[~repeated]
    resolved = []
    for _, group in frame.loc[repeated].groupby(BUSINESS_KEY, dropna=False):
        row = group.iloc[0].copy()
        label_conflict = group[["frequency", "reported_revenue_name"]].nunique(dropna=False).gt(1).any()
        money_conflicts = {column: not _money_values_agree(group[column]) for column in MONEY_COLUMNS}
        conflicting = label_conflict or any(money_conflicts.values())
        row["observation_status"] = "conflicting_sources" if conflicting else "matching_sources"
        if conflicting:
            for column, differs in money_conflicts.items():
                if differs or label_conflict:
                    row[column] = None
        for column in SOURCE_COLUMNS:
            row[column] = " | ".join(sorted(group[column].dropna().astype(str).unique()))
        resolved.append(row)
    if resolved:
        frame = pd.concat([unique_rows, pd.DataFrame(resolved)], ignore_index=True)
    return frame.sort_values(BUSINESS_KEY).reset_index(drop=True)


def _state_period_row(rows: pd.DataFrame, source: str, basis: str | None, operator_count: int) -> dict:
    """Summarize selected rows and keep their coverage limits and sources visible."""
    first = rows.iloc[0]
    keys = ["state_code", "jurisdiction", "vertical", "channel", "period_start", "period_end", "frequency"]
    result = first[keys].to_dict()
    for column in MONEY_COLUMNS + ["revenue"]:
        values = pd.to_numeric(rows[column], errors="coerce")
        # Every contributing row must have a value. This alone does not prove
        # that every operator in the market is present.
        result[column] = values.sum(min_count=len(rows))
    result["revenue_basis"] = basis
    result["aggregation_source"] = source
    result["operator_count"] = operator_count
    if source == "official_statewide_total":
        result["completeness"] = "reported_total"
    else:
        result["completeness"] = "operator_coverage_unverified"
    if rows["revenue"].isna().any():
        result["completeness"] = "missing_values"
    if rows["observation_status"].eq("conflicting_sources").any():
        result["completeness"] = "conflicting_sources"
    for column in SOURCE_COLUMNS + ["reported_revenue_name"]:
        result[column] = " | ".join(sorted(rows[column].dropna().astype(str).unique()))
    return result


def build_state_period_revenue(results: pd.DataFrame, metric: str | None = None) -> pd.DataFrame:
    """Build state-period rows for an explicit metric, preserving source labels.

    Prefer a published statewide total. Otherwise show the observed operator
    sum with unverified market coverage. The legacy export (metric=None)
    keeps separate revenue bases; the daily notebook always chooses a column.
    """
    if metric is not None and metric not in MONEY_COLUMNS:
        raise ValueError(f"Choose a financial column from {MONEY_COLUMNS}")
    frame = dedupe_latest_observation(results)
    frame = frame[
        frame["vertical"].isin(PRIMARY_VERTICALS)
        & frame["channel"].isin(ONLINE_CHANNELS | {"combined", "location_based_mobile"})
    ].copy()
    if metric is None:
        frame["revenue_basis"] = frame.apply(revenue_basis, axis=1) if not frame.empty else None
        frame["revenue"] = frame.apply(revenue_amount, axis=1) if not frame.empty else None
    else:
        frame["revenue_basis"] = REVENUE_LABELS[metric]
        frame["revenue"] = frame[metric]

    output = []
    keys = ["state_code", "vertical", "channel", "period_start", "period_end", "frequency"]
    for _, group in frame.groupby(keys, dropna=False):
        operators = group[group["row_type"] == "operator"]
        # Older IL/IN collectors wrote calculated sums as official totals.
        # Most use derived_from_operator_sum; two early IN months used bare ok.
        # Accept IL/IN statewide totals only with explicit printed provenance.
        # State code alone must not reject a genuine printed control total.
        printed_official = group["report_status"].isin(PRINTED_OFFICIAL_PROVENANCE)
        derived = group["report_status"].eq("derived_from_operator_sum") | (
            group["state_code"].isin(["IL", "IN"]) & ~printed_official
        )
        official = group[(group["row_type"] == "official_statewide_total") & ~derived]
        if not official.empty:
            chosen = official.copy()
            if len(chosen) > 1:
                chosen["observation_status"] = "conflicting_sources"
                chosen[MONEY_COLUMNS + ["revenue"]] = None
            basis = chosen.iloc[0]["revenue_basis"]
            summary = _state_period_row(chosen, "official_statewide_total", basis, len(operators))
            output.append(summary)
            continue

        chosen = operators if not operators.empty else group
        if chosen.empty:
            continue
        bases = chosen["revenue_basis"].dropna().unique()
        if metric is None and len(bases) > 1:
            for basis in sorted(bases):
                subset = chosen[chosen["revenue_basis"] == basis]
                summary = _state_period_row(subset, "operator_sum_split_by_basis", basis, len(subset))
                output.append(summary)
        else:
            basis = bases[0] if len(bases) else None
            source = "operator_sum" if not operators.empty else "legacy_derived_total"
            output.append(_state_period_row(chosen, source, basis, len(operators)))

    columns = [
        "jurisdiction", *keys, *MONEY_COLUMNS, "revenue", "revenue_basis",
        "aggregation_source", "operator_count", "completeness",
        *SOURCE_COLUMNS, "reported_revenue_name",
    ]
    return pd.DataFrame(output, columns=columns)


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
        "source_file",
        "source_sha256",
        "retrieved_at_utc",
        "report_status",
        "observation_status",
    ]
    return ops[cols].sort_values(["state_code", "vertical", "period_start", "operator"])


def build_source_coverage_export(connection, root: Path | None = None) -> pd.DataFrame:
    root = root or project_root()
    inventory = pd.read_csv(root / "config" / "state_gaming_source_inventory.csv")
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

    conn = connect_readonly(default_db_path(root))

    results = pd.read_sql_query("SELECT * FROM gaming_results", conn)
    results_path = processed / "gaming_results.csv"
    results.to_csv(results_path, index=False)

    state_period = build_state_period_revenue(results)
    state_period_path = processed / "state_period_revenue.csv"
    state_period.to_csv(state_period_path, index=False)

    operator = build_operator_revenue(results)
    operator_path = processed / "operator_revenue.csv"
    operator.to_csv(operator_path, index=False)

    coverage = build_source_coverage_export(conn, root)
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
