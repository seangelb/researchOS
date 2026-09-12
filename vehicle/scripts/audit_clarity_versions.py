"""Archive supplied Clarity workbooks once and compare their retained contents.

Example: python scripts/audit_clarity_versions.py --workbook FILE --workbook FILE2
         --destination data/experiments/clarity_method/NEW_UTC/workbooks
Workbooks are read, never saved or recalculated. The destination must not exist.
"""
import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vehicle_tracker.vendor_audit import audit_vendor_workbook


METRICS = {"sales": "Sales", "prior_sales": "2024 Sales", "orders": "Orders",
           "listings": "Listings", "inventory": "Inventory", "pending": "Pending",
           "sold_price": "Sold Price", "sold_model_year": "Sale Model Year"}


def number(value):
    """Missing text, blank and formula text remain unavailable; zero stays zero."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def compare_cells(before, after):
    """Compare same sheet/cell coordinates, separating source, cache and style edits."""
    result = {key: [] for key in ["added", "removed", "changed_source", "changed_cache",
                                  "format_only", "changed_comment"]}
    for key in sorted(before.keys() | after.keys()):
        left, right = before.get(key), after.get(key)
        if left is None or right is None:
            result["added" if left is None else "removed"].append(key)
            continue
        source_same = left["source"] == right["source"] and left["formula"] == right["formula"]
        if not source_same:
            result["changed_source"].append(dict(cell=key, before=left["source"], after=right["source"]))
        if left["cached"] != right["cached"]:
            result["changed_cache"].append(key)
        if left["comment"] != right["comment"]:
            result["changed_comment"].append(key)
        if source_same and left["cached"] == right["cached"] and left["comment"] == right["comment"]:
            if left["style"] != right["style"]:
                result["format_only"].append(key)
    result["unchanged_existing_source_count"] = sum(
        key in after and before[key]["source"] == after[key]["source"]
        and before[key]["formula"] == after[key]["formula"] for key in before)
    return result


def stock_flow(rows):
    """A conditional accounting residual, requiring adjacent dates and all inputs."""
    result = []
    for previous, current in zip(rows, rows[1:]):
        adjacent = datetime.fromisoformat(current["date"]) - datetime.fromisoformat(previous["date"]) == timedelta(days=1)
        inputs = [previous["inventory"], current["listings"], current["inventory"], current["sales"]]
        if not adjacent or not all(number(value) for value in inputs):
            continue
        exits = inputs[0] + inputs[1] - inputs[2]
        result.append(dict(date=current["date"], implied_exits=exits, reported_sales=inputs[3],
            unexplained_residual=exits-inputs[3], source_cells=[previous["source_cells"]["inventory"],
            *[current["source_cells"][key] for key in ["listings", "inventory", "sales"]]]))
    return result


def inspect_workbook(path):
    cells, coverage = audit_vendor_workbook(path)
    cells = cells.astype(object).where(cells.notna(), None)
    if set(cells.sheet) != {"Sheet1"}:
        raise ValueError("This bounded audit expects the supplied Sheet1 layout")
    source = cells.set_index("cell")
    headers = {row.source_value: row.column for row in cells.itertuples() if row.cell == f"{row.column}1"}
    columns = {key: headers[label] for key, label in METRICS.items()}
    def value(cell):
        return source.loc[cell, "source_value"] if cell in source.index else None
    rows = []
    for entry in cells[(cells.column == "A") & cells.date.notna()].itertuples():
        row = int(entry.cell[1:])
        refs = {key: f"Sheet1!{col}{row}" for key, col in columns.items()}
        rows.append(dict(date=entry.date.date().isoformat(), source_row=row,
            **{key: value(f"{col}{row}") for key, col in columns.items()}, source_cells=refs))
    populated = [row for row in rows if number(row["sales"])]
    prior = [row["prior_sales"] for row in rows if number(row["prior_sales"])]
    runrate = None
    if populated and len(prior) == len(rows) and all(number(row["prior_sales"]) for row in populated):
        current_qtd, prior_qtd = sum(row["sales"] for row in populated), sum(row["prior_sales"] for row in populated)
        last = populated[-1]["source_row"]
        runrate = dict(as_of_date=populated[-1]["date"], current_qtd=current_qtd, prior_qtd=prior_qtd,
            prior_quarter=sum(prior), seasonal_runrate=current_qtd/prior_qtd*sum(prior) if prior_qtd else None,
            flat_calendar_alternative=current_qtd/len(populated)*len(rows),
            source_cells=[f"Sheet1!I{last}", f"Sheet1!J{last}"],
            source_formulas=[value(f"I{last}"), value(f"J{last}")],
            status="arithmetic reconstruction from populated dates; no Excel formula evaluation")
    snapshots = {}
    workbook = openpyxl.load_workbook(path, data_only=False)
    try:
        for row in cells.itertuples():
            cell = workbook[row.sheet][row.cell]
            snapshots[f"{row.sheet}!{row.cell}"] = dict(source=row.source_value, formula=row.formula,
                cached=row.cached_value, comment=row.comment,
                style=tuple(str(getattr(cell, field)) for field in
                            ["font", "fill", "border", "alignment", "number_format", "protection"]))
    finally:
        workbook.close()
    report = dict(coverage=json.loads(coverage.to_json(orient="records", date_format="iso")),
        sales_dates=len(populated), first_sales_date=populated[0]["date"] if populated else None,
        last_sales_date=populated[-1]["date"] if populated else None,
        source_date_rows=len(rows), blank_sales_dates=sum(row["sales"] is None for row in rows),
        missing_sales_are_zero=False, daily_rows=rows, run_rate=runrate, stock_flow=stock_flow(rows),
        stock_flow_assumption="Listings are additions to the same inventory population over the same daily interval. Residuals are unexplained, not sales or cancellations.",
        comments=[dict(cell=f"{row.sheet}!{row.cell}", text=row.comment) for row in cells.itertuples() if row.comment],
        formula_count=int(cells.formula.sum()), formula_cache_absent_count=int(cells.cache_absent.sum()))
    return report, snapshots


def audit_versions(paths, destination):
    """Hash before reading, archive distinct bytes, and record all input aliases."""
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")
    retained_at = datetime.now(timezone.utc).isoformat()
    inputs, unique, snapshots = [], {}, {}
    for supplied in paths:
        path = Path(supplied).resolve(strict=True)
        blob = path.read_bytes()
        digest = hashlib.sha256(blob).hexdigest()
        inputs.append(dict(original_path=str(path), sha256=digest, bytes=len(blob),
            retained_path=f"{digest}.xlsx", retained_at=retained_at,
            filesystem_modified_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            source_published_at=None, source_available_at=None, duplicate=digest in unique))
        if digest not in unique:
            report, snapshot = inspect_workbook(path)
            unique[digest] = dict(sha256=digest, input_names=[], **report)
            snapshots[digest] = snapshot
        unique[digest]["input_names"].append(path.name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Source changed during audit: {path}")
    ordered = sorted(unique, key=lambda key: (unique[key]["sales_dates"], key))
    comparisons = [dict(before_sha256=a, after_sha256=b, **compare_cells(snapshots[a], snapshots[b]))
                   for a, b in combinations(ordered, 2)]
    result = dict(schema_version=1, retained_at=retained_at, versions=[unique[key] for key in ordered],
        comparisons=comparisons, comparison_order="increasing populated sales dates, not proven publication chronology",
        limits=["Filesystem timestamps are not point-in-time availability.",
                "A free sample with blank sales is not a complete sales timeline.",
                "Workbook aggregates cannot identify the private scraping or sale classification rule."])
    serialized = json.dumps(result, indent=2, default=str, allow_nan=False)
    destination.mkdir(parents=True, exist_ok=False)
    for digest in unique:
        original = next(entry["original_path"] for entry in inputs if entry["sha256"] == digest)
        blob = Path(original).read_bytes()
        if hashlib.sha256(blob).hexdigest() != digest:
            raise RuntimeError(f"Source changed before retention: {original}")
        with (destination / f"{digest}.xlsx").open("xb") as handle:
            handle.write(blob)
    (destination / "manifest.json").write_text(json.dumps(dict(inputs=inputs), indent=2), encoding="utf-8")
    (destination / "audit.json").write_text(serialized, encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, action="append", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    result = audit_versions(args.workbook, args.destination)
    print(json.dumps(dict(destination=str(args.destination.resolve()), distinct_versions=len(result["versions"]),
        sales_coverage=[dict(names=v["input_names"], days=v["sales_dates"], last=v["last_sales_date"]) for v in result["versions"]]), indent=2))
