# Full-inventory terminal outcome: September 25, 2026 (adaptive r5)

The scheduled adaptive sweep under
`vehicle/data/experiments/carvana_full_inventory_years_r5/2026-09-25`
started at 12:01 AM Eastern and ended at **2026-09-25T07:39:18Z** (3:39 AM
Eastern) with status `collection_finished`. `attempt_outcome.json` records
`complete_with_gaps`. The runner then failed the offline export with
`Overlap siblings differ from declared model-id cluster` and exited 1. Later
hourly wakes skipped the date because a finished attempt already covers it.
The durable ledger has `pending_request=false` and 4,325 charged requests.
No query failed on access. This is a sequential morning snapshot, not a sales
census and not national completeness.

## Counts

| Field | Value |
|---|---|
| Requests | 4,325 / 7,000 |
| Spacing | 3 seconds |
| Opening native total | 83,639 |
| Closing native total | 83,894 |
| Primary observed VINs | 83,797 |
| Opening residual | −158 (union is 0.19% above the open) |
| Closing residual | 97 (union is 0.12% below the close) |
| Declared leaf queries | 1,130 |
| Leaves complete by union | 1,130 |
| Unverified native count | 0 |
| Cross-leaf duplicate VINs | 3, all Chevrolet Silverado 3500 name pairs |
| Geographic checks | 8 planned; Miami `year_2026_make_020` stopped `pagination_unstable` at native count 156 |

The 97-car gap to the closing total, and the 158-car excess over the opening
total, are observation-clock drift across the 3 hour 38 minute run. They are
not failed leaves. Missing is not sold.

## Why the export exited 1

Six Chevrolet leaves for 2021, 2024, and 2025 share a native model id between
`Silverado 3500` and `Silverado 3500 HD Chassis Cab`. The parser admitted those
rows. Adaptive splits did not write `model_id_overlap` on the make probe, so
the export allowed-sibling set was empty. Three VINs appear in both parent
names and are counted once in the 83,797 union:

- `1GC3KSE77SF161034` (2025 Silverado 3500 HD Regular Cab & Chassis, listing 4640881)
- `1GC3KTEYXSF242172` (2025 Silverado 3500 HD Regular Cab, listing 4603043)
- `1GC3YTE72MF303703` (2021 Silverado 3500 HD Regular Cab, listing 4762865)

The retained report, SQLite, and stop markers were not rewritten. A later
offline replay derives that cluster from the retained make-probe facet and
writes `analysis/`. Future adaptive probes store the cluster at collection time.
