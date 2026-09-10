"""Explicit, synthetic offline benchmark. Temporary evidence only; no network or exports.

Example: python -B vehicle/scripts/benchmark_history.py --vehicles 10000 --days 30
Mixed:   python -B vehicle/scripts/benchmark_history.py --vehicles 10000 --days 14 --scenario mixed
Peak working set is process-wide through each phase, not an isolated phase allocation.
"""
import argparse
import ctypes
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import pandas as pd
from vehicle_tracker.history import import_reports, read_history
from vehicle_tracker.events import vin_events, daily_counts
from vehicle_tracker.daily import daily_tables
from vehicle_tracker.checks import CHECK_COLUMNS
from vehicle_tracker.search import project_response


def peak_mb():
    if os.name == 'nt':
        class Counters(ctypes.Structure):
            _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [
                (name, ctypes.c_size_t) for name in ('peak_working', 'working', 'peak_paged', 'paged',
                    'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        assert ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(kernel.GetCurrentProcess()),
            ctypes.byref(counters), counters.cb)
        return counters.peak_working/1024**2
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform == 'darwin' else 1024)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vehicles', type=int, default=10000)
    parser.add_argument('--days', type=int, default=1)
    parser.add_argument('--scenario', choices=['constant', 'mixed'], default='constant')
    args = parser.parse_args()
    if not 1 <= args.vehicles <= 80000 or not 1 <= args.days <= 30:
        parser.error('Use 1..80000 vehicles and 1..30 days; data is entirely synthetic')
    mixed = args.scenario == 'mixed'
    if mixed and (args.vehicles < 100 or args.days != 14):
        parser.error('The mixed scenario requires at least 100 vehicles and exactly 14 calendar days')
    removed, returning, added = args.vehicles//50, args.vehicles//100, args.vehicles//50
    started = time.perf_counter()
    results = []
    def measured(phase):
        nonlocal started
        results.append(dict(phase=phase, seconds=time.perf_counter()-started, process_peak_mb=peak_mb()))
        print(json.dumps(results[-1]), flush=True)
        started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='vehicle-synthetic-benchmark-') as temporary:
        root = Path(temporary)
        reports, days, expected_rows, capture_count = [], [], 0, 0
        for day in range(args.days):
            if mixed and day == 7:
                continue  # Missing September 8; never an observed zero-inventory day.
            stamp = datetime(2026, 9, 1, 14, tzinfo=timezone.utc)+timedelta(days=day)
            run_id = f'synthetic-{day}'
            ids = list(range(args.vehicles))
            if mixed and day >= 2:
                ids = ids[removed:]  # Two percent persistently absent from day 3.
            if mixed and 3 <= day <= 5:
                ids = [i for i in ids if not removed <= i < removed+returning]
            if mixed and day >= 4:
                ids.extend(range(args.vehicles, args.vehicles+added))
            total = len(ids)
            partial = mixed and day == 9
            if partial:
                ids = ids[:args.vehicles*2//5]  # Forty percent captured on September 10.
            expected_rows += len(ids)
            entries = []
            for page, first in enumerate(range(0, len(ids), 24), 1):
                vehicles = [dict(vehicleId=i+1+(args.vehicles*2 if mixed and day >= 6
                    and removed <= i < removed+returning else 0), vin=f'{i:017}', year=2024,
                    make='Synthetic', model='Example', mileage=100, price={'total':20000},
                    isPurchasePending=(None if i % 101 == 0 else (i+day) % 7 == 0) if mixed else False,
                    vehicleLockType=0) for i in ids[first:first+24]]
                request = dict(filters={}, pagination=dict(page=page, pageSize=24), sortBy='MostPopular', zip5='08542')
                data = dict(userDeliveryInfo=dict(zip5='08542'), inventory=dict(vehicles=vehicles,
                    pagination=dict(currentPage=page, pageSize=24, totalMatchedInventory=total,
                                    totalMatchedPages=(total+23)//24)))
                capture = project_response(data, request, observed_at=(stamp+timedelta(milliseconds=page)).isoformat())
                path = root/f'{day}-{page}.json'
                path.write_text(json.dumps(capture))
                entries.append(dict(page=page, status='parsed', stored_rows=len(vehicles), retained_source=str(path),
                                    source_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            report = root/f'report-{day}.json'
            capture_count += len(entries)
            report.write_text(json.dumps(dict(run_id=run_id, filters={}, zip_code='08542', query_complete=not partial,
                reported_total=total, unique_listings=len(ids), pages=entries,
                started_utc=stamp.isoformat(), ended_utc=(stamp+timedelta(hours=1)).isoformat())))
            reports.append(report)
            days.append(dict(cycle_id=run_id, cycle_date=stamp.date().isoformat(), timezone='UTC', scope_id='synthetic',
                window_start=stamp.isoformat(), window_end=(stamp+timedelta(hours=1)).isoformat(),
                available_at=(stamp+timedelta(hours=1)).isoformat(), coverage_complete=not partial,
                coverage_reason='Synthetic partial capture' if partial else 'Synthetic complete query'))
        measured('generate_temporary_evidence')
        imported = import_reports(reports, root/'history.sqlite')
        assert imported.imported_rows.sum() == expected_rows
        measured('import_retained_reports')
        _, _, rows = read_history(root/'history.sqlite')
        measured('read_history')
        observations = rows.assign(cycle_id=rows.run_id)
        events = vin_events(pd.DataFrame(days), observations)
        counts = daily_counts(pd.DataFrame(days), events)
        assert counts.estimated_sales.isna().all()
        if mixed:
            assert events.event_type.eq('relisted').sum() == returning
            assert events.event_type.eq('persistent_absence').sum() == removed+returning
        else:
            assert len(events) == args.vehicles*args.days and counts.observed_vins.eq(args.vehicles).all()
        measured('events_and_daily_summary')
        if mixed:
            del events  # The full report builds its own event table.
            checks = []
            statuses = ['available', 'pending', 'sold_label', 'unknown', 'unavailable', 'access_blocked']
            wording = ['Get Started', 'Purchase in progress', 'Sold', 'Inspection in progress',
                       'No longer available', 'Challenge']
            for i in range(60):
                check = dict(check_id=f'synthetic-{i}', retailer='carvana', vin=f'{i:017}', listing_id=str(i+1),
                    checked_at='2026-09-04T16:00:00Z', available_at='2026-09-04T16:05:00Z',
                    observed_status=statuses[i%6], native_text=wording[i%6],
                    source=f'synthetic://benchmark/{i}', reviewer='Synthetic benchmark', note='Invented evidence')
                checks.append(check)
                if i < 10:
                    checks.append(dict(check, check_id=f'synthetic-correction-{i}',
                        available_at='2026-09-05T17:00:00Z', observed_status='unknown', native_text='Corrected interpretation'))
                    checks.append(dict(check, check_id=f'synthetic-recheck-{i}',
                        checked_at='2026-09-11T16:00:00Z', available_at='2026-09-11T16:05:00Z',
                        observed_status='unknown', native_text='Unresolved at recheck'))
            tables = daily_tables(pd.DataFrame(days), observations, as_of='2026-09-14T23:00:00Z',
                timezone_name='UTC', checks=pd.DataFrame(checks, columns=CHECK_COLUMNS))
            summary = tables['daily_inventory'].set_index('cycle_date')
            assert summary.loc['2026-09-08', 'coverage_status'] == 'missing'
            assert summary.loc['2026-09-10', 'coverage_status'] == 'partial'
            assert summary.loc[['2026-09-08', '2026-09-10'], 'inventory_count'].isna().all()
            assert summary.loc[['2026-09-09', '2026-09-11'], 'absent_since_previous_day'].isna().all()
            assert summary.estimated_sales.isna().all()
            assert len(tables['sale_candidates']) == removed+returning
            assert tables['sale_candidates'].followup_state.eq('reappeared').sum() == returning
            assert len(tables['listing_checks']) == 60 and tables['detail_followups'].selected_for_check.sum() <= 20
            measured('daily_tables_with_saved_checks')
    print(json.dumps(dict(synthetic=True, scenario=args.scenario, vehicles=args.vehicles, days=args.days,
        retained_cycles=len(days), observation_rows=expected_rows, captures=capture_count, results=results,
        limitations='Local synthetic processing only; not live access, registered-source revalidation, national coverage or sales accuracy.')), flush=True)


if __name__ == '__main__':
    main()
