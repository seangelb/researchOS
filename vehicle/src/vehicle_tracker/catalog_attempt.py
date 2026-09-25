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
from vehicle_tracker.catalog_config import _strategy, digest, preview, query, settings, utcnow
from vehicle_tracker.catalog_roots import _attempt_root_state, _capture_roots, _require_clear_roots, _reviewed_failures
from vehicle_tracker.catalog_plan import (
    _candidate_queries, _discovery_probes, _feasibility, _validate_year_children, adaptive_cell,
    adaptive_cells_from_capture, adaptive_estimate, adaptive_feasibility, native_makes)
from vehicle_tracker.catalog_reconcile import (
    _best_leaf_entry, _coverage_union, _duplicate_membership_summary, _facets, _leaf_attempts,
    _leaf_complete_any, _make_reconciliation, _rows, _year_diagnostics)
from vehicle_tracker.catalog_schedule import cooldown_minutes

FATAL_OUTCOMES = frozenset({'access_failure', 'storage_failure'})


ISOLATABLE_OUTCOMES = frozenset({'pagination_unstable', 'schema_failure',
                                 'identity_failure', 'transport_failure', 'server_failure'})


ISOLATABLE_ROLES = frozenset({'primary_inventory', 'primary_inventory_retry', 'geographic_inventory'})


def _attempt_kind(report):
    from vehicle_tracker.catalog_schedule import classify_terminal
    terminal = classify_terminal(report)
    if terminal:
        return terminal
    if report.get('format') == 'carvana-full-inventory-run-v3':
        if report.get('feasibility_blocked'):
            return 'infeasible'
        if report.get('declared_collection_complete'):
            return 'complete'
        if report.get('coverage_acceptable') and report.get('status') == 'collection_finished':
            return 'complete_with_gaps'
        if report.get('status') == 'collection_finished':
            return 'incomplete'
        return 'client_failure'
    if report.get('declared_collection_complete'):
        return 'complete'
    if report.get('status') == 'collection_finished':
        return 'finished_incomplete'
    return 'client_failure'


def _failure_http_status(report):
    for entry in report.get('entries') or []:
        if entry.get('outcome_kind') not in {'access_failure', 'server_failure'} or not entry.get('report'):
            continue
        try:
            child = json.loads(Path(entry['report']).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        pages = child.get('pages') or []
        if pages and pages[-1].get('http_status') is not None:
            return pages[-1]['http_status']
    return None


def _finalize_attempt(report, folder, root, config):
    """Record how the attempt ended. Access failures also publish a cooldown marker.

    An existing access_stop.json is left untouched. Later attempts read cooldown
    from this attempt file, so a historical marker cannot be rewritten.
    """
    from vehicle_tracker.catalog_schedule import cooldown_minutes
    kind = _attempt_kind(report)
    index = report.get('spacing_index')
    if type(index) is not int or index < 0:
        index = 0
    ended = report.get('ended_at')
    cooldown_until = None
    if kind in {'access_stop', 'degraded', 'client_failure', 'incomplete'} and ended:
        cooldown_kind = 'degraded' if kind == 'incomplete' else kind
        cooldown_until = (aware(ended) + timedelta(
            minutes=cooldown_minutes(config['retry_policy'], cooldown_kind, index))).isoformat()
    outcome = dict(kind=kind, ended_at=ended, http_status=_failure_http_status(report),
                   spacing_seconds=report.get('spacing_seconds'), spacing_index=index,
                   requests=report.get('requests'), attempt=report.get('attempt'),
                   cooldown_until=cooldown_until)
    write_json_atomic(folder/'attempt_outcome.json', outcome)
    if kind == 'access_stop' and not (root/'access_stop.json').exists():
        write_json_atomic(root/'access_stop.json', dict(
            run=str(folder/'catalog_report.json'), recorded_at=ended,
            reason='Access failure; cooldown before another attempt',
            http_status=outcome['http_status'], cooldown_until=cooldown_until))
    report['attempt_outcome'] = outcome['kind']


def collect_catalog(config_path, *, expected_sha256, post=None, attempt=1, spacing_seconds=None, force=False):
    """One fresh attempt folder and one shared durable budget.

    Exhausted page-level leaf failures are isolated so later leaves can run.
    Repeated isolated leaves end the attempt as degraded. HTTP 401/403, 429 and
    Cloudflare challenges end the attempt and record a cooldown. A folder is
    never replaced or resumed.
    """
    from vehicle_tracker import catalog as _api
    utcnow = _api.utcnow
    preview = _api.preview
    cycle_lock = _api.cycle_lock
    collect_search = _api.collect_search
    model_partitions = _api.model_partitions
    _make_reconciliation = _api._make_reconciliation
    start = preview(config_path, attempt=attempt, spacing_seconds=spacing_seconds)
    if expected_sha256 != start['config_sha256']:
        raise ValueError('Full-inventory config changed after preview')
    if not start['destination_fresh']:
        raise ValueError('Existing date or unresolved access stop; no replacement attempt')
    if not start['ceiling_pacing_fits_window'] and not force:
        raise ValueError('Remaining local-date window is shorter than three-second pacing of the request ceiling'
                         if start['strict_ceiling'] else
                         'Remaining local-date window cannot pace this attempt')
    _require_clear_roots(start['capture_root_states'])
    config, folder = start['config'], Path(start['destination'])
    root = folder.parent
    roots = _capture_roots(config)
    with ExitStack() as locks:
        for peer in roots:
            # Do not create capture directories in other worktrees. Lock only
            # roots that already exist, plus this attempt's own root.
            if peer != root and not peer.exists():
                continue
            peer.mkdir(parents=True, exist_ok=True)
            locks.enter_context(cycle_lock(peer))
        # Recheck under every root's existing OS lock, including old v1 collectors.
        if folder.exists():
            raise ValueError('Existing date; no replacement attempt')
        now = utcnow()
        zone = ZoneInfo(config['timezone'])
        local = now.astimezone(zone)
        midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), zone)
        end = min(now + timedelta(seconds=config['max_seconds']), midnight - timedelta(microseconds=1))
        from vehicle_tracker.catalog_config import _window_fits
        if (not _window_fits(config, start['spacing_seconds'], (end - now).total_seconds(),
                             strict=start['strict_ceiling']) and not force):
            raise ValueError('Remaining local-date window is shorter than three-second pacing of the request ceiling'
                             if start['strict_ceiling'] else
                             'Remaining local-date window cannot pace this attempt')
        reviewed = _reviewed_failures(config)
        _require_clear_roots([_attempt_root_state(peer, reviewed.get(peer, ()), check_lock=False)
                              for peer in roots])
        folder.mkdir()
        selected = Path(config_path).read_bytes()
        if hashlib.sha256(selected).hexdigest() != expected_sha256:
            raise ValueError('Config changed before collection')
        (folder/'selected_config.json').write_bytes(selected)
        broad = query('broad_open', config['primary_zip'], {})
        budget_path = folder/'catalog_budget.json'
        state = cycle_config([broad], cycle_date=start['cycle_date'], timezone_name=config['timezone'],
            window_start=start['window_start'], window_end=start['window_end'],
            max_requests=config['max_requests'], max_seconds=config['max_seconds'])
        write_json_atomic(budget_path, dict(state, cycle_id=uuid4().hex, created_at=utcnow().isoformat(),
            budget=dict(requests=0, stopped=False, pending_request=False, last_request_utc=None),
            pause_seconds=start['spacing_seconds']))
        strategy = _strategy(config)
        formats = {'year_then_make_model': 'carvana-full-inventory-run-v2',
                   'year_make_adaptive': 'carvana-full-inventory-run-v3'}
        report = dict(format=formats.get(strategy, 'carvana-full-inventory-run-v1'), started_at=utcnow().isoformat(),
            partition_strategy=strategy,
            capture_directory=str(folder),
            config_sha256=expected_sha256, cycle_date=start['cycle_date'], status='running',
            planned_discovery_probes=_discovery_probes(config),
            entries=[], leaf_queries=[], makes=[], discovery_complete=False,
            planned_geographic_checks=[], geographic_checks=[], leaf_retry_attempts=[],
            primary_queries_complete=False, declared_collection_complete=False, national_coverage_verified=False,
            estimated_sales=None, requests=0,
            window_start=start['window_start'], window_end=start['window_end'],
            attempt=start['attempt'], spacing_seconds=start['spacing_seconds'],
            spacing_index=start['spacing_index'], consecutive_isolated_leaves=0,
            code_hashes={str(p): digest(p) for p in sorted(Path(__file__).parent.glob('*.py'))})
        report_path = folder/'catalog_report.json'
        if strategy in ('year_then_make_model', 'year_make_adaptive'):
            report.update(year_plan=None, planned_year_probes=[], planned_make_probes=[],
                          year_discoveries=[], native_zero_categories=[], discovery_residuals=[])
        budget = CycleBudget(budget_path, pause_seconds=start['spacing_seconds'])
        known, primary, reused, leaf_counts = {}, {}, set(), {}
        events_path = folder/'catalog_events.jsonl'
        last_report_write = {'at': 0.0}
        progress_mark = {'requests': -1}

        def save(force=False):
            report['requests'] = budget.requests
            report['updated_at'] = utcnow().isoformat()
            now_m = time.monotonic()
            if force or now_m - last_report_write['at'] >= 30:
                write_json_atomic(report_path, report)
                last_report_write['at'] = now_m

        def event(kind, **fields):
            record = dict(fields, kind=kind, at=utcnow().isoformat(), requests=budget.requests)
            with events_path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, sort_keys=True) + '\n')

        def progress(phase=None):
            if phase:
                report['phase'] = phase
            count = budget.requests
            if phase or (count and count % 50 == 0 and count != progress_mark['requests']):
                progress_mark['requests'] = count
                elapsed = time.monotonic() - budget.started
                print('PROGRESS phase={0} requests={1} elapsed_s={2:.0f} vins={3}'.format(
                    report.get('phase'), count, elapsed, report.get('primary_observed_vins')), flush=True)

        def run(q, role, *, probe=False, retry_of=None, validate_context=False):
            entry = dict(query=q, role=role, status='unattempted')
            if retry_of:
                entry['retry_of'] = retry_of
            report['entries'].append(entry)
            event('query_intent', query_id=q['query_id'], role=role)
            save(True)  # Intent is in the event log; the summary report is written at phase boundaries.
            if budget.stopped or budget.requests >= budget.max_requests:
                return None, pd.DataFrame()
            budget.timeout_ms()
            before = budget.requests
            child = folder/q['query_id']
            # A discovery probe must prove its whole native context before it can
            # plan later queries. An inventory leaf validates only an empty page,
            # because its rows are already checked against the requested filters.
            check = validate_first_page if (probe or validate_context) else empty_context_status
            probe_retries = config.get('page_retries', 2) if strategy == 'year_make_adaptive' else 0
            result = collect_search(filters=q['filters'], zip_code=q['zip_code'], destination=child,
                target_listings=1 if probe else None, budget=budget, post=send,
                known_listing_vins=known, retain_facets=True, allow_empty_missing_makes=True,
                first_page_validator=lambda capture, facets: check(capture, facets, q),
                page_retries=probe_retries if probe else config.get('page_retries', 2),
                retry_backoff_seconds=config['retry_policy']['retry_backoff_seconds'])
            entry.update(status=result['status'], report=str(child/'run_report.json'),
                report_sha256=digest(child/'run_report.json'), query_complete=result['query_complete'],
                reported_total=result['reported_total'], requests=result['requests'],
                outcome_kind=result.get('outcome_kind'),
                context_validated=result.get('first_page_context_validated', False),
                context_status=result.get('first_page_context_status', 'unverified'))
            isolatable_roles = ISOLATABLE_ROLES
            if strategy == 'year_make_adaptive':
                isolatable_roles = isolatable_roles | {'year_discovery', 'make_discovery'}
            if ((result.get('outcome_kind') in FATAL_OUTCOMES
                 or (result.get('outcome_kind') in ISOLATABLE_OUTCOMES and role not in isolatable_roles))
                    and 'failure_reason' not in report):
                # Keep the child's own safe diagnosis; a later generic message
                # must not be the only explanation left in the retained report.
                report['failure_type'] = 'CollectionStopped'
                report['failure_reason'] = result.get('reason') or result['outcome_kind']
            isolatable = (result.get('outcome_kind') in ISOLATABLE_OUTCOMES and role in isolatable_roles)
            if isolatable:
                verify_isolated_leaf_failure(child/'run_report.json', known_listing_vins=known,
                    requests=budget.requests-before, outcome=result.get('outcome_kind'))
                budget.isolate_query_failure(requests=budget.requests, report=child/'run_report.json',
                    report_sha256=entry['report_sha256'])
                entry['failure_scope'] = ('query; reconciled pagination failure'
                                          if result.get('outcome_kind') == 'pagination_unstable'
                                          else 'query; isolated retryable failure')
            inventory_role = role in ('primary_inventory', 'primary_inventory_retry', 'geographic_inventory')
            counted_role = inventory_role or (strategy == 'year_make_adaptive'
                                              and role in ('year_discovery', 'make_discovery'))
            if isolatable and counted_role:
                report['consecutive_isolated_leaves'] = report.get('consecutive_isolated_leaves', 0) + 1
                if report['consecutive_isolated_leaves'] >= config['retry_policy']['consecutive_failure_breaker']:
                    report['failure_type'] = 'degraded'
                    report['failure_reason'] = 'consecutive isolated leaf failures'
                    budget.stop()
            elif counted_role and result and (result.get('query_complete') or result.get('outcome_kind') == 'sample_limit'):
                report['consecutive_isolated_leaves'] = 0
            rows = _rows(child)
            if not rows.empty:
                known.update(zip(rows.listing_id, rows.vin))
            dest = retry_of or q['query_id']
            if role in ('primary_inventory', 'primary_inventory_retry'):
                if strategy == 'year_make_adaptive':
                    previous = primary.get(dest)
                    frames = [frame for frame in (previous, rows) if frame is not None and not frame.empty]
                    if frames:
                        combined = pd.concat(frames, ignore_index=True)
                        primary[dest] = combined.drop_duplicates(['retailer', 'vin'])
                    elif dest not in primary:
                        primary[dest] = rows
                elif result.get('query_complete') or dest not in primary:
                    primary[dest] = rows
            event('query_result', query_id=q['query_id'], role=role, status=entry.get('status'),
                  outcome=entry.get('outcome_kind'), query_complete=bool(entry.get('query_complete')))
            save()
            progress()
            if not budget.stopped:
                budget.timeout_ms()  # Even the final response must arrive inside the window.
            return result, rows

        def second_pass():
            """Re-attempt incomplete primary leaves without overwriting first evidence."""
            for leaf in list(report['leaf_queries']):
                if budget.stopped or budget.requests >= budget.max_requests:
                    return
                if leaf['query_id'] in reused and _leaf_complete_any(report, leaf['query_id']):
                    continue
                if _leaf_complete_any(report, leaf['query_id']):
                    continue
                if not any(entry.get('report') for entry in _leaf_attempts(report, leaf['query_id'])):
                    continue
                retry = dict(leaf, query_id=leaf['query_id'] + '_pass2')
                report['leaf_retry_attempts'].append(retry)
                save()
                run(retry, 'primary_inventory_retry', retry_of=leaf['query_id'])

        def collect_make(q, *, enumerate_children=True):
            make = q['filters']['makes'][0]['name']
            probe, rows = run(q, 'make_discovery', probe=True)
            if not probe or budget.stopped:
                return False
            facets = _facets(probe)
            children, reason, overlap = model_partitions(facets, make, q['zip_code'], q['query_id'],
                                                year_bounds=q['filters'].get('year'))
            models = facets['facet_data']['makes'].get(make, {}).get('parentModels', [])
            report['entries'][-1].update(discovery_observed_at_utc=facets['captured_at_utc'],
                native_model_count_sum=sum(child['count'] for child in models) if models else None)
            if probe['query_complete']:
                children, reason, overlap = [q], 'complete make probe reused', None
                primary[q['query_id']] = rows
                reused.add(q['query_id'])
            if strategy == 'year_then_make_model':
                _validate_year_children(children, q)
            report['leaf_queries'].extend(children)
            report['entries'][-1]['partition_reason'] = reason
            if overlap is not None:
                report['entries'][-1]['model_id_overlap'] = overlap
            counts = {child['key']: child['count'] for child in models}
            for child in children:
                requested = child['filters']['makes'][0].get('parentModels')
                leaf_counts[child['query_id']] = (counts.get(requested[0]['name']) if requested
                                                  else probe['reported_total'])
            save()
            if not enumerate_children:
                return True
            for child in children:
                if child['query_id'] not in reused:
                    run(child, 'primary_inventory')
                if budget.stopped:
                    break
            return True

        def enumerate_leaves():
            """Collect the frozen leaf plan; a reused complete probe is not resent."""
            for leaf in report['leaf_queries']:
                if leaf['query_id'] in reused:
                    continue
                run(leaf, 'primary_inventory')
                if budget.stopped:
                    return

        def observed_make_names(capture, scope):
            if strategy != 'year_make_adaptive':
                return native_makes(capture)
            names, residual = native_makes(capture, allow_residual=True)
            if residual:
                report['discovery_residuals'].append(dict(scope=scope, residual=residual,
                    observed_at=capture.get('captured_at_utc')))
            return names

        def enumerate_adaptive():
            """Whole cells up to the threshold; larger cells are probed, then split."""
            threshold = config['split_threshold_vehicles']
            report['adaptive_cells'] = []
            for part, q in zip(report['year_plan']['partitions'], report['planned_year_probes']):
                if budget.stopped:
                    return
                result, _ = run(q, 'year_discovery', probe=True)
                if not result or budget.stopped:
                    break
                if result.get('outcome_kind') in ISOLATABLE_OUTCOMES:
                    continue
                try:
                    _facets(result)
                    page = result['pages'][0]
                    discovery = year_make_candidates(page['facet_source'],
                        expected_sha256=page['facet_sha256'], partition=part, allow_count_residual=True)
                except ValueError as error:
                    report['entries'][-1]['isolation_reason'] = str(error)
                    report['consecutive_isolated_leaves'] = report.get('consecutive_isolated_leaves', 0) + 1
                    continue
                if discovery.get('count_residual'):
                    report['discovery_residuals'].append(dict(scope=q['query_id'],
                        residual=discovery['count_residual']))
                report['year_discoveries'].append(discovery)
                report['entries'][-1]['year_context_validated'] = True
                candidates = _candidate_queries(discovery)
                _validate_year_children(candidates, q)
                report['planned_make_probes'].extend(candidates)
                report['native_zero_categories'].extend(dict(row, year_query_id=q['query_id'])
                                                       for row in discovery['native_zero_categories'])
                capture = json.loads(Path(page['facet_source']).read_text(encoding='utf-8'))
                for cell in adaptive_cells_from_capture(capture, part, threshold=threshold):
                    report['adaptive_cells'].append(dict(query_id=cell['query_id'], kind=cell['kind'],
                        native_count=cell['native_count'], make=cell['make']))
            all_years = len(report['year_discoveries']) == len(report['planned_year_probes'])
            estimate = sum(page_count_or_probe(cell) for cell in report['adaptive_cells'])
            report['leaf_plan_frozen'] = all_years and not budget.stopped
            report['feasibility'] = adaptive_feasibility(config, enumeration_requests=estimate,
                requests_used=budget.requests, max_requests=budget.max_requests,
                seconds_remaining=budget.max_seconds-(time.monotonic()-budget.started),
                spacing=start['spacing_seconds'])
            save(True)
            progress('enumerate')
            if not report['feasibility']['fits_remaining_allowance'] or not report['leaf_plan_frozen']:
                report['feasibility_blocked'] = not report['feasibility']['fits_remaining_allowance']
                if not force:
                    return
                report['forced_incomplete_start'] = True
            for cell in list(report['adaptive_cells']):
                if budget.stopped:
                    return
                resolved = resolve_adaptive_cell(cell, threshold)
                if resolved is None:
                    continue
                for leaf in resolved:
                    report['leaf_queries'].append(leaf)
                    save(True)
                    run(leaf, 'primary_inventory', validate_context=True)
                    if budget.stopped:
                        return
            if not budget.stopped:
                second_pass_unverified()

        def page_count_or_probe(cell):
            from vehicle_tracker.catalog_plan import page_count
            if cell['kind'] == 'whole':
                return page_count(cell['native_count'])
            return 1 + page_count(cell['native_count'])

        def resolve_adaptive_cell(cell, threshold):
            identity = cell['query_id']
            part = next(part for part in report['year_plan']['partitions']
                        if identity.startswith(part['query_id'] + '_make_'))
            bounds = part['filters']['year']
            bucket = dict(count=cell['native_count'], parentModels=[])
            if cell['kind'] == 'whole':
                return adaptive_cell(bucket, make=cell['make'], query_id=identity, year_bounds=bounds,
                                     zip_code=config['primary_zip'], threshold=threshold)['leaves']
            probe = query(identity, config['primary_zip'], {'makes': [{'name': cell['make']}], 'year': dict(bounds)})
            result, _ = run(probe, 'make_discovery', probe=True)
            if not result or budget.stopped or result.get('outcome_kind') in ISOLATABLE_OUTCOMES:
                return None
            try:
                facets = _facets(result)
                live = facets['facet_data']['makes'].get(cell['make'], {})
                decided = adaptive_cell(dict(live, count=live.get('count', cell['native_count'])),
                    make=cell['make'], query_id=identity, year_bounds=bounds,
                    zip_code=config['primary_zip'], threshold=threshold)
            except (ValueError, KeyError) as error:
                report['entries'][-1]['isolation_reason'] = str(error)
                return None
            if decided['kind'] == 'split':
                entry = report['entries'][-1]
                models = (facets['facet_data']['makes'].get(cell['make']) or {}).get('parentModels') or []
                try:
                    children, reason, overlap = model_partitions(
                        facets, cell['make'], config['primary_zip'], identity, year_bounds=bounds)
                except ValueError:
                    children, reason, overlap = None, None, None
                if children == decided['leaves']:
                    entry.update(discovery_observed_at_utc=facets['captured_at_utc'],
                        native_model_count_sum=sum(child['count'] for child in models) if models else None,
                        partition_reason=reason)
                    if overlap is not None:
                        entry['model_id_overlap'] = overlap
                elif decided.get('overlap') is not None:
                    entry['model_id_overlap'] = decided['overlap']
                return decided['leaves']
            return [decided['fallback']]

        def second_pass_unverified():
            report.update(_coverage_union(report))
            verified = {row['query_id'] for row in report['leaf_union'] if row['complete_by_union']}
            for leaf in list(report['leaf_queries']):
                if budget.stopped or budget.requests >= budget.max_requests:
                    return
                if leaf['query_id'] in verified or _leaf_complete_any(report, leaf['query_id']):
                    continue
                if not any(entry.get('report') for entry in _leaf_attempts(report, leaf['query_id'])):
                    continue
                retry = dict(leaf, query_id=leaf['query_id'] + '_pass2')
                report['leaf_retry_attempts'].append(retry)
                save(True)
                run(retry, 'primary_inventory_retry', retry_of=leaf['query_id'])

        save(True)
        with search_transport(post) as send:
            try:
                progress('discovery')
                opening, _ = run(broad, 'discovery', probe=True)
                if not opening or budget.stopped:
                    raise ValueError('Broad discovery failed: '
                                     + ((opening or {}).get('reason') or 'no retained response'))
                report['makes'] = observed_make_names(_facets(opening), 'broad_open')
                report['opening_total'] = opening['reported_total']
                report['inventory_payload_page_floor'] = (opening['reported_total']+23)//24
                if strategy == 'year_then_make_model':
                    page = opening['pages'][0]
                    report['year_plan'] = plan_year_partitions(page['facet_source'],
                        expected_sha256=page['facet_sha256'], zip_code=config['primary_zip'])
                    report['planned_year_probes'] = [query(p['query_id'], p['zip_code'], p['filters'])
                                                     for p in report['year_plan']['partitions']]
                    save(True)  # Freeze all integer-year contexts and both tails before any year request.
                    for part, q in zip(report['year_plan']['partitions'], report['planned_year_probes']):
                        result, _ = run(q, 'year_discovery', probe=True)
                        if not result or budget.stopped:
                            break
                        _facets(result)  # Bind its source, clock and query to the charged response.
                        page = result['pages'][0]
                        discovery = year_make_candidates(page['facet_source'],
                            expected_sha256=page['facet_sha256'], partition=part)
                        report['year_discoveries'].append(discovery)
                        report['entries'][-1]['year_context_validated'] = True
                        candidates = _candidate_queries(discovery)
                        _validate_year_children(candidates, q)
                        report['planned_make_probes'].extend(candidates)
                        report['native_zero_categories'].extend(dict(row, year_query_id=q['query_id'])
                                                               for row in discovery['native_zero_categories'])
                        save(True)  # Every positive candidate is declared before enumeration starts.
                        for candidate in candidates:
                            # Probe each cell first; the whole leaf plan is frozen
                            # and checked against the allowance before enumeration.
                            if not collect_make(candidate, enumerate_children=False) or budget.stopped:
                                break
                        if budget.stopped or budget.requests >= budget.max_requests:
                            break
                    all_years = len(report['year_discoveries']) == len(report['planned_year_probes'])
                    report['leaf_plan_frozen'] = all_years and not budget.stopped
                    report['feasibility'] = _feasibility(report, config, leaf_counts=leaf_counts,
                        already_collected=reused,
                        requests=budget.requests, max_requests=budget.max_requests,
                        seconds_remaining=budget.max_seconds-(time.monotonic()-budget.started),
                        spacing=start['spacing_seconds'])
                    save(True)  # Publish the request floor before charging for enumeration.
                    if not report['feasibility']['fits_remaining_allowance']:
                        # Enumerating part of a plan that cannot finish would spend
                        # the allowance without producing a reconciled population.
                        report['feasibility_blocked'] = True
                    elif not budget.stopped:
                        enumerate_leaves()
                        if not budget.stopped:
                            second_pass()
                elif strategy == 'year_make_adaptive':
                    page = opening['pages'][0]
                    report['year_plan'] = plan_year_partitions(page['facet_source'],
                        expected_sha256=page['facet_sha256'], zip_code=config['primary_zip'])
                    report['planned_year_probes'] = [query(p['query_id'], p['zip_code'], p['filters'])
                                                     for p in report['year_plan']['partitions']]
                    save(True)
                    progress('years')
                    enumerate_adaptive()
                    all_years = len(report['year_discoveries']) == len(report['planned_year_probes'])
                else:
                    report['planned_make_probes'] = [query(f'make_{i:03d}', config['primary_zip'],
                        {'makes': [{'name': make}]}) for i, make in enumerate(report['makes'])]
                    save()
                    for q in report['planned_make_probes']:
                        if not collect_make(q) or budget.stopped:
                            break
                    all_years = True
                    if not budget.stopped:
                        second_pass()
                validated_makes = [e for e in report['entries'] if e['role'] == 'make_discovery'
                                   and 'partition_reason' in e]
                if strategy == 'year_make_adaptive':
                    report['discovery_complete'] = bool(all_years)
                else:
                    report['discovery_complete'] = bool(all_years
                        and len(validated_makes) == len(report['planned_make_probes']))
                save()
                if strategy == 'year_make_adaptive':
                    report.update(_coverage_union(report))
                union_complete = {row['query_id'] for row in report.get('leaf_union') or []
                                  if row.get('complete_by_union')}
                if not report.get('feasibility_blocked'):
                    # Geographic checks are diagnostic; never pooled into the primary denominator.
                    by_id = {e['query']['query_id']: e for e in report['entries']}
                    eligible = [q for q in report['leaf_queries']
                        if (_leaf_complete_any(report, q['query_id']) or q['query_id'] in union_complete)
                        and 0 < _best_leaf_entry(report, q['query_id']).get('reported_total', 0)
                        <= config['validation_max_native_count']]
                    # Rotate deterministically by date; not a representative statistical sample.
                    eligible.sort(key=lambda q: hashlib.sha256((start['cycle_date']+q['query_id']).encode()).hexdigest())
                    selected_checks = eligible[:config['validation_queries_per_zip']]
                    report['planned_geographic_checks'] = [dict(query=query(f'zip_{z}_{q["query_id"]}', z, q['filters']),
                        primary_query_id=q['query_id']) for z in config['validation_zips'] for q in selected_checks]
                    report['geographic_checks'] = []
                    save()
                    for z in config['validation_zips']:
                        result, _ = run(query('broad_zip_'+z, z, {}), 'geographic_discovery', probe=True)
                        if result and not budget.stopped:
                            entry = report['entries'][-1]
                            entry['additional_makes'] = sorted(set(observed_make_names(_facets(result), 'broad_zip_'+z))-set(report['makes']))
                            entry['count_difference_from_opening'] = result['reported_total']-report['opening_total']
                            save()
                    for check in report['planned_geographic_checks']:
                        result, rows = run(check['query'], 'geographic_inventory')
                        previous = primary[check['primary_query_id']]
                        complete = bool(result and result['query_complete'])
                        new = sorted(set(rows.vin)-set(previous.vin)) if complete else None
                        missing = sorted(set(previous.vin)-set(rows.vin)) if complete else None
                        report['geographic_checks'].append(dict(**check, complete=complete,
                            additional_vins=new, primary_vins_not_seen=missing))
                        save()
                    closing, _ = run(query('broad_close', config['primary_zip'], {}), 'closing_discovery', probe=True)
                    if closing and not budget.stopped:
                        report['closing_total'] = closing['reported_total']
                        report['closing_additional_makes'] = sorted(set(observed_make_names(_facets(closing), 'broad_close'))-set(report['makes']))
                by_id = {e['query']['query_id']: e for e in report['entries']}
                leaves = report['leaf_queries']
                report['primary_queries_complete'] = bool(report['discovery_complete'] and leaves
                    and all(_leaf_complete_any(report, q['query_id']) or q['query_id'] in union_complete
                            for q in leaves))
                if strategy == 'year_make_adaptive':
                    report.update(_coverage_union(report))
                    union_complete = {row['query_id'] for row in report['leaf_union'] if row['complete_by_union']}
                    report['primary_queries_complete'] = bool(report['discovery_complete'] and leaves and all(
                        _leaf_complete_any(report, q['query_id']) or q['query_id'] in union_complete for q in leaves))
                    opening_total = report.get('opening_total') or 0
                    share = report['unverified_native_count'] / opening_total if opening_total else 1
                    report['unverified_share'] = share
                    zips_done = all(any(entry['role'] == 'geographic_discovery' and entry['query']['zip_code'] == z
                                        and entry.get('report') for entry in report['entries'])
                                    for z in config['validation_zips'])
                    checks_done = (zips_done and 'closing_total' in report
                                   and len(report['geographic_checks']) == len(report['planned_geographic_checks']))
                    report['coverage_acceptable'] = bool(
                        not budget.stopped and report.get('leaf_plan_frozen') and not report.get('feasibility_blocked')
                        and checks_done and share <= config['max_unverified_share'])
                report['unverified_context_queries'] = sorted(
                    entry['query']['query_id'] for entry in report['entries']
                    if entry.get('report') and not entry.get('context_validated', True))
                frames = [rows for rows in primary.values() if not rows.empty]
                union = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                report['primary_observed_vins'] = union.vin.nunique() if not union.empty else 0
                report.update(_duplicate_membership_summary(report, primary))
                for name in ['opening', 'closing']:
                    report[name+'_count_residual'] = (report[name+'_total']-report['primary_observed_vins']
                        if name+'_total' in report else None)
                report['primary_scope_reconciled'] = bool(report['primary_queries_complete']
                    and report['unexplained_duplicate_primary_memberships'] == 0
                    and report['opening_count_residual'] == report['closing_count_residual'] == 0
                    and report.get('closing_additional_makes') == [])
                report['geographic_checks_complete'] = bool(report['planned_geographic_checks']
                    and len(report['geographic_checks']) == len(report['planned_geographic_checks'])
                    and all(c['complete'] for c in report['geographic_checks'])
                    and all(e.get('additional_makes') is not None for e in report['entries']
                            if e['role'] == 'geographic_discovery'))
                report['geographic_membership_stable'] = bool(report['geographic_checks_complete']
                    and all(c['additional_vins'] == c['primary_vins_not_seen'] == []
                            for c in report['geographic_checks'])
                    and all(e.get('additional_makes') == [] for e in report['entries']
                            if e['role'] == 'geographic_discovery'))
                report['declared_collection_complete'] = bool(not budget.stopped
                    and report['primary_queries_complete'] and report['geographic_checks_complete']
                    and 'closing_total' in report)
                report['coverage_note'] = ('A sequential observed union, not a point-in-time census. '
                    'Opening/closing counts and sampled ZIP membership cannot prove national completeness.')
                report['status'] = 'collection_finished' if not budget.stopped else 'stopped'
            except BaseException as error:
                report.update(status='stopped', failure_type=type(error).__name__,
                    failure_reason=str(error) if isinstance(error, ValueError) else type(error).__name__)
                budget.stop()
                save(True)
                if not isinstance(error, Exception):
                    raise
            finally:
                try:
                    report['make_reconciliation'] = _make_reconciliation(report, primary)
                    if strategy in ('year_then_make_model', 'year_make_adaptive'):
                        report.update(_year_diagnostics(report, primary))
                except Exception as error:
                    # A diagnostic failure must not leave a terminal process labelled running.
                    report.update(status='stopped', declared_collection_complete=False,
                        make_reconciliation=None, reconciliation_failure_type=type(error).__name__)
                    report.setdefault('failure_type', type(error).__name__)
                    report.setdefault('failure_reason', 'Make reconciliation failed; retained evidence requires review')
                    try:
                        budget.stop()
                    except Exception as storage_error:
                        report['budget_finalization_failure_type'] = type(storage_error).__name__
                report['ended_at'] = utcnow().isoformat()
                save(True)
                _finalize_attempt(report, folder, root, config)
        return report


