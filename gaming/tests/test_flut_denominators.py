"""Reject plausible arithmetic when the statewide control is not established."""

import ast
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from variant_gaming.denominators import denominator_provenance


def control(**changes):
    return dict(state_code="MI", vertical="online_sports_betting", channel="online",
                frequency="monthly", row_type="official_statewide_total",
                operator="STATEWIDE", report_status="ok") | changes


@pytest.mark.parametrize("state,vertical,frequency,status", [
    ("MA", "online_sports_betting", "monthly", "reconciled_printed_total"),
    ("MI", "online_sports_betting", "monthly", "ok"),
    ("MI", "online_casino", "monthly", "ok"),
    ("KS", "online_sports_betting", "monthly", "unaudited"),
    ("WY", "online_sports_betting", "monthly", "reported"),
    ("OH", "online_sports_betting", "monthly", "ok"),
    ("NY", "online_sports_betting", "weekly", "ok"),
])
def test_only_named_native_printed_controls_are_eligible(state, vertical, frequency, status):
    row = control(state_code=state, vertical=vertical, frequency=frequency, report_status=status)
    assert denominator_provenance(row) == f"printed:{state}:{status}"


@pytest.mark.parametrize("changes", [
    {"report_status": "unknown_unreviewed_status"}, {"report_status": ""},
    {"report_status": None}, {"report_status": pd.NA},
    {"report_status": "printed_statewide_total"},
    {"report_status": "derived_from_operator_sum"},
    {"state_code": "MA", "report_status": "ok"},
    {"state_code": "IN", "report_status": "ok"},
    {"state_code": "KS", "report_status": "visually_transcribed"},
    {"state_code": "KS", "report_status": "reported"},
    {"state_code": "NY", "frequency": "monthly"},
    {"state_code": "WY", "vertical": "online_casino", "report_status": "reported"},
    {"channel": "combined"}, {"channel": "retail"},
    {"row_type": "operator"}, {"operator": "FanDuel"},
])
def test_status_does_not_transfer_across_sources_products_or_row_types(changes):
    assert denominator_provenance(control(**changes)) is None


def test_missing_contract_fields_fail_closed():
    for key in control():
        row = control()
        del row[key]
        assert denominator_provenance(row) is None


def notebook_panel(rows):
    path = Path(__file__).resolve().parents[1] / "notebooks/92_flut_multistate_sportsbook_signal.ipynb"
    book = json.loads(path.read_text(encoding="utf-8"))
    functions = []
    for cell in book["cells"]:
        if cell["cell_type"] == "code":
            functions.extend(node for node in ast.parse("".join(cell["source"])).body
                             if isinstance(node, ast.FunctionDef) and node.name in {"panel", "is_derived"})
    scope = dict(pd=pd, np=np, monthly=rows, RECON_TOL=5., denominator_provenance=denominator_provenance)
    exec(compile(ast.Module(body=functions, type_ignores=[]), "notebook92", "exec"), scope)
    return scope["panel"]


def market_rows(state="MI", status="ok"):
    base = control(state_code=state, report_status=status)
    base.update(period_start=pd.Timestamp("2026-07-01"), period_end=pd.Timestamp("2026-07-31"))
    return pd.DataFrame([
        base | dict(operator="FanDuel", row_type="operator", handle=40.),
        base | dict(operator="Other", row_type="operator", handle=60.),
        base | dict(handle=100.),
    ])


def test_notebook_uses_known_control_but_rejects_unknown_status_even_when_sums_match():
    valid = notebook_panel(market_rows())("MI", ["FanDuel"])
    assert valid.iloc[0]["share"] == pytest.approx(.4)
    assert valid.iloc[0]["denom"] == "printed:MI:ok"
    unknown = market_rows(status="unknown_unreviewed_status")
    assert notebook_panel(unknown)("MI", ["FanDuel"]).empty


def test_derived_sensitivity_cannot_make_unknown_indiana_status_eligible():
    unknown = market_rows(state="IN", status="ok")
    assert notebook_panel(unknown)("IN", ["FanDuel"], allow_derived=True).empty
    explicit = market_rows(state="IN", status="derived_from_operator_sum")
    calculate = notebook_panel(explicit)
    assert calculate("IN", ["FanDuel"], allow_derived=False).empty
    derived = calculate("IN", ["FanDuel"], allow_derived=True)
    assert derived.iloc[0]["denom"] == "derived_operator_sum"
    assert bool(derived.iloc[0]["derived"])


@pytest.mark.parametrize("problem", ["infinite_operator", "infinite_total", "negative_handle",
                                      "period_mismatch", "noncalendar_start", "duplicate_operator",
                                      "duplicate_total", "missing_handle", "failed_reconciliation"])
def test_notebook_requires_finite_complete_same_period_operator_reconciliation(problem):
    rows = market_rows()
    if problem == "infinite_operator":
        rows.loc[0, "handle"] = np.inf
    elif problem == "infinite_total":
        rows.loc[2, "handle"] = np.inf
    elif problem == "negative_handle":
        rows.loc[0:1, "handle"] = [-40., 140.]
    elif problem == "period_mismatch":
        rows.loc[0, "period_end"] = pd.Timestamp("2026-08-31")
    elif problem == "noncalendar_start":
        rows["period_start"] = pd.Timestamp("2026-07-02")
    elif problem.startswith("duplicate"):
        rows = pd.concat([rows, rows.iloc[[1 if problem == "duplicate_operator" else 2]]])
    elif problem == "missing_handle":
        rows.loc[1, "handle"] = np.nan
    else:
        rows.loc[1, "handle"] = 50.
    assert notebook_panel(rows)("MI", ["FanDuel"]).empty
