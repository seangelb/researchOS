# Audit the Carvana research workflow

Use the [operating guide](status_experiment.md) for the current reading order,
ordinary settings, exact commands and retained examples. This page is the compact
source/code reference. Default notebook execution remains offline and read-only.
The original dated findings and test records remain linked below; their counts
describe those snapshots, not the current retained dataset.

## Active code map

All module paths below are under `vehicle/src/vehicle_tracker/`.

| Stage | Entry points | Inputs, outputs and side effects |
|---|---|---|
| Request plan | `search_plan.validate_plan`; `cycles.cycle_config` | Explicit query IDs, filters, ZIP, location setting, date/window and limits; validated scope fingerprint |
| Daily collection | `scripts/run_carvana_daily.py`; `cycles.collect_cycle` | Preview by default; `--live` permits sequential requests and new experiment files. Persistent budget and one owner across resumes |
| Query collection | `search.collect_search`; `collect.NavigationBudget` | Requests, pacing, validation and stop reasons; explicit injected transport in tests |
| Retained source | `search.project_response`; `storage.retain_capture`, `store_capture` | Bounded native response projection, response/projection hashes and per-page database; original fields preserved |
| Pure parsing | `search.parse_search_capture`; `carvana.parse_capture`, `parse_projection` | Retained dictionaries to normalized DataFrames; no network or writes |
| Evidence validation | `history.read_query_evidence`; `cycles.cycle_evidence` | Verify source hashes, IDs, row counts, reported totals and clocks; diagnostic completeness and references |
| History import | `history.import_reports`; `cycles.import_cycle` | Explicit database write to three existing tables; repeat imports idempotent, altered evidence rejected |
| Read-only selection | `history.read_history`; `cycles.read_cycle_history` | Explicit selected runs/cycles, SQLite `mode=ro`; cutoff excludes later evidence before selection |
| Matched intraday research | `history.comparison_checks`, `classify_changes` | Comparable scope/window checks and outer identity comparison; no inferred sales |
| Daily research | `events.vin_events`, `daily_counts` | Retailer/VIN histories, native transitions, absences and relistings; pure pandas with explicit complete-day threshold |
| Readiness | `readiness.query_readiness` | All declared queries and explicit attempts, memberships and missing diagnostics; no latest-version resolution |
| Expectations | `expectations.quarter_coverage`, `dated_input`, `revision_bridge` | Calendar gaps, dated assumptions and ordered arithmetic checks; visible pandas/calculations in notebook 30 |
| Deliberate export | `expectations.export_research` | New directory only; CSV tables plus input/code/output hashes, cutoff and assumptions |

`scripts/collect_carvana.py` and the headed-browser part of `collect.py` are older
experimental access paths. They have not demonstrated unattended reliability.
`coverage.py` supports retained browser/cohort audits; `vendor_audit.py` inspects supplied
workbooks. Paid CAPTCHA transport remains disabled. There is no automatic fallback,
background job, scheduler or new data framework.

## Follow one vehicle

Notebook 00 starts with the real retained browser sample before the synthetic CSV.
Notebook 10 selects a matched `PLAN_PATH` / `RUN_PATH` pair and exposes source fields,
normalized observations and coverage. In Notebook 20, set `EXAMPLE_IDENTITY` in
ordinary settings and follow `evidence_trace`: match retailer, VIN, listing, run and
capture, then inspect the retained source hash, clocks and asking-price calculation.
Use its commented `RETAINED_CYCLES` pair for the real September 11/12 source-only
comparison. Notebook 21 retains the older intraday source/SQLite walkthrough.

A retailer/VIN identity follows a vehicle; listing IDs remain separate native
references. Reloading a retained source does not create another observation.
Reappearance, relisting, pending, website Sold and economic sale stay distinct.

## What each output can mean

| Output | Authority |
|---|---|
| Native inventory/status/asking price | Observed within the displayed source interval and scope |
| First observation, pending change, absence, reappearance | Derived observation diagnostic; no transaction or cancellation is established |
| 2/3/7-day horizons | Alternative research assumptions, not probabilities or confidence intervals |
| Analyst conversion and remaining daily rate | Explicit dated assumptions; optional, empty by default |
| Scenario units | Exploratory arithmetic for the declared scope, clearly separate from actual estimated sales |
| Estimated retail units/sales | Unavailable: no calibrated conversion or verified national population |
| Guidance/consensus | Unavailable unless explicitly supplied with compatible period, units, scope and availability |

Cars can appear and disappear between scans. A scan assembled over several hours is
not an instantaneous census. First observed is not original listing date; first absent
is not transaction date. Marketplace inventory, returns and opaque native codes need
separate evidence. Changes in coverage cannot be called sales acceleration.

## Cutoffs, assumptions and reproducibility

`AS_OF` excludes cycles created later, later attempts and later observation windows.
The quarter calendar starts at the actual quarter boundary, so early missing dates
cannot disappear from coverage. Inputs are checked against explicit dates, scope,
units and freshness limits; duplicate input versions block the scenario. Actual sales
remain unavailable even when the optional assumption scenario can be calculated.

This is current-code reanalysis of evidence available at a cutoff. To reproduce an
old published research vintage, retain its exact selected sources, code and assumption
versions. Do not call a recalculation with today's parser the old published estimate.
An explicit export from notebook 30 hashes source reports, captures, selected database,
notebook and calculation modules, and records assumptions/cutoff and output hashes.
Existing raw evidence and prior exports are never replaced.

The visible revision bridge separates revised common dates, new dates, passage of
time and changed assumptions. It rejects changed scope/coverage. The arithmetic
example is synthetic and has no authority as a Carvana forecast.

## Preview and recover

Use the selected configuration and exact preview/import/refresh commands in the
[operating guide](status_experiment.md). The [cycle reference](daily_cycles.md)
explains lower-level same-window recovery; its older candidate-plan examples are
historical, not the current seven-date instruction. The
[collector/storage handoff](collector_storage_handoff.md) explains evidence contracts,
clocks and interrupted publication.

Declared query completion establishes the selected scope during a collection
interval. It does not establish national coverage, transaction timing or a calibrated
sale conversion. Preserve failed attempts, uncertainty and skipped dates.

## Offline verification and scale

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py
git diff --check
# Explicit slow benchmarks; all evidence and databases are temporary and synthetic.
.\.venv\Scripts\python.exe -B vehicle/scripts/benchmark_history.py --vehicles 10000 --days 1
.\.venv\Scripts\python.exe -B vehicle/scripts/benchmark_history.py --vehicles 80000 --days 1
.\.venv\Scripts\python.exe -B vehicle/scripts/benchmark_history.py --vehicles 10000 --days 30
```

The guarded notebook check blocks network, CSV exports and writable database
connections. Invalid gaming approval bindings are tested as blocked; none are renewed.
See `audit_results_20260908.md` for exact results, measured capacity and limitations.
Offline throughput does not establish live access, complete population or sales accuracy.
