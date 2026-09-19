# Daily full-inventory collection

The [revised goal](full_inventory_goal.md) replaces the approximately 10,000-VIN
target with current native category discovery. Use
`vehicle/scripts/run_carvana_full_inventory.py` and
`vehicle/config/carvana_full_inventory_years.json` for the prospective year-first
strategy. The original `carvana_full_inventory.json` preserves the first all-year
make/model strategy and its separate captures. The historical panel runner remains
available for its frozen experiments. The new strategy is not live-validated yet.

## Population and request sequence

1. Read one broad all-year first-page response in ZIP 08542. Retain its native
   make counts, displayed year metadata and inventory source. Require the make
   counts to sum to the broad total.
2. Freeze exact-year contexts between the displayed minimum and maximum, plus
   an older-year tail and a newer-year tail. These cover integer years without
   treating the displayed endpoints as universe bounds. Missing/noninteger years
   remain an explicit uncertainty. Every future year-only response must match its
   requested native applied bounds and expose make counts summing to its total.
3. Probe each positive make/year cell. Reuse a complete single-page make probe;
   otherwise split into model families only when native counts and IDs form a
   partition. Keep the whole make/year context when they do not. Positive tails
   retain their one-sided year filters throughout enumeration. Native zero make
   categories remain source-count evidence, not invented inventory-query reports.
4. Enumerate every page of every selected make/model/year leaf. Do not stop at a
   VIN target. Preserve partial queries and continue only after a reconciled
   pagination-only failure; all access, transport, context, schema, identity and
   storage failures stop the whole invocation. Year-only and one-sided filter
   behavior must be established by real retained responses before claiming it
   works; synthetic tests cannot establish this.
5. Read broad counts in ZIPs 98101 and 33130, then repeat up to four completed
   primary queries with at most 240 native matches in each ZIP. Selection rotates
   deterministically with the date. Show additional and missing VINs separately;
   this is a diagnostic sample, not proof that those ZIPs cover the whole country.
6. Read a closing broad response in the primary ZIP. Report its count and any
   new makes, year/make count residuals, opening/closing count residuals, duplicate
   memberships and gaps, with the actual observation clocks.

All steps share **6,000 charged request starts, six hours and minimum three-second
spacing**. A late start shortens the window to the end of the same local date.
Discovery and validation consume the same allowance as enumeration. No retry,
resume, alternate transport, concurrency, automatic budget extension or replacement
date is provided. Each strategy/local-date pair has one immutable destination.
The strategies retain separate attempt roots; they never fill or replace each
other's failed queries. The v2 config lists related capture roots, including the
original execution worktree and the integrated checkout. The collector locks all
these roots before creating a date, so it cannot overlap the frozen v1 collector
that already owns its root lock. Unresolved attempts and fatal-stop records in
any listed root block collection. Keep the reviewed root list when moving between
checkouts; do not remove a root to bypass an active attempt or retained stop.

The first all-year strategy avoids a fixed year boundary but has demonstrated
pagination gaps. The prospective year-first strategy preserves outside-tail
contexts and reduces query size. Large or changing single-year categories can
still have unstable pagination; incomplete categories remain unknown. Count
agreement across hours does not prove identical membership.
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
reviewed hash. This does not activate a recurring schedule. Fatal conditions retain
an `access_stop.json`; do not delete it to bypass review or restart a failed date.

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

The new per-run databases retain the same observation schema used by the old
history system. Keep old operating, recovery and Tesla histories intact. Existing
query-comparison gates require complete matching contexts before absence analysis;
dynamic partition changes require comparing a compatible subset or a reviewed
whole-population reconciliation. First-seen history can use valid positive rows
from partial captures. Missingness cannot.

`vehicle_tracker.catalog_history.observed_catalog_history` reads explicitly
selected catalog exports and legacy cycle/database pairs, verifies their retained
evidence, and returns VIN history, first-observed cohorts and source memberships.
It preserves distinct same-date attempts and differing query scopes. Catalog
analysis becomes available at its export publication time; actual observations
keep their original source clocks. This adapter produces no absence or sales
classification. Notebook 25 exposes these inputs without collecting or writing.

Review Notebook 25 for the expanded collector's saved reports, then Notebook 20
for existing comparable-history analysis and Notebook 24 for native-status
validation. A native pending flag, missing VIN or asking-price change is not a
transaction or a confirmed sales count. No actual full-inventory baseline exists
merely because this code, budget and goal have been updated.
