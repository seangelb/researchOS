"""Run existing synthetic benchmarks and focused recovery tests; never collect live data.

Example: python -B vehicle/scripts/run_offline_scale_trial.py --destination
  vehicle/data/experiments/sales_method_20260912/scale_OFFLINE

Each volume runs in a fresh process so process peak memory is comparable. Large
volumes replay invented retained pages; they do not bypass live request limits.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
TESTS = 'vehicle/tests/'
# Reuse meaningful existing faults, instead of copying their implementations.
FAULT_TESTS = {
    'pagination_duplicates_and_constant_total_churn': ['test_offline_scale_trial.py'],
    'timeouts_malformed_responses_changing_totals': [
        'test_search.py::test_incomplete_or_wrong_context_stays_blocked',
        'test_search.py::test_payload_failures_and_allowlist',
        'test_search.py::test_http_failure_stops_without_retry'],
    'interrupted_saves_and_import_idempotence': [
        'test_storage_recovery.py::test_interrupted_retention_never_publishes_truncated_bytes',
        'test_storage_recovery.py::test_identical_page_import_is_noop_but_different_observation_is_new',
        'test_storage_recovery.py::test_page_observation_collision_rolls_back_entire_page',
        'test_history_recovery.py::test_first_query_interruption_keeps_plan_and_restarts',
        'test_cycle_recovery.py::test_import_before_registration_crash_has_offline_idempotent_recovery'],
    'partial_sweeps_and_event_replay': [
        'test_daily_events.py::test_gaps_and_partial_days_break_absence_streak_and_expose_uncertainty',
        'test_daily_events.py::test_partial_missing_then_return_is_not_a_confirmed_reappearance',
        'test_daily_events.py::test_thresholds_have_current_availability_and_no_backdating',
        'test_daily_cycles.py::test_missing_stored_observation_blocks_absence_comparison'],
    'inventory_overlap_and_durable_budget': [
        'test_daily_cycles.py::test_cycle_os_lock_refuses_a_second_owner',
        'test_cycle_recovery.py::test_registration_cannot_bind_an_active_cycle',
        'test_daily_cycles.py::test_cumulative_limit_cannot_be_reset_by_resume',
        'test_daily_cycles.py::test_uncertain_request_remains_counted_and_blocks_retry'],
    'browser_detail_failure_recovery_and_overlap': [
        'test_detail_batch.py::test_invalid_native_target_is_retained_and_stops_batch',
        'test_detail_batch.py::test_explicit_failure_remains_a_real_attempt_without_native_status',
        'test_detail_batch.py::test_recover_orphan_preserves_bytes_and_uses_recovery_availability_even_after_expiry',
        'test_detail_batch.py::test_record_refuses_to_replace_a_different_orphan',
        'test_detail_batch.py::test_cross_batch_reservation_blocks_same_vin_even_after_old_window_expires',
        'test_detail_batch.py::test_started_visit_cannot_repeat_and_successful_visits_enforce_spacing'],
}


def run_stage(arguments, destination, name, *, timeout_seconds=900):
    """Save child output even when it fails; never replace an earlier stage log."""
    started = time.perf_counter()
    command = [sys.executable, '-B', *arguments]
    with (destination/f'{name}.log').open('x', encoding='utf-8') as output:
        try:
            result = subprocess.run(command, cwd=ROOT, stdout=output,
                                    stderr=subprocess.STDOUT, timeout=timeout_seconds)
            exit_code, timed_out = result.returncode, False
        except subprocess.TimeoutExpired:
            exit_code, timed_out = None, True
    return dict(command=command, elapsed_seconds=time.perf_counter()-started,
                exit_code=exit_code, timed_out=timed_out, log=f'{name}.log')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    destination = args.destination.resolve()
    allowed = (ROOT/'vehicle/data/experiments').resolve()
    if not destination.is_relative_to(allowed) or 'OFFLINE' not in destination.name:
        parser.error('Use a new directory beneath vehicle/data/experiments with OFFLINE in its name')
    destination.mkdir(parents=True, exist_ok=False)
    volumes = [(1000, 2, 'constant'), (10000, 2, 'constant'), (50000, 2, 'constant'),
               (1000, 14, 'mixed')]
    report = dict(synthetic=True, live_requests=0, created_at=datetime.now(timezone.utc).isoformat(),
        status='running', criteria=dict(max_seconds_per_stage=900, max_process_peak_mb=2048,
            advance='All assertions pass, no timeout, process peak below 2048 MiB; otherwise stop.',
            integrity='No missing/duplicate counted observations, incomplete days never count as exits.'),
        planned_stages=[dict(vehicles=n, days=d, scenario=s) for n, d, s in volumes],
        fault_evidence_map=FAULT_TESTS, stages=[], limitations=[
            'No live API or browser throughput/access is tested by this offline script.',
            'Synthetic retained pages are temporary and never enter the operating register or database.',
            'Process peak memory is cumulative within each child, not isolated per phase.',
            'Stable totals and unique pages can still miss within-sweep inventory churn.',
            'No national coverage, unattended operation, repeated live reliability or sales accuracy claim.'])
    source_paths = [Path(__file__), ROOT/'vehicle/scripts/benchmark_history.py',
                    ROOT/'vehicle/tests/test_offline_scale_trial.py']
    source_paths += list((ROOT/'vehicle/src/vehicle_tracker').glob('*.py'))
    report['source_sha256'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in source_paths}
    # The checkpoint is diagnostic only; synthetic sources stay in child temporary folders.
    def checkpoint():
        (destination/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    checkpoint()
    selectors = list(dict.fromkeys(TESTS+name for names in FAULT_TESTS.values() for name in names))
    faults = run_stage(['-m', 'pytest', '-q', *selectors,
                       f'--junitxml={destination / "faults.xml"}'], destination, 'faults')
    if (destination/'faults.xml').exists():
        suite = ET.parse(destination/'faults.xml').getroot().find('testsuite')
        faults['tests'] = {key: int(suite.attrib.get(key, 0))
                           for key in ('tests', 'failures', 'errors', 'skipped')}
    report['fault_tests'] = faults
    checkpoint()
    if faults['exit_code'] != 0:
        report['status'] = 'stopped_fault_test_failure'
        checkpoint()
        return 1
    for vehicles, days, scenario in volumes:
        name = f'{vehicles}VIN_{days}days_{scenario}'
        stage = run_stage(['vehicle/scripts/benchmark_history.py', '--vehicles', str(vehicles),
                           '--days', str(days), '--scenario', scenario], destination, name)
        stage.update(vehicles=vehicles, days=days, scenario=scenario, live_requests=0)
        if stage['exit_code'] == 0:
            lines = (destination/stage['log']).read_text(encoding='utf-8').splitlines()
            measurements = json.loads(lines[-1])
            stage['measurements'] = measurements
            stage['observations_per_second'] = measurements['observation_rows']/stage['elapsed_seconds']
            stage['process_peak_mb'] = max(row['process_peak_mb'] for row in measurements['results'])
        report['stages'].append(stage)
        checkpoint()
        print(json.dumps(stage), flush=True)
        if stage['exit_code'] != 0 or stage.get('process_peak_mb', float('inf')) >= 2048:
            report['status'] = 'stopped_volume_stage_limit_or_failure'
            checkpoint()
            return 1
    report.update(status='passed_OFFLINE_only', finished_at=datetime.now(timezone.utc).isoformat())
    checkpoint()
    print(f'Offline report: {destination / "report.json"}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
