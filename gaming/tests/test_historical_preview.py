"""Historical comparison tests use only small DataFrames and temporary files."""
import hashlib
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("preview_historical", ROOT / "scripts/preview_historical_corrections.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def observations(**overrides):
    row = dict(state_code="NJ", jurisdiction="New Jersey", vertical="online_casino", channel="online",
        operator="Native Casino", row_type="operator", period_start="2026-01-01", period_end="2026-01-31",
        frequency="monthly", gross_revenue=100., handle=None, adjusted_revenue=None, taxable_revenue=None,
        net_proceeds=None, tax=10., reported_revenue_name="Gross Receipts", source_sha256="one",
        source_file="data/raw/NJ/fixture.pdf", source_url="https://example.invalid/retained.pdf",
        retrieved_at_utc="2026-09-01T00:00:00+00:00", report_status="ok")
    return pd.DataFrame([dict(row, **overrides)])


@pytest.mark.parametrize("value,status,difference", [(120., "changed", 20.), (-100., "changed", -200.),
    (0., "changed", -100.), (100., "unchanged", 0.), (None, "changed", None)])
def test_value_changes_preserve_zero_sign_and_missing(value, status, difference):
    result = preview.compare_observations(observations(), observations(gross_revenue=value))
    row = result[result.metric.eq("gross_revenue")].iloc[0]
    assert row.status == status
    assert pd.isna(row.difference) if difference is None else row.difference == difference
    if value is None:
        assert pd.isna(row.candidate_value) and "unknown" in row.reason
    assert row.units == "USD"


def test_removed_and_added_identities_do_not_survive_as_old_rows():
    result = preview.compare_observations(observations(), observations(operator="Correct native casino"))
    assert set(result[result.operator.eq("Native Casino")].status) == {"removed"}
    assert result[result.operator.eq("Native Casino")].candidate_value.isna().all()
    assert set(result[result.operator.eq("Correct native casino")].status) == {"added"}


def test_rejected_report_is_blocked_not_a_valid_old_value_or_approved_removal():
    empty = pd.DataFrame(columns=preview.RESULT_COLUMNS)
    result = preview.compare_observations(observations(), empty, error="parser returned no observations")
    assert result.status.eq("blocked").all() and result.candidate_value.isna().all()
    assert not result.candidate_available.any()


def test_missing_metric_is_not_zero_even_when_both_sides_missing():
    result = preview.compare_observations(observations(), observations())
    row = result[result.metric.eq("handle")].iloc[0]
    assert row.status == "unchanged" and pd.isna(row.candidate_value)


@pytest.mark.parametrize("problem", ["missing", "wrong_hash", "outside"])
def test_retained_source_requires_existing_matching_file_inside_raw(tmp_path, problem):
    path = tmp_path / "data/raw/NJ/fixture.pdf"
    path.parent.mkdir(parents=True)
    content = b"retained bytes"
    row = observations(source_sha256=hashlib.sha256(content).hexdigest())
    if problem != "missing":
        path.write_bytes(content)
    if problem == "wrong_hash":
        row["source_sha256"] = "wrong"
    elif problem == "outside":
        row["source_file"] = "../outside.pdf"
    with pytest.raises((FileNotFoundError, ValueError)):
        preview.verify_sources(tmp_path, row)


def test_all_paths_for_source_must_be_verified(tmp_path):
    content = b"retained"
    path = tmp_path / "data/raw/NJ/fixture.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    row = observations(source_sha256=hashlib.sha256(content).hexdigest())
    assert preview.verify_sources(tmp_path, row) == content
    alternate = row.assign(source_file="data/raw/NJ/missing.pdf")
    with pytest.raises(FileNotFoundError):
        preview.verify_sources(tmp_path, pd.concat([row, alternate]))


@pytest.mark.parametrize("definition", [False, True])
def test_conflicting_versions_remain_blocked_without_newest_capture_choice(definition):
    a = observations()
    b = observations(source_sha256="two", **({"reported_revenue_name":"Adjusted Gross"} if definition else {"gross_revenue":200.}))
    parts = [preview.compare_observations(frame, frame).assign(database="staging", source_sha256=frame.source_sha256.iloc[0]) for frame in (a,b)]
    result = preview.flag_conflicts(pd.concat(parts, ignore_index=True))
    revenue = result[result.metric.eq("gross_revenue")]
    assert revenue.status.eq("blocked").all()
    assert revenue.proposed_status.eq("unchanged").all()
    assert set(revenue.source_sha256) == {"one", "two"}
    if definition:
        assert result.status.eq("blocked").all()


def test_equal_source_versions_remain_separate_without_false_conflict():
    result = preview.compare_observations(observations(), observations()).assign(database="staging", source_sha256="one")
    two = result.assign(source_sha256="two")
    flagged = preview.flag_conflicts(pd.concat([result, two], ignore_index=True))
    assert flagged.status.eq("unchanged").all() and len(flagged) == 14


def test_duplicate_within_source_is_rejected():
    row = observations()
    with pytest.raises(ValueError, match="duplicate observation keys"):
        preview.compare_observations(pd.concat([row,row]), row)


def test_network_is_blocked():
    import requests
    with preview.no_network(), pytest.raises(RuntimeError, match="retained local reports only"):
        requests.get("https://example.invalid/report")


@pytest.mark.parametrize("failed", [False, True])
def test_preview_uses_readonly_databases_and_excludes_failed_candidates(tmp_path, monkeypatch, failed):
    from variant_gaming.storage import connect, ensure_schema, upsert_gaming_results
    content = b"retained report"
    path = tmp_path / "data/raw/NJ/fixture.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    old = observations(source_sha256=hashlib.sha256(content).hexdigest())
    database = tmp_path / "old.sqlite"
    conn = connect(database)
    ensure_schema(conn)
    upsert_gaming_results(conn, old)
    conn.close()
    before = database.read_bytes()
    def parse(content, frame):
        if failed:
            raise ValueError("rejected fixture")
        return frame.assign(gross_revenue=None)
    monkeypatch.setattr(preview, "parse_retained", parse)
    inventory, comparison, candidates = preview.preview(tmp_path, {"test":database})
    assert database.read_bytes() == before and inventory.hash_verified.all()
    if failed:
        assert candidates.empty and comparison.status.eq("blocked").all()
    else:
        assert candidates.gross_revenue.isna().all()
        assert candidates.approval.eq("exploratory_unapproved").all()
        assert comparison[comparison.metric.eq("gross_revenue")].status.eq("changed").all()


@pytest.mark.parametrize("kind,skip", [("operator", True), ("official_statewide_total", False)])
def test_ny_replay_matches_existing_collector_placeholder_policy(monkeypatch, kind, skip):
    observed = []
    old = observations(state_code="NY", vertical="online_sports_betting", row_type=kind)
    def parse(content, **kwargs):
        observed.append(kwargs)
        return pd.DataFrame({"fixture":[1]}), pd.DataFrame()
    monkeypatch.setattr(preview.new_york, "parse_ny_workbook", parse)
    monkeypatch.setattr(preview.new_york, "build_normalized_rows", lambda *a, **k:old)
    preview.parse_retained(b"fixture", old)
    assert observed == [{"skip_zero_placeholders":skip}]


def test_michigan_label_change_keeps_gross_and_adjusted_values_distinct():
    old = observations(state_code="MI", gross_revenue=100., adjusted_revenue=80., reported_revenue_name="Adjusted Gross")
    new = old.assign(reported_revenue_name="Gross Receipts")
    result = preview.compare_observations(old, new)
    assert result[result.metric.isin(preview.MONEY)].status.eq("unchanged").all()
    assert result[result.status.eq("changed")].metric.tolist() == ["reported_revenue_name"]
