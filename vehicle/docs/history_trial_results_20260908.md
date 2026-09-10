# September 8 inventory-history results

The bounded trial reached 10,019 distinct Carvana listing IDs and VINs. The history
database, notebook and derived tables are ready for review. This establishes a useful
inventory-observation workflow; it does not establish national inventory or daily sales.

## Saved evidence and tables

Paths below are relative to the repository root.

| Artifact | Contents |
|---|---|
| `vehicle/data/analysis/20260908_history/history.sqlite` | 192 query attempts, 637 captures, 12,820 observation rows; historical union of 10,808 listing IDs and VINs |
| `vehicle/data/analysis/20260908_history/tables_01/` | Nine CSVs: query/collection coverage, inventory by query, broader observed listings/mix, comparison checks, listing changes, change summary and selected-current mix |
| `vehicle/data/analysis/20260908_history/trial_summary.json` | Exact counts, request phases, intervals and scope flags |
| `vehicle/data/analysis/20260908_history/parent_zip_audit.csv` | Parent/child and ZIP identity reconciliation |
| `vehicle/data/analysis/20260908_history/facet_reconciliation.csv` | 30 retained make/year facet checks with source paths |
| `vehicle/data/analysis/20260908_history/candidate_partition_coverage.csv` | Every candidate partition, including incomplete and unattempted entries |
| `vehicle/data/experiments/20260908_history_trial/` | New immutable raw projections, query databases/reports, explicit manifests and aggregate request ledger |

The historical union is not one contemporaneous inventory count. Comparisons and
broader-sample tables select explicit query reports. Asking prices, transport costs,
native status codes and capture windows remain separate.

## What changed in the matched intraday cohort

The seven repeated Tesla Model 3 year queries were complete with matching settings:
694 earlier listings versus 693 later. There were 693 observed on both sides,
one not observed later, zero matched asking-price changes and 17 native pending/lock
changes. The missing listing is ID `4678187`, VIN `5YJ3E1EA4MF081629`; source references
remain available through the stored capture IDs and the notebook's unusual-row table.
These are intraday observations, not confirmed orders, sales or daily estimates.

The small Tesla Model 3 / 2024–2025 parent query and child union each contain 69
listing IDs. The matched 08542/08540 ZIPs have intersection 69, union 69 and zero
incremental nearby-ZIP IDs. The reviewer found no VIN conflicts or asking-price
differences, but three ZIP-dependent transport charges differ and remain separate.

## Trial capacity and coverage

The broader trial saved 10,019 unique IDs/VINs in 500 requests over 1,500.754134
seconds (25 minutes). Its explicit 261-query plan has 158 complete queries, four
incomplete attempts and 99 unattempted queries. Three attempts stopped on changed
totals or repeated identities; the fourth stopped at the sample target. All admitted
rows and failed-page evidence remain retained. No validation was weakened to reach
the target.

All phases together used 576 requests: repeat 34, discovery/probe 2, partition
facets 30, audits 10 and broader collection 500. Every response was HTTP 200.
Minimum measured spacing was 3.000079 seconds; first-to-last request span was
2,326.662621 seconds (38 minutes 47 seconds), inside the 600-request/60-minute limits.
No further live request was made after the target. No paid solver, personal cookies,
dependency installation or scheduler was used.

The retained broad response reported 80,576 results. Its 40 make counts sum to that
number, and its native year range is 2010–2027. All 30 probed make/year model-facet
sums reconcile at their respective capture times. These checks do not prove that
unknown/out-of-range categories, alternative inventory types or all national retail
vehicles are represented. Facet observations and enumeration happen at different
times; the site can change during a run.

The saved broader sample delivered about 24,033 listings/hour under these particular
queries. A simple 24-per-page lower bound for 80,576 results is 3,358 requests, or
about 2.8 hours of three-second pacing alone. Full enumeration adds small/empty
partitions, failures, reconciliation and temporal drift. A reliable full daily cycle
has not yet been demonstrated.

## Open and run

Use the existing root environment; no reinstall is needed. Open the vehicle notebooks
in order: `00_source_walkthrough.ipynb`, `10_carvana_inventory.ipynb`, then
`20_carvana_history_analysis.ipynb`. Notebook 20 shows requests, native fields,
normalization, coverage, explicit SQL, outer joins, calculations, tables, plots,
source references and a blocked in-memory example. It performs no collection or writes.

```powershell
powershell -File scripts/start_jupyter.ps1
.\.venv\Scripts\python.exe -B vehicle/scripts/build_carvana_history.py
.\.venv\Scripts\python.exe -B vehicle/scripts/build_carvana_history.py --write
.\.venv\Scripts\python.exe -B vehicle/scripts/build_carvana_history.py --export vehicle/data/analysis/20260908_history/tables_02
```

The first history command previews only. Import is explicit and idempotent. Export
requires a new destination; `tables_01` already contains this trial's saved tables.

## Later enumeration: explicit instructions, not an active job

`vehicle/config/carvana_full_candidate_20260908.json` contains 951 candidate queries,
including the verified model subdivisions. The remaining manifest has 793 queries:
the full candidate set minus the trial's 158 completed queries. Completing that
remaining set would extend historical coverage, not make the old captures fresh.

Preview a future fresh full-candidate attempt:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_search.py --plan vehicle/config/carvana_full_candidate_20260908.json --experiment full-candidate-01 --target-listings 1000000 --max-requests 600 --max-seconds 3600
```

When deliberately authorizing that later live attempt, use the same command with
`--live`. The target is deliberately above the reported universe so it does not
silently stop at a small sample. The existing 600-request invocation limit means
one attempt cannot enumerate the whole candidate manifest. Complete unchanged
queries can be resumed under later separately authorized budgets:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_search.py --plan vehicle/config/carvana_full_candidate_20260908.json --experiment full-candidate-02 --target-listings 1000000 --max-requests 600 --max-seconds 3600 --resume-from vehicle/data/experiments/full-candidate-01/run_report.json --live
```

Use another new experiment name for each invocation. Do not resume after an access
block without resolving access first. Resumed captures retain their original clocks;
they do not automatically become today's inventory. To fill only this historical
trial's gaps, substitute `carvana_remaining_20260908.json` in a new explicit attempt.
Refresh/review the candidate facet evidence before relying on it on another date.

The next measurement milestone is repeated comparable daily coverage and a tested
definition for persistent disappearance, cancellations and reappearance. Sales
estimation remains separate; no synthetic sales series has been added here.

## Verification and implementation size

- Full offline suite: **509 passed, 10 plotting warnings in 125.76 seconds**.
- Final explicit-zero export correction: **4 focused tests passed in 1.10 seconds**.
- All **13 active notebooks passed** guarded execution; notebook 20 ran all 11 code
  cells. Network, CSV exports and writable SQLite connections were blocked.
- Tests reproduced first-query interruption and recovered-count problems before
  fixing them, plus identity/coverage gaps. Independent-review findings were also
  reproduced before repair. Incomplete, zero, absent-source, native-type, invalid-window
  and deliberately invalid gaming-approval paths are covered offline.
- The read-only reviewer resolved all five findings, rechecked the repairs, verified
  all 637 imported capture hashes and matched CSV identities/counts to SQLite.
- Preflight verified all 2,001 baseline hashes. Final checks preserve 1,995 files
  byte-for-byte; the six existing-file changes are deliberate vehicle implementation,
  documentation, ignore rules and notebook-checker updates. All existing raw files,
  31 databases, notebooks, gaming approval bindings and the vendor workbook are unchanged.
- HEAD and branch remain unchanged; `git diff --check` passes. Everything is local
  and uncommitted; no push, deployment or historical-source rewrite occurred.

There are 327 added executable module/command lines and 212 executable lines in the
new notebook: 539 total, excluding tests and explanations. This exceeds the approximate
400-line target by 139 lines because SQL, pandas joins, scope checks, diagnostics and
interpretation remain visible in the notebook. The reusable modules stay below 400
lines; no framework or hidden notebook engine was added. Trial-only workspace scripts
are separate from the reusable collector.
