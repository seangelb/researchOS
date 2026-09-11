# Carvana: one notebook-first workflow

**Collect with `run_carvana_daily.py`. Analyze inventory in notebook 20. Inspect
native website-status checks in notebook 22.** Python, pandas, SQLite and retained
JSON remain the whole design; there is no new service or scheduler.

## 1. Preview, then explicitly collect

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

## 2. Open the existing analysis notebooks

Start Jupyter with `powershell -File scripts/start_jupyter.ps1`, using the shared
root `.venv`. Normal **Run All is offline and read-only**.

| Notebook | Use it for |
| --- | --- |
| [20: inventory and vehicle review](notebooks/20_carvana_history_analysis.ipynb) | Population/dates/completeness → native inventory → matched VIN/price changes → composition and reductions by days since first observed → SQL/source trace |
| [22: website-status evidence](notebooks/22_carvana_sale_status_validation.ipynb) | Cohort/repeat coverage → latest native statuses → first Sold/repeated Sold/reappearances → next checks → one evidence timeline; historical studies follow as optional sections |
| [10: how the POST works](notebooks/10_carvana_inventory.ipynb) | Learn the request, response fields, pagination and parsing |
| [21: earlier intraday reference](notebooks/21_carvana_intraday_reference.ipynb) | Inspect the retained morning experiment; it is separate from daily history |
| [00: source introduction](notebooks/00_source_walkthrough.ipynb) | Existing learning examples and one retained source |
| [30: sales expectations](notebooks/30_carvana_sales_expectations.ipynb) | Optional dated assumptions/scenarios; it does not establish measured sales |

Notebook 20 now selects its input settings/cutoff/cycles once. Short explanations
precede the joins and calculations. The mean-price table separates common-vehicle
repricing from inventory composition; if prices are missing, the residual is
explicitly labelled composition **plus price coverage**. The accounting sequence
is visible, with a reconciliation to the actual total change.

Notebook 22 keeps full audit tables available but leads with a compact status and
attention view. Its frozen-cohort JSON captures remain separate from notebook 20's
canonical manual check/review histories. Failed checks do not refresh native status
or invent a between-check change. Saving a real page check remains an explicit
manual action: [recording guide](docs/listing_checks.md), [pilot capture/import guide](docs/sale_pilot.md).

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
