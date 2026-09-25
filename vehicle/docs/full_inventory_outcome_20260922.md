# Full-inventory terminal outcome: September 22, 2026 (year-first r3)

The scheduled year-first sweep under
`vehicle/data/experiments/carvana_full_inventory_years_r3/2026-09-22`
started at 08:00 AM Eastern and ended at **2026-09-22T16:15:37Z** with status
`stopped`. The daily task `researchOS-CarvanaFullInventoryDaily` recorded
`LastTaskResult 1`. There is no `access_stop.json`. The durable ledger has
`pending_request=false` and 5,063 charged requests. The automatic offline export
still wrote `analysis/`. This is an aborted partial day, not a reconciled
full-inventory baseline and not a sales day.

## Counts

| Field | Value |
|---|---|
| Requests | 5,063 / 6,000 |
| Opening native total | 80,142 |
| Closing native total | none; closing discovery never ran |
| Primary observed VINs | 64,030 |
| Opening residual | 16,112 |
| Discovery complete | true |
| Leaf plan frozen | true |
| Feasibility | 5,962 estimated minimum; fits remaining allowance |
| Declared leaf queries | 3,466 |
| Primary leaves attempted | 2,927 |
| Primary leaves complete | 2,922 |
| Geographic checks | not run |

The 64,030 VIN figure is how far oldest-year-first enumeration had reached. It
is not a VIN cap.

## Why it stopped

Leaf `year_2025_make_006_model_009` (2025 Chevrolet Silverado 3500) returned
HTTP 200 with five vehicles. Four rows used parent model `Silverado 3500`. One
row used `Silverado 3500 HD Chassis Cab`. Those names share native model id
497. The make probe had already split them as an overlap cluster (`count_excess`
2). `parse_search_capture` treated the sibling parent-model name as a filter
violation, recorded `ValueError: schema_failure`, and stopped the invocation.
The parser's actual message was discarded. 539 declared leaves, the remaining
2025–2027 work, geographic checks and closing discovery never ran.

Four earlier primary leaves were already `pagination_unstable` and isolated:

| Query | Outcome |
|---|---|
| `year_2021_make_010_model_007` | pagination_unstable |
| `year_2023_make_016_model_003` | pagination_unstable |
| `year_2024_make_006_model_005` | pagination_unstable |
| `year_2024_make_035_model_000` | pagination_unstable |

## What this day is not

September 20 r2 stopped near 61,181 VINs on a transport error, a different
failure. September 20 r3 finished at 79,934 VINs because 2025 Chevrolet was
collected as a whole make, not split. September 21 never collected: the evening
preview was blocked by a short remaining window.

This date cannot be resumed. Later collection needs a new local date. Missing
is not sold. Residuals remain unreconciled inventory differences.
