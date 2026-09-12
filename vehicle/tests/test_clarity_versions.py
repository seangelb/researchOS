"""Core Clarity audit semantics without creating or modifying workbooks."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "audit_clarity_versions", Path(__file__).parents[1] / "scripts/audit_clarity_versions.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def cell(value, formula=False, cached=None, style="normal", comment=None):
    return dict(source=value, formula=formula, cached=cached, style=style, comment=comment)


def test_version_comparison_distinguishes_formula_edits_additions_and_formatting():
    before = {"S!C2": cell(10), "S!J2": cell("=C2*2", True), "S!A2": cell("date")}
    after = {"S!C2": cell(10, style="new"), "S!J2": cell("=C2*3", True), "S!C3": cell(0)}
    result = audit.compare_cells(before, after)
    assert result["changed_source"] == [dict(cell="S!J2", before="=C2*2", after="=C2*3")]
    assert result["format_only"] == ["S!C2"]
    assert result["added"] == ["S!C3"]
    assert result["removed"] == ["S!A2"]
    assert result["unchanged_existing_source_count"] == 1


def test_cached_formula_result_is_distinct_from_source_revision():
    result = audit.compare_cells({"S!J2": cell("=C2*2", True)},
                                 {"S!J2": cell("=C2*2", True, cached=20)})
    assert result["changed_cache"] == ["S!J2"]
    assert not result["changed_source"] and not result["format_only"]


def row(date, inventory, listings=5, sales=3):
    return dict(date=date, inventory=inventory, listings=listings, sales=sales,
                source_cells={key: key+date for key in ["inventory", "listings", "sales"]})


def test_stock_flow_requires_adjacent_dates_and_preserves_zero():
    assert not audit.stock_flow([row("2025-05-01", 10), row("2025-05-03", 8)])
    assert not audit.stock_flow([row("2025-05-01", 10), row("2025-05-02", 8, sales=None)])
    result = audit.stock_flow([row("2025-05-01", 10), row("2025-05-02", 8, listings=0, sales=0)])
    assert result[0]["implied_exits"] == result[0]["unexplained_residual"] == 2
    assert result[0]["reported_sales"] == 0


@pytest.mark.parametrize("value", [None, "null", "nan", float("nan"), True, "=SUM(C2:C3)"])
def test_missing_or_unevaluated_input_is_not_numeric(value):
    assert not audit.number(value)


def test_existing_destination_fails_before_reading_inputs(tmp_path):
    with pytest.raises(FileExistsError):
        audit.audit_versions([tmp_path / "missing.xlsx"], tmp_path)
