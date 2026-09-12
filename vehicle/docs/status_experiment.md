# Carvana: notebook review and operating guide

Learn **00 → 10 → 11 → 20 → 24 → 30**. In routine use, review **20 and 24**;
open 30 for quarterly assumptions and forecasts. Notebooks 21, 22 and 23 are
intraday, original frozen-cohort and specialist references.

Normal **Restart Kernel and Run All is offline and read-only**. Notebook 11's
live function is defined but never invoked by default. Collection is a separate
deliberate action; the proposed operating dates are not a scheduler.

For the research question, populations, row keys, table contracts and function
flow, start with the [code walkthrough](code_walkthrough.md). This document is
the operating reference for exact settings, commands, authorization, recovery
and the retained historical evidence below.

## Settings, real examples and exercises

| Notebook | Ordinary settings and output | One short exercise |
| --- | --- | --- |
| **00** | Select `retained_path` in the real-source section. Native rows and `normalized_retained` show how a source becomes columns. The later starter CSV is synthetic. | Trace Ford listing **4474057**, VIN **3FA6P0D94ER264351**, from source price to normalized **$14,590**. Identify one missing field without replacing it with zero. |
| **10** | Select `PLAN_PATH` and `RUN_PATH` together. Inspect the request, per-query coverage and `observations`. An incomplete query does not establish its full population. | Find one observed VIN, its asking price and native pending field; locate the source path and explain its query coverage. |
| **11** | Edit `MAKE`, `MODEL`, `MODEL_YEAR`, `ZIP_CODE`, `LOCATION_FILTER`; select `SELECTED_REPORT`, `ANALYSIS_CUTOFF`, `EXAMPLE_VIN`. `summary` and normalized rows describe the chosen retained observation. | Predict a request change, deliberately fetch one page if desired, then reload its evidence and reproduce a calculation offline. Detailed instructions follow. |
| **20** | Edit `TRACKING_CONFIG`, `ANALYSIS_CUTOFF`, `EXAMPLE_IDENTITY`; select `RETAINED_CYCLES` / `RETAINED_DATABASE` for an explicit offline comparison. `None` cutoff means current retained-evidence review; a timestamp gives historical replay. `daily_review` explains eligibility before changes/prices. | Replay the September 11/12 pair below and reconcile **706 + 64 − 49 = 721**. Explain why disappearances are not sales. |
| **24** | Edit `STUDY`, `AS_OF`, `EXTRA_CYCLE_PATHS`, `EXAMPLE_VIN`. The existing **32-VIN study remains the default**. Its stored population and endpoints govern the outputs. | Compare resolved-outcome Sold proportion with full-selected missing bounds. Explain how unvisited observations change what can be concluded. |
| **30** | Edit `AS_OF`, `QUARTER`, `CYCLE_REPORTS`, `ANALYST_INPUTS`, `FORECAST_PATHS`, `REPORTED_RESULTS`. Empty defaults deliberately produce unavailable estimates. | Work through the labelled synthetic arithmetic, then inspect why an empty actual forecast table cannot supply an accuracy estimate. |

All main notebooks show their question, selected source/population, observation
clocks/cutoff, output meanings and missing-result interpretation near the start.
The `*_OVERRIDE` names remain advanced test/export hooks. Change ordinary settings
for normal use, and restart the kernel when switching populations.

**22 is the original frozen 7+26-VIN legacy-capture reference.** It reads the
original pilot captures, not every later native check of those VINs. Newer browser
batches and research-pass evidence are reviewed in 23/24; they do not update 22
automatically. Its cohort definitions and observation rules remain unchanged. **23 is specialist research:** its earlier
14-day collection/seven-day follow-up proposal is historical. The current
operating instruction is the fixed seven-date proposal linked below.

## Notebook 11: one-page teaching lab

Start with `lab-settings` → `lab-request-preview` → `lab-load` →
`lab-inspect-rows` → `lab-field-mapping` → `lab-vin-source` →
`lab-vin-calculation`. These cells need no network and create no files.

The default retained example is Chevrolet Tahoe 2023, ZIP **08542**, location
filtering disabled, from the completed scale trial. One real vehicle is VIN
**1GNSCRKD6PR500004**, listing **4714618**, observed at
**2026-09-12T14:15:02.843171Z**: 45,854 miles and a **$54,590 asking price**.
Its native `isPurchasePending` is false and `vehiclePurchaseType` is `Purchasable`.
Source `previousPrice` is not a separately collected prior snapshot. The source
mapping and calculation cells let you inspect these distinctions directly.
Set `EXAMPLE_VIN = '1GNSCRKD6PR500004'` to reproduce this example; the empty
default selects the first retained VIN, which is a different vehicle.

The preview displays the exact endpoint, payload, page size, filters and location
setting. ZIP is search/delivery context, not proof of the vehicle's physical location.
Changing a setting only changes the preview; it does not collect anything.

To deliberately collect one page, run this in a separate cell after reviewing the
preview and the function definition:

```python
live_report_path = fetch_one_page()
if live_report_path is not None:
    SELECTED_REPORT = live_report_path
    loaded = load_capture(SELECTED_REPORT)
    display(loaded['summary'].T, loaded['pages'])
```

Each call requests fresh confirmation for that exact search, request limit,
isolated destination and remaining teaching allowance. There is **at most one
inventory request per action**, no automatic pagination/retry, and **three attempted
requests per teaching session**. Failed attempts count. Repeated calls and reruns
of the settings cell share the same in-memory allowance and pacing for at most
one hour after session creation. Restarting
the kernel loses that memory; it does not authorize another allowance. These
teaching limits are separate from operating and detail-check budgets.

The existing collector saves each action beneath
`vehicle/data/experiments/carvana_notebook_lab/` in a new destination. It never
imports teaching captures into daily history. Its `summary` shows counts, query
completion and database path; `pages` shows HTTP outcomes, elapsed time, query/page
information, observation timestamps and saved source paths. Together they explain
whether the query is complete or only one page was observed. Access, identity,
pagination and storage failures remain visible and use the existing stop behavior.

To reload, put the saved `run_report.json` path in `SELECTED_REPORT`, then rerun
`lab-load` and the inspection/calculation cells. Or call:

```python
saved = load_capture(SELECTED_REPORT, as_of=ANALYSIS_CUTOFF)
```

For an explicit comparison, set `COMPARE_BEFORE` and `COMPARE_AFTER` to two
separate reports for the **same query**, then run `lab-compare`. It shows shared
VINs, asking-price differences and collection intervals. A VIN missing from one
sampled page is not established as missing from the full inventory.

The three exercises support request understanding, source inspection and offline
reproduction. Pending flags, native Sold labels, missing listings and estimated
economic sales remain different quantities.

## Notebook 20: population, inventory and asking prices

`daily-analyst-settings` selects the population. The original seven-query Tesla
panel remains the default. To select the broader panel, use the commented
`TRACKING_CONFIG` path to `config/proposed_daily_tracking.json` under the scale
acceptance directory. Its configuration, register and database travel together.
The first collected date will establish this panel's operating baseline; the
10,000-VIN scale trial is not imported as a daily baseline.

Read `daily-cycle-data`, `interrupted-health-data`, `daily-query-quality`, then
`daily-review-summary`. Missing/partial dates block daily comparison. The three
trial-audit rows are separate capacity observations, gated by audit completion
time and source hashes. Their overlapping VIN counts are never added together.

The calculation sequence is `daily-vin-analysis` (identity checks),
`daily-comparison-eligibility`, `daily-vin-join`, `daily-asking-prices`,
`daily-observed-age-prices`, `daily-observed-age-clock`,
`daily-observed-age-summary`, then the composition/price bridge.

For the retained September 11/12 Tesla comparison, uncomment the pair in
Notebook 20's ordinary settings, or replace those settings with:

```python
RETAINED_CYCLES = [
    ROOT / 'data/experiments/carvana_daily/2026-09-11/cycle.json',
    ROOT / 'data/experiments/sales_method_20260912/inventory/cycle.json',
]
RETAINED_DATABASE = None
ANALYSIS_CUTOFF = '2026-09-12T14:22:50.159714+00:00'
```

`RETAINED_DATABASE = None` replays source files without import; an existing matching
SQLite path verifies its imported rows instead. This selection excludes operating
check/review ledgers. Restore `RETAINED_CYCLES = None` and restart the kernel to
return to the registered history. An empty list deliberately selects no cycles.

The compatible pair has **706 beginning VINs, 64 additions, 49 disappearances,
721 ending VINs**. Of **657 matched VINs**, five asking prices fell, none rose,
and 652 were unchanged. Reduction frequency is **5/657 = 0.761%** and median
reduction among the five cuts is **$600**. Whole-inventory mean price rose from
$28,371.30 to $28,474.60 while matched-VIN mean price fell $3.96: composition and
repricing are different calculations. Days since first observed is not true listing age.

`daily-source-trace` → `daily-source-identity` → `daily-source-calculation` shows
one retained source through SQLite (or explicit source-only replay) to a formula.
VIN **5YJ3E1EA0RF732049**, listing **4736254**, is **$33,590** in both selected
dates, so its price change is **$0**. Inspect the hash, native pending field and
observation clocks before interpreting the result.

The separate `planned-operating-inputs` → `planned-date-review` table keeps every
planned date, actual date/start, complete/planned query counts, observed VINs,
requests, elapsed time, failures/recovery, exports and measured operator minutes.
Unfinished attempt totals stay unknown; completed-report counts are only a lower
bound. Recovery labels describe the current register, not historical registration.
`OPERATOR_MINUTES_BY_DATE = {}` means unmeasured, not zero. Only enter measured
active labor. Scheduled dates are not observations; missed dates stay missing.

## Notebook 24: selected study, outcomes and missingness

The default [frozen study](../data/experiments/sales_method_20260912/study/plan.json)
contains 32 VINs. Its original sample, endpoint rules and observation windows are
preserved. Select another existing study explicitly through `STUDY`; a missing
path fails clearly rather than silently using the default.

Read `status-sources`, `status-stock-flow`, `status-frozen-sample`,
`status-outcomes-tests`, `status-repeat-windows`, `status-vehicle-walkthrough`.
The retained September 12 evidence shows:

| Arm | Selected | Matched | Sold | Available | Unavailable | Unvisited | Full-selected Sold bounds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Pending exits | 10 | 5 | 4 | 1 | 0 | 5 | 40–90% |
| Non-pending exits | 2 | 2 | 0 | 1 | 1 | 0 | 0–50% |
| Still-listed pending | 10 | 4 | 0 | 4 | 0 | 6 | 0–60% |
| Still-listed non-pending | 10 | 4 | 0 | 4 | 0 | 6 | 0–60% |

Fifteen matched checks resolve 14 binary endpoints; 17 VINs are unvisited and one
native Unavailable remains unresolved. Among resolved pending exits, 4/5 = 80%
has a conditional Wilson 95% interval of 37.55–96.38%. It does not correct selective
missingness and is not transaction accuracy. Repeated checks do not add vehicles,
and later favorable evidence cannot replace the predefined first qualifying endpoint.

The primary window is September 12 08:08:46.075745 EDT to September 14 at the same
time; the repeat window is September 19 to September 21 at those times. Use the
actual-clock feasibility/admission report before any already-authorized follow-up.
The local pilot cap remains **12 attempted browser starts per rolling 24 hours**,
including unresolved reservations. It is not an established Carvana access limit.
Future studies must fit remaining starts, VIN spacing, both windows and operator
capacity. Defaults of 1.5 minutes/check and 20 minutes/day are explicit assumptions
until measured; infeasible future freezes are rejected. Missed endpoints remain missing.

## Sales proxies and quarterly evaluation

Keep observed inventory exits, persistent absence, native Sold evidence,
reappearances and experimental estimates separate. The operational baseline uses
exits between complete consecutive dates. The existing three-complete-absent-date
rule and native Available-to-Sold rule remain separate comparisons. Seven inventory
dates do not mature every seven-day absence event; later evidence is still needed.
Uniform allocation over an observation interval is an assumption, not a known
transaction or delivery date. Neither this selected panel nor a frozen cohort
supports national scaling without coverage/calibration evidence.

Notebook 30 keeps **first publication** separate from **revision availability**:
`first_published_at` controls prospective forecast eligibility; `available_at`
selects which reported-result revision was known by the cutoff. A post-release
forecast stays ineligible against later revisions; unknown first publication
stays unresolved. Keep every forecast vintage and its frozen source/model copies.

```text
signed error = forecast units − reported units
absolute percentage error = 100 × abs(forecast units − reported units) / reported units
```

Empty actual inputs/forecasts are intentional defaults. Labelled synthetic
arithmetic teaches the formulas without creating an observed result. The
**1–3% quarterly target requires future prospective validation**.

## Preview, operate, recover, review

Run from `C:\Users\Sean\VscProjects\researchOS` with the repository `.venv`.
These exact previews make no requests and write no research evidence:

```powershell
$validationConfig = 'vehicle/data/experiments/mvp_completion_20260912/scale_acceptance_20260912/config/proposed_daily_tracking.json'
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --config $validationConfig
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_details.py preview --config $validationConfig --limit 6 --controls 2
```

The detail preview displays the selected population, source references, cutoff,
available capacity and targets. With no broader daily baseline it explains that
selection is unavailable. After two complete consecutive dates exist, it can
preview exits and controls from that population. It does not mix the Tesla history
or resize frozen studies. Omitting `--config` preserves the original detail-planner
population. Prepared plans bind their configuration/scope/sources; recording or
recovering an existing reserved visit remains separate from planning a new one.
The preserved proposal document predates this `--config` integration; its former
detail-planner limitation is now resolved by the command above.

After comparable dates exist and the preview fits the available budget, prepare
the reviewed broader-panel selection in a fresh shared batch directory:

```powershell
$batch = 'vehicle/data/experiments/carvana_detail_batches/CHOOSE_A_NEW_NAME'
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_details.py prepare --config $validationConfig --batch $batch --limit 6 --controls 2 --minutes 45
```

This saves the plan without visiting pages. Continue with the existing
[reserve, Chrome capture, record and recovery steps](browser_detail_batches.md#read-and-save-one-page-at-a-time)
using the same `$batch`. The current absence of a broader baseline blocks preparation.

For each **separately authorized** planned daily observation:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --config $validationConfig --live
```

The preserved [seven-date plan](../data/experiments/mvp_completion_20260912/scale_acceptance_20260912/proposed_seven_date_validation.md)
uses all **101 query definitions in their original order**, ZIP 08542, location
filtering off, America/New_York time and **600 requests/60 minutes per date**.
It completes the declared scope rather than stopping at a moving 10,000-VIN target.
Proposed dates are **September 13–19, 2026, at 09:00 ±15 minutes**. These are
unexecuted/unapproved future cycles, not a scheduler. The CLI uses the actual
invocation date/window and does not enforce the proposed schedule itself.

On failure, rerun the same preview and inspect the retained cycle/page records.
After reviewing the exact finished evidence and population, intentional offline
recovery uses the existing commands:

```powershell
$validationCycle = '<exact retained cycle.json path>'
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --config $validationConfig --import-cycle $validationCycle
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --config $validationConfig --refresh
```

Import/register and refresh write derived history/exports but make no requests.
They do not complete missing pages. Never delete a failed destination, reset its
budget or extend its window. The daily wrapper has no `--resume` flag. Different
evidence cannot replace a registered date. See the [existing recovery guide](collector_storage_handoff.md)
and [browser reserve/capture/record/recovery instructions](browser_detail_batches.md).

Inspect the default frozen study without browser requests:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_status_experiment.py report --study vehicle/data/experiments/sales_method_20260912/study/plan.json
```

For an intended follow-up of that frozen study, review its actual-clock window
and capacity report first. Prepare a fresh batch using its fixed membership:

```powershell
$study = 'vehicle/data/experiments/sales_method_20260912/study/plan.json'
$batch = 'vehicle/data/experiments/carvana_detail_batches/CHOOSE_A_NEW_STUDY_BATCH'
.\.venv\Scripts\python.exe -B vehicle/scripts/run_status_experiment.py batch --study $study --wave primary --limit 12 --destination $batch
```

Use `--wave repeat` only during the frozen repeat window. This saves a batch;
it visits no pages. Continue with the browser guide's `next`/capture/`record`
steps using that same `$batch`. The study command preserves selected VINs and
deadlines, defers recent or unresolved visits, and checks shared capacity. A
blocked or unfinished study stays unresolved; the fresh inventory sampler above
does not replace its frozen selection. See [Notebook 24](../notebooks/24_carvana_status_experiment.ipynb)
for follow-up scoring and `EXTRA_CYCLE_PATHS`.

For reproducible Notebook 20 source-only exports, use two exact compatible cycle
paths and a new destination; repeat with the same cutoff in another new directory:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/export_sales_proxy.py --notebook 20 --as-of '<fixed timezone-aware cutoff>' --cycle-report '<first cycle.json>' --cycle-report '<second cycle.json>' --destination '<new export directory>'
```

Always choose `--notebook` explicitly: use `20` for inventory, `24` for the selected
study, `30` for quarterly review or `23` for the specialist proxy reference. The
exporter reruns the **saved notebook file**, not the current Jupyter kernel. Save
ordinary-setting edits first and pass the same cutoff. In particular, 24 reads
the saved `STUDY` selection; 30 reads its saved cycle, assumption, forecast and
reported-result selections. Empty selections remain empty exports. Notebook 20's
explicit `--cycle-report` arguments take precedence over its saved selection.

Preview a forecast with explicit analyst assumptions, evidence and model files:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/freeze_quarter_forecast.py --input '<analyst.json>' --sources '<input.json>' '<model.json>' --destination '<NEW-forecast.json>'
```

Adding `--save` records the reviewed current-clock vintage without overwriting an
earlier one.

## Evidence and verification

The earlier [scale reconciliation](../data/experiments/mvp_completion_20260912/scale_acceptance_20260912/reconciliation.json)
verified **10,000 distinct VINs, 466 requests, 101 complete queries, 45 unattempted,
zero partial/blocked queries, 23.44 minutes and a 3.006595-second minimum gap**.
There were no retries or interventions; active human labor was unmeasured.
This demonstrates one-run capacity, not sustained daily reliability. The
[original failed trial](../data/experiments/mvp_completion_20260912/trial_reconciliation.json)
and [401-VIN repair acceptance](../data/experiments/mvp_completion_20260912/acceptance_20260912/reconciliation_immutable.json)
remain preserved and separate. Full original HTTP bodies are unavailable; selected
source JSON/projections and source-to-SQLite reconciliation are retained.

The dated [September 12 completion and preservation record](../data/experiments/notebook_workflow_20260912/completion.json)
binds that development stage's tests, notebook checks and retained file hashes.
Its supporting [suite record](../data/experiments/notebook_workflow_20260912/final_vehicle_suite_03/validation.json),
[offline notebook results](../data/experiments/notebook_workflow_20260912/final_validation_02/notebooks.txt),
[export comparison](../data/experiments/notebook_workflow_20260912/final_validation_02/export_parity.json)
and [00/10/22 comparison](../data/experiments/notebook_workflow_20260912/reference_notebooks_validation.json)
remain historical evidence for their recorded source hashes. They do not certify
later source edits. The separate [Windows path and source/SQLite replay proof](../data/experiments/notebook_workflow_20260912/long_path_validation/validation_interpreted.json)
uses simulated captures; it establishes no operating baseline or live observation.

For current offline software checks, run:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider vehicle/tests
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py
```

The smallest next step is an offline notebook walkthrough, then separately
authorized operation under the unchanged seven-date plan. Record every date,
coverage/budget failure, export and measured effort; require 7/7 comparable planned
dates before claiming the operating validation passed. Status outcomes and future
quarterly earnings remain external validation milestones. Tests establish software
behavior, not sales accuracy.
