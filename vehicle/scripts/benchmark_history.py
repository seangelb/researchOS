"""Explicit, synthetic offline benchmark. Temporary evidence only; no network or exports.

Example: python -B vehicle/scripts/benchmark_history.py --vehicles 10000 --days 30
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
    args = parser.parse_args()
    if not 1 <= args.vehicles <= 80000 or not 1 <= args.days <= 30:
        parser.error('Use 1..80000 vehicles and 1..30 days; data is entirely synthetic')
    started = time.perf_counter()
    results = []
    def measured(phase):
        nonlocal started
        results.append(dict(phase=phase, seconds=time.perf_counter()-started, process_peak_mb=peak_mb()))
        print(json.dumps(results[-1]), flush=True)
        started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='vehicle-synthetic-benchmark-') as temporary:
        root = Path(temporary)
        reports, days = [], []
        for day in range(args.days):
            stamp = datetime(2026, 9, 1, 14, tzinfo=timezone.utc)+timedelta(days=day)
            run_id = f'synthetic-{day}'
            entries = []
            for page, first in enumerate(range(0, args.vehicles, 24), 1):
                vehicles = [dict(vehicleId=i+1, vin=f'{i:017}', year=2024, make='Synthetic', model='Example',
                    mileage=100, price={'total':20000}, isPurchasePending=False, vehicleLockType=0)
                    for i in range(first, min(first+24, args.vehicles))]
                request = dict(filters={}, pagination=dict(page=page, pageSize=24), sortBy='MostPopular', zip5='08542')
                data = dict(userDeliveryInfo=dict(zip5='08542'), inventory=dict(vehicles=vehicles,
                    pagination=dict(currentPage=page, pageSize=24, totalMatchedInventory=args.vehicles,
                                    totalMatchedPages=(args.vehicles+23)//24)))
                capture = project_response(data, request, observed_at=(stamp+timedelta(milliseconds=page)).isoformat())
                path = root/f'{day}-{page}.json'
                path.write_text(json.dumps(capture))
                entries.append(dict(page=page, status='parsed', stored_rows=len(vehicles), retained_source=str(path),
                                    source_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            report = root/f'report-{day}.json'
            report.write_text(json.dumps(dict(run_id=run_id, filters={}, zip_code='08542', query_complete=True,
                reported_total=args.vehicles, unique_listings=args.vehicles, pages=entries,
                started_utc=stamp.isoformat(), ended_utc=(stamp+timedelta(hours=1)).isoformat())))
            reports.append(report)
            days.append(dict(cycle_id=run_id, cycle_date=stamp.date().isoformat(), timezone='UTC', scope_id='synthetic',
                window_start=stamp.isoformat(), window_end=(stamp+timedelta(hours=1)).isoformat(),
                available_at=(stamp+timedelta(hours=1)).isoformat(), coverage_complete=True, coverage_reason='synthetic'))
        measured('generate_temporary_evidence')
        imported = import_reports(reports, root/'history.sqlite')
        assert imported.imported_rows.sum() == args.vehicles*args.days
        measured('import_retained_reports')
        _, _, rows = read_history(root/'history.sqlite')
        measured('read_history')
        observations = rows.assign(cycle_id=rows.run_id)
        events = vin_events(pd.DataFrame(days), observations)
        assert len(events) == args.vehicles*args.days
        counts = daily_counts(pd.DataFrame(days), events)
        assert counts.observed_vins.eq(args.vehicles).all() and counts.estimated_sales.isna().all()
        measured('events_and_daily_summary')
    print(json.dumps(dict(synthetic=True, vehicles=args.vehicles, days=args.days, results=results)), flush=True)


if __name__ == '__main__':
    main()
