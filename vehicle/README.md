# Carvana: one notebook-first workflow

Start with the offline notebook review below. Python, pandas, SQLite and retained
JSON remain the whole design. Normal **Run All reads existing local evidence**.

## Notebook review guide

Open Jupyter with `powershell -File scripts/start_jupyter.ps1` from the repository
root, using its `.venv`. Review **00 → 10 → 20 → 22 → 23 → 30**; **21 is optional**.
Run each notebook from the top. The names in backticks below are stable cell IDs
and output variables, so they can also be found by searching the notebook source.

For a common evidence vintage, use `AS_OF_OVERRIDE = '2026-09-11T16:41:25.820875+00:00'`
before running the settings in 20, 22 and 23. Notebook 23 now defaults to the later
browser validation cutoff; that same override reproduces its earlier review. Notebook 30 deliberately starts with no inventory reports; its introduction
shows how to select the retained September 11 report and matching database.
For Notebook 20's September 8/9 reconciliation below, replay
`AS_OF_OVERRIDE = '2026-09-09T12:00:00Z'`. At the common September 11 cutoff, the
missing September 10 correctly prevents a daily comparison; Notebook 23 labels
the longer endpoint interval separately.

| Notebook and question | First cells / main outputs | Concrete trace | Safe settings; meaning of missing results |
| --- | --- | --- | --- |
| [00: source walkthrough](notebooks/00_source_walkthrough.ipynb) — how does a saved source become a row? | `vehicle-walkthrough-1`, then `real-source-example`; native and normalized fields | Ford listing `4474057`, VIN `3FA6P0D94ER264351`, asking price $14,590 | Choose another existing retained source; a missing field stays unknown. The later CSV is explicitly synthetic. |
| [10: one inventory snapshot](notebooks/10_carvana_inventory.ipynb) — what did this query observe? | `inventory-setup`, `inventory-query`, `inventory-load`, `inventory-source`, `inventory-diagnostics` | Follow a vehicle from the saved Tesla query through raw fields and normalized columns | Change retained query/report paths together; incomplete pages cannot establish the full population. The long POST lesson is optional. |
| [20: daily history](notebooks/20_carvana_history_analysis.ipynb) — which dates/queries, VINs and asking prices changed? | `daily-operating-view` settings, `daily-cycle-data`, `daily-operating-tables`, `daily-query-quality`; then changes, matched prices and source trace | September 8/9: 674 + 8 entries − 1 exit = 681 VINs; trace VIN `5YJ3E1EAXPF590130` | Change cutoff, retained tracking config or inspected VIN; a missing date/partial query is unavailable coverage, not zero inventory or sales. |
| [22: native status validation](notebooks/22_carvana_sale_status_validation.ipynb) — what happened within the frozen cohort? | `pilot-settings`, `pilot-coverage`, `pilot-source-rows`, `pilot-results`, `prospective-followup-plan`, `pilot-evidence-timeline` | VIN `5YJ3E1EA7NF288274`, listing `4567173`: native Available-to-Sold interval and repeat clock | Change cutoff, example VIN, display flag or preview cap (1–12); failed/unvisited checks do not establish status or refresh native evidence. Preserve cohort files. |
| [23: daily sales research](notebooks/23_carvana_daily_sales_research.ipynb) — how do proxies, revisions and validation checks compare? | `research-settings`, `methods`, `estimate-table`, `pending-and-revisions`, `exit-validation-results`, `followup-selection-table` | Exit VIN `5YJ3E1EA0MF058297`: last inventory row → native Sold capture → outstanding follow-up | Change cutoff, example VINs, seed or preview counts. Unknown transaction outcomes and unmatured absence estimates stay unavailable. Older/vendor studies follow the main workflow. |
| [30: quarterly scenarios](notebooks/30_carvana_sales_expectations.ipynb) — what assumptions and evidence would a quarterly estimate require? | `quarter-setup`, `quarter-observations`, `quarter-inputs`, `quarter-synthetic-arithmetic` | Explicit synthetic scenario: 63 → 93 units, +30; inspect assumptions before arithmetic | Change dated analyst assumptions or matching retained inputs; an empty input/estimate is a visible evidence gap, not zero sales. Leave export settings empty for review. |
| [21: optional intraday reference](notebooks/21_carvana_intraday_reference.ipynb) — what did the earlier experiment show? | `history-1`, `history-3`, then matched comparison/source cells | The saved same-day `baseline`/`repeat` experiment | Inspect existing historical config and evidence only; an invalid comparison is not an inventory change. Do not append it to daily history. |

The reference evidence contains **one qualifying frozen-cohort transition** and
**ten separate ranked exit checks with later native Sold labels**. Neither source
confirms economic transactions. Notebook 23 retains exits through later sweeps,
shows their completion/recheck clocks, and previews priority conflicts, due repeats,
random new exits and random controls. Only genuine random frames receive inclusion
probabilities. A few controls cannot measure a reliable missed-event rate; there
is no company-sales multiplier. The bounded validation follow-up completes with a
matched check at least seven days after its current-target baseline. A late first
repeat can meet that minimum; a missed intermediate 24-hour check stays unobserved.
Failures and replacement targets keep work outstanding. Frozen-cohort reminders
remain separate.

Notebook 23 also contains the optional [Clarity method experiment](docs/clarity_method_experiment.md).
Review `clarity-method-settings` through `clarity-workbooks`: documented order rules,
exit-rule replay, the stopped anonymous HTTP trial, and the supplied workbook vintages.
Run All reads retained evidence and makes no live requests.

For new detail checks, use the [browser batch guide](docs/browser_detail_batches.md).
Notebook 23's `browser-batch-review` shows saved observations and unresolved visits
before the next-check preview. The workflow uses connected Chrome and saves each
visit separately; it is browser-assisted, with explicit recovery after interruption.

## Collection commands (separate from notebook review)

Run from `C:\Users\Sean\VscProjects\researchOS`:

```powershell
# Read-only preview: settings, destination and any interrupted work.
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py

# Explicit live action, only when you intend a fresh daily observation.
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --live
```

The live command collects public search pages, retains evidence, imports observations
into daily SQLite history, registers the date, and saves derived tables. The search
endpoint is observed and undocumented, not an officially supported API. Access
failures stop collection; the collector does not switch methods to evade a block.

The unchanged population is **Tesla Model 3, years 2020-2026, seven queries,
ZIP 08542, location prefiltering omitted**. It is not national inventory or a
representative sample. Use approximately the same local time on each intended day;
a missed day stays missing. No live collection was performed during simplification.

### Experimental four-model preview

The separate [experimental configuration](config/carvana_four_model_tracking.json)
reuses the frozen September 10 proposal: 16 year queries covering Tesla Model 3
(2020–2026), Chevrolet Equinox, Ford Escape and Toyota Corolla (2022–2024 each).
From the repository root, this verified command is offline and read-only:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --config vehicle/config/carvana_four_model_tracking.json
```

It prints all queries, the 120-request/900-second limits, and separate capture,
SQLite, register, export, check and review destinations. Existing search spacing
remains at least three seconds. The proposal's retained counts are dated planning
evidence, not current counts or guaranteed request requirements. The seven-query
operating configuration and the 33-VIN page-check cohort remain unchanged.
The configuration is ready for a separately authorized bounded trial; its live
completeness and access reliability have not been tested together.

The [September 11 review package](docs/applied_workflow_review_20260911.md) lists
exact review cells, all 16 query IDs, resolved destinations, cutoff comparisons
and validation results. It also documents the intentional `On Hold\nMM:SS`
interpretation correction: pending purchase activity, never a completed sale.

## 3. Follow the files when something fails

All paths below are relative to `vehicle/`.

| File/table | What to inspect |
| --- | --- |
| `config/carvana_daily_tracking.json` and `carvana_daily_pilot.json` | Destinations and exact query population |
| `data/experiments/carvana_daily/<date>/cycle.json` | Window, scope, request budget and overall coverage |
| `attempt_0001/<query>/run_report.json` and `attempts/*.json` inside that cycle | The request/page that failed, its stage and recorded outcome |
| `response_sources/` and `raw/` inside a query | Safe source JSON when available, then selected capture projections; these are different representations |
| Each query's `vehicle.sqlite` | Original collector page/observation storage; not the database notebook 20 normally opens |
| `data/analysis/carvana_daily/history.sqlite` | Daily `query_runs`, `captures`, `observations`, replayed from retained query evidence |
| `data/analysis/carvana_daily/cycles.json` | Explicit date-to-cycle selections and evidence hashes |
| `data/analysis/carvana_daily/tables/` | Derived CSV exports; unfinished exports remain `.partial` |
| `data/experiments/carvana_sale_signals/` | Notebook 22's retained detail-check JSON and import manifests |

The preview lists recoverable/unregistered work. Read the specific failure before
retrying. `--import-cycle "<exact cycle.json path>"` deliberately imports/registers
retained evidence without requests; `--refresh` only regenerates derived daily tables.
Both write outputs and must be intentional. Neither makes incomplete evidence complete.
See [the recovery handoff](docs/collector_storage_handoff.md) for interrupted writes,
access stops, stale windows, hash conflicts and the limits of body replay.

## One retained vehicle, end to end

For the retained September 8/9 comparison, notebook 20 traces VIN
**`5YJ3E1EA0RF763544`**, listing **`4712757`** (unless you select another vehicle):

1. Its [retained capture](data/experiments/carvana_daily/2026-09-08/attempt_0001/tesla_model3_2024/raw/0042a4573188db9fa4a2f0738e4e47735174cfcc302b1fdc5ccda072117033f6.json)
   has `price.total = 34990` and `isPurchasePending = false`, observed at
   `2026-09-09T01:44:11.074146+00:00` (September 8 locally). This older file is a
   selected projection; the full response was not retained.
2. `search.py` projects/parses the public fields; pure native validation now lives
   in `carvana.py`, alongside the other Carvana parsing rules. `storage.py` retains
   the capture and saves page observations. The daily import replays those captures
   through `history.py` into SQLite. Existing identity/time/coverage checks remain.
3. In SQLite, `observations.asking_price_usd = 34990` and `purchase_pending = 0`.
   `capture_id` joins to `captures.source_path`, and the notebook verifies that file's
   SHA-256. SQLite 0 here represents the source boolean false, not a missing value.
4. The visible retailer/VIN merge finds this listing on both dates at $34,990, so
   its asking-price change is **$0**. Across the whole retained pair, the mean rose
   **$72.83**, while all 673 matched VINs had unchanged asking prices. All prices were
   known: that observed mean movement comes from composition under the displayed
   breakdown, not common-vehicle repricing. These are dated observations, not a fresh scrape.

## Other scripts are specialist tools, not competing daily commands

| Script | Scope |
| --- | --- |
| `collect_carvana_search.py` | Advanced bounded search experiments; a generic sample does **not** register/import the operating daily history |
| `build_carvana_history.py` | Explicit offline import/rebuild/export to a selected analysis destination |
| `collect_carvana.py` | Earlier browser experiment; not the normal collector or an automatic fallback after access failure |
| `import_carvana_sale_pilot.py` | Preview/explicit save of manually obtained detail captures for notebook 22; no daily inventory DB write |
| `benchmark_history.py` | Existing optional offline benchmark, not an inventory collector |

For example, `collect_carvana_search.py --experiment search-sample-preview` without
`--live` only previews a separate sample. For an actual daily collection use the
command at the top. Nothing is deleted or automatically migrated between these paths.
[Daily guide](docs/daily_inventory.md) | [Cycle controls](docs/daily_cycles.md)
| [History/import guide](docs/inventory_history_20260908.md) | [Audit guide](docs/audit_guide.md)

Earlier trials remain available in the [collection decision](docs/collection_method_decision.md),
[history trial report](docs/history_trial_results_20260908.md),
[September 8 evaluation](docs/collection_evaluation_20260908.md),
[original five-page study](docs/status_validation_20260908.md), and
[previous MVP handoff](docs/overnight_mvp_handoff.md). Their numbers and software/access
findings describe those trials. In particular, the broader 10,019-vehicle admitted
sample is not 10,019 complete-query vehicles; the reviewed complete subset was 9,107.

## Limits and offline verification

Missing, pending, unavailable and Sold-labelled remain different observations.
A website Sold label does not establish a completed economic sale or an exact sale
date. Asking prices are not transaction prices. Stable query counts do not prove
stable membership during a sweep; incomplete coverage stays visible. The project
has not established national coverage or dependable unattended daily access.
See [metric definitions](docs/metric_dictionary.md) and [sales review rules](docs/sales_tracking.md).

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider vehicle/tests
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py
```

Retained captures, databases, cohort selections and historical exports are preserved.
No schema migration, collection, commit or push is part of this simplification.
