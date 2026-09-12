"""Exercise the actual notebook cells on incomplete data and temporary storage."""
import ast
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import pytest

from test_notebook_execution import checker
from test_notebook_analysis import casino_scope, execute_casino
from variant_gaming.storage import connect, ensure_schema

ROOT = Path(__file__).resolve().parents[1]


def source(prefix, marker):
    path = next((ROOT / "notebooks").glob(f"{prefix}_*.ipynb"))
    book = json.loads(path.read_text(encoding="utf-8"))
    return next("".join(c["source"]) for c in book["cells"]
                if c["cell_type"] == "code" and marker in "".join(c["source"]))


def function_scope(name, **inputs):
    tree = ast.parse(source("92", "def ny_window"))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    shown = []
    scope = dict(pd=pd, display=shown.append, **inputs)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "notebook92", "exec"), scope)
    return scope[name], shown


def ny_rows():
    dates = pd.date_range("2026-01-01", "2026-07-25", freq="W-MON")
    return pd.DataFrame(dict(period_start=dates, period_end=dates + pd.Timedelta(days=6),
                             handle=100., source_file="fixture.xlsx", source_url="fixture"))


@pytest.mark.parametrize("problem", ["fd_gap", "both_gap", "first_gap", "duplicate", "missing", "infinite", "wrong_end", "zero_market"])
def test_ny_rejects_incomplete_or_duplicate_weeks_before_comparison(problem):
    fd, market = ny_rows(), ny_rows()
    if problem == "fd_gap":
        fd = fd.drop(index=8)
    elif problem == "both_gap":
        fd, market = fd.drop(index=8), market.drop(index=8)
    elif problem == "first_gap":
        fd, market = fd.iloc[1:], market.iloc[1:]
    elif problem == "duplicate":
        fd = pd.concat([fd, fd.iloc[[0]]])
    elif problem == "wrong_end":
        fd.loc[0, "period_end"] += pd.Timedelta(days=1)
    elif problem == "zero_market":
        market.loc[0, "handle"] = 0
    else:
        fd.loc[0, "handle"] = None if problem == "missing" else float("inf")
    calculate, shown = function_scope("ny_window", ny_fd=fd, ny_mkt=market)
    with pytest.raises(SystemExit, match="NY.*weekly"):
        calculate(2026)
    assert shown and isinstance(shown[0], pd.DataFrame)


def test_ny_uses_observed_days_and_excludes_boundary_straddlers():
    fd = ny_rows()
    boundary = pd.DataFrame(dict(period_start=pd.to_datetime(["2025-12-29", "2026-07-27"]),
                                period_end=pd.to_datetime(["2026-01-04", "2026-08-02"]), handle=100.))
    calculate, _ = function_scope("ny_window", ny_fd=pd.concat([fd, boundary]), ny_mkt=fd)
    result = calculate(2026)
    assert result["complete_weeks"] == 29 and result["covered_days"] == 203
    assert result["fd_handle"] == result["mkt_handle"] == 2900
    assert "2025-12-29" in result["excluded_boundary_weeks"]


def nj_rows():
    months = list(pd.date_range("2026-01-01", periods=7, freq="MS"))
    return months, pd.DataFrame([dict(period_start=m, period_end=m + pd.offsets.MonthEnd(),
        operator=operator, gross_revenue=value, source_file="fixture.pdf", source_url="fixture")
        for m in months for operator, value in [("Fanduel", 40.), ("DraftKings", 60.)]])


@pytest.mark.parametrize("problem", ["all_fd", "one_fd", "month", "duplicate", "missing", "infinite", "zero"])
def test_nj_never_turns_missing_rows_into_zero_or_shortens_window(problem):
    months, rows = nj_rows()
    if problem == "all_fd":
        rows = rows[rows.operator.ne("Fanduel")]
    elif problem == "one_fd":
        rows = rows.drop(index=0)
    elif problem == "month":
        rows = rows[rows.period_start.ne(months[2])]
    elif problem == "duplicate":
        rows = pd.concat([rows, rows.iloc[[0]]])
    elif problem == "zero":
        rows["gross_revenue"] = 0
    else:
        rows.loc[0, "gross_revenue"] = None if problem == "missing" else float("inf")
    calculate, shown = function_scope("nj_rev", nj=rows, nj_fd=rows[rows.operator.eq("Fanduel")])
    with pytest.raises(SystemExit, match="NJ.*monthly"):
        calculate(months)
    assert shown and isinstance(shown[0], pd.DataFrame)


def test_nj_available_operators_are_explicitly_unverified():
    months, rows = nj_rows()
    rows = rows.drop(index=13)  # Cannot invent a roster to claim this is the complete market.
    calculate, shown = function_scope("nj_rev", nj=rows, nj_fd=rows[rows.operator.eq("Fanduel")])
    assert calculate(months) == (280., 640.)
    assert shown[0]["denominator_status"].eq("unverified observed operators").all()
    assert "unverified observed-operator denominator" in source("92", "def ny_window")


def weekly_scope():
    table = pd.DataFrame(dict(state_code=["NY"]*2, frequency=["weekly"]*2, channel=["online"]*2,
        period_start=pd.to_datetime(["2026-06-02", "2026-06-16"]),
        period_end=pd.to_datetime(["2026-06-08", "2026-06-22"]), revenue=[100., 300.],
        aggregation_source=["official_statewide_total"]*2))
    return dict(pd=pd, plt=plt, table=table, period_rows=table, states=["NY"], frequency="weekly",
                channel="online", start=pd.Timestamp("2026-06-02"), end=pd.Timestamp("2026-06-22"),
                product="online_sports_betting", metric="gross_revenue", display=lambda *a: None)


def test_weekly_chart_keeps_missing_week_as_gap(monkeypatch):
    monkeypatch.setattr(plt, "show", lambda: None)
    scope = weekly_scope()
    try:
        exec(source("90", "ax.plot(series.index"), scope)
        y = scope["ax"].lines[0].get_ydata()
        assert len(y) == 3 and pd.isna(y[1])
    finally:
        plt.close("all")


def test_weekly_coverage_uses_source_weekday():
    scope = weekly_scope()
    exec(source("90", "missing_months ="), scope)
    assert scope["missing_weeks"] == [dict(state="NY", missing_week_start=pd.Timestamp("2026-06-09").date(),
                                           missing_week_end=pd.Timestamp("2026-06-15").date())]


@pytest.mark.parametrize("prefix", ["00", "10", "11"])
@pytest.mark.parametrize("has_database", [False, True])
def test_source_walkthrough_defaults_are_offline_and_readonly(tmp_path, monkeypatch, prefix, has_database):
    for folder in ["config", "src", "tests/fixtures/NY", "tests/fixtures/IL"]:
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "config/state_gaming_source_inventory.csv", tmp_path / "config")
    for state in ["NY", "IL"]:
        for path in (ROOT / "tests/fixtures" / state).iterdir():
            shutil.copy(path, tmp_path / "tests/fixtures" / state)
    destination = tmp_path / "data/staging/gaming_nationwide.sqlite"
    if has_database:
        conn = connect(destination)
        ensure_schema(conn)
        conn.close()
    path = next((ROOT / "notebooks").glob(f"{prefix}_*.ipynb"))
    assert checker.run_notebook(path, tmp_path)["status"] == "PASS"
    assert destination.exists() == has_database
    assert not (tmp_path / "data/gaming.sqlite").exists()
    assert "connect_readonly" in path.read_text(encoding="utf-8")
    assert 'data/staging/gaming_nationwide.sqlite' in path.read_text(encoding="utf-8")


def test_nc_description_does_not_claim_unchecked_ggr_reconciliation(capsys):
    records = [dict(year=2024, month=3, handle=659308541, gross_revenue=1e9, tax=None),
               dict(year=2024, month=4, handle=648934226, gross_revenue=10, tax=None)]
    scope = dict(PDFS={"April 2024 (cumulative)":SimpleNamespace(read_bytes=lambda:b"fixture")},
                 parse_revenue_pdf=lambda _:records)
    exec(source("30", "april_path ="), scope)
    output = capsys.readouterr().out
    assert "report reconciled" not in output
    assert "GGR/tax reconciliation not performed" in output


@pytest.mark.parametrize("fd_current,market_current,direction,dominant", [
    (30., 120., "gain", "Share"), (10., 120., "loss", "Share"),
    (30., 140., "gain", "Market"), (24., 120., "unchanged", "Market"),
    (20., 100., "unchanged", "equal"),
])
def test_casino_narrative_tracks_calculated_direction_and_components(monkeypatch, capsys, fd_current, market_current, direction, dominant):
    scope = casino_scope()
    current = scope["raw"].period_start.dt.year.eq(2026)
    scope["raw"].loc[current & scope["raw"].operator.eq("FanDuel (MotorCity Casino)"), "gross_revenue"] = fd_current
    scope["raw"].loc[current & scope["raw"].operator.eq("Other"), "gross_revenue"] = market_current-fd_current
    scope["raw"].loc[current & scope["raw"].row_type.eq("official_statewide_total"), "gross_revenue"] = market_current
    monkeypatch.setattr(plt, "show", lambda: None)
    execute_casino(scope)
    osb = pd.DataFrame([dict(period_start=m, operator=op, row_type=kind, handle=value,
                            source_file="fixture", source_url="fixture")
        for m in scope["PRIOR"] + scope["CURRENT"]
        for op, kind, value in [(scope["fd_name"], "operator", 20. if m.year==2025 else 19.),
            ("Other", "operator", 80. if m.year==2025 else 81.), ("Total", "official_statewide_total", 100.)]])
    monkeypatch.setattr(pd, "read_sql_query", lambda *a, **k:osb)
    scope.update(connect_readonly=lambda _:SimpleNamespace(close=lambda:None), STAGING_DB="fixture", ORIGINAL_DB="fixture",
                 Markdown=lambda x:x, sha256=lambda _:"fixed", h0="fixed", s0="fixed")
    exec(source("93", "def osb_pooled"), scope)
    exec(source("93", "lines ="), scope)
    output = capsys.readouterr().out + "\n".join(scope["lines"])
    assert f"casino: {direction}" in output
    assert f"{dominant} contribution" in output
