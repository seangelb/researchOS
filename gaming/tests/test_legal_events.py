"""Legal status, provenance and clock errors must not become business facts."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from variant_gaming.legal import load_legal_events, capture_legal_source


@pytest.fixture
def event(tmp_path):
    source = tmp_path / "data/raw/GAMING_LEGAL/law.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"Test enacted law, retained original document")
    row = dict(event_id="tax_change", jurisdiction="MA", topic="gaming_tax", status="enacted",
        event_date="2025-06-30", effective_date="2025-07-01", published_on="2025-06-30",
        status_as_of="2025-06-30", recheck_on="2026-09-12", tickers=["FLUT", "DKNG", "CZR"],
        applicable_scope="Covered state online gambling operations only", observed_fact="A law was enacted",
        accounting_effect="A source-specific statutory tax; company cost unquantified",
        business_interpretation="Analyst inference: may affect costs", limitations="Subsequent amendments unreviewed",
        next_check="Recheck official statute", source_url="https://official.example/law.pdf",
        source_file=source.relative_to(tmp_path).as_posix(), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        captured_at="2026-09-12T01:00:00Z")
    path = tmp_path / "events.json"
    def load(**changes):
        path.write_text(json.dumps({"events": [dict(row, **changes)]}))
        return load_legal_events(path, root=tmp_path, as_of="2026-09-12T12:00:00Z")
    return dict(root=tmp_path, source=source, row=row, path=path, load=load)


def test_historical_status_capture_and_effective_date_remain_distinct(event):
    row = event["load"]().iloc[0]
    assert row.status == "enacted" and row.recheck_status == "recheck_due"
    assert row.effective_date_context == "effective_date_has_passed"
    assert row.status_age_days > 400
    assert row.evidence_status == "retained_historical_status_not_current_legal_confirmation"


@pytest.mark.parametrize("status", ["proposed", "interim_ruling", "litigation_pending", "unknown"])
def test_pending_or_unknown_status_never_becomes_enacted_by_capture(event, status):
    row = event["load"](status=status, effective_date=None, published_on=None).iloc[0]
    assert row.status == status and row.effective_date is None
    assert row.publication_clock == "unknown"


@pytest.mark.parametrize("changes", [
    {"captured_at": "2026-09-13T01:00:00Z"}, {"captured_at": "2026-09-12T01:00:00"},
    {"event_date": "2026-10-01"}, {"status_as_of": "2026-10-01"}, {"published_on": "2026-10-01"},
    {"effective_date": "2025-02-30"}, {"status": "in_force", "effective_date": "2027-01-01"},
    {"status": "proposed"}, {"status": "final_nationwide_ban"}, {"tickers": []},
    {"applicable_scope": ""}, {"source_file": "../escape.pdf"}, {"source_sha256": "a"*64},
])
def test_inconsistent_status_dates_scope_and_source_bytes_are_rejected(event, changes):
    with pytest.raises(ValueError):
        event["load"](**changes)


def test_changed_raw_source_and_duplicate_event_identity_cannot_reuse_validation(event):
    event["source"].write_bytes(b"new amendment")
    with pytest.raises(ValueError, match="source hash"):
        event["load"]()
    event["path"].write_text(json.dumps({"events": [event["row"], event["row"]]}))
    with pytest.raises(ValueError, match="Duplicate"):
        load_legal_events(event["path"], root=event["root"], as_of="2026-09-12T12:00:00Z")


def test_capture_previews_without_network_or_writes_and_retains_first_receipt(tmp_path, monkeypatch):
    calls = []
    def fetch(url):
        calls.append(url)
        return SimpleNamespace(content=b"official statute text", status_code=200, headers={}, text="official statute", url=url)
    monkeypatch.setattr("variant_gaming.legal.http_get", fetch)
    options = dict(root=tmp_path, url="https://official.example/law", filename="law.html")
    assert capture_legal_source(**options)["status"] == "preview"
    assert calls == [] and not (tmp_path / "data").exists()
    first = capture_legal_source(**options, live=True)
    source = tmp_path / first["source_file"]
    receipt = source.with_name(source.name + ".metadata.json")
    before = receipt.read_bytes()
    assert json.loads(before) == first
    assert capture_legal_source(**options, live=True) == first
    assert receipt.read_bytes() == before
