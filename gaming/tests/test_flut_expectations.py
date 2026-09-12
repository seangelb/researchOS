"""Prospective errors, clock failures, missing assumptions and source identity."""
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
import sqlite3

import pandas as pd
import pytest

from variant_gaming import flut_expectations as expectations
from variant_gaming.storage import RESULT_COLUMNS, SCHEMA_SQL, UPSERT_SQL
from variant_gaming.flut_scorecard import MI_LABEL, build_monthly_scorecard, build_quarterly_scorecard


@pytest.fixture
def setup(tmp_path, monkeypatch):
    clock = {"now": datetime(2026, 9, 12, 12, tzinfo=timezone.utc)}
    monkeypatch.setattr(expectations, "_now", lambda: clock["now"])
    raw = tmp_path / "data/raw/report.pdf"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"Retained official test fixture")
    binding = dict(source_file="data/raw/report.pdf", source_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
                   source_url="https://official.example/report.pdf")
    config = dict(schema_version="flut_company_reference_v1", sources=[dict(binding, source_id="q2",
                  published_on="2026-08-05", captured_at="2026-09-11T12:00:00Z")],
                  reported_actuals=[], management_reference=dict(kind="derived_management_reference",
                  scope=expectations.SCOPE, period="2026Q3", unit=expectations.UNIT, metric="revenue",
                  fy_guidance_low="7125", fy_guidance_mid="7400", fy_guidance_high="7675",
                  value_low="1425", value_mid="1480", value_high="1535",
                  q3_approximate_fraction="0.20", source_id="q2"))
    config_path = tmp_path / "reference.json"
    config_path.write_text(json.dumps(config))
    reference = expectations.load_company_reference(config_path, project_root=tmp_path)
    database = tmp_path / "capture.sqlite"
    rows = []
    for year, fd, other in [(2026, 120, 180), (2025, 100, 150)]:
        for operator, amount, row_type in [("FanDuel (MotorCity Casino)", fd, "operator"),
                                          ("Other", other, "operator"),
                                          ("STATEWIDE", fd + other, "official_statewide_total")]:
            row = dict.fromkeys(RESULT_COLUMNS)
            row.update(binding, jurisdiction="Michigan", state_code="MI", vertical="online_sports_betting",
                       channel="online", operator=operator, row_type=row_type, period_start=f"{year}-07-01",
                       period_end=f"{year}-07-31", frequency="monthly", handle=amount,
                       gross_revenue=amount/10, adjusted_revenue=amount/20, reported_revenue_name=MI_LABEL,
                       retrieved_at_utc="2026-09-11T11:00:00Z", report_status="ok")
            rows.append(row)
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.executemany(UPSERT_SQL, rows)
        connection.commit()
    def receipts():
        digest = hashlib.sha256(database.read_bytes()).hexdigest()
        (tmp_path / "run_manifest.json").write_text(json.dumps(dict(database_sha256=digest,
                    finished_at="2026-09-11T12:00:00Z", observation_count=len(rows))))
        (tmp_path / "validation.json").write_text(json.dumps(dict(database_sha256=digest, validated_observations=len(rows))))
    receipts()
    quarterly = build_quarterly_scorecard(build_monthly_scorecard(pd.DataFrame(rows)),
                                          quarter="2026Q3", through_month="2026-07")
    def review():
        return expectations.build_expectations_review(quarterly, reference, as_of="2026-09-12T12:00:00Z",
                    data_capture_at="2026-09-11T12:00:00Z", database_path=database)
    actual = dict(binding, period="2026Q3", scope=expectations.SCOPE, metric="revenue", unit=expectations.UNIT,
                  value="1490", published_at="2026-11-12T12:00:00Z", captured_at="2026-11-12T13:00:00Z")
    return dict(root=tmp_path, clock=clock, raw=raw, reference=reference, config=config, config_path=config_path,
                database=database, quarterly=quarterly, review=review, actual=actual, receipts=receipts)


def scenario(**changes):
    kwargs = dict(period="2026Q3", sportsbook_handle_usd_millions="10000", sportsbook_net_margin="0.09",
                  igaming_revenue_usd_millions="540", other_revenue_usd_millions="60",
                  rationale="Explicit synthetic full-US inputs for a unit test")
    return expectations.analyst_scenario(**dict(kwargs, **changes))


def later(setup):
    setup["clock"]["now"] = datetime(2026, 11, 20, tzinfo=timezone.utc)
    return "2026-11-20T00:00:00Z"


@pytest.mark.parametrize("field", ["sportsbook_handle_usd_millions", "sportsbook_net_margin",
                                     "igaming_revenue_usd_millions", "other_revenue_usd_millions"])
def test_missing_scenario_assumption_never_defaults(field):
    with pytest.raises(ValueError, match="explicitly supplied"):
        scenario(**{field: None})


def test_margin_is_fraction_and_explicit_zero_is_preserved():
    with pytest.raises(ValueError, match="fraction"):
        scenario(sportsbook_net_margin="8.7")
    row = scenario(sportsbook_net_margin="0")
    assert row["value"] == "600" and row["sportsbook_revenue"] == "0"
    assert row["approval_status"] == "unapproved"


@pytest.mark.parametrize("value", ["NaN", "Infinity", True])
def test_nonfinite_or_boolean_financial_input_rejected(value):
    with pytest.raises(ValueError):
        scenario(sportsbook_handle_usd_millions=value)


def test_native_evidence_and_reference_keep_scope_and_units(setup):
    review = setup["review"]()
    assert next(row for row in review["state_evidence"] if row["state_code"] == "MI" and row["metric"] == "handle")["fd_amount"] == 120
    assert review["state_evidence_unit"] == "USD"
    assert review["management_reference"]["unit"] == "USD_millions"
    assert review["management_reference"]["value_mid"] == "1480"
    assert review["state_to_company_adjustment"] is None
    assert not review["performance_claims_allowed"]


@pytest.mark.parametrize("field,value", [("quarter", "2026Q2"), ("through_month", "2026-09")])
def test_wrong_quarter_and_future_month_rejected(setup, field, value):
    setup["quarterly"].loc[0, field] = value
    with pytest.raises(ValueError):
        setup["review"]()


def test_capture_lookahead_and_naive_clock_rejected(setup):
    for capture in ["2026-09-13T00:00:00Z", "2026-09-11T12:00:00"]:
        with pytest.raises(ValueError):
            expectations.build_expectations_review(setup["quarterly"], setup["reference"],
                 as_of="2026-09-12T12:00:00Z", data_capture_at=capture, database_path=setup["database"])


def test_company_source_captured_after_cutoff_is_excluded(setup):
    setup["config"]["sources"][0]["captured_at"] = "2026-09-13T00:00:00Z"
    setup["config_path"].write_text(json.dumps(setup["config"]))
    setup["reference"].update(expectations.load_company_reference(setup["config_path"], project_root=setup["root"]))
    with pytest.raises(ValueError, match="captured after"):
        setup["review"]()


@pytest.mark.parametrize("payload", ["midpoint", "source", "actual"])
def test_mutated_loaded_reference_cannot_reuse_original_config_hash(setup, payload):
    before = setup["config_path"].read_bytes()
    if payload == "midpoint":
        setup["reference"]["management_reference"]["value_mid"] = "1490"
    elif payload == "source":
        setup["reference"]["sources"][0]["source_url"] = "https://official.example/different-report.pdf"
    else:
        setup["reference"]["reported_actuals"].append(dict(period="2026Q2", scope=expectations.SCOPE,
            metric="revenue", value="9999", unit=expectations.UNIT, source_id="q2"))
    with pytest.raises(ValueError, match="differs from its retained config"):
        setup["review"]()
    assert setup["config_path"].read_bytes() == before


def test_reference_requires_consistent_phasing_arithmetic(setup):
    setup["config"]["management_reference"]["value_mid"] = "1500"
    setup["config_path"].write_text(json.dumps(setup["config"]))
    with pytest.raises(ValueError, match="phasing"):
        expectations.load_company_reference(setup["config_path"], project_root=setup["root"])


def test_freeze_refuses_changed_database_or_unbound_wal(setup):
    review = setup["review"]()
    setup["database"].write_bytes(b"changed")
    with pytest.raises(ValueError, match="database changed"):
        expectations.freeze_expectation(review, setup["root"] / "snapshot")
    setup["database"].with_name("capture.sqlite-wal").write_bytes(b"unbound WAL data")
    with pytest.raises(ValueError, match="WAL"):
        setup["review"]()


def test_freeze_refuses_changed_source_and_scenario(setup):
    review = setup["review"]()
    changed = scenario()
    changed["value"] = "1600"
    with pytest.raises(ValueError, match="scenario arithmetic"):
        expectations.freeze_expectation(review, setup["root"] / "snapshot", scenario=changed)
    setup["raw"].write_bytes(b"new revision")
    with pytest.raises(ValueError, match="[Ss]ource hash mismatch"):
        expectations.freeze_expectation(review, setup["root"] / "snapshot")


def test_supplied_capture_clock_must_match_completed_run(setup):
    with pytest.raises(ValueError, match="differs from run_manifest.finished_at"):
        expectations.build_expectations_review(setup["quarterly"], setup["reference"],
            as_of="2026-09-12T12:00:00Z", data_capture_at="2026-09-10T12:00:00Z",
            database_path=setup["database"])


@pytest.mark.parametrize("clock,reason", [("2026-09-12T00:00:00Z", "future retrieval"),
                                          ("2026-09-11T11:00:00", "timezone"),
                                          ("", "Missing provenance")])
def test_actual_row_capture_clock_is_validated_even_when_receipts_match(setup, clock, reason):
    with closing(sqlite3.connect(setup["database"])) as connection:
        connection.execute("UPDATE gaming_results SET retrieved_at_utc = ?", (clock,))
        connection.commit()
    setup["receipts"]()
    with pytest.raises(ValueError, match=reason):
        setup["review"]()


def test_arbitrary_bytes_cannot_be_a_validated_database(setup):
    setup["database"].write_bytes(b"This is not a SQLite database")
    setup["receipts"]()
    with pytest.raises(sqlite3.DatabaseError):
        setup["review"]()


@pytest.mark.parametrize("name", ["run_manifest.json", "validation.json"])
def test_receipts_must_bind_database_and_remain_unchanged_before_freeze(setup, name):
    review = setup["review"]()
    path = setup["root"] / name
    receipt = json.loads(path.read_text())
    path.write_text(json.dumps(dict(receipt, changed_after_review=True)))
    with pytest.raises(ValueError, match="receipts changed"):
        expectations.freeze_expectation(review, setup["root"] / "snapshot")
    path.write_text(json.dumps(dict(receipt, database_sha256="a" * 64)))
    with pytest.raises(ValueError, match="receipt does not bind"):
        setup["review"]()


def test_existing_snapshot_is_never_overwritten(setup):
    destination = setup["root"] / "snapshot"
    path = expectations.freeze_expectation(setup["review"](), destination)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        expectations.freeze_expectation(setup["review"](), destination)
    assert path.read_bytes() == before


def test_review_changes_and_wrong_scenario_quarter_rejected(setup):
    review = setup["review"]()
    with pytest.raises(ValueError, match="scenario arithmetic or period"):
        expectations.freeze_expectation(review, setup["root"] / "snapshot", scenario=scenario(period="2026Q4"))
    review["management_reference"]["value_mid"] = "1490"
    with pytest.raises(ValueError, match="review changed"):
        expectations.freeze_expectation(review, setup["root"] / "snapshot")


def test_pending_and_unknown_actual_remain_missing(setup):
    path = expectations.freeze_expectation(setup["review"](), setup["root"] / "snapshot")
    pending = expectations.evaluate_expectation(path, as_of="2026-09-12T12:00:00Z").iloc[0]
    assert pending.status == "pending_actual" and pending.actual is None and pending.absolute_error is None
    unknown = expectations.evaluate_expectation(path, dict(setup["actual"], value=None),
                 as_of=later(setup), source_root=setup["root"]).iloc[0]
    assert unknown.status == "unknown_actual_value" and unknown.actual is None


def test_reference_and_scenario_errors_remain_distinct(setup):
    path = expectations.freeze_expectation(setup["review"](), setup["root"] / "snapshot", scenario=scenario())
    rows = expectations.evaluate_expectation(path, setup["actual"], as_of=later(setup), source_root=setup["root"])
    assert rows.kind.tolist() == ["derived_management_reference", "exploratory_analyst_scenario"]
    assert rows.signed_error.tolist() == ["-10", "10.00"]
    assert rows.absolute_error.tolist() == ["10", "10.00"]
    assert not rows.performance_claims_allowed.any()


@pytest.mark.parametrize("field,value", [("period", "2026Q2"), ("scope", "fanduel_state_panel"),
                                         ("unit", "USD"), ("metric", "gross_revenue")])
def test_wrong_actual_cannot_be_compared(setup, field, value):
    path = expectations.freeze_expectation(setup["review"](), setup["root"] / "snapshot")
    with pytest.raises(ValueError, match="exact forecast"):
        expectations.evaluate_expectation(path, dict(setup["actual"], **{field: value}),
                    as_of=later(setup), source_root=setup["root"])


def test_future_actual_and_future_evaluation_cutoff_rejected(setup):
    path = expectations.freeze_expectation(setup["review"](), setup["root"] / "snapshot")
    with pytest.raises(ValueError, match="not publicly available"):
        expectations.evaluate_expectation(path, setup["actual"], as_of="2026-09-12T12:00:00Z",
                                            source_root=setup["root"])
    with pytest.raises(ValueError, match="future"):
        expectations.evaluate_expectation(path, as_of="2026-12-31T00:00:00Z")


def test_forecast_frozen_after_release_cannot_claim_prospective_error(setup):
    review = setup["review"]()
    later(setup)
    path = expectations.freeze_expectation(review, setup["root"] / "snapshot")
    with pytest.raises(ValueError, match="not a prospective forecast"):
        expectations.evaluate_expectation(path, setup["actual"], as_of=later(setup), source_root=setup["root"])


def test_snapshot_tampering_is_detected(setup):
    path = expectations.freeze_expectation(setup["review"](), setup["root"] / "snapshot")
    path.write_bytes(path.read_bytes().replace(b'"1480"', b'"1490"'))
    with pytest.raises(ValueError, match="integrity check"):
        expectations.evaluate_expectation(path, as_of="2026-09-12T12:00:00Z")


@pytest.mark.parametrize("field,value", [("fd_amount", 12000), ("fd_growth_pct", 999),
                                         ("matched_window_months", 0), ("status", "excluded")])
def test_edited_quarterly_figures_cannot_inherit_database_verification(setup, field, value):
    quarterly = setup["quarterly"]
    index = quarterly.index[quarterly.state_code.eq("MI") & quarterly.metric.eq("handle")][0]
    quarterly.loc[index, field] = value
    with pytest.raises(ValueError, match="recomputed from the bound database"):
        setup["review"]()


def test_subset_or_extra_claim_cannot_inherit_database_verification(setup):
    setup["quarterly"]["unbound_company_forecast"] = 9999
    with pytest.raises(ValueError, match="recomputed from the bound database"):
        setup["review"]()
