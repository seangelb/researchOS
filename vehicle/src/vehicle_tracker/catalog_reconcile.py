"""Full-inventory catalog helpers. See catalog.py for the public entry points."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from vehicle_tracker.cycles import CycleBudget, aware, cycle_config, cycle_lock
from vehicle_tracker.carvana import NATIVE_FIELDS
from vehicle_tracker.history import OBSERVATION_COLUMNS, import_reports, read_query_evidence
from vehicle_tracker.search import ENDPOINT, collect_search, search_transport
from vehicle_tracker.search_context import empty_context_status, validate_first_page
from vehicle_tracker.search_plan import verify_isolated_leaf_failure, verify_isolated_pagination
from vehicle_tracker.storage import read_snapshots, write_json_atomic
from vehicle_tracker.catalog_partitions import plan_year_partitions, year_make_candidates
from vehicle_tracker.catalog_config import digest
from vehicle_tracker.search import build_search_request, overlap_siblings

MAKE_RECONCILIATION_COLUMNS = ('''make discovery_query_id discovery_observed_at_utc
    discovery_native_count native_model_count_sum native_model_count_residual
    declared_leaf_queries attempted_leaf_queries completed_leaf_queries
    unverified_context_leaf_queries all_leaf_queries_complete
    leaf_reported_total_sum observed_rows observed_unique_vins duplicate_memberships
    discovery_minus_leaf_native_count discovery_minus_observed_vins leaf_native_minus_observed_vins
    inventory_observation_start inventory_observation_end observed_count_scope''').split()


YEAR_RECONCILIATION_COLUMNS = ('''query_id kind year_min year_max status context_validated
    context_status native_make_categories_available
    observed_at_utc reported_total positive_make_candidates native_zero_categories
    declared_make_queries validated_make_queries declared_leaf_queries completed_leaf_queries
    observed_rows observed_unique_vins native_minus_observed_vins''').split()


def _leaf_complete(entry):
    """A leaf counts only when its pages finished and its context was confirmed.

    An empty page whose requested make/model application could not be confirmed
    contributes no rows and is not evidence of absence, so it stays incomplete.
    """
    return bool(entry.get('query_complete') and entry.get('context_validated', True))


def _leaf_attempts(report, query_id):
    return [entry for entry in report.get('entries', [])
            if entry.get('query', {}).get('query_id') == query_id or entry.get('retry_of') == query_id]


def _leaf_complete_any(report, query_id):
    return any(_leaf_complete(entry) for entry in _leaf_attempts(report, query_id))


def _best_leaf_entry(report, query_id):
    attempts = _leaf_attempts(report, query_id)
    complete = [entry for entry in attempts if _leaf_complete(entry)]
    return (complete[-1] if complete else attempts[-1]) if attempts else {}


def _overlap_leaf_sets(report):
    """Map each overlap-cluster leaf to the full set of leaves in its cluster."""
    membership = {}
    for entry in report['entries']:
        overlap = entry.get('model_id_overlap')
        if entry.get('role') != 'make_discovery' or not overlap:
            continue
        make = entry['query']['filters']['makes'][0]['name']
        year = entry['query']['filters'].get('year')
        names = {}
        for leaf in report['leaf_queries']:
            requested = leaf['filters']['makes'][0]
            models = requested.get('parentModels') or []
            if (requested['name'] == make and leaf['filters'].get('year') == year
                    and len(models) == 1):
                names[models[0]['name']] = leaf['query_id']
        for cluster in overlap['clusters']:
            leaves = frozenset(names[name] for name in cluster['models'] if name in names)
            for query_id in leaves:
                membership[query_id] = leaves
    return membership


def _duplicate_membership_summary(report, primary):
    """Count raw and unexplained retailer/VIN duplicates across primary leaves.

    A duplicate is explained only when every query containing that VIN belongs to
    one declared overlap cluster. Identity conflicts remain a separate fatal stop.
    """
    frames = [rows.assign(query_id=query_id) for query_id, rows in primary.items()
              if not rows.empty]
    if not frames:
        return dict(duplicate_primary_memberships=0, unexplained_duplicate_primary_memberships=0,
                    explained_duplicate_primary_memberships=0)
    union = pd.concat(frames, ignore_index=True)
    raw = int(union.duplicated(['retailer', 'vin']).sum())
    if raw == 0:
        return dict(duplicate_primary_memberships=0, unexplained_duplicate_primary_memberships=0,
                    explained_duplicate_primary_memberships=0)
    clusters = _overlap_leaf_sets(report)
    unexplained = 0
    for _, group in union[union.duplicated(['retailer', 'vin'], keep=False)].groupby(
            ['retailer', 'vin'], sort=False):
        query_ids = frozenset(group.query_id)
        cluster = next((clusters[query_id] for query_id in query_ids if query_id in clusters), None)
        if cluster is None or not query_ids <= cluster:
            unexplained += max(0, len(group) - 1)
    return dict(duplicate_primary_memberships=raw,
                unexplained_duplicate_primary_memberships=unexplained,
                explained_duplicate_primary_memberships=raw - unexplained)


def _facets(report):
    page = report['pages'][0]
    if digest(page['facet_source']) != page['facet_sha256']:
        raise ValueError('Retained discovery facet changed')
    capture = json.loads(Path(page['facet_source']).read_text(encoding='utf-8'))
    from vehicle_tracker.search import build_search_request
    expected = build_search_request(filters=report['filters'], zip_code=report['zip_code'])
    if (capture['request'] != expected or capture['zip_code'] != report['zip_code']
            or capture['captured_at_utc'] != page['response_received_at_utc']
            or capture['pagination']['totalMatchedInventory'] != report['reported_total']):
        raise ValueError('Facet request, clock or count differs from inventory response')
    return capture


def _rows(folder):
    path = Path(folder)/'vehicle.sqlite'
    return read_snapshots(path)[1] if path.is_file() else pd.DataFrame()


def _make_reconciliation(report, primary):
    """Expose category drift and unknown coverage without cancelling residuals."""
    entries = {entry['query']['query_id']: entry for entry in report['entries']}
    records = []
    for probe in report.get('planned_make_probes', []):
        make = probe['filters']['makes'][0]['name']
        discovery = entries.get(probe['query_id'], {})
        leaves = [q for q in report['leaf_queries'] if q['filters']['makes'][0]['name'] == make
                  and q['filters'].get('year') == probe['filters'].get('year')]
        leaf_entries = [entries.get(q['query_id'], {}) for q in leaves]
        frames = [primary[q['query_id']] for q in leaves
                  if q['query_id'] in primary and not primary[q['query_id']].empty]
        rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        native = discovery.get('reported_total')
        model_total = discovery.get('native_model_count_sum')
        best_leaves = [_best_leaf_entry(report, q['query_id']) for q in leaves]
        leaf_total = (sum(e['reported_total'] for e in best_leaves)
                      if leaves and all(e.get('reported_total') is not None for e in best_leaves) else None)
        distinct = int(rows.vin.nunique()) if not rows.empty else 0
        clocks = pd.to_datetime(rows.observed_at_utc, utc=True, format='ISO8601') if not rows.empty else None
        record = dict(make=make, discovery_query_id=probe['query_id'],
            discovery_observed_at_utc=discovery.get('discovery_observed_at_utc'),
            discovery_native_count=native, native_model_count_sum=model_total,
            native_model_count_residual=native-model_total if native is not None and model_total is not None else None,
            declared_leaf_queries=len(leaves),
            attempted_leaf_queries=sum(bool(e.get('report')) for e in leaf_entries),
            completed_leaf_queries=sum(_leaf_complete_any(report, q['query_id']) for q in leaves),
            unverified_context_leaf_queries=sum(bool(e.get('report'))
                                                and not e.get('context_validated', True) for e in leaf_entries),
            all_leaf_queries_complete=bool(leaves and all(_leaf_complete_any(report, q['query_id']) for q in leaves)),
            leaf_reported_total_sum=leaf_total, observed_rows=len(rows), observed_unique_vins=distinct,
            duplicate_memberships=int(rows.duplicated(['retailer','vin']).sum()) if not rows.empty else 0,
            discovery_minus_leaf_native_count=native-leaf_total if native is not None and leaf_total is not None else None,
            discovery_minus_observed_vins=native-distinct if native is not None else None,
            leaf_native_minus_observed_vins=leaf_total-distinct if leaf_total is not None else None,
            inventory_observation_start=clocks.min().isoformat() if clocks is not None else None,
            inventory_observation_end=clocks.max().isoformat() if clocks is not None else None,
            observed_count_scope=('Retained positive observations only; incomplete/unattempted leaves '
                                  'give lower bounds, not zero inventory or evidence of absence.'))
        if report.get('partition_strategy') == 'year_then_make_model':
            record.update(year_min=probe['filters']['year'].get('min'),
                          year_max=probe['filters']['year'].get('max'))
        records.append(record)
    return records


def _year_reconciliation(report, primary):
    """Count evidence per frozen year context, including both unbounded tails."""
    entries = {entry['query']['query_id']: entry for entry in report['entries']}
    discoveries = {item['partition']['query_id']: item for item in report['year_discoveries']}
    records = []
    for part in (report.get('year_plan') or {}).get('partitions', []):
        bounds, identity = part['filters']['year'], part['query_id']
        entry, discovery = entries.get(identity, {}), discoveries.get(identity)
        probes = [q for q in report.get('planned_make_probes', []) if q['filters']['year'] == bounds]
        leaves = [q for q in report['leaf_queries'] if q['filters'].get('year') == bounds]
        frames = [primary[q['query_id']] for q in leaves
                  if q['query_id'] in primary and not primary[q['query_id']].empty]
        rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        total = discovery['reported_total'] if discovery is not None else None
        unique = int(rows.vin.nunique()) if not rows.empty else 0
        records.append(dict(query_id=identity, kind=part['kind'], year_min=bounds.get('min'),
            year_max=bounds.get('max'), status=entry.get('status', 'unattempted'),
            context_validated=discovery is not None,
            context_status=entry.get('context_status', 'unattempted'),
            native_make_categories_available=(discovery['native_make_categories_available']
                                              if discovery else None),
            observed_at_utc=discovery['source']['observed_at_utc'] if discovery else None,
            reported_total=total, positive_make_candidates=len(discovery['candidates']) if discovery else None,
            native_zero_categories=len(discovery['native_zero_categories']) if discovery else None,
            declared_make_queries=len(probes),
            validated_make_queries=sum('partition_reason' in entries.get(q['query_id'], {}) for q in probes),
            declared_leaf_queries=len(leaves),
            completed_leaf_queries=sum(_leaf_complete_any(report, q['query_id']) for q in leaves),
            observed_rows=len(rows), observed_unique_vins=unique,
            native_minus_observed_vins=total-unique if total is not None else None))
    return records


def _year_diagnostics(report, primary):
    records = _year_reconciliation(report, primary)
    complete = bool(records and all(row['context_validated'] for row in records))
    known = sum(row['reported_total'] for row in records if row['reported_total'] is not None)
    total = known if complete else None
    return dict(year_reconciliation=records, year_contexts_validated=complete,
        year_native_count_partial_sum=known, year_native_count_sum=total,
        opening_minus_year_native_count=report['opening_total']-total if total is not None else None,
        year_tail_counts={kind: next((row['reported_total'] for row in records if row['kind'] == kind), None)
                          for kind in ['lower_tail', 'upper_tail']},
        missing_or_noninteger_year_count=None,
        year_count_note='Counts have different observation clocks. Residuals may reflect drift or unallocated years; zero does not establish national or missing-year coverage.')


def _coverage_union(report):
    """VIN union across every pass of a leaf, compared with the latest native total.

    complete_by_union is separate from query_complete. A reshuffled page can fail
    one pass and still be covered once a later pass sees the missing VINs.
    An unvalidated context is never complete, even when both sides are zero.
    """
    rows_out = []
    unverified = 0
    for leaf in report.get('leaf_queries') or []:
        vins = set()
        latest = None
        validated = False
        for entry in _leaf_attempts(report, leaf['query_id']):
            if entry.get('reported_total') is not None:
                latest = entry['reported_total']
            if entry.get('context_validated'):
                validated = True
            if entry.get('report'):
                observed = _rows(Path(entry['report']).parent)
                if not observed.empty and 'vin' in observed.columns:
                    vins.update(vin for vin in observed.vin if pd.notna(vin))
        complete = bool(validated and type(latest) is int and len(vins) == latest)
        rows_out.append(dict(query_id=leaf['query_id'], union_vins=len(vins), latest_total=latest,
                             complete_by_union=complete, context_validated=validated))
        if not complete and not _leaf_complete_any(report, leaf['query_id']):
            unverified += latest if type(latest) is int else 0
    return dict(leaf_union=rows_out, unverified_native_count=unverified)


