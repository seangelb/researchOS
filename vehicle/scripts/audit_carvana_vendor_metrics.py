"""Reproduce the supplied free workbook audit without editing or recalculating it.

This is a bounded audit of one named workbook, not a vendor ingestion pipeline.
Run from the repository root with --workbook and a new --destination JSON path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vehicle_tracker.vendor_audit import audit_vendor_workbook


EXPECTED_SHA256 = "d9c7caa593793126c0f79098545e4ca0881746dfe1c4e950ab85ea9fe37cbed5"


def workbook_metrics(path: Path) -> dict:
    """Keep workbook formulas, numerical reconstruction and hypotheses separate."""
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    if before != EXPECTED_SHA256:
        raise ValueError("This audit requires the exact supplied (1).xlsx vintage")
    cells, coverage = audit_vendor_workbook(path)
    source = cells[cells.sheet.eq("Sheet1")].set_index("cell")

    def value(address):
        return source.loc[address, "source_value"]

    daily = pd.DataFrame([
        {"row": r, **{c: value(f"{c}{r}") for c in ["A", "C", "D", "O", "P", "S", "T", "BK", "BL"]}}
        for r in range(2, 42)
    ])
    daily["implied_inventory_exits"] = daily.BL.shift() + daily.S - daily.BL
    daily["exits_minus_sales"] = daily.implied_inventory_exits - daily.C
    daily["implied_pending_out_if_orders_are_entries"] = daily.BK.shift() + daily.O - daily.BK
    # This is conditional stock-flow algebra, not an assertion that O counts entries.
    daily["pending_out_minus_sales"] = daily.implied_pending_out_if_orders_are_entries - daily.C
    prior = pd.Series([value(f"D{r}") for r in range(2, 94)], dtype=float)
    # Difference/13 discovers a candidate common step. All 92 values then test it.
    candidate_step = (value("D5") - value("D4")) / 13
    candidate_integers = np.rint(prior / candidate_step).astype(int)
    inferred_raw_total = int(candidate_integers.sum())
    normalization = value("D96") / inferred_raw_total
    reconstruction_error = float((prior - candidate_integers * normalization).abs().max())
    current_qtd = int(daily.C.sum())
    prior_qtd, prior_quarter = float(daily.D.sum()), float(prior.sum())
    run_rate = current_qtd / prior_qtd * prior_quarter
    shares = pd.Series({int(line.split()[0]): float(line.split()[1])
                        for line in value("AK2").splitlines()[1:]})
    denominators = [n for n in range(1, 501)
                    if ((shares * n - np.rint(shares * n)).abs() <= n * 0.0000005 + 1e-10).all()
                    and int(np.rint(shares * n).sum()) == n]
    full_count = value("BL2")
    incompatible_categories = int(((shares * full_count - np.rint(shares * full_count)).abs()
                                   > full_count * 0.0000005 + 1e-10).sum())
    rolling_mismatches = {}
    for result, underlying in [("P", "O"), ("T", "S")]:
        matches = daily[result].iloc[6:].eq(daily[underlying].rolling(7).sum().iloc[6:])
        rolling_mismatches[result] = daily.loc[matches.index[~matches], "row"].tolist()
    selected = ["C2", "C3", "D2", "D3", "D4", "D5", "D95", "D96", "D97", "D99",
                "I41", "J41", "F2", "F8", "O7", "P7", "Q7", "R7", "AK2", "AJ2",
                "BJ2", "CE2", "BK2", "BK3", "BL2", "BL3", "S3", "AL2", "A22", "A35"]
    selected_cells = source.loc[selected].reset_index().astype(object)
    selected_cells = selected_cells.where(selected_cells.notna(), None)
    daily_columns = {"date": "A", "current_year_sales": "C", "prior_year_sales": "D",
                     "inventory": "BL", "pending": "BK", "orders": "O", "new_listings": "S",
                     "qtd_growth_formula": "I", "run_rate_formula": "J", "trailing_week_sales_formula": "F"}
    daily_rows = [{"source_row": r, **{name: value(f"{col}{r}") if f"{col}{r}" in source.index else None
                                      for name, col in daily_columns.items()}} for r in range(2, 94)]
    report = {
        "workbook_path": str(path.resolve()), "sha256_before": before,
        "source_cells": selected_cells.to_dict("records"),
        "summary_cells": {row["cell"]: row["source_value"] for row in selected_cells.to_dict("records")},
        "daily_rows": daily_rows,
        "metric_coverage": json.loads(coverage.to_json(orient="records", date_format="iso")),
        "formula_count": int(cells.formula.sum()),
        "formula_cache_absent_count": int(cells.cache_absent.sum()),
        "missing_text_counts": cells.loc[cells.missing_token, "source_value"].value_counts().to_dict(),
        "run_rate": {"formula_cell": "J41", "as_of_source_date": "2026-08-09",
                     "current_qtd": current_qtd, "prior_qtd": prior_qtd, "prior_quarter": prior_quarter,
                     "qtd_growth": current_qtd / prior_qtd - 1, "reconstructed_value": run_rate,
                     "flat_calendar_alternative": current_qtd / len(daily) * len(prior),
                     "formula_evaluation_engine_used": False},
        "prior_year_normalization": {"candidate_integer_total": inferred_raw_total,
                     "reference_total_D96": value("D96"), "factor": normalization,
                     "max_reconstruction_error": reconstruction_error,
                     "fractional_source_days": int(prior.ne(prior.round()).sum()),
                     "candidate_integer_counts": candidate_integers.tolist(),
                     "status": "all values reconstruct; raw origin and original vintage remain unverified"},
        "stock_flow": {"comparable_numeric_intervals": len(daily) - 1,
                       "implied_exits": int(daily.implied_inventory_exits.sum()),
                       "reported_sales_matching_intervals": int(daily.C.iloc[1:].sum()),
                       "net_exits_minus_sales": int(daily.exits_minus_sales.sum()),
                       "positive_residual_intervals": int(daily.exits_minus_sales.gt(0).sum()),
                       "negative_residual_intervals": int(daily.exits_minus_sales.lt(0).sum()),
                       "zero_residual_intervals": int(daily.exits_minus_sales.eq(0).sum()),
                       "classification": "unexplained residual; not cancellations"},
        "population_checks": {"AK2_smallest_compatible_denominator_tested": min(denominators),
                      "denominator_search_max": 500, "AK2_categories_incompatible_with_BL2": incompatible_categories,
                      "AK2_category_count": len(shares), "BL2": full_count,
                      "CE2_equal_halves_incompatible_with_C2": bool(value("C2") % 2),
                      "meaning": "different population or erroneous output; small denominator is not proven sample size"},
        "rolling_seven_day_mismatch_rows": rolling_mismatches,
        "R7_stated_text": value("R7"), "R7_reconstructed_daily_comparison": value("O7") / value("Q7") - 1,
        "daily_stock_flow": json.loads(daily.to_json(orient="records", date_format="iso")),
    }
    report["sha256_after"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if report["sha256_after"] != before:
        raise RuntimeError("Workbook changed during read-only audit")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    result = workbook_metrics(args.workbook)
    serialized = json.dumps(result, indent=2, default=str, allow_nan=False)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    with args.destination.open("x", encoding="utf-8") as handle:
        handle.write(serialized)
    print(json.dumps({k: result[k] for k in ["run_rate", "stock_flow", "population_checks"]}, indent=2))
