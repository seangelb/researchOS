"""Execute visible notebook calculations against small, distinct financial measures."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def casino_scope():
    rows = []
    for year in (2025, 2026):
        for month in range(1, 8):
            for operator, kind, gross, adjusted in [
                ("FanDuel (MotorCity Casino)", "operator", 20 if year == 2025 else 30, 2),
                ("Other", "operator", 80 if year == 2025 else 90, 48),
                ("Total", "official_statewide_total", 100 if year == 2025 else 120, 50),
            ]:
                rows.append(dict(state_code="MI", channel="online", frequency="monthly",
                    period_start=pd.Timestamp(year, month, 1), operator=operator, row_type=kind,
                    gross_revenue=gross, adjusted_revenue=adjusted, reported_revenue_name="Adjusted Gross",
                    source_file="fixture.xlsx", source_url="https://example.invalid/report", source_sha256="fixture"))
    return dict(raw=pd.DataFrame(rows), pd=pd, plt=plt, display=lambda *args: None,
                C_BLUE="blue", C_ORANGE="orange", C_BLACK="black")


def execute_casino(scope):
    notebook = json.loads((ROOT / "notebooks/93_flut_online_casino_signal.ipynb").read_text(encoding="utf-8"))
    cell = next(c for c in notebook["cells"] if c["cell_type"] == "code" and "mi = raw[" in "".join(c["source"]))
    try:
        exec("".join(cell["source"]), scope)
    finally:
        plt.close("all")
    return scope


def test_michigan_notebook_uses_gross_receipts_and_labels_them():
    scope = execute_casino(casino_scope())
    assert scope["share0"] == pytest.approx(.20)
    assert scope["share1"] == pytest.approx(.25)
    assert scope["delta"] == pytest.approx(70)
    assert scope["mkt_fx"] + scope["sh_fx"] == pytest.approx(70)
    assert set(scope["cas"].label) == {"Gross Receipts"}
    text = (ROOT / "notebooks/93_flut_online_casino_signal.ipynb").read_text(encoding="utf-8")
    assert "FanDuel Adjusted Gross share" not in text


@pytest.mark.parametrize("problem", ["missing", "duplicate", "unreconciled", "absent_month", "empty"])
def test_casino_rejects_incomplete_or_conflicting_matched_window(problem):
    scope = casino_scope()
    raw = scope["raw"]
    if problem == "missing":
        raw.loc[0, "gross_revenue"] = float("nan")
    elif problem == "duplicate":
        scope["raw"] = pd.concat([raw, raw.iloc[[0]]], ignore_index=True)
    elif problem == "unreconciled":
        raw.loc[0, "gross_revenue"] += 2
    elif problem == "absent_month":
        scope["raw"] = raw[raw.period_start.ne(pd.Timestamp("2025-01-01"))]
    else:
        scope["raw"] = raw.iloc[:0]
    with pytest.raises(SystemExit, match="matched window incomplete"):
        execute_casino(scope)


@pytest.mark.parametrize("missing_state", ["KS", "OH"])
def test_multistate_missing_month_shows_audit_before_blocking_or_skipping(missing_state):
    notebook = json.loads((ROOT / "notebooks/92_flut_multistate_sportsbook_signal.ipynb").read_text(encoding="utf-8"))
    source = next("".join(c["source"]) for c in notebook["cells"]
                  if c["cell_type"] == "code" and "PRIOR =" in "".join(c["source"]))
    months = list(pd.date_range("2025-01-01", periods=7, freq="MS")) + list(pd.date_range("2026-01-01", periods=7, freq="MS"))
    states = ["KS", "MI", "WY", "MA", "OH", "IN"]
    panels = {state: pd.DataFrame({"state": state, "month": months,
              "fd_handle": 20., "mkt_handle": 100.}) for state in states}
    panels["MA"]["fd_handle"] = [27.11] * 7 + [27.11 * .9503] * 7
    panels["MA"]["mkt_handle"] = [100.] * 7 + [102.77] * 7
    panels[missing_state] = panels[missing_state].iloc[:-1]
    monthly = pd.DataFrame([dict(state_code=state, period_start=month, handle=100.,
        row_type="official_statewide_total", operator="STATEWIDE", source_file="fixture.pdf",
        source_url="https://example.invalid") for state in states for month in months])
    displayed = []
    scope = dict(pd=pd, panels=panels, monthly=monthly, BASELINE=states[:4],
                 display=displayed.append, Markdown=lambda x: x)
    if missing_state == "KS":
        with pytest.raises(SystemExit, match="KS.*2026-07"):
            exec(source, scope)
        assert "base_jj" not in scope
    else:
        exec(source, scope)
        assert scope["w_oh"] is None
        assert scope["exp_jj"].empty
        assert scope["w_base"] is not None
    assert displayed[0] is scope["comparison_coverage"]
    missing = scope["comparison_coverage"].query("state == @missing_state and eligible == False")
    assert missing.status.tolist() == ["rejected by panel checks"]
