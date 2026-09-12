"""A small retained legal-event table; observations and analyst inference stay separate."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import pandas as pd

from variant_gaming.common import http_get, http_block_reason, save_raw_bytes, sha256_bytes

STATUSES = {"enacted", "in_force", "proposed", "litigation_pending", "interim_ruling",
            "final_ruling", "withdrawn", "unknown"}
FIELDS = ["event_id", "jurisdiction", "topic", "status", "event_date", "effective_date", "published_on",
          "status_as_of", "recheck_on", "tickers", "applicable_scope", "observed_fact", "accounting_effect",
          "business_interpretation", "limitations", "next_check", "source_url", "source_file", "source_sha256", "captured_at"]


def _date(value, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Legal dates require YYYY-MM-DD, or null for genuinely unknown dates")
    return pd.Timestamp(value).date()


def load_legal_events(path, *, root, as_of):
    """Read retained events, checking bytes and clocks without asserting current law.

    A historical cutoff rejects later captures rather than silently using them.
    Publication dates can be unknown: capture proves possession only by that time.
    Effective-date context describes the retained event, not subsequent law.
    """
    cutoff = pd.Timestamp(as_of)
    if cutoff.tzinfo is None or pd.isna(cutoff) or cutoff > pd.Timestamp.now(tz="UTC"):
        raise ValueError("Legal cutoff must be a past/current timezone timestamp")
    root = Path(root).resolve()
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    events = document["events"]
    if len({row.get("event_id") for row in events}) != len(events):
        raise ValueError("Duplicate legal event identity; record revisions explicitly")
    output = []
    for row in events:
        if not set(FIELDS).issubset(row):
            raise ValueError("Legal event is missing required evidence/scope fields")
        for field in set(FIELDS) - {"effective_date", "published_on", "tickers"}:
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError(f"Missing legal event value: {field}")
        if row["status"] not in STATUSES:
            raise ValueError("Unknown legal status; do not infer enacted law from a proposal")
        tickers = row["tickers"]
        if not isinstance(tickers, list) or not tickers or set(tickers) - {"FLUT", "DKNG", "CZR"} or len(set(tickers)) != len(tickers):
            raise ValueError("Use explicit unique applicable research tickers")
        captured = pd.Timestamp(row["captured_at"])
        if pd.isna(captured) or captured.tzinfo is None or captured > cutoff:
            raise ValueError("Legal source capture is missing a timezone or follows the information cutoff")
        dates = {name: _date(row[name], nullable=name in {"effective_date", "published_on"})
                 for name in ["event_date", "effective_date", "published_on", "status_as_of", "recheck_on"]}
        if any(dates[name] is not None and dates[name] > captured.date()
               for name in ["event_date", "published_on", "status_as_of"]):
            raise ValueError("Legal event, publication or status date follows the source capture")
        if dates["status_as_of"] < dates["event_date"]:
            raise ValueError("Legal status date precedes its event")
        if row["status"] == "in_force" and (dates["effective_date"] is None or dates["effective_date"] > dates["status_as_of"]):
            raise ValueError("An in-force status requires an effective date by the observed status date")
        if row["status"] in {"proposed", "withdrawn"} and dates["effective_date"] is not None:
            raise ValueError("A proposal or withdrawn event has no established effective date")
        source = (root / row["source_file"]).resolve()
        if not source.is_relative_to(root / "data/raw"):
            raise ValueError("Legal source must stay within retained data/raw")
        if not re.fullmatch(r"[0-9a-f]{64}", row["source_sha256"]) or sha256_bytes(source.read_bytes()) != row["source_sha256"]:
            raise ValueError("Legal source bytes do not match their source hash")
        if not row["source_url"].startswith("https://"):
            raise ValueError("Legal source requires an HTTPS URL")
        effective = dates["effective_date"]
        context = ("effective_date_unknown" if effective is None else
                   "future_effective_date" if effective > cutoff.date() else "effective_date_has_passed")
        output.append(dict(row, effective_date_context=context,
                           recheck_status="recheck_due" if dates["recheck_on"] <= cutoff.date() else "scheduled_recheck",
                           status_age_days=(cutoff.date() - dates["status_as_of"]).days,
                           publication_clock="date_known_time_unknown" if dates["published_on"] else "unknown",
                           evidence_status="retained_historical_status_not_current_legal_confirmation"))
    return pd.DataFrame(output, columns=[*FIELDS, "source_id", "source_locator", "effective_date_context", "recheck_status",
                                        "status_age_days", "publication_clock", "evidence_status"])


def capture_legal_source(*, root, url, filename, live=False):
    """Explicit one-source capture, preview by default; event interpretation is manual.

    Uses the existing content-hashed raw-byte saver. This never updates the event
    table or overwrites old evidence. The caller chooses the project destination.
    """
    if not url.startswith("https://") or Path(filename).name != filename or not filename:
        raise ValueError("Choose an HTTPS source URL and a plain filename")
    root = Path(root).resolve()
    if not live:
        return dict(status="preview", source_url=url, destination=str(root / "data/raw/GAMING_LEGAL"), filename=filename)
    response = http_get(url)
    blocked = http_block_reason(response)
    if blocked or not response.content:
        raise ValueError(blocked or "Empty legal source")
    captured = datetime.now(timezone.utc)
    path = save_raw_bytes(root, "GAMING_LEGAL", response.content, filename, retrieved_at=captured)
    binding = dict(source_url=url, source_file=path.relative_to(root).as_posix(),
                   source_sha256=sha256_bytes(response.content), captured_at=captured.isoformat())
    receipt = path.with_name(path.name + ".metadata.json")
    if receipt.exists():
        retained = json.loads(receipt.read_text(encoding="utf-8"))
        if any(retained.get(key) != binding[key] for key in ["source_url", "source_file", "source_sha256"]):
            raise ValueError("Existing raw-byte receipt has a different source identity; retain it for review")
        return retained  # Preserve the first captured-at clock for identical bytes.
    with receipt.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(binding, indent=2) + "\n")
    return binding
