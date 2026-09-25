# Full-inventory terminal outcome: September 24, 2026 (year-first r4)

The scheduled year-first sweep under
`vehicle/data/experiments/carvana_full_inventory_years_r4/2026-09-24`
started at 8:00 AM Eastern and ended at **2026-09-24T12:50:15Z** with status
`collection_finished` and attempt kind `finished_incomplete`. The daily task
recorded `LastTaskResult 2`. The durable ledger has `pending_request=false`,
`stopped=false`, and 599 charged requests. No query failed. The automatic
offline export still wrote `analysis/`. This is a planning stop, not an access
stop and not a sales day.

## Counts

| Field | Value |
|---|---|
| Requests | 599 / 7,000 |
| Spacing | 5 seconds, because September 23 was labeled an access stop |
| Opening native total | 81,759 |
| Primary observed VINs | 1,815 |
| Opening residual | 79,944 |
| Discovery complete | true |
| Declared leaf queries | 3,497 |
| Estimated minimum requests | 6,060 |
| Estimated seconds at 5s spacing | 27,305 |
| Seconds remaining | 18,593 |
| Feasibility | `pacing_fits=false`, `feasibility_blocked=true` |

Discovery of every year and make finished. Enumeration never started. The
6,060-request plan cannot be paced at five seconds inside the six-hour window.
The 1,815 VIN figure is discovery reuse of single-page probes, not a census.

## What this day is not

September 23's HTTP 520 was not an access denial, so the five-second spacing
was not required. A later day that is not an access stop returns to three
seconds. This date cannot be resumed. Missing is not sold.
