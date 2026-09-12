"""Compare retained DE/PA/NJ/NH/NY/MI reports with both read-only snapshots.

No collection or database replacement. Outputs are exploratory CSV DataFrames;
status is added/changed/removed/unchanged/blocked, monetary units are USD. The
proposed_status column preserves a mechanical difference when conflicts block it.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from variant_gaming.storage import RESULT_COLUMNS, connect_readonly, validate_gaming_results_frame
from variant_gaming.states import delaware, michigan, new_hampshire, new_jersey, new_york, pennsylvania

KEY = ["state_code", "vertical", "channel", "operator", "row_type", "period_start", "period_end", "frequency"]
MONEY = ["handle", "gross_revenue", "adjusted_revenue", "taxable_revenue", "net_proceeds", "tax"]
METRICS = MONEY + ["reported_revenue_name"]
SCOPE = {("DE", "online_casino"), ("PA", "online_casino"), ("NJ", "online_casino"),
         ("NJ", "online_sports_betting"), ("NH", "online_sports_betting"),
         ("NY", "online_sports_betting"), ("MI", "online_casino")}


def verify_sources(root: Path, rows: pd.DataFrame) -> bytes:
    """All stored paths must resolve inside data/raw and match the one source hash."""
    hashes = rows.source_sha256.unique()
    if len(hashes) != 1 or pd.isna(hashes[0]):
        raise ValueError("ambiguous source hash")
    payload = None
    for relative in rows.source_file.unique():
        path = (root / relative).resolve()
        if not path.is_relative_to((root / "data/raw").resolve()):
            raise ValueError(f"source outside retained raw directory: {relative}")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != hashes[0]:
            raise ValueError(f"source hash mismatch: {relative}")
        payload = content
    if payload is None:
        raise ValueError("no retained source")
    return payload


def parse_retained(content: bytes, old: pd.DataFrame) -> pd.DataFrame:
    """Use existing state parsers and normalizers; metadata comes from stored evidence."""
    row = old.iloc[0]
    metadata = dict(source_url=row.source_url, source_file=row.source_file,
                    source_sha256=row.source_sha256, retrieved_at=pd.Timestamp(row.retrieved_at_utc).to_pydatetime())
    state, product = row.state_code, row.vertical
    if state == "DE":
        parsed = delaware.parse_igaming_html(content.decode("utf-8-sig"))
        result = delaware.build_normalized_rows(parsed, vertical=product, **metadata)
    elif state == "PA":
        parsed = pennsylvania.parse_interactive_workbook(content)
        result = pennsylvania.build_casino_normalized(parsed, **metadata)
    elif state == "NJ":
        parsed = new_jersey.parse_nj_pdf(content, vertical=product)
        result = new_jersey.build_normalized_rows(parsed, vertical=product, **metadata)
    elif state == "NH":
        parsed = new_hampshire.parse_summary_pdf(content)
        result = new_hampshire.build_normalized_rows(parsed, **metadata)
    elif state == "NY":
        identities = old[["operator", "row_type"]].drop_duplicates()
        if len(identities) != 1:
            raise ValueError("ambiguous NY workbook/operator binding")
        # Match the existing collector: operator templates contain future zero rows.
        operator_workbook = identities.iloc[0].row_type == "operator"
        parsed, reconciliation = new_york.parse_ny_workbook(content, skip_zero_placeholders=operator_workbook)
        result = new_york.build_normalized_rows(parsed, **identities.iloc[0].to_dict(), **metadata)
    elif state == "MI":
        years = pd.to_datetime(old.period_start).dt.year.unique()
        if len(years) != 1:
            raise ValueError("ambiguous MI workbook year selection")
        parsed = michigan.parse_michigan_workbook(content, vertical=product, year_hint=int(years[0]))
        result = michigan.build_normalized_rows(parsed, vertical=product, **metadata)
    else:
        raise ValueError(f"unsupported preview scope: {state}/{product}")
    if result.empty:
        raise ValueError("parser returned no observations; existing rows are not certified")
    result = result.reindex(columns=RESULT_COLUMNS)
    validate_gaming_results_frame(result)
    if result[KEY].isna().any(axis=None) or result.duplicated(KEY).any():
        raise ValueError("parser produced missing or duplicate observation keys")
    if result[MONEY].isin([float("inf"), -float("inf")]).any(axis=None):
        raise ValueError("parser produced infinite monetary values")
    return result


def compare_observations(old: pd.DataFrame, new: pd.DataFrame, *, error: str = "") -> pd.DataFrame:
    """Outer comparison: missing rows and values never become zero or survive silently."""
    if old.duplicated(KEY).any() or new.duplicated(KEY).any():
        raise ValueError("duplicate observation keys within source version")
    left = old.set_index(KEY)[METRICS]
    right = new.set_index(KEY)[METRICS]
    rows = []
    for key in left.index.union(right.index, sort=False):
        existed, available = key in left.index, key in right.index
        for metric in METRICS:
            before = left.loc[key, metric] if existed else None
            after = right.loc[key, metric] if available else None
            before = None if pd.isna(before) else before
            after = None if pd.isna(after) else after
            same = before == after  # No rounding away small differences in retained financial values.
            status = "unchanged" if same else "changed"
            if not existed:
                status = "added"
            elif not available:
                status = "removed"
            if error:
                status = "blocked"
            reason = error or {"unchanged": "same retained source and value", "added": "parser now emits this observation",
                "removed": "parser no longer emits this observation; review omission/rejection",
                "changed": "retained-source replay differs from stored value"}[status]
            if not error and existed and available and before is not None and after is None:
                reason = "parser now leaves this metric unknown; old value must not survive"
            rows.append(dict(zip(KEY, key), metric=metric, existing_value=before, candidate_value=after,
                difference=after-before if metric in MONEY and before is not None and after is not None else None,
                units="USD" if metric in MONEY else "native label", status=status, proposed_status=status,
                reason=reason, candidate_available=available and not error))
    return pd.DataFrame(rows)


def flag_conflicts(comparison: pd.DataFrame) -> pd.DataFrame:
    """Keep all versions and block differing values/definitions; never pick a capture."""
    result = comparison.copy()
    group = ["database"] + KEY + ["metric"]
    tokens = result[["existing_value", "candidate_value"]].astype(object).where(
        result[["existing_value", "candidate_value"]].notna(), "<missing>")
    for side in tokens:
        count = tokens[side].groupby([result[c] for c in group], dropna=False).transform("nunique")
        conflict = count.gt(1) & result.groupby(group, dropna=False).source_sha256.transform("nunique").gt(1)
        result.loc[conflict, "status"] = "blocked"
        result.loc[conflict, "reason"] += f"; unresolved source versions disagree on {side}"
    # A conflicting native definition blocks all metrics of the economic observation.
    bad_definitions = result[result.metric.eq("reported_revenue_name") & result.status.eq("blocked")]
    if not bad_definitions.empty:
        keys = ["database"] + KEY
        conflicts = pd.MultiIndex.from_frame(bad_definitions[keys])
        mask = pd.MultiIndex.from_frame(result[keys]).isin(conflicts)
        result.loc[mask, "status"] = "blocked"
        result.loc[mask, "reason"] += "; unresolved native definition"
    return result


def preview(root: Path, databases: dict[str, Path]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return report inventory, metric comparison, and unapproved parsed observations."""
    inventory, comparisons, candidates, cache = [], [], [], {}
    for label, path in databases.items():
        connection = connect_readonly(path)
        try:
            stored = pd.read_sql_query("SELECT * FROM gaming_results", connection)
        finally:
            connection.close()
        stored = stored[[pair in SCOPE for pair in zip(stored.state_code, stored.vertical)]]
        reports = stored.groupby(["state_code", "vertical", "source_sha256"], dropna=False)
        for number, ((state, product, digest), old) in enumerate(reports, 1):
            print(f"{label}: {number}/{len(reports)} {state} {product} {digest[:12]}", flush=True)
            reason, candidate = "", pd.DataFrame(columns=RESULT_COLUMNS)
            verified = False
            try:
                content = verify_sources(root, old)
                verified = True
                if old.source_url.nunique(dropna=False) != 1:
                    raise ValueError("ambiguous source URL binding")
                cache_key = (state, product, digest, tuple(sorted(set(zip(old.operator, old.row_type)))) if state == "NY" else None,
                             tuple(pd.to_datetime(old.period_start).dt.year.unique()) if state == "MI" else None)
                if cache_key not in cache:
                    try:
                        cache[cache_key] = parse_retained(content, old)
                    except Exception as exc:
                        cache[cache_key] = f"{type(exc).__name__}: {exc}"
                parsed = cache[cache_key]
                if isinstance(parsed, str):
                    raise ValueError(parsed)
                candidate = parsed.copy()
                # Metadata references are those of this database, not another cached capture.
                for col in ("source_file", "source_url", "retrieved_at_utc"):
                    candidate[col] = old.iloc[0][col]
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
            comparison = compare_observations(old, candidate, error=reason)
            comparison = comparison.assign(database=label, source_file=" | ".join(sorted(old.source_file.unique())),
                source_sha256=digest, source_url=" | ".join(sorted(old.source_url.unique())))
            comparisons.append(comparison)
            if not candidate.empty:
                candidates.append(candidate.assign(database=label, approval="exploratory_unapproved"))
            inventory.append(dict(database=label, state_code=state, vertical=product, source_sha256=digest,
                source_file=" | ".join(sorted(old.source_file.unique())), source_url=" | ".join(sorted(old.source_url.unique())),
                first_period=old.period_start.min(), last_period=old.period_end.max(), stored_rows=len(old),
                candidate_rows=len(candidate), hash_verified=verified, status="blocked" if reason else "parsed", reason=reason))
    if not comparisons:
        raise ValueError("No stored observations in the historical preview scope")
    comparison = flag_conflicts(pd.concat(comparisons, ignore_index=True))
    candidate_rows = pd.concat(candidates, ignore_index=True) if candidates else pd.DataFrame(columns=RESULT_COLUMNS)
    if not candidate_rows.empty:
        join = ["database"] + KEY + ["source_sha256"]
        blocked = comparison[comparison.status.eq("blocked")][join].drop_duplicates().assign(conflict_or_block=True)
        candidate_rows = candidate_rows.merge(blocked, on=join, how="left", validate="one_to_one")
        candidate_rows["review_status"] = candidate_rows.conflict_or_block.map({True: "blocked"}).fillna("needs_analyst_review")
        candidate_rows = candidate_rows.drop(columns="conflict_or_block")
    return pd.DataFrame(inventory), comparison, candidate_rows


@contextlib.contextmanager
def no_network():
    def deny(*args, **kwargs):
        raise RuntimeError("Historical preview permits retained local reports only")
    with patch("requests.sessions.Session.request", deny), patch.object(socket.socket, "connect", deny), patch("socket.create_connection", deny):
        yield


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True, help="New directory for unapproved CSV review artifacts")
    args = parser.parse_args()
    root, output = args.root.resolve(), args.output.resolve()
    if output.exists():
        raise FileExistsError(f"Preview output must be new: {output}")
    databases = {"original": root / "data/gaming.sqlite", "staging": root / "data/staging/gaming_nationwide.sqlite"}
    before = {label: hashlib.sha256(path.read_bytes()).hexdigest() for label, path in databases.items()}
    with tempfile.TemporaryDirectory(prefix="researchos-historical-preview-") as directory, no_network():
        inventory, comparison, candidates = preview(root, databases)
        # Temporary artifacts first. No candidate or existing database is written.
        frames = {"source_inventory": inventory, "comparison": comparison,
                  "differences": comparison[comparison.status.ne("unchanged")], "candidate_observations": candidates}
        for name, frame in frames.items():
            frame.to_csv(Path(directory) / f"{name}.csv", index=False)
        after = {label: hashlib.sha256(path.read_bytes()).hexdigest() for label, path in databases.items()}
        if before != after:
            raise RuntimeError("Protected database changed during preview; results not published")
        output.mkdir(parents=True, exist_ok=False)
        for path in Path(directory).iterdir():
            shutil.copy2(path, output / path.name)
        files = [Path(__file__), root / "src/variant_gaming/common.py", root / "src/variant_gaming/storage.py"] + [Path(module.__file__) for module in
            (delaware, pennsylvania, new_jersey, new_hampshire, new_york, michigan)]
        code_hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        (output / "manifest.json").write_text(json.dumps(dict(databases=before, code_sha256=code_hashes, approval="exploratory_unapproved",
            candidate_database=None, reports=len(inventory), parsed_rows=len(candidates), metric_rows=len(comparison)), indent=2), encoding="utf-8")
    print(comparison.groupby(["database", "state_code", "status"]).size().to_string())
    print(f"Unapproved review artifacts: {output}")


if __name__ == "__main__":
    main()
