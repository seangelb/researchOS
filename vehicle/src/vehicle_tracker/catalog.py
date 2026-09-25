"""Bounded full-catalog discovery; ordinary search evidence, no sales inference.

Discover all current makes, then non-overlapping model families where the native
counts support them. Never constrain the population to a fixed year range. Small
make probes are already complete queries and are reused without another request.

The work is split across catalog_config, catalog_roots, catalog_plan,
catalog_reconcile, catalog_attempt and catalog_export. This module is the
import surface scripts and tests use, including the names tests replace.
"""
from vehicle_tracker.cycles import cycle_lock
from vehicle_tracker.search import ENDPOINT, collect_search

from vehicle_tracker.catalog_config import (
    _strategy, _unique_roots, digest, preview, query, settings, utcnow)
from vehicle_tracker.catalog_roots import (
    REVIEW_FATAL_OUTCOMES, _active_cooldown, _attempt_root_state, _capture_root_state,
    _capture_roots, _lock_held, _require_clear_roots, _reviewed_failures)
from vehicle_tracker.catalog_plan import (
    _candidate_queries, _discovery_probes, _feasibility, _model_id_overlap,
    _planned_coverage, _validate_discovery, _validate_year_children, model_partitions,
    native_makes)
from vehicle_tracker.catalog_reconcile import (
    MAKE_RECONCILIATION_COLUMNS, YEAR_RECONCILIATION_COLUMNS, _best_leaf_entry,
    _duplicate_membership_summary, _facets, _leaf_attempts, _leaf_complete,
    _leaf_complete_any, _make_reconciliation, _overlap_leaf_sets, _rows,
    _year_diagnostics, _year_reconciliation)
from vehicle_tracker.catalog_attempt import (
    FATAL_OUTCOMES, ISOLATABLE_OUTCOMES, ISOLATABLE_ROLES, _attempt_kind,
    _failure_http_status, _finalize_attempt, collect_catalog)
from vehicle_tracker.catalog_export import export_catalog

__all__ = [name for name in globals() if not name.startswith('__')]
