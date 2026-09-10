# Vehicle research

Start with [the daily inventory guide](docs/daily_inventory.md). One explicit command
collects a fresh seven-query Tesla Model 3 pilot, imports its observations into the
daily SQLite history and saves dated tables. Notebook 20 displays the registered
daily inventory, visible VIN comparisons, saved page checks and the next step.
Notebook 21 preserves the earlier intraday source-to-database walkthrough.

From the repository root (omit `--live` to preview without requests or writes):

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --live
```

Repeat once per day at approximately the same time. A first snapshot is a baseline;
comparable consecutive days reveal additions and disappearances. Sale candidates
require review, and sales estimates remain unavailable. Neither national coverage
nor an observation-to-sales conversion is validated. The [audit guide](docs/audit_guide.md)
traces the broader implementation; the [cycle guide](docs/daily_cycles.md) explains
the lower-level collection/recovery commands.

Save real page checks and analyst reviews using the existing command's
`--record-check` and `--record-review` options. [The recording guide](docs/listing_checks.md)
provides templates and explains the queue. Notebook 20 and daily exports read the
same saved histories; later corrections preserve earlier cutoff results.

The inventory-history milestone now includes a **10,019-vehicle broader sample**
and a read-only notebook tracing source observations into SQLite and comparisons.
See [the results, validation and next commands](docs/history_trial_results_20260908.md).

The September 8 MVP saved **1,006 unique Carvana listing IDs and VINs in 47 public
search requests, taking 138.94 seconds**. Seven Tesla Model 3 year partitions
completed; Toyota/2022 stopped at the sample target. This proves a bounded collection
path. It does not establish national inventory, unattended daily reliability, or sales.

## Open the notebooks

Sale-candidate review is now available in notebook 20: qualifying absence episodes,
reappearances, evidence gaps and explicitly selected analyst outcomes. See the
[sales-tracking guide](docs/sales_tracking.md) for the review format and prepared
seven-query daily pilot. No calibrated daily sales estimate is supplied.

1. `notebooks/00_source_walkthrough.ipynb`: synthetic learning example followed by a real retained source.
2. `notebooks/10_carvana_inventory.ipynb`: the working public-search scrape explained step by step,
   request and field mappings, retained-source validation, query coverage, and
   identity/missing-value checks on the saved operational sample.

3. `notebooks/20_carvana_history_analysis.ipynb`: population/time, daily inventory,
   a visible VIN merge, saved page-status evidence, candidates and next steps.
   The [first page study](docs/status_validation_20260908.md) checked five real
   listings; no explicit Sold status or second daily snapshot was observed.

4. `notebooks/21_carvana_intraday_reference.ipynb`: the retained morning experiment,
   source-to-SQL trace, price/status comparison and broader coverage audits.

5. `notebooks/30_carvana_sales_expectations.ipynb`: explicit cutoff, full calendar,
   missing dates, dated input checks, visible scenario arithmetic and revision bridge.
   Synthetic examples are labeled. Collection, import and export are disabled by default.

Use the shared root `.venv`. Notebooks run offline and read-only. If ignored local
experiment data is absent, they report that rather than inventing observations.

## Earlier sample collection and recovery

From the researchOS root, preview without network requests or writes:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_search.py --experiment daily-pilot-01
```

Run once with explicit opt-in and a new destination:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_search.py --experiment daily-pilot-01 --target-listings 1000 --max-requests 120 --max-seconds 1200 --live
```

`config/carvana_search_queries.json` lists the sample's explicit make/model/year
queries. It is not a national roster. Query counts are unioned by retailer/listing ID;
VINs remain available for aliases and conflicts. Whole batches are saved, so the
target can be exceeded slightly. A target stop is partial, even if every saved row is valid.

To continue the identical plan after an interruption, choose another new folder:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_search.py --experiment daily-pilot-02 --resume-from vehicle/data/experiments/daily-pilot-01/run_report.json --live
```

Completed partitions are referenced only after checking their report, database and
retained-source hashes. Incomplete partitions restart at page one in the new folder.
Previous pages remain intact. Resuming can span different observation windows and
does not create an instantaneous daily census. Start a fresh experiment for a new day.

Every run retains source projections, SQLite observations and one plan checkpoint
plus per-query reports. Failures and unfinished queries remain visible. Existing
destinations are refused. There are no automatic retries, concurrent requests or
schedulers. Requests are spaced by at least three seconds. Defaults remain 120
requests / 20 minutes; explicit controls allow at most 600 / 60 minutes per invocation.

## ZIP context and source meaning

The normal website requests `LocationBasedPrefiltering`. A controlled September 8
comparison returned 80,585 broad results without that feature, 71,015 with it for
08542, and 58,993 with it for 90210. The broad response without it was again 80,585.
This explains the main observed scope difference; it does not establish ownership,
retail eligibility or exhaustive national coverage of the broader response.

Each query can set `"location_filter": true` to use the observed feature. False is
the explicit default and the saved MVP setting. Preserve that choice when comparing
runs. Do not add regional counts. Use a primary discovery context and audit additional
ZIPs against complete matched cohorts, recording intersection and incremental IDs.

Native pending/lock fields are retained, without an invented order or sale mapping.
Missing listings are not sales; asking prices are not transaction prices. Review
[the metric dictionary](docs/metric_dictionary.md) and
[the September 8 evaluation](docs/collection_evaluation_20260908.md).

## Access approach

The primary collector sends the public search request observed through normal Chrome
to Carvana's search endpoint, using no browser cookies or authentication. It succeeded
in the retained trials. A 403, challenge, 429 or other failed response stops the run.
Future availability is not guaranteed; no paid solver or personal profile is used.

The older headed-browser command `scripts/collect_carvana.py` remains an experimental
fallback. Fresh headed installed Chrome loaded inventory, but a dedicated persistent
profile restart received a Cloudflare challenge. Browser pagination also exposed stale
JSON-LD. That route has not demonstrated unattended reliability.

The authorized optional installation added Playwright 1.62.0, greenlet 3.5.5 and pyee
13.0.1. Existing packages were not upgraded; no browser binaries were downloaded.
The fresh-profile test used installed Chrome 152.0.7977.82. Dedicated profiles belong
under ignored `vehicle/.browser_profiles/`; never copy a personal Chrome profile.

## Follow the implementation

- `carvana.py`: pure schema/selected-DOM parsing and shared field validation.
- `search.py`: pure public-response projection/parser and sequential page collection.
- `search_plan.py`: explicit partitions, identity union and safe query-level restart.
- `collect.py`: shared request/time budget and the earlier browser experiment.
- `cycles.py`: explicit daily windows, persistent budgets, root-report import and selected read-only history.
- `events.py`: pure pandas VIN relisting, reappearance and absence diagnostics; no sales inference.
- `sales.py`: read-only sale-candidate episodes, dated analyst reviews and daily review counts.
- `daily.py`: explicit date register, daily tables and bounded collection/import/export orchestration.
- `checks.py`: saved check/review validation, cutoff selection and follow-up priorities; explicit CSV recording.
- `readiness.py`: retained per-query attempts, missing partitions and request estimates.
- `expectations.py`: quarter calendar, dated inputs, arithmetic revision checks and explicit new-folder exports.
- `storage.py`: immutable retention and atomic per-page SQLite storage; analysis is read-only.
- `coverage.py`: earlier browser-query and matched-period diagnostics.
- `vendor_audit.py`: read-only workbook inspection. Paid solver transport remains disabled.

## Verify offline

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py
git diff --check
```

## Inventory history and deliberate exports

`config/carvana_history_example.json` names the analysis database, imported reports
and explicit previous/current comparisons. It imports complete and partial attempts;
partial observations remain exploratory. Running the command without flags previews
the selection without network access or writes.

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/build_carvana_history.py
.\.venv\Scripts\python.exe -B vehicle/scripts/build_carvana_history.py --write
.\.venv\Scripts\python.exe -B vehicle/scripts/build_carvana_history.py --export vehicle/data/analysis/20260908_history/tables_02
```

Choose a new export folder each time. Query/capture/observation grains and native
units are described in [the history guide](docs/inventory_history_20260908.md).
The derived database is separate from all original experiment databases. Re-import
checks retained hashes and avoids duplicate observations. Changed imported evidence
requires a new reviewed analysis build, not automatic replacement.

Complete comparable scopes require every selected query, matching filters/ZIP/
location setting/sort, unique identities, and ordered actual capture windows.
The invocation clock is shown separately. A resumed complete query can be reused
for historical analysis but is not automatically a fresh daily observation.

Checkpoints are published atomically. The explicit manifest is saved before the
first request. Missing/behind plan checkpoints can recover complete child attempts;
incomplete attempts restart at page one in a new directory. No cross-window page
splicing, newest-conflict selection, national census or sales inference is performed.
