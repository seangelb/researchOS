"""Dated FLUT references and explicit scenarios; no automatic state-to-company mapping."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

import pandas as pd

from variant_gaming.refresh import validate_snapshot

SCOPE = "flutter_us_segment"
UNIT = "USD_millions"
SCHEMA = "flut_expectation_snapshot_v1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clock(value) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps require an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _quarter(value: str) -> pd.Period:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}Q[1-4]", value):
        raise ValueError("quarter must use YYYYQ1 through YYYYQ4")
    return pd.Period(value, freq="Q-DEC")


def _decimal(value, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{name} must be explicitly supplied")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not number.is_finite():
        raise ValueError(f"{name} must be a finite number")
    return number


def _bytes(document) -> bytes:
    return (json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(root: Path, binding: dict) -> Path:
    path = (root / binding["source_file"]).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("source file must exist inside the source root")
    if not re.fullmatch(r"[0-9a-f]{64}", binding["source_sha256"]):
        raise ValueError("source SHA256 must be a lowercase content hash")
    if _sha(path) != binding["source_sha256"]:
        raise ValueError(f"source hash mismatch: {path}")
    if not str(binding["source_url"]).startswith("https://"):
        raise ValueError("a retained source requires its HTTPS source URL")
    return path


def _database_hash(path: Path) -> str:
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError("frozen database has an unbound WAL or journal; checkpoint it before review")
    return _sha(path)


def _capture_binding(root: Path, database: Path, capture: datetime) -> dict:
    """Revalidate real rows/source bytes and bind their original completion receipts."""
    digest = _database_hash(database)
    documents, hashes = {}, {}
    for name in ("run_manifest.json", "validation.json"):
        raw = database.with_name(name).read_bytes()
        documents[name] = json.loads(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
        if documents[name].get("database_sha256") != digest:
            raise ValueError("capture receipt does not bind the current database")
    if _clock(documents["run_manifest.json"].get("finished_at")) != capture:
        raise ValueError("supplied data capture time differs from run_manifest.finished_at")
    validation, _ = validate_snapshot(root=root, database=database, as_of=capture)
    if validation["database_sha256"] != digest:
        raise ValueError("database changed while binding the capture")
    # Validation checks actual SQLite schema, row clocks, monetary values and raw hashes.
    for name, raw in documents.items():
        count = raw.get("validated_observations", raw.get("observation_count"))
        if count is not None and count != validation["validated_observations"]:
            raise ValueError(f"capture receipt observation count differs: {name}")
        if _sha(database.with_name(name)) != hashes[name]:
            raise ValueError("capture receipt changed during validation")
    return dict(receipt_hashes=hashes, database_sha256=digest,
                validated_observations=validation["validated_observations"])


def load_company_reference(path: Path, *, project_root: Path) -> dict:
    """Verify retained official PDFs and the explicit management phasing arithmetic."""
    raw = Path(path).read_bytes()
    document = json.loads(raw)
    if document.get("schema_version") != "flut_company_reference_v1":
        raise ValueError("unsupported company reference schema")
    sources = document["sources"]
    if len({row["source_id"] for row in sources}) != len(sources):
        raise ValueError("duplicate company source identity")
    for row in sources:
        _source(Path(project_root), row)
        if _clock(row["captured_at"]).date() < datetime.fromisoformat(row["published_on"]).date():
            raise ValueError("source capture cannot precede its publication date")
    ids = {row["source_id"] for row in sources}
    reference = document["management_reference"]
    if (reference["kind"] != "derived_management_reference" or reference["scope"] != SCOPE
            or reference["unit"] != UNIT or reference["metric"] != "revenue"):
        raise ValueError("company reference must be the separately labeled US revenue reference")
    _quarter(reference["period"])
    fraction = _decimal(reference["q3_approximate_fraction"], "phasing fraction")
    if not 0 < fraction <= 1:
        raise ValueError("management phasing fraction must be in (0, 1]")
    for name in ["low", "mid", "high"]:
        expected = _decimal(reference[f"fy_guidance_{name}"], name) * fraction
        if expected != _decimal(reference[f"value_{name}"], name):
            raise ValueError("management reference does not reconcile to its stated phasing")
    for row in [reference, *document["reported_actuals"]]:
        if row["source_id"] not in ids:
            raise ValueError("company figure is missing its retained source identity")
    document["source_root"] = str(Path(project_root).resolve())
    document["config_path"] = str(Path(path).resolve())
    document["config_sha256"] = hashlib.sha256(raw).hexdigest()
    return document


def analyst_scenario(*, period: str, sportsbook_handle_usd_millions=None,
                     sportsbook_net_margin=None, igaming_revenue_usd_millions=None,
                     other_revenue_usd_millions=None, rationale: str) -> dict:
    """Full reported-US scope, with margin as a fraction, e.g. 0.087 means 8.7%."""
    _quarter(period)
    raw = dict(sportsbook_handle_usd_millions=sportsbook_handle_usd_millions,
               sportsbook_net_margin=sportsbook_net_margin,
               igaming_revenue_usd_millions=igaming_revenue_usd_millions,
               other_revenue_usd_millions=other_revenue_usd_millions)
    inputs = {key: _decimal(value, key) for key, value in raw.items()}
    if inputs["sportsbook_handle_usd_millions"] < 0:
        raise ValueError("sportsbook handle cannot be negative")
    if not -1 <= inputs["sportsbook_net_margin"] <= 1:
        raise ValueError("net margin must be a fraction between -1 and 1")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("scenario rationale is required")
    sportsbook = inputs["sportsbook_handle_usd_millions"] * inputs["sportsbook_net_margin"]
    total = sportsbook + inputs["igaming_revenue_usd_millions"] + inputs["other_revenue_usd_millions"]
    return dict(kind="exploratory_analyst_scenario", period=period, scope=SCOPE, unit=UNIT,
                metric="revenue", value=str(total), sportsbook_revenue=str(sportsbook),
                inputs={key: str(value) for key, value in inputs.items()}, rationale=rationale.strip(),
                approval_status="unapproved", state_to_company_translation_applied=False)


def build_expectations_review(quarterly: pd.DataFrame, reference: dict, *, as_of,
                              data_capture_at, database_path: Path) -> dict:
    """Bind the native state evidence and separate issuer reference to today's capture.

    State amounts are USD and remain separate from issuer USD millions. No state
    evidence delta is converted into a forecast adjustment. All supplied quarter
    rows, including incomplete comparisons, are retained for review.
    """
    cutoff, capture = _clock(as_of), _clock(data_capture_at)
    if capture > cutoff or cutoff > _now():
        raise ValueError("data capture and information cutoff must already have occurred")
    retained = load_company_reference(Path(reference["config_path"]), project_root=Path(reference["source_root"]))
    if _bytes(reference) != _bytes(retained):
        raise ValueError("loaded company reference differs from its retained config; reload and review the changes")
    reference = retained
    target = reference["management_reference"]["period"]
    required = {"state_code", "vertical", "metric", "native_metric", "quarter", "through_month",
                "expected_window_months", "observed_window_months", "matched_window_months",
                "quarter_complete", "status", "source_refs"}
    if not required.issubset(quarterly.columns) or quarterly.empty:
        raise ValueError("quarterly scorecard rows and coverage columns are required")
    if set(quarterly.quarter) != {target}:
        raise ValueError("state evidence and company reference must have the exact same quarter")
    for month in quarterly.through_month.unique():
        p = pd.Period(month, freq="M")
        if str(p.asfreq("Q-DEC")) != target or p.end_time.date() > cutoff.date():
            raise ValueError("state evidence contains a future or wrong-quarter reporting month")
    root = Path(reference["source_root"])
    sources = list(reference["sources"])
    for row in sources:
        if _clock(row["captured_at"]) > cutoff:
            raise ValueError("company source was captured after the information cutoff")
        _source(root, row)
    refs = sorted({tuple(ref) for group in quarterly.source_refs for ref in group})
    state_sources = [dict(source_url=u, source_file=f, source_sha256=h) for u, f, h in refs]
    for row in state_sources:
        _source(root, row)
    if _sha(Path(reference["config_path"])) != reference["config_sha256"]:
        raise ValueError("company reference config changed after loading")
    capture_binding = _capture_binding(root, Path(database_path), capture)
    review = dict(schema_version=SCHEMA, period=target, scope=SCOPE,
                information_cutoff=cutoff.isoformat(), data_capture_at=capture.isoformat(),
                database_path=str(Path(database_path).resolve()), database_sha256=capture_binding["database_sha256"],
                capture_binding=capture_binding,
                source_root=str(root), company_sources=sources, state_sources=state_sources,
                company_reference_config_sha256=reference["config_sha256"],
                management_reference=reference["management_reference"],
                company_reported_actuals=reference["reported_actuals"],
                state_evidence=json.loads(quarterly.to_json(orient="records", date_format="iso")),
                state_evidence_unit="USD", state_to_company_adjustment=None,
                translation_status="unavailable_no_approved_coverage_or_gross_to_net_mapping",
                approval_status="unapproved_research", performance_claims_allowed=False)
    review["review_sha256"] = hashlib.sha256(_bytes(review)).hexdigest()
    return review


def freeze_expectation(review: dict, destination: Path, *, scenario: dict | None = None) -> Path:
    """Write a new immutable snapshot directory; the real write time cannot be supplied."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("expectation destination already exists; choose a new destination")
    frozen_at = _now()
    if review.get("schema_version") != SCHEMA or _clock(review["information_cutoff"]) > frozen_at:
        raise ValueError("invalid review schema or future information cutoff")
    checked = {key: value for key, value in review.items() if key != "review_sha256"}
    if hashlib.sha256(_bytes(checked)).hexdigest() != review.get("review_sha256"):
        raise ValueError("expectations review changed after its validation")
    if _database_hash(Path(review["database_path"])) != review["database_sha256"]:
        raise ValueError("database changed after the expectations review")
    binding = _capture_binding(Path(review["source_root"]), Path(review["database_path"]),
                               _clock(review["data_capture_at"]))
    if binding != review["capture_binding"]:
        raise ValueError("capture receipts changed after the expectations review")
    for row in [*review["company_sources"], *review["state_sources"]]:
        _source(Path(review["source_root"]), row)
    if scenario is not None:
        rebuilt = analyst_scenario(period=scenario["period"], rationale=scenario["rationale"], **scenario["inputs"])
        if rebuilt != scenario or scenario["period"] != review["period"]:
            raise ValueError("scenario arithmetic or period differs from the reviewed inputs")
    snapshot = dict(review, frozen_at=_now().isoformat(), scenario=scenario)
    raw = _bytes(snapshot)
    digest = hashlib.sha256(raw).hexdigest()
    destination.mkdir(parents=True, exist_ok=False)
    path = destination / "expectation.json"
    with path.open("xb") as stream:
        stream.write(raw)
    with (destination / "manifest.json").open("xb") as stream:
        stream.write(_bytes(dict(schema_version=SCHEMA, snapshot_sha256=digest,
                                 frozen_at=snapshot["frozen_at"], approval_status="unapproved_research")))
    return path


def evaluate_expectation(snapshot_path: Path, actual: dict | None = None, *, as_of,
                         source_root: Path | None = None) -> pd.DataFrame:
    """Compare a frozen US revenue reference/scenario to one exact reported actual.

    Missing actual -> pending. Known-but-unreported value -> unknown. No automatic
    writes, historical skill claims, or substitution of state totals for issuer revenue.
    """
    path = Path(snapshot_path)
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    manifest = json.loads(path.with_name("manifest.json").read_text(encoding="utf-8"))
    if (_sha(path) != manifest["snapshot_sha256"] or snapshot.get("schema_version") != SCHEMA
            or snapshot["frozen_at"] != manifest["frozen_at"]):
        raise ValueError("expectation snapshot failed its integrity check")
    cutoff = _clock(as_of)
    frozen = _clock(snapshot["frozen_at"])
    if cutoff > _now() or frozen > cutoff:
        raise ValueError("evaluation cutoff must follow the snapshot and cannot be in the future")
    value, status = None, "pending_actual"
    if actual is not None:
        expected = dict(period=snapshot["period"], scope=SCOPE, metric="revenue", unit=UNIT)
        if any(actual.get(key) != wanted for key, wanted in expected.items()):
            raise ValueError("actual must match the exact forecast period, scope, metric and unit")
        published, captured = _clock(actual["published_at"]), _clock(actual["captured_at"])
        if published > cutoff or captured > cutoff or captured < published:
            raise ValueError("actual was not publicly available and captured by the evaluation cutoff")
        if published.date() <= _quarter(actual["period"]).end_time.date():
            raise ValueError("quarterly actual publication must follow the completed quarter")
        if frozen >= published or _clock(snapshot["information_cutoff"]) >= published:
            raise ValueError("snapshot written after the result release is not a prospective forecast")
        if source_root is None:
            raise ValueError("actual source root is required to verify retained source bytes")
        _source(Path(source_root), actual)
        value = None if actual.get("value") is None else _decimal(actual["value"], "actual value")
        status = "unknown_actual_value" if value is None else "evaluated_not_skill_claim"
    reference = snapshot["management_reference"]
    estimates = [("derived_management_reference", _decimal(reference["value_mid"], "reference"))]
    if snapshot["scenario"] is not None:
        estimates.append(("exploratory_analyst_scenario", _decimal(snapshot["scenario"]["value"], "scenario")))
    output = []
    for kind, estimate in estimates:
        error = None if value is None else estimate - value
        output.append(dict(kind=kind, period=snapshot["period"], scope=SCOPE, unit=UNIT,
                           frozen_at=snapshot["frozen_at"], snapshot_sha256=manifest["snapshot_sha256"],
                           status=status, forecast=str(estimate), actual=None if value is None else str(value),
                           signed_error=None if error is None else str(error),
                           absolute_error=None if error is None else str(abs(error)),
                           error_pct=None if error is None or value == 0 else str(100*error/abs(value)),
                           actual_source_sha256=None if actual is None else actual["source_sha256"],
                           actual_source_url=None if actual is None else actual["source_url"],
                           performance_claims_allowed=False))
    return pd.DataFrame(output)
