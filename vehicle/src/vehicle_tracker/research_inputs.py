"""Read-only retained research inputs shared by notebooks and the CLI."""
import json
import hashlib
from pathlib import Path

import pandas as pd

from vehicle_tracker.detail_batch import load_browser_batches
from vehicle_tracker.sale_pilot import (known_disjoint_cohorts, load_pilot,
    load_research_pass, load_selection_plan)


COHORT_FILES = ('config/carvana_sale_pilot.json',
                'config/carvana_sale_pilot_extension_20260909.json')


def load_detail_cohorts(root, as_of):
    """Read the two frozen cohort definitions without loading page observations."""
    return known_disjoint_cohorts([json.loads((Path(root)/name).read_text(encoding='utf-8'))
                                  for name in COHORT_FILES], as_of=as_of)


def load_detail_evidence(root, as_of, *, cohorts=None):
    """Read existing studies at a cutoff; plans never become observed page checks.

    An explicit cohort list preserves existing caller selection. The default uses
    the two configured cohorts. All source readers retain their identity/hash checks.
    """
    root = Path(root)
    cohorts = (load_detail_cohorts(root, as_of) if cohorts is None else
               known_disjoint_cohorts(cohorts, as_of=as_of))
    paths = {root/name for name in COHORT_FILES}
    frames = [load_pilot(root, cohort, as_of=as_of) for cohort in cohorts]
    # Pilot run manifests bind source identity and availability, as well as captures.
    paths.update((root/'data/experiments/carvana_sale_signals').glob('*/run.json'))
    paths.update(root/cohort['baseline_source'] for cohort in cohorts
                 if cohort.get('baseline_source') and (root/cohort['baseline_source']).is_file())
    plans, research_passes = [], {}
    review = root/'data/experiments/vendor_review/20260911T162600Z'
    for plan_name, pass_name in [('visible_check_plan.json', 'pass.json'),
                                 ('exit_batch_plan.json', 'exit_batch_pass.json')]:
        selected = load_selection_plan(review/plan_name, as_of=as_of)
        report, records = load_research_pass(review/pass_name, selected, cohorts, as_of=as_of)
        research_passes[pass_name] = dict(report=report, selected=selected, records=records)
        plans.append(selected)
        frames.append(records)
        paths.update([review/plan_name, review/pass_name])
    browser_records, browser_plans, browser_health, browser_paths = load_browser_batches(root, cohorts, as_of=as_of)
    frames.append(browser_records)
    plans.append(browser_plans)
    frames, plans = [f for f in frames if not f.empty], [p for p in plans if not p.empty]
    records = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    selections = pd.concat(plans, ignore_index=True) if plans else pd.DataFrame()
    paths.update(browser_paths)
    if not records.empty:
        paths.update(Path(p) for p in records.source if Path(p).is_file())
    return dict(cohorts=cohorts, records=records, selection_plans=selections,
                research_passes=research_passes, browser_records=browser_records,
                browser_health=browser_health, input_paths=paths)


def load_trial_review(selections, *, as_of):
    """Read explicitly selected capacity audits available by the cutoff.

    Returns the review and checkpoint tables plus every retained input path.
    Future audits cannot expose measured results or read underlying captures.
    """
    trial_cutoff = pd.Timestamp(as_of)
    if pd.isna(trial_cutoff) or trial_cutoff.tzinfo is None:
        raise ValueError('Trial evidence cutoff must have a timezone.')
    trial_rows, trial_checkpoint_records, trial_input_paths = [], [], set()
    for trial_label, filename in selections:
        reconciliation_path = Path(filename)
        row = dict(trial_label=trial_label, review_status='reconciliation_missing',
                   reconciliation_path=str(reconciliation_path))
        if not reconciliation_path.is_file():
            trial_rows.append(row)
            continue
        trial_input_paths.add(reconciliation_path)
        trial = json.loads(reconciliation_path.read_text(encoding='utf-8'))
        available_at = trial.get('audit_finished_at', trial.get('audited_at'))
        if available_at is None:
            raise ValueError('Trial reconciliation has no audit completion time.')
        audit_time = pd.Timestamp(available_at)
        if pd.isna(audit_time) or audit_time.tzinfo is None:
            raise ValueError('Trial audit completion time must have a timezone.')
        row['audit_available_at'] = audit_time.isoformat()
        if audit_time > trial_cutoff:
            row['review_status'] = 'unavailable_at_cutoff'
            trial_rows.append(row)
            continue
        # Only eligible audits may read source artifacts or contribute measured results.
        artifact_hashes = trial.get('source_artifact_sha256')
        if not isinstance(artifact_hashes, dict) or not artifact_hashes:
            raise ValueError('Trial reconciliation has no source artifact hashes.')
        for source_filename, expected_hash in artifact_hashes.items():
            source_path = Path(source_filename)
            if hashlib.sha256(source_path.read_bytes()).hexdigest() != expected_hash:
                raise ValueError('Trial source changed; reconcile before using capacity evidence.')
            trial_input_paths.add(source_path)
        counts, timing = trial['counts'], trial['timing']
        partial_queries = counts.get('partial_queries', counts.get('blocked_partial_queries'))
        # The small full-plan audit has no partial field; zero follows from all queries completing.
        if partial_queries is None and counts.get('planned_queries') is not None and counts.get('complete_queries') == counts['planned_queries']:
            partial_queries = 0
        complete_vins = counts.get('verified_vins_in_complete_queries')
        # If every declared query completed, every verified distinct VIN belongs to complete queries.
        if complete_vins is None and counts.get('planned_queries') is not None and counts.get('complete_queries') == counts['planned_queries']:
            complete_vins = counts.get('distinct_vins', counts.get('unique_vins'))
        row.update(review_status='reconciled', acceptance_verdict=trial.get('verdict'),
            target_vins=trial.get('target_vins'), target_reached=trial.get('target_reached'),
            distinct_vins=counts.get('distinct_vins', counts.get('unique_vins')),
            attempted_requests=counts.get('attempted_requests'),
            complete_queries=counts.get('complete_queries'), partial_queries=partial_queries,
            blocked_queries=counts.get('blocked_queries', counts.get('blocked_partial_queries')),
            unattempted_queries=counts.get('unattempted_queries'),
            verified_vins_in_complete_queries=complete_vins,
            verified_vins_in_partial_queries=counts.get('verified_vins_in_partial_queries',
                                                       counts.get('verified_vins_in_blocked_partial_queries')),
            elapsed_seconds=timing.get('collector_elapsed_seconds'),
            minimum_start_gap_seconds=timing.get('minimum_request_start_gap_seconds',
                                                timing.get('minimum_observed_start_gap_seconds')),
            gaps_below_three_seconds=timing.get('gaps_below_three_seconds',
                                              timing.get('observed_gaps_below_three_seconds')),
            stop_reason=trial.get('actual_stop_reason'))
        if row['stop_reason'] is None and trial.get('verdict') == 'PASS' and counts.get('planned_queries') is not None and counts.get('complete_queries') == counts['planned_queries']:
            row['stop_reason'] = 'Declared query plan complete'
        trial_rows.append(row)
        for checkpoint in trial.get('checkpoints', []):
            trial_checkpoint_records.append(dict(checkpoint, trial_label=trial_label))
    trial_columns = ['trial_label', 'review_status', 'audit_available_at', 'acceptance_verdict',
        'target_vins', 'target_reached', 'distinct_vins', 'attempted_requests', 'complete_queries',
        'partial_queries', 'blocked_queries', 'unattempted_queries', 'verified_vins_in_complete_queries',
        'verified_vins_in_partial_queries', 'elapsed_seconds', 'minimum_start_gap_seconds',
        'gaps_below_three_seconds', 'stop_reason', 'reconciliation_path']
    trial_capacity = pd.DataFrame(trial_rows, columns=trial_columns)
    trial_checkpoint_rows = pd.DataFrame(trial_checkpoint_records, columns=[
        'trial_label', 'target_vins', 'unique_vins', 'unique_listings', 'requests',
        'elapsed_seconds', 'observed_at', 'query_id', 'page'])
    return trial_capacity, trial_checkpoint_rows, trial_input_paths


def load_operating_runs(settings, *, as_of):
    """Read each retained daily attempt, including unregistered failures.

    Calendar completion and analyst effort belong in the notebook. This reader
    uses the existing cycle/source checks and never imports or resumes a run.
    """
    from vehicle_tracker.cycles import _read_cycle_evidence, _source_cycle_rows
    from vehicle_tracker.daily import registered_cycles

    cutoff = pd.Timestamp(as_of)
    if pd.isna(cutoff) or cutoff.tzinfo is None:
        raise ValueError('Operating review cutoff must have a timezone.')
    entries = registered_cycles(settings)
    registered = {Path(e['path']).resolve() for e in entries}
    paths = registered | {p.resolve() for p in settings['capture_root'].glob('*/cycle.json')}
    inputs, records = {settings['config_path'], settings['plan']}, []
    # A directory created before an interrupted first checkpoint is evidence of
    # unresolved work, not an observed zero or an ordinary missed collection.
    for folder in sorted(settings['capture_root'].glob('????-??-??')):
        if folder.is_dir() and not (folder / 'cycle.json').exists():
            records.append(dict(actual_date=folder.name, collection_status='missing cycle metadata',
                cycle_path=str(folder / 'cycle.json'), failures='Retained folder has no cycle record; availability unknown',
                recovery_status='review retained folder; do not recollect into it'))
    if settings['register'].is_file():
        inputs.add(settings['register'])
    for path in sorted(paths):
        inputs.add(path)
        row = dict(actual_date=path.parent.name, cycle_path=str(path),
                   collection_status='invalid retained evidence', export_status='not verified')
        try:
            native = json.loads(path.read_text(encoding='utf-8'))
            # Do not admit observations or clocks from a later-created cycle.
            if pd.Timestamp(native['created_at']) > cutoff:
                continue
            if native['queries'] != settings['queries'] or native['timezone'] != settings['timezone']:
                raise ValueError('Selected tracking population differs from retained cycle.')
            state, coverage, reports, replayed = _read_cycle_evidence(path, as_of=as_of)
            rows = _source_cycle_rows(coverage, replayed)
            inputs.update(reports)
            if not rows.empty:
                inputs.update(map(Path, rows.source_path))
            report_rows = [json.loads(p.read_text(encoding='utf-8')) for p in reports]
            starts = [pd.Timestamp(r['started_utc']) for r in report_rows if r.get('started_utc')]
            ends = [pd.Timestamp(r['ended_utc']) for r in report_rows if r.get('ended_utc')]
            failures = [r.get('reason') or r.get('status') for r in report_rows
                        if not r.get('query_complete')]
            reported_requests = sum(r['requests'] for r in report_rows)
            last_request = native['budget'].get('last_request_utc')
            budget_at_cutoff = not last_request or pd.Timestamp(last_request) <= cutoff
            # Completed query reports are only a lower bound during a sweep.
            # A later final budget cannot turn unreported in-flight starts into zero.
            unresolved = (not budget_at_cutoff or native['budget'].get('pending_request') or
                          native['budget']['requests'] != reported_requests)
            row.update(actual_date=state['cycle_date'], scope_id=state['scope_id'],
                actual_start=min(starts).isoformat() if starts else state['created_at'],
                actual_end=max(ends).isoformat() if ends else None,
                complete_queries=int(coverage.coverage_complete.sum()),
                observed_vins=rows.vin.nunique() if not rows.empty else 0,
                requests=None if unresolved else reported_requests,
                reported_requests=reported_requests,
                elapsed_seconds=(max(ends)-min(starts)).total_seconds() if starts and ends else None,
                collection_status='complete' if state['coverage_complete'] else 'partial or failed',
                failures='; '.join(filter(None, failures)) or state['coverage_reason'],
                recovery_status=('registered in current register' if path in registered else 'unregistered; preview recovery')
                    + ('; request outcome unresolved' if unresolved else ''),
                attempt_count=len({p.parent.parent.name for p in reports}),
                available_at=state['available_at'])
        except (ValueError, OSError, KeyError, TypeError) as error:
            row['failures'] = str(error)
            row['recovery_status'] = 'review retained evidence; do not recollect into this destination'
        records.append(row)
    columns = ['actual_date', 'actual_start', 'actual_end', 'complete_queries', 'observed_vins',
               'requests', 'reported_requests', 'elapsed_seconds', 'collection_status', 'failures',
               'recovery_status', 'attempt_count', 'export_status', 'cycle_path', 'scope_id', 'available_at']
    return pd.DataFrame(records, columns=columns), inputs


def load_operating_exports(settings, *, as_of):
    """Identify complete retained CSV exports for the selected fixed query plan.

    An old export binds its own register vintage, not today's mutable register.
    Config/query hashes and all exported file hashes must still match.
    """
    from vehicle_tracker.daily import digest
    records, inputs = [], set()
    for manifest in sorted(settings['exports'].glob('*/manifest.json')):
        if manifest.parent.name.endswith('.partial'):
            continue
        inputs.add(manifest)
        data = json.loads(manifest.read_text(encoding='utf-8'))
        if pd.Timestamp(data['as_of']) > pd.Timestamp(as_of):
            continue
        if any(data['sources'].get(str(settings[k])) != digest(settings[k]) for k in ['config_path', 'plan']):
            continue
        for filename, expected in data['outputs'].items():
            path = manifest.parent / filename
            if path.name != filename or digest(path) != expected:
                raise ValueError('Operating export changed; inspect its retained manifest.')
            inputs.add(path)
        inventory = manifest.parent / 'daily_inventory.csv'
        if inventory.name not in data['outputs']:
            continue
        dates = pd.read_csv(inventory, usecols=['cycle_date']).cycle_date
        records.extend(dict(actual_date=day, export_as_of=data['as_of'], export_path=str(manifest)) for day in dates)
    return pd.DataFrame(records, columns=['actual_date', 'export_as_of', 'export_path']), inputs
