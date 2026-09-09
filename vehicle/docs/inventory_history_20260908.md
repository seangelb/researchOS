# Inventory history: evidence, tables and interpretation

This milestone keeps the existing Python, pandas, SQLite and Jupyter design.
Open `00_source_walkthrough`, then `10_carvana_inventory`, then
`20_carvana_history_analysis`. Notebook 00 is synthetic teaching data; 10 explains
the real collection source; 20 traces retained API evidence into a separate history
database and explicit comparisons. Existing notebook cells are preserved.

## Schema and units

The schema was kept to three ordinary tables. Collection membership is expressed
by existing plan reports referencing query reports; it does not require duplicated
observations or another table.

| Table | One row represents | Important columns |
|---|---|---|
| `query_runs` | One query attempt, including partial/failed attempts | Query-report path/hash, canonical request context, completeness and reason, reported/admitted row counts, actual observation interval, invocation interval |
| `captures` | One retained page projection or failure record | SHA-256 capture identity, query ID, page, UTC timestamp, parse status/count/error, retained path |
| `observations` | One capture plus retailer/listing ID | VIN, UTC observation time, native make/model/year, odometer miles, USD asking price, native fields, capture/query IDs |

`context_json` retains endpoint, filters, ZIP, location-feature setting and sort.
`purchase_pending` and `on_demand` retain native booleans as SQLite integers/null;
`vehicle_lock_type` and `inventory_type` retain native integer codes/null.
`purchase_type` and `parent_model` preserve native names. `transport_cost_usd` is
separate from `asking_price_usd`. Missing values are null; no order/sale meaning is
invented for native flags or codes. `availability_native` remains missing for the
API source rather than receiving fabricated schema availability.

`normalizer_sha256` fingerprints the history, search and shared Carvana parser files
used for that import. `original_normalizer_sha256`, when present in these experiments,
is the collector's **search-file fingerprint only**, not proof of a known historical
version of every shared parser. Older unrecorded versions stay null. Re-importing an
identical report does not rewrite the prior import/version or add rows. Corrected
parser replays require a separately named, reviewed analysis build.

Source projections keep the public fields used in analysis, not the entire HTTP
body. Query reports also retain response-byte hashes, HTTP status, timings and
projection hashes. Existing raw files and experiment databases remain unchanged.

## Coverage and comparisons

Select explicit previous/current query reports in
`config/carvana_history_example.json`; the command never picks the newest capture.
Every requested query must exist and be complete. Filters, ZIP, feature setting and
sort must match, without dropping missing partitions. Actual capture windows must
be ordered and not overlap. Invocation times alone cannot make resumed data fresh.
Duplicate listing memberships or VIN aliases require scope/identity review.

The notebook displays both input tables, an outer join and its indicator, and
`asking_price_after - asking_price_before` for matched identities. It checks that
visible formula against `classify_changes`. A listing seen only before is
`not_observed`, never automatically sold. Listings seen only later are
`first_observed` in available history or `reappearing` when older retained evidence
exists. Matched VIN conflicts suppress price/native-status calculations. Missing
status values do not become false.

The example comparison is **intraday**. It provides no daily sales series. Daily
measurement needs repeated comparable daily coverage, cancellations/reappearances,
scope stability and a separately tested event definition.

## Import and export

All paths below are relative to the repository root. Preview is the default:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/build_carvana_history.py
.\.venv\Scripts\python.exe -B vehicle/scripts/build_carvana_history.py --write
.\.venv\Scripts\python.exe -B vehicle/scripts/build_carvana_history.py --export vehicle/data/analysis/20260908_history/tables_02
```

Choose a new export directory. The exports include query coverage, collection-plan
coverage (including unattempted partitions), distinct identities by query, comparison
checks, listing changes, a change summary and selected-current-cohort price/mix.
Coverage tables remain available when comparisons are blocked. SQLite is read-only
in notebooks and export reads. Import is explicit and idempotent; unrelated databases
are refused. Zero observed rows in a failed/partial query are not zero inventory.

## Recovery and operational limits

The manifest and initial plan checkpoint are saved before requests. JSON checkpoints
are replaced atomically. Recovery verifies complete child source hashes, native
completeness and database contents even if the root checkpoint is missing or behind.
Incomplete attempts restart at page one in a new folder. Retained pages from different
capture windows are never spliced into one complete query. Existing completed reports
and databases must still match their recorded hashes before ordinary resume.

This task's live trial uses a durable aggregate ledger at
`vehicle/data/experiments/20260908_history_trial/task_budget.json`: at most 600 public
requests, sequentially spaced at least three seconds, and one 60-minute deadline
shared across repeat, facet discovery, audits and broader collection. No personal
cookies, solver, retries after access failure, paid service or scheduler is used.

The broader manifest uses observed make/year facets, subdividing larger queries by
observed parent model. Zero residual between published facet counts is a consistency
check, not proof of an exhaustive national population. Unattempted makes/years,
unknown/out-of-range vehicles, filter semantics and changes during enumeration still
limit coverage. ZIP unions are identity unions; ZIP-dependent prices are not merged.

See [the measured trial results and next commands](history_trial_results_20260908.md)
for saved counts, verification and remaining manifests. A later enumeration requires
its own explicit live command; nothing is scheduled automatically.
