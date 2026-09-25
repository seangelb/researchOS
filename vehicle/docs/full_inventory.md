# Daily full-inventory collection

The [revised goal](full_inventory_goal.md) replaces the approximately 10,000-VIN
target with current native category discovery. The daily run uses
`vehicle/scripts/run_carvana_full_inventory.py` and
`vehicle/config/carvana_full_inventory_adaptive.json`. Make/year cells of at most
480 vehicles are one query. Larger cells are probed, then split into models.
`carvana_full_inventory_years.json` preserves the earlier year-first strategy.
The original `carvana_full_inventory.json` preserves the first all-year
make/model strategy and its separate captures. The historical panel runner remains
available for its frozen experiments. The adaptive strategy is not live-validated yet.

The [September 19 terminal outcome](full_inventory_outcome_20260919.md) records
the original sweep's incomplete coverage, successful export and verified backup.
[September 23](full_inventory_outcome_20260923.md) stopped on HTTP 520.
[September 24](full_inventory_outcome_20260924.md) stopped because the old
model-level plan did not fit five-second spacing.

## Population and request sequence

The steps below are the year-first sequence preserved in
`carvana_full_inventory_years.json`. The adaptive daily config does not probe
every make before enumerating. After the year responses, a cell of at most 480
vehicles is collected in one query. A larger cell is probed, then split with the
same model-partition rule. A run that finishes, attempts the ZIP checks and the
closing count, and leaves unverified queries covering at most 0.5 percent of
the opening count is `complete_with_gaps`. Strict reconciliation flags stay
visible. HTTP 520 is a server failure, not an access stop.

1. Read one broad all-year first-page response in ZIP 08542. Retain its native
   make counts, displayed year metadata and inventory source. Require the make
   counts to sum to the broad total.
2. Freeze exact-year contexts between the displayed minimum and maximum, plus
   an older-year tail and a newer-year tail. These cover integer years without
   treating the displayed endpoints as universe bounds. Missing/noninteger years
   remain an explicit uncertainty. Every future year-only response must match its
   requested native applied bounds and expose make counts summing to its total.
3. Probe each positive make/year cell. Reuse a complete single-page make probe;
   otherwise split into model families when native counts and IDs form a partition,
   or when shared model IDs create an overlap whose count excess fits the cluster
   capacity. Keep the whole make/year context when counts fall short or the excess
   is too large. Positive tails retain their one-sided year filters throughout
   enumeration. Native zero make categories remain source-count evidence, not
   invented inventory-query reports.
4. Enumerate every page of every selected make/model/year leaf. Do not stop at a
   VIN target. Preserve partial queries. A retry-exhausted page-level schema,
   pagination, identity or transport failure isolates that leaf so later queries
   can run. HTTP 403, 429, Cloudflare challenges, context and storage failures
   still stop the whole invocation. Year-only and one-sided filter behavior must
   be established by real retained responses before claiming it works; synthetic
   tests cannot establish this.
5. Read broad counts in ZIPs 98101 and 33130, then repeat up to four completed
   primary queries with at most 240 native matches in each ZIP. Selection rotates
   deterministically with the date. Show additional and missing VINs separately;
   this is a diagnostic sample, not proof that those ZIPs cover the whole country.
6. Read a closing broad response in the primary ZIP. Report its count and any
   new makes, year/make count residuals, opening/closing count residuals, duplicate
   memberships and gaps, with the actual observation clocks.

All steps share **7,000 charged request starts, six hours and at least
three-second spacing**. A late start shortens the window to the end of the same
local date. The first attempt of a clean day still refuses to start when the
remaining window cannot pace the full request ceiling at three seconds. A later
or slower attempt may start in the time that remains and end incomplete.
Discovery and validation consume the same allowance as enumeration. A
page-level schema, pagination, identity, transport or server failure
(HTTP 408 and 5xx, including 520) re-requests that page up to two extra times.
Server retries wait 10 seconds, then 30 seconds. The leaf is then isolated so
later queries can run. Five consecutive isolated leaves end the attempt as
`degraded`. HTTP 401/403, 429 and Cloudflare challenges end the attempt
immediately, write `attempt_outcome.json` with kind `access_stop`, and record
`cooldown_until`. They do not retry inside the attempt. Incomplete leaves get
one second pass before closing discovery. There is no resume inside an attempt,
alternate transport, concurrency, or automatic budget extension. Each attempt
has its own immutable folder: `<date>`, then `<date>_a02`, then `<date>_a03`.
An attempt never fills another attempt's leaves. Same-day retries use the next
spacing in `[3, 5, 8]` seconds. The day after an access stop starts one step
slower; a day that does not end in an access stop returns to three seconds.
The strategies retain separate capture roots; they never fill or replace each
other's failed queries. The v2 config lists related capture roots, including the
original execution worktree and the integrated checkout. The collector locks all
these roots before creating an attempt, so it cannot overlap the frozen v1
collector that already owns its root lock. A held lock or an unexpired access
cooldown blocks a new attempt. A terminal failure with no active cooldown does
not. Keep the reviewed root list when moving between checkouts; do not delete
a stop marker to bypass a cooldown.

## Empty responses and unverified contexts

Carvana returns two retained empty layouts that are valid but leave a requested
filter unconfirmed: zero inventory with **one** native page and omitted
`facetData.makes`, and a **zero-count model omitted** from its applied make's
native children. Both are now recognized instead of stopping the invocation.

Every probe validates its whole native context before it may plan later queries.
An inventory leaf validates only an empty page, because its rows are already
checked against the requested make, model and year, which is stronger evidence
than a facet flag. A response that contradicts the request, returned ZIP or an
applied year boundary is a page-level schema failure: it retries, then isolates
the leaf. Access and storage failures still stop everything.

An unverified context is recorded as `context_status` in the coverage ledger and
year reconciliation. It admits **no** vehicle rows, is **not** evidence of
absence, and keeps its leaf incomplete. A zero-inventory year context whose ZIP
and applied year bounds are confirmed remains a validated zero even when its
make categories are unavailable; `native_make_categories_available` records that
separately. Zero observed vehicles never becomes zero inventory or a sale.

## Request feasibility

The year strategy now runs discovery, year probes and make/model probes first,
freezes the whole leaf plan, and only then charges for enumeration. Before that
bulk work it publishes a `feasibility` record: the declared leaves, their native
count total, the estimated page floor, declared geographic and closing overhead,
the resulting minimum requests, and the pacing seconds at three-second spacing.

The estimate is a **lower bound** from counts observed during this discovery. It
excludes drift, retries and failed pages. When the frozen plan cannot fit the
remaining request ceiling or the remaining window, enumeration is skipped and
`feasibility_blocked` is recorded. That shortfall is a planning outcome, not a
fault: the retained date keeps its discovery evidence, no stop marker is written,
and the next attempt needs a revised plan or allowance rather than a review.
Preview separately reports `pacing_seconds_floor` for the configured ceiling.

## Reviewed peer failures

`related_capture_roots` must list every related full-inventory and recovery root
so they are locked together. A new attempt is blocked only while a root lock is
held or an access-stop `cooldown_until` is still in the future. Historical
`access_stop.json` files without a cooldown, and terminal budgets with
`pending_request` true or false, do not block a later date. Listed
`reviewed_peer_failures` keep their exact report, budget and stop hashes.
That disposition never edits or deletes a stop marker. A changed hash still
fails preview. Eligible historical shapes remain HTTP-200 schema failures
(with `pending_request=false`), including a `CollectionStopped` wrap of
`ValueError: schema_failure` that also isolated ordinary pagination leaves,
discovery ValueError stops that kept every charged outcome non-fatal, and
transport-uncertain stops that keep `pending_request=true` on the failed date.
Analysis for a date uses the most complete attempt folder and does not merge
attempts.

The first all-year strategy avoids a fixed year boundary but has demonstrated
pagination gaps. The prospective year-first strategy preserves outside-tail
contexts and reduces query size. Large or changing single-year categories can
still have unstable pagination; incomplete categories remain unknown. Count
agreement across hours does not prove identical membership.
Shared native model IDs (for example Chevrolet Silverado 3500 and Silverado 3500
HD Chassis Cab) are collected as separate model leaves when the model-count excess
fits the overlap capacity. A VIN that appears in every leaf of one declared
overlap cluster is an explained duplicate: it stays visible in
`duplicate_primary_memberships` and does not by itself block
`primary_scope_reconciled`. A VIN shared by leaves outside one declared cluster
is unexplained and keeps the day unreconciled. Conflicting listing/VIN identities
still stop the invocation.
The sequential sweep's actual observation interval must accompany every result.
The [September 19 pagination review](full_inventory_partition_review_20260919.md)
records the first observed overlap and an offline year-partition proposal. That
proposal is separate from the frozen collector and still needs live validation.

## Speed changes

Connection reuse and native search pages already avoid opening thousands of browser
detail pages. Year-level discovery supplies make counts, so only positive make/year
cells need further collection. Complete single-page probes are reused, and the
whole inventory is not repeated at every ZIP. The original all-year strategy used
one broad discovery response plus one per make, but large queries proved unstable.
No observed live speedup is claimed before a measured full run. At 24 rows per page,
80,000–100,000 vehicles alone require at least 3,334–4,167 requests; category rounding,
probes, geographic checks and failures add cost. Minimum spacing remains the main
throughput limit. The user confirmed that the authorized source is the public
website only; this workflow has no bulk feed or private API documentation.

## Preview and operate

From the repository root, preview without requests or writes:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_full_inventory.py
```

The preview prints the selected configuration hash, fresh dated destination,
effective observation window, request ceiling and retained stop/unresolved state
for every related root. Preview does not acquire locks, so it cannot certify that
no process is active. Before a
live invocation, inspect other live processes, the active checkout and access-stop
evidence, and confirm the source/destination matches the user's instruction. The
September 19 software-update instruction is not recorded as an executed baseline.

The default preview selects the v2 year-first config. Use `--config` with the
original v1 file to explicitly inspect its retained strategy. Old collector code
rejects v2 configuration rather than silently ignoring its new strategy.

For a separately initiated live run, pass `--live --config-sha256` with that exact
reviewed hash. An access failure writes `access_stop.json` only when that root
does not already have one, and always writes `attempt_outcome.json`. Do not
delete a stop marker to skip its cooldown.

## Daily local schedule

Windows Task Scheduler runs the year-first decision every hour from 12:01 AM
through 8:01 PM local time, and again at logon. Starting at 12:01 AM gives the
adaptive plan a full local day before the midnight window ends. The PC must be
awake and logged on, and VPN must be off (Carvana previously returned HTTP 403
on the VPN path). The wrapper calls `daily_decision` and exits without
requests unless the action is `start`. A missed 12:01 AM start is picked up by
a later hour. After an access stop or a degraded attempt, the next hour waits
out the cooldown and then starts the next attempt folder more slowly. A
finished attempt is not repeated that day.

```powershell
# Register (default 12:01 AM local / Eastern on this machine)
powershell -File vehicle/scripts/register_carvana_full_inventory_daily_task.ps1

# Status / remove
powershell -File vehicle/scripts/register_carvana_full_inventory_daily_task.ps1 -Action Status
powershell -File vehicle/scripts/register_carvana_full_inventory_daily_task.ps1 -Action Unregister

# Manual dry gate (no live requests)
powershell -File vehicle/scripts/run_carvana_full_inventory_daily.ps1 -PreviewOnly
```

Task name: `researchOS-CarvanaFullInventoryDaily`. Logs:
`vehicle/data/experiments/carvana_full_inventory_daily_logs/` (kept outside the
capture root so dated log files are not mistaken for collection folders).

The live command automatically attempts an offline reconciliation/export to the
date folder's `analysis` directory after the collector stops. An uncertain or
unreconciled attempt blocks publication; its original collection evidence remains.
To independently replay a terminal capture into another fresh export destination:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_full_inventory.py --replay <retained-date-folder> --output <fresh-analysis-folder>
```

Replay checks the durable request ledger, page journals, source hashes, native
discovery plan, request spacing/windows and source-to-collector-SQLite parity.
It writes primary observations, a coverage ledger, geographic comparisons, a
per-make count reconciliation, a per-run history SQLite database and a hash
manifest. Every declared query stays in the ledger, including unattempted work.
`declared_collection_complete` means discovery, primary pages and declared ZIP
checks finished. Count residuals and ZIP membership differences remain separate
diagnostics; finishing the collection does not establish national completeness.
Per-make observed counts are lower bounds when any leaf is incomplete.
Discovery/validation samples
are excluded from primary inventory except a make probe that actually completed.
Year-first exports also include `year_reconciliation.csv` and
`native_zero_categories.json`, preserving count evidence and outside-tail outcomes
separately from vehicle observations. The original strategy's replay remains
available; its sources and outcomes are not upgraded to the new strategy.
Transport-uncertain or storage-unreconciled attempts stay blocked for import, with
their original journals retained. Exports are new vintages, never overwrites.

## Historical interpretation

The [September 19 retained-history publication](full_inventory_history_20260919.md)
records the verified results, exclusions, review order and exact Notebook 25 inputs.

The new per-run databases retain the same observation schema used by the old
history system. Keep old operating, recovery and Tesla histories intact. Existing
query-comparison gates require complete matching contexts before absence analysis;
dynamic partition changes require comparing a compatible subset or a reviewed
whole-population reconciliation. First-seen history can use valid positive rows
from partial captures. Missingness cannot.

`vehicle_tracker.catalog_history.observed_catalog_history` reads explicitly
selected catalog exports, legacy cycle/database pairs and reviewed retained-history
manifests. It verifies their retained evidence and returns VIN history,
first-observed cohorts and source memberships. The additional manifests explicitly
select older query reports and genuine samples, with source hashes and database
comparisons where retained. Copies of the same capture count as one physical
observation after their values and context agree; provenance aliases remain visible.
Distinct captures and differing query scopes remain separate evidence.

Older query reports and samples have no invented daily-cycle identity. They can
establish an earlier sighting, but cannot establish complete daily coverage or
absence. Unknown original evidence-availability clocks remain unknown; a reviewed
publication supplies a conservative new analysis-availability clock. A legacy
cycle with unknown source availability also needs that publication before its
positive rows enter this combined history. Catalog analysis becomes available at
its export publication time; actual observations keep their source clocks.
`first_available_at` describes when the earliest observed evidence became eligible;
`first_known_at` describes when any selected evidence first made the VIN known.
Neither field establishes the vehicle's original listing date. This adapter
produces no absence or sales classification. Notebook 25 exposes these inputs
without collecting or writing.

The [September 20 year-first r3 outcome](full_inventory_outcome_20260920.md)
records a finished partial sweep (~79.9k VINs, geo-stable sample, 11 incomplete
leaves). It is not a reconciled baseline or sales day; daily sales still need a
second comparable complete day.

Review Notebook 25 for the expanded collector's saved reports, then Notebook 20
for existing comparable-history analysis and Notebook 24 for native-status
validation. A native pending flag, missing VIN or asking-price change is not a
transaction or a confirmed sales count. No actual full-inventory baseline exists
merely because this code, budget and goal have been updated.
