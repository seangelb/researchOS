# Full-inventory terminal outcome: September 20, 2026 (year-first r3)

The year-first sweep under
`vehicle/data/experiments/carvana_full_inventory_years_r3/2026-09-20`
finished at **2026-09-20T22:32:32Z** with status `collection_finished`. There is
no `access_stop.json`, the durable ledger has `pending_request=false`, and the
automatic offline export succeeded. This is a strong **partial** finished run, not
a reconciled full-inventory baseline and not a sales day.

The runner exited **1** because `declared_collection_complete` stayed false.
`primary_queries_complete` and `primary_scope_reconciled` are also false.

## Counts and geographic limits

| Field | Value |
|---|---|
| Requests | 5,946 / 6,000 |
| Opening native total | 80,981 |
| Closing native total | 80,815 |
| Primary observed VINs | 79,934 |
| Opening residual | 1,047 |
| Closing residual | 881 |
| Discovery complete | true |
| Geographic membership stable | true (8 declared checks) |
| Duplicate primary memberships | 0 |

The opening-to-closing native decline of **166** is a sequential count move, not a
transaction or sales figure. Residuals across the sweep are unreconciled inventory
differences, not missing sales.

Geographic checks completed with empty `additional_vins` and
`primary_vins_not_seen` on every declared sample. That supports stable membership
for the sampled ZIP checks only; it does not prove national completeness.

Export publication clock (analysis manifest `created_at`):
**2026-09-20T22:32:31.841869+00:00**. Use that or a later aware UTC cutoff for
Notebook 25 history reads.

## Eleven incomplete primary leaves

Coverage ledger `leaf_complete=false` for **11** primary leaves:

### Nine `pagination_unstable` (blocked; partial rows retained)

| Query | Scope | Native total | Verified rows |
|---|---|---:|---:|
| `year_2021_make_017_model_003` | 2021 Jeep Grand Cherokee | 232 | 192 |
| `year_2023_make_006_model_012` | 2023 Chevrolet Traverse | 105 | 72 |
| `year_2023_make_032_model_003` | 2023 Tesla Model Y | 209 | 120 |
| `year_2024_make_006_all` | 2024 Chevrolet (whole make) | 936 | 696 |
| `year_2024_make_009_model_003` | 2024 Ford Escape | 43 | 24 |
| `year_2025_make_004_model_003` | 2025 Buick Envista | 46 | 24 |
| `year_2025_make_006_all` | 2025 Chevrolet (whole make) | 988 | 432 |
| `year_2025_make_016_model_000` | 2025 Jeep Compass | 269 | 216 |
| `year_2026_make_012_model_007` | 2026 Hyundai Santa Fe | 104 | 72 |

Earlier accepted pages on these queries remain positive evidence. Deduplicating
failed pages would not prove omitted inventory.

### Two empty-model unverified contexts (zero inventory; complete_query)

| Query | Context status | Scope | Native total |
|---|---|---|---:|
| `year_2023_make_013_model_006` | `empty_model_context_unavailable` | 2023 Hyundai Kona N | 0 |
| `year_2026_make_011_model_009` | `empty_model_context_unavailable` | 2026 Honda Prologue | 0 |

These admit **no** vehicle rows and are not evidence of absence. Year-tail
`empty_make_context_unavailable` probes completed separately and are not among
these eleven primary incompletes.

## Sales path (what this day cannot do)

Manifest interpretation: *Observed primary make/model inventory; retain query gaps
and ZIP diagnostics. Missing is not sold.*

`vehicle_tracker.catalog_history.observed_catalog_history` returns VIN history and
first-seen cohorts only. It produces **no absence or sales classification**. A
single incomplete full-inventory day cannot establish daily sales.

To move toward daily sales capture:

1. Collect a **second** fresh year-first day (new local date, same strategy, VPN
   off) aiming for `primary_queries_complete` / comparable leaf completeness.
2. Only then apply Notebook 20 comparable-history / absence rules on compatible
   scopes (or a reviewed whole-population reconciliation if partitions drift).
3. Keep native Sold, pending, asking-price change, and transaction-confirmed sale
   separate (Notebooks 22 / 24). Companywide sales estimates stay withheld until
   coverage and labeling support them (`full_inventory_goal.md`).

## Notebook 25 inputs

```text
EXPORT = vehicle/data/experiments/carvana_full_inventory_years_r3/2026-09-20/analysis
HISTORY_AS_OF = 2026-09-20T22:32:31.841869+00:00
CATALOG_EXPORTS = [EXPORT]
LEGACY_SOURCES = []
RETAINED_MANIFESTS = []
```

Interpret first-seen cohorts as positive sightings and first-known clocks only.
Do not treat VINs absent from older panel history as inventory exits.

## First-seen history (offline, this export only)

With `HISTORY_AS_OF = 2026-09-20T22:32:31.841869+00:00` and `CATALOG_EXPORTS` pointing
at this export alone, `observed_catalog_history` returned:

| Result | Count |
|---|---:|
| VIN history rows | 79,934 |
| Source memberships | 79,934 |
| First-seen cohorts | 1 (this capture/publication) |

Observation clocks span roughly **17:30–22:29 UTC** on 2026-09-20. Analysis
availability is the export publication clock above. These are positive sightings
and first-known clocks only—not absences, exits, or daily sales.
