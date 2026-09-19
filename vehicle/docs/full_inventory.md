# Daily full-inventory collection

The [revised goal](full_inventory_goal.md) replaces the approximately 10,000-VIN
target with current native category discovery. Use
`vehicle/scripts/run_carvana_full_inventory.py` and
`vehicle/config/carvana_full_inventory.json` for this expanded scope. The historical
panel runner remains available for its frozen experiments.

## Population and request sequence

1. Read one broad first-page response in ZIP 08542. Retain its native make counts
   and inventory source. Require the make counts to sum to the broad total.
2. Visit each current make without a year filter. Discover model families from
   that make's response. Split only when child counts sum to the parent and native
   model IDs do not overlap. Otherwise enumerate the entire make, retaining the
   reason. A complete single-page make probe is reused without another request.
3. Enumerate every page of each selected make/model query, including zero-count
   model categories. Do not stop at a VIN target. Preserve partial queries and
   continue only after a reconciled pagination-only failure; all access, transport,
   context, schema, identity and storage failures stop the whole invocation.
4. Read broad counts in ZIPs 98101 and 33130, then repeat up to four completed
   primary queries with at most 240 native matches in each ZIP. Selection rotates
   deterministically with the date. Show additional and missing VINs separately;
   this is a diagnostic sample, not proof that those ZIPs cover the whole country.
5. Read a closing broad response in the primary ZIP. Report its count and any
   new makes, opening/closing count residuals, duplicate memberships and gaps.

All steps share **6,000 charged request starts, six hours and minimum three-second
spacing**. A late start shortens the window to the end of the same local date.
Discovery and validation consume the same allowance as enumeration. No retry,
resume, alternate transport, concurrency, automatic budget extension or replacement
date is provided. Each local date has one immutable destination. Unresolved prior
attempts and fatal-stop records block later invocations pending review.

All-year make/model queries avoid the old fixed year boundary. Very large or
changing categories can still have unstable pagination; incomplete categories
remain unknown. Count agreement across hours does not prove identical membership.
The sequential sweep's actual observation interval must accompany every result.

## Speed changes

Connection reuse and native search pages already avoid opening thousands of browser
detail pages. This workflow needs one broad discovery response plus one per make,
rather than a separate 720-cell make/year count scan before enumeration. It reuses
complete make probes and does not repeat the entire inventory at every ZIP.
No observed live speedup is claimed before a measured full run. At 24 rows per page,
80,000–100,000 vehicles alone require at least 3,334–4,167 requests; category rounding,
probes, geographic checks and failures add cost. Minimum spacing remains the main
throughput limit. A supported bulk source, if available under the user's access
arrangement, would need its own source contract before adoption.

## Preview and operate

From the repository root, preview without requests or writes:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_full_inventory.py
```

The preview prints the selected configuration hash, fresh dated destination,
effective observation window, request ceiling and retained stop state. Before a
live invocation, inspect other live processes, the active checkout and access-stop
evidence, and confirm the source/destination matches the user's instruction. The
September 19 software-update instruction is not recorded as an executed baseline.

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
