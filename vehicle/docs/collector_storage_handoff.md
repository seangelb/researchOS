# Carvana collector and storage handoff

Implemented September 10, 2026 UTC against branch `codex/notebook-reliability`, HEAD `052ed0b6d5b6e9ad0aae1edcb3b283d800ccdc72`, in `C:\Users\Sean\VscProjects\researchOS`. The branch and HEAD matched the requested preflight. The worktree was already dirty; those changes and previous-stage artifacts were preserved. **No live collection, operating database migration, commit, or push was performed.**

The collector now retains safe response evidence **before** projection can fail, records each request attempt, validates reuse against retained evidence and stored rows, and makes interrupted daily work discoverable. SQLite imports can be repeated safely. Daily exports are published only after their CSVs and manifest are complete.

The architecture remains the one recommended in [the collection decision](collection_method_decision.md): the observed, undocumented search POST supplies inventory and asking prices; a small explicit Chrome cohort supplies detailed website-status evidence. This implementation does not establish reliable unattended access or national coverage.

## Start with one car and one page

```text
Explicit make/model/year query + ZIP
    -> one POST for page 1 (up to 24 vehicles)
    -> safe source JSON retained before parsing
    -> selected capture with request, observation time and source references
    -> validated pandas rows
    -> transactional per-query SQLite write
    -> query/cycle coverage checks
    -> explicit import into SQLite history
    -> cycle registration -> derived CSVs / read-only notebook
```

For example, a returned `vehicleId`, VIN and `price.total` become a retailer/listing/VIN observation and `asking_price_usd`. The price is the website's asking price in USD, not a transaction price. Mileage is in miles. Native pending, lock, purchase and inventory values remain native values; missing transport cost remains missing.

The next day is another observation even if the car and price are unchanged. Importing yesterday's same evidence twice is only one import. A missing car is an absence observation, not automatically a sale. The existing Sold-status cohorts and interpretation rules are unchanged.

## Evidence contract: three different representations

New query reports declare `evidence_contract: carvana-search-source-v1`. [search_evidence.py](../src/vehicle_tracker/search_evidence.py) is a small source-specific helper, not a new storage framework.

| Representation | What is retained | What its hash means |
|---|---|---|
| Original response content, when safely retainable | Exact `requests.Response.content`, only when the decoded document contains exclusively the supported public fields and safe values. | `response_content_sha256` hashes `Response.content`. `source_sha256` hashes the retained file. For this kind they must agree, including byte length. These can be decompressed content bytes, **not original HTTP wire bytes**. |
| Selected source JSON | Allowlisted public fields under `inventory.pagination`, `inventory.vehicles` and `userDeliveryInfo.zip5`, selected **before schema validation**. Wrong shapes, missing keys, nulls and ordinary numeric values survive for diagnosis. | Hash of the exact canonical selected JSON file, with an explicit `selected_source` kind and limitation. It does not preserve omitted fields or original serialization. |
| Selected capture and normalized rows | Existing capture projection plus request, observation clock, attempt ID and response-evidence references; then the existing pandas/SQLite columns. | The page's `source_sha256` hashes the projection/failure JSON file. It is separate from both response-content and source-JSON hashes. Normalized rows are checked by replay, not described as original source content. |

Every hash records its scope. An omitted or unavailable body is never described as reproducible from its hash. Schema failures such as a missing pagination object can now be replayed from the retained safe source even when `project_response()` never completed.

Unknown JSON fields are omitted. Unsafe strings are redacted and disqualify that response from inventory admission. Duplicate JSON keys disqualify admission and prevent an earlier hidden value from being retained as safe original content. HTML, non-200 response bodies and undecodable JSON are **not retained** under this contract; the report records their content hash/length and a replay limitation. This avoids retaining arbitrary account/challenge text, cookies or credentials. Response headers and exception messages are not copied wholesale. Numeric `Retry-After` seconds can be recorded.

The source contract is deliberately narrow. A new legitimate field or unexpected value can require a reviewed parser/allowlist change. Redacted or omitted content cannot be reconstructed later. Legacy evidence still has its original projection-only limits; old files are not rewritten.

## Clocks and provenance

| Clock | Meaning |
|---|---|
| `request_reserved_at_utc` / `request_started_at_utc` | Budget reservation and local POST start. An interrupted reservation can consume budget even if transmission cannot be proved. |
| `response_received_at_utc` / capture `captured_at_utc` | Local time after the response content was received. This is the source observation clock, not a server event or economic sale timestamp. Unknown after a transport failure. |
| `evidence_available_at_utc` | Local time after the page's retained capture was published; a conservative availability time for the retained source/capture pair. Separate from the observation clock. |
| `query_runs.imported_at_utc` | First successful import into that history database. A repeated import does not replace it. |

The source JSON is published before the capture; the recorded pair availability may therefore be later than the first source-file write. An interruption can leave a source file without a completed pair or availability record. The journal exposes that incomplete state; no timestamp is invented.

New-contract replay binds page metadata to the capture's attempt ID, response evidence, response hash/length and observation clock. Cycle cutoff checks also account for known evidence availability. Existing historical clocks are preserved. Old rows have NULL for the two newly added history columns where the clock was never recorded; read-only access supplies NULL aliases without migrating the database.

Historical `as_of` remains a reconstruction using retained evidence and today's parser, not a reconstruction of precisely what an old database/old parser displayed at that time. The new import clock does not silently change that meaning.

## Collection and coverage

The existing search endpoint, page size 24, `MostPopular` ordering, verified filter validation, checkpoints, request limits and single-writer cycle controls are reused. The active daily configuration remains **seven Tesla Model 3 queries, years 2020-2026, ZIP 08542, location prefiltering omitted, 120 requests / 1,200 seconds**. No configuration or frozen status cohort changed.

One `requests.Session` can now reuse connections across a plan. It disables environment-derived authentication/proxy settings (`trust_env=False`), clears cookies before and after every POST, disables redirects, and closes at the end. No personal browser profile or cookie is copied. This preserves credential-free requests. Connection reuse is an implementation capability, **not a measured live speedup**.

The minimum three-second spacing is anchored at actual POST start, after potentially slow checkpoint writes. Reservations and attempts stay within the existing budget. A Requests socket timeout is still not a guaranteed hard wall-clock deadline for the whole run.

Each query has `run_report.json` and `attempts/0001.json`, etc. A new incomplete-query retry gets a new query directory/run ID and begins at page 1. The journal records unattempted, reserved, received, retained and parsed progress. Final outcomes distinguish:

| Outcome | Treatment |
|---|---|
| `success` | Page admitted only after source, schema, identity, pagination and storage checks. |
| `access_failure` | Non-200/challenge/unexpected content stops access; no automatic retry or method switch. |
| `transport_failure` | Uncertain request outcome remains visible and blocks automatic resume. |
| `schema_failure` | Safe evidence retained where possible; no affected-page inventory admitted. |
| `pagination_unstable` | Changed totals/pages or repeated identities make the query incomplete; no deduplication to manufacture completeness. |
| `budget_exhausted` | No extra request is allowed; unfinished work remains incomplete/unattempted. |
| `storage_failure` | Commit outcome may be unknown; do not overwrite a possibly committed success as a failure. |
| `sample_limit` / `unattempted` | A sample target or unvisited partition is not complete coverage. |

If the process dies between two checkpoints, the last durable state may be reserved or pending rather than a final error. Conservative budget accounting and recovery checks preserve this uncertainty.

Completed-query reuse validates settings, endpoint, source hashes, projection replay, identities, counts and per-query database rows. Continuing an incomplete daily cycle additionally requires the same scope, date, timezone, limits and observation window, valid durable counters/clocks, and remaining time/budget. Completed cycles can still be recovered offline after their window expires. An old access/transport stop in retained evidence still blocks new requests even if a budget flag was changed. An incomplete query restarts; old and new pages are never spliced into a complete query.

Keep four questions separate: did the pages succeed, did the query reconcile, did every query in the declared population reconcile, and did the whole collection fit a fresh comparable daily window? Stable totals still cannot prove stable membership or an instantaneous census under a changing `MostPopular` order. Generic `--resume-from` is historical recovery, not a fresh daily collection.

The previous stage's [proposed 16-query manifest](../data/experiments/collection_method_review/20260910T020000Z/proposed_broader_manifest.json) remains a separate, unactivated specification. Any later expansion needs a distinct reviewed scope/configuration and history denominator. Do not mix its counts into Tesla comparisons.

## Storage and interruption recovery

[storage.py](../src/vehicle_tracker/storage.py) still provides retained captures and per-query SQLite storage. Complete content-addressed files are now written to a temporary file, flushed, then published with a no-overwrite hard link. Existing hash paths are verified, never replaced. The filesystem must support this publication operation; an unsupported operation fails rather than weakening immutability. Temporary/orphan files after a hard interruption are not automatically admitted.

`store_capture()` writes capture metadata and observations in one `BEGIN IMMEDIATE` transaction. Repeating the identical run/page verifies both metadata and rows and performs no new writes; conflicting evidence raises an error. A new run/page observation stays separate.

[history.py](../src/vehicle_tracker/history.py) replays every supplied report before opening the destination, then imports each `import_reports()` batch in one SQLite transaction. Repeated imports verify actual stored run/capture/observation facts, not only a matching report hash. Parser provenance from the first import is retained. A conflict requires investigation or a separate reviewed rebuild, not replacement of history.

Two nullable provenance columns are added transactionally **on a future explicit import**: `query_runs.imported_at_utc` and `captures.evidence_available_at_utc`. Fresh-database and backup-copy migration were tested. The operating database was not migrated in this stage.

Filesystem publication, SQLite commit, cycle registration and CSV export are separate operations. They are not claimed to form one transaction:

| Interruption boundary | Evidence / next action |
|---|---|
| Before a response or before safe source retention | Pending attempt and durable budget remain. Actual outcome may be unknown; no automatic live resume. Missing body has an explicit limit or an incomplete checkpoint requiring review. |
| Safe source retained, projection failed | Replay selected source offline. The failure stays out of normalized inventory. |
| Capture retained, SQLite not committed | Capture/journal survive. Replay into a new scratch DB for diagnosis; a permitted query retry starts again at page 1. Do not edit the old report to claim completion. |
| SQLite committed, report acknowledgement failed | Existing rows may be present while the report says `database_outcome: unconfirmed`. Repeating the exact capture import is idempotent; plan counts exclude pages not acknowledged as parsed. |
| History import committed, registration failed | Default daily preview lists the unregistered retained cycle. Explicit `--import-cycle` verifies/reimports it idempotently, then registers it. No new requests. |
| Registration succeeded, export failed | Registration/history survive. `--refresh` produces a new derived export without recollecting or reimporting. |
| CSV or manifest write / final export publication failed | The directory remains `.<timestamp>-<id>.partial` and appears in preview. It is preserved for inspection; `--refresh` creates a new complete export. |

Daily registration holds the existing daily writer lock and the selected cycle lock so an active collector cannot change the evidence being bound. Locks release with the process. Daily CSVs and their hash manifest are written under `.partial`, flushed, then the whole directory is renamed for publication. Previous complete exports remain intact. This atomic publication improvement applies to the daily runner; the older standalone history-export CLI remains a derived/manual export path.

Preview's `import_allowed` means evidence/settings can be imported, not that coverage is complete. Registering a partial cycle deliberately freezes that date's partial evidence; subsequent comparisons keep it partial. Do not register a still-recoverable incomplete cycle before deciding whether a same-window, same-budget resume is appropriate. An expired/stopped cycle must not be relabeled fresh by changing its dates or flags.

## Exact commands

Run from the repository root. These first commands are **offline, read-only previews**:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_search.py --plan vehicle/config/carvana_daily_pilot.json --experiment storage-improved-preview --full-plan --max-requests 120 --max-seconds 1200
.\.venv\Scripts\python.exe -B vehicle/scripts/build_carvana_history.py --cycle-report vehicle/data/experiments/carvana_daily/2026-09-08/cycle.json --cycle-report vehicle/data/experiments/carvana_daily/2026-09-09/cycle.json --database vehicle/data/analysis/storage_rebuild_review/history.sqlite
```

The last command previews an explicitly separate rebuild destination. To actually create that review database from retained evidence, use this **offline write** command (not an operating DB migration):

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/build_carvana_history.py --cycle-report vehicle/data/experiments/carvana_daily/2026-09-08/cycle.json --cycle-report vehicle/data/experiments/carvana_daily/2026-09-09/cycle.json --database vehicle/data/analysis/storage_rebuild_review/history.sqlite --write
```

The named review destination was not created by this implementation; verification used disposable temporary databases. If that destination already contains a different project/rebuild, choose a new explicit destination. Repeating the identical import adds zero observations; changed evidence is rejected.

To inspect a new-contract source failure in a notebook without requests or writes, choose an actual page from a new report and use the pure replay helper:

```python
import json
from pathlib import Path
from vehicle_tracker.search_evidence import verify_response_evidence, replay_response

report_path = Path("<actual new query directory>/run_report.json")
report = json.loads(report_path.read_text(encoding="utf-8"))
page = report["pages"][0]
source = verify_response_evidence(page["response_evidence"])
source  # Inspect safe fields, missing keys and shapes before parsing.

capture = json.loads(Path(page["retained_source"]).read_text(encoding="utf-8"))
projection, rows = replay_response(
    page["response_evidence"], capture["request"],
    observed_at=page["response_received_at_utc"],
)
rows.head()  # Or the reproducible validation error, if this page failed schema checks.
```

If no `retained_source` exists, inspect the attempt journal's request and incomplete stage; this snippet cannot make missing evidence complete. Legacy captures have no `response_evidence` and retain their existing `read_query_evidence()` replay path. Do not execute placeholder paths as though they were real evidence.

These **future activation/recovery commands write the configured operating analysis outputs**. They are documented here, not run in this stage:

```powershell
# Next explicitly authorized fresh day: search POST -> retained cycle -> import/register/export.
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --live

# Offline recovery of a specific retained cycle. Replace with the exact path from preview.
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --import-cycle "<exact cycle.json path from preview>"

# Rebuild derived tables from already registered evidence; no requests.
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --refresh
```

`--live` still uses only the existing Tesla population. It refuses an already registered day or an existing capture destination. It does not automatically choose a recovery path or expand the population. `--import-cycle` can trigger the small nullable-column migration on its explicitly selected history DB. Review preview diagnostics before activation.

The daily CLI provides offline recovery, not automatic live resume. An explicitly authorized same-window continuation uses the existing Python `collect_cycle(..., resume=True)` with the retained cycle's exact `queries`, directory, `cycle_date`, `timezone`, `window_start`, `window_end`, `max_requests` and `max_seconds`. The generic search CLI's `--experiment` accepts a plain name, so it cannot address the nested `carvana_daily/<date>` destination. Do not create a differently named cycle as a shortcut around a blocked resume.

## Verification and remaining work

Fault-injection tests cover early schema failures, invalid/unsafe bodies, duplicate JSON keys, duplicate identities, changing totals with changing membership, partial runs, interrupted retention/commits/registration/exports, unknown request outcomes, stale or altered resume, slow-checkpoint pacing, conflicting repeated imports, and fresh/legacy schema migration. All requests in tests are offline fixtures.

An offline rebuild of **14 retained query reports** produced **1,355 observations**, matching every expected observation column against the operating history: retailer, listing ID, VIN, prices, native values, capture IDs and clocks. The two cycles remained complete with **674** and **681** observations respectively. A repeat import added zero rows and left rebuilt DB bytes unchanged. A migration on a temporary copy preserved old rows and unknown clocks. The operating DB SHA-256 remained `816257a2ba794a7d9226ef0627923c3059107b811331458fff7d5dd260b346ee`.

Validation completed:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider vehicle/tests
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py
```

- **497 vehicle tests passed** in 79.83 seconds; **860 full-repository tests passed** in 183.40 seconds, including those vehicle tests. The 14 full-suite warnings concern existing plotting/layout behavior.
- **All 16 notebooks passed** guarded offline execution. No notebook was edited.
- All three read-only previews above passed and created neither preview nor rebuild destinations. All five local document links resolved; `git diff --check` passed.
- Of **3,550 pre-existing files**, only the intended seven source modules and four existing tests changed; **3,539 remained byte-for-byte unchanged**. The pre-existing additions in `daily.py` also remain present. The five new files are this handoff, `search_evidence.py`, and three focused test modules.
- Previous-stage decision/experiment artifacts, retained data, operating DB/register/exports, configurations, frozen cohorts and gaming files were unchanged. Branch, HEAD and staged diff were unchanged. Independent read-only storage/collection and recovery/documentation reviews found no remaining critical issues.

Smallest next steps:

1. Read the preview and this recovery flow; keep notebook 20 for coverage/history and notebook 22 for the frozen website-status cohort.
2. On a separately authorized day, run one unchanged Tesla cycle to exercise the new evidence contract in real use. Observe failures, actual request spacing, runtime and retained bytes; offline tests do not measure live reliability.
3. Only after that, validate the separate proposed broader manifest and explicitly version its daily population. Do not change the Tesla denominator or interpret disappearance as a confirmed economic sale.
