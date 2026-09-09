# Audit the Carvana research workflow

The software can retain observations, verify declared query coverage, track VINs and
show explicit quarterly assumption scenarios. It does not yet support a credible
national daily-sales estimate. The daily pilot has one September 8 baseline and
five later page checks; the older broader trials cover one morning. Neither is a
multi-day sales panel. See the [page study](status_validation_20260908.md).

## Reading order

Use the root `.venv` kernel. Open the vehicle notebooks in order, starting with a fresh
kernel. All default runs are offline and read-only.

1. **00_source_walkthrough**: small synthetic schema example, then a real retained
   browser capture, native records and normalized fields.
2. **10_carvana_inventory**: source parsing and validation, primary ZIP versus overlap
   audits, retained query attempts, candidate coverage and visible capacity arithmetic.
3. **20_carvana_history_analysis**: daily population and windows, snapshot counts,
   visible VIN comparison, saved page evidence, candidates and next steps.
4. **21_carvana_intraday_reference**: the earlier report/SQL selection, source lineage,
   matched intraday comparisons, native statuses and broader coverage audits.
5. **30_carvana_sales_expectations**: quarter calendar and evidence cutoff, unavailable
   sales estimate, dated inputs, optional analyst scenario, synthetic revision bridge
   and an explicit new-directory export.

Notebook 21's intraday selection is separate from the daily `AS_OF` in 20 and 30.
Set the daily cutoff deliberately. Never infer a daily series from intraday repeats.

## Active code map

All module paths below are under `vehicle/src/vehicle_tracker/`.

| Stage | Entry points | Inputs, outputs and side effects |
|---|---|---|
| Request plan | `search_plan.validate_plan`; `cycles.cycle_config` | Explicit query IDs, filters, ZIP, location setting, date/window and limits; validated scope fingerprint |
| Daily collection | `scripts/collect_carvana_search.py`; `cycles.collect_cycle` | Preview by default; `--live` permits sequential requests and new experiment files. Persistent budget and one owner across resumes |
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

In notebook 20, the selected query report points to retained pages and their hashes.
The source trace displays a native record before normalization, then its capture and
observation in SQLite. `AUDIT_VIN` selects one VIN in those explicitly chosen runs.
The final lineage table joins observations to captures by `capture_id` and query runs
by `run_id`, showing source path, report path, actual observation clock and original/
current parser hashes. A visible outer join calculates an asking-price difference;
missing native fields remain unknown. The in-memory conflicting/incomplete example
blocks comparison. Those observations cannot establish a sale.

Worked retained example: VIN `5YJ3E1ET5RF828714`, listing `4710782`, appears in
the selected Tesla Model 3 2024 reports at 11:32:58.875154 and 12:14:13.197536 UTC
on September 8. Both asking prices are $44,990 and both native pending flags are
false, giving a visible matched asking-price change of $0. The source paths shown
by the notebook are under `20260908_mvp1000_verified/tesla_model3_2024/raw/` and
`20260908_history_trial/repeat/tesla_model3_2024/raw/`. Two intraday observations
of this VIN establish neither a daily absence nor a sale; notebook 30 leaves the
actual sales estimate unavailable.

For daily analysis, only selected complete cycles can establish absence. A vehicle
moving between partitions is retained through the whole-plan VIN union. Duplicate
listing/VIN relationships block ambiguous interpretation. Missing calendar days and
partial cycles break absence streaks. A returning VIN creates a reappearance event
when it is observed, never retroactively at an earlier cutoff. Its cause is unknown.

Read `history.py` column constants and [the metric dictionary](metric_dictionary.md)
alongside these DataFrames. Source observations have a run/capture/retailer/listing
key; daily events use cycle/retailer/VIN. Asking prices and changes are USD, activity
counts are vehicles or events as labeled, durations are seconds/hours, clocks are UTC.
Query membership preserves overlap and ZIP-dependent values; never sum ZIP totals.

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

## Preview now; live pilot only as a separate deliberate action

Run from `C:\Users\Sean\VscProjects\researchOS`. These exact example parameters
describe September 9, 07:00–13:00 New York time. For another date, change the date,
timestamps and experiment name together. The destination is a NEW
`vehicle/data/experiments/carvana-audit-20260909` directory. Nothing is scheduled.

```powershell
$cycleArgs = @(
  '--plan', 'vehicle/config/carvana_full_candidate_20260908.json',
  '--experiment', 'carvana-audit-20260909',
  '--cycle-date', '2026-09-09', '--timezone', 'America/New_York',
  '--window-start', '2026-09-09T07:00:00-04:00',
  '--window-end', '2026-09-09T13:00:00-04:00',
  '--max-requests', '6000', '--max-seconds', '21600'
)
# PREVIEW ONLY: no requests and no writes.
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_search.py @cycleArgs
```

Later live command, **not executed by this implementation**:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_search.py @cycleArgs --live
```

At least three seconds separate request starts. The persistent 6,000-request/six-hour
budget is a ceiling, not a completeness promise. Access/challenge/rate-limit failures
stop the run. Invalid or drifting pages retain an incomplete diagnostic; never loosen
checks to declare success. Ambiguous interrupted requests remain counted and blocked.
Use [the daily guide](daily_cycles.md) for explicit same-window recovery and imports.

Pilot success means every requested query reconciles, no ambiguous identities remain,
and actual captures fit the declared window under the persistent budgets. The existing
951-query candidate still needs category/population reconciliation. Finishing its
queries proves declared-plan completion only. Notebook 10 shows unsupported/unattempted
partitions and retained facet residuals; changing categories require a new reviewed
plan, not an assumption that a finite manifest remains exhaustive.

Repeat fresh cycles prospectively before evaluating sales: inspect event examples,
quantify within-window drift and between-scan losses, audit primary ZIP overlap,
establish inventory/marketplace semantics, then compare matched periods with sourced
vendor and company data. Freeze a candidate conversion before testing a later period
not used to tune it. Quarterly agreement cannot validate daily transaction timing.

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
