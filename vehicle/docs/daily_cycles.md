# Daily Carvana observations and VIN history

For everyday use, start with [the daily inventory command](daily_inventory.md). It wraps the existing cycle, import and analysis functions into one explicit operation. This guide documents the lower-level controls. No sales conversion rule has been calibrated.

## Follow the data

`search request → retained page → query report → cycle.json → SQLite observations → notebook 20`

`cycles.py` keeps the daily window, full query plan, cumulative budget and attempt references. `history.py` imports retained evidence into the existing three tables. `events.py` calculates daily VIN diagnostics from explicitly selected cycles. Parsers, transport, storage and analysis remain separate small functions.

The earlier September 8 retained history spans one morning. It is still useful for source inspection and intraday comparisons and cannot be relabeled as several daily cycles. Notebook 20 now reads the daily command's explicit date register by default; deliberate cycle/cutoff overrides remain available. Its separately labeled synthetic example demonstrates relisting, missing coverage and reappearance in memory.

## Preview a candidate daily cycle

Run from `C:\Users\Sean\VscProjects\researchOS` using the existing environment. Choose a date/window when the computer will be awake. These September 9 values are examples; change the date, experiment name and timestamps together for a later day.

```powershell
$cycleArgs = @(
  '--plan', 'vehicle/config/carvana_full_candidate_20260908.json',
  '--experiment', 'carvana-20260909',
  '--cycle-date', '2026-09-09',
  '--timezone', 'America/New_York',
  '--window-start', '2026-09-09T07:00:00-04:00',
  '--window-end', '2026-09-09T13:00:00-04:00',
  '--max-requests', '6000',
  '--max-seconds', '21600'
)
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_search.py @cycleArgs
```

This command previews only. It prints the exact plan path, query count, scope fingerprint, destination, window and limits. Inspect the plan file before collecting. The 951-query candidate manifest reflects retained September 8 discovery; completing it would establish coverage of that declared plan, not independently prove the entire national retail population. Refresh/reconcile its categories before making broader claims. Keep one primary ZIP/context and audit regional overlap separately; never sum ZIP counts or silently choose conflicting observations.

After deliberately choosing the source, window, new destination and limits, the live form is:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_search.py @cycleArgs --live
```

Daily mode always attempts the full declared plan. It has no sample-target success shortcut. Every query must reconcile and have capture times within the target window. A 6,000-request budget is an explicit upper bound, not a promise of complete coverage or an instruction to consume it. The software caps a configured daily cycle at 10,000 requests and six hours, with at least three seconds between request starts.

The capture result is assembled over an interval; it is not an instantaneous census. Displayed totals or rankings can change. Such queries remain incomplete and retain their evidence. No weaker count check, invented cursor or larger undocumented page size is used.

## Recover within the same cycle

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_search.py @cycleArgs --resume-cycle
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_search.py @cycleArgs --resume-cycle --live
```

The first line previews; the second explicitly continues. Use the same experiment, date, query plan, window and limits. Complete fresh queries are reused; incomplete queries restart at page one in a new attempt directory. Previous raw files and query databases are preserved. The aggregate request counter, minimum spacing and elapsed-time budget survive restarts. Downtime consumes the cycle's time allowance. An OS lock prevents simultaneous owners.

A completed cycle can be recovered after its window or budget expires because no new requests are needed. An incomplete cycle cannot obtain more requests by restarting or changing its limits. A transport/access block remains stopped. A request interrupted before its outcome was safely recorded remains counted and blocks automatic retry. Inspect and resolve the access/uncertainty issue before planning another explicitly bounded cycle. There is no automatic retry loop, proxy rotation, solver spending or scheduler.

For the next day, use a new cycle date, matching timestamps and a new experiment name. Do not resume the previous day's captures into it. Sample collection and the older `--resume-from` path remain available for exploratory work; they do not establish daily coverage. `--full-plan` also exists for a bounded non-daily plan, under the older 600-request/60-minute invocation cap.

## Import and save diagnostic tables explicitly

Cycle import requires an explicit analysis database; it does not default to an existing historical database. Preview first:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/build_carvana_history.py --cycle-report vehicle/data/experiments/carvana-20260909/cycle.json --database vehicle/data/analysis/carvana_daily/history.sqlite
```

Add `--write` to import the retained complete and partial query attempts. Repeating the import is idempotent. The cycle report retains requested-but-unattempted partitions even though they have no observation rows. No source request is made by this command.

Repeat `--cycle-report` to select several explicit daily cycles. To save tables, add `--export` with a new directory, for example `vehicle/data/analysis/carvana_daily/tables_01`. Exports include `daily_coverage.csv`, `daily_observations.csv`, `vin_events.csv` and `daily_counts.csv`. An ambiguous event comparison saves the coverage/observation diagnostics and returns a blocked result. Existing export directories are refused.

The read-only loader selects only the requested query runs in SQLite, verifies their stored rows/captures against retained evidence and excludes damaged or stale evidence from daily presence. It does not repair the database. Missing imports, stale captures and incomplete collection remain visible in coverage. Freshness is checked against the target window; event tables show the actual capture window separately. Actual observation time and evidence availability are distinct.

## Open the notebook

Open `00_source_walkthrough.ipynb`, `10_carvana_inventory.ipynb`, then `20_carvana_history_analysis.ipynb`. Select the root `.venv` kernel. To launch Jupyter from the repository root:

```powershell
powershell -File scripts/start_jupyter.ps1
```

Notebook 20 reads the operating daily register by default. It shows collection
quality, a visible VIN merge, page evidence, and a selected VIN's history, with
optional manual check recording. Run All is offline/read-only. Use
`AS_OF_OVERRIDE` for a historical cutoff; `CYCLE_REPORTS_OVERRIDE` and
`DAILY_DATABASE_OVERRIDE` deliberately select other retained evidence. The older
intraday walkthrough is in notebook 21. See [daily_inventory.md](daily_inventory.md)
for the normal daily workflow; the lower-level commands here are for explicit
collection/recovery cases, not a second competing daily process.

Continue in notebook 30 for quarter calendars and explicitly dated scenarios.
`read_cycle_history(..., as_of=...)` excludes later attempts and observations before
selecting evidence. This is current-code reanalysis of evidence available by a cutoff;
reproducing an old published vintage also requires its code, selected sources and
assumption versions. An explicit new-folder export in notebook 30 records their hashes.
The command-line history export remains a selected-current-evidence diagnostic;
use the notebooks for explicit as-of research. See [the audit guide](audit_guide.md).

## Interpret the tables

| Output | Meaning |
|---|---|
| Observed VINs | Distinct retailer/VIN identities actually observed in the selected cycle. A partial cycle's zero observations are not zero inventory. |
| First observed | First appearance in retained history; not necessarily the original listing date. |
| Relisted | A known VIN appears under a changed native listing ID. Native IDs and prior source references remain visible. |
| Reappeared | A previously absent VIN returns. This does not identify cancellation versus return versus another cause. |
| Pending started/cleared | Changes in the known native pending flag; not confirmed orders or cancellations. |
| First/persistent absence | Diagnostics requiring complete comparable daily coverage. The persistent event is emitted once per absence episode when its threshold is reached. |
| Gap/incomplete cycle | Breaks a consecutive absence streak. Absence counts remain missing for incomplete cycles; timing uncertainty is visible. |
| Asking-price change | Difference between observed asking prices for a known VIN; never a transaction price. Missing prices remain missing. |
| estimated_sales | Missing. No calibrated sales rule exists yet. |

Absence rows retain the last presence's source fields, marked `observed_in_cycle=False` and `last_observed_cycle_id`. Do not mistake those old prices/statuses for a fresh observation. Unknown native fields remain unknown. Simultaneous duplicate VIN/listing identities or changed populations block event interpretation rather than selecting a newest row.

Events carry `rule_version` and `available_at`. A threshold reached after later observations is not backdated as information available on the first missing day. A seven-day diagnostic parameter is not automatically justified by a return policy.

## What remains to prove

Collect the unchanged pilot on consecutive actual local dates and follow missing
and changed VINs, plus controls. Establish status persistence, reappearances and
timing uncertainty before evaluating sales rules. Broader coverage needs a separate
history and tests of partitions, regional context and collection drift. Preserve
forecast vintages and revisions. Offline tests do not establish daily sales accuracy
or national coverage.
