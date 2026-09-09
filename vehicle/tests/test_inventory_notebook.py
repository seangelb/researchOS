"""Exercise the inventory notebook against temporary retained search evidence."""
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from test_search import response_data
from test_vehicle_history import retained_query
from vehicle_tracker.readiness import query_readiness


def execute_loader(run_path, plan):
    notebook = json.loads((Path(__file__).parents[1] / "notebooks/10_carvana_inventory.ipynb").read_text(encoding="utf-8"))
    code = "".join(next(c["source"] for c in notebook["cells"] if c["id"] == "inventory-load"))
    namespace = dict(RUN_PATH=run_path, plan=plan, Path=Path, hashlib=hashlib,
                     json=json, pd=pd, query_readiness=query_readiness, display=lambda *_: None)
    exec(compile(code, "inventory-load", "exec"), namespace)
    return namespace


def plan():
    return dict(description="Synthetic test scope", queries=[dict(
        query_id="test", filters={}, zip_code="08542", location_filter=False)])


def test_missing_local_inventory_stays_unknown(tmp_path):
    result = execute_loader(tmp_path / "absent.json", plan())
    assert result["observations"].empty
    assert result["coverage"].status.tolist() == ["unattempted"]
    assert result["coverage"].admitted_rows.isna().all()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("problem", ["valid", "partial", "changed_source", "missing_source"])
def test_inventory_replays_only_verified_evidence(tmp_path, response_data, problem):
    if problem == "partial":
        response_data["inventory"]["pagination"].update(totalMatchedInventory=27, totalMatchedPages=2)
    report_path = retained_query(tmp_path, response_data)
    report = json.loads(report_path.read_text())
    artifacts = [report_path, report_path.parent / "vehicle.sqlite"]
    artifacts += [Path(page["retained_source"]) for page in report["pages"]]
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps(dict(outcomes=[dict(report=str(report_path), artifact_hashes={
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts})])))
    if problem == "changed_source":
        artifacts[-1].write_text("{}")
    elif problem == "missing_source":
        artifacts[-1].unlink()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    if problem in {"changed_source", "missing_source"}:
        with pytest.raises(ValueError, match="missing or changed"):
            execute_loader(checkpoint, plan())
    else:
        result = execute_loader(checkpoint, plan())
        assert len(result["observations"]) == 3
        assert bool(result["coverage"].query_complete.iloc[0]) == (problem == "valid")
        assert result["observations"].purchase_pending.eq(False).all()
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
