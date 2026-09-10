# Run the daily Carvana inventory

The daily command collects the seven fixed Tesla Model 3 year queries, saves their
evidence, imports observations into SQLite, registers the date, and writes tables.
Notebook 20 starts with those tables and remains offline/read-only. The first day
is a baseline; it cannot tell us how many vehicles sold that day.

## Daily review in five minutes

1. Run the preview and, when you intend to collect for the actual local date,
   the `--live` command under **Each day** below. Do not relabel an old capture.
2. Open notebook 20 and Run All. Read the opening daily review, query coverage,
   and explanation of whether a consecutive-day comparison is available.
3. Read the matched-VIN asking-price categories, paired means and composition
   tables. Check the actual start-to-start interval beside those results; a
   consecutive-date comparison is not necessarily a 24-hour flow. Inspect new/absent VINs and the selected follow-up queue. Unresolved listings
   can be up to date; the queue separately shows first checks and due rechecks.
4. In notebook 20's vehicle-inspection section, copy a full retailer/VIN/listing identity into `SELECTED_IDENTITY`.
   Rerun that cell to see its original URL and chronological evidence. When there
   is no queue, choose a retained inventory control; it is not a sale candidate.
5. After actually checking a page, fill `CHECK_DRAFT` in the optional manual-check section and rerun that
   draft cell. Follow its
   manual console steps to prepare, preview, save, and reread one record. Those
   commands are Markdown examples, so Run All does not save anything.

Preparation produces a stable record ID. Retry saving that same prepared record
instead of preparing again. Corrections retain the original check time and use
later availability; a new visit has a new check time. See
[the recording guide](listing_checks.md). A historical cutoff will intentionally
exclude newly saved evidence until you advance it.

Review first disappearances, native changes and due rechecks, plus a few unchanged
controls. Preserve exact wording, evidence references, and uncertainty. These
targeted checks are a biased sample, not a measurement of population-wide sales.

The separate [Sold-status notebook 22](../notebooks/22_carvana_sale_status_validation.ipynb)
contains both frozen cohorts, explicit observation coverage, and experimental
capture/import instructions. It does not write canonical checks or analyst reviews.

## First real baseline: September 8, 2026

All seven queries completed during **21:44:11-21:45:40 America/New_York**
(September 9, 01:44:11-01:45:40 UTC), using 31 requests. SQLite contains seven
query runs, 31 captures and 674 observation rows. All VINs are unique; make,
parent model and each query's year reconcile to the configured population.

| Date | Coverage | Inventory VINs | Native pending | Unknown pending | Mean asking price, USD | Daily sales |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 2026-09-08 | Seven pilot queries complete | 674 | 215 | 0 | 28,219.38 | Unknown |

The year counts are 100 (2020), 143 (2021), 167 (2022), 187 (2023), 40 (2024),
28 (2025), and 9 (2026). No asking prices are missing. No daily change can yet be
calculated. Zero qualified candidates on the baseline does not mean zero sales.

Open the [daily table](../data/analysis/carvana_daily/tables/20260909T014542Z-04d2a2fa/daily_inventory.csv),
[actual vehicle rows](../data/analysis/carvana_daily/tables/20260909T014542Z-04d2a2fa/vehicle_observations.csv),
or [retained cycle](../data/experiments/carvana_daily/2026-09-08/cycle.json).
Their first export manifest was verified against all source and output hashes.
The next useful observation is September 9 at approximately 21:45 New York time.

The [later page study](status_validation_20260908.md) checked five baseline listings
that same evening. It did not create another daily snapshot. Notebook 20 now follows
population/time, daily inventory, VIN changes, page evidence, and next steps. The
older intraday walkthrough and coverage audits are preserved in notebook 21.

## Each day

Run from `C:\Users\Sean\VscProjects\researchOS`:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --live
```

The first command previews without requests or writes. The second deliberately
collects and writes. Use approximately the same time each day; there is no scheduler.
The command uses today's date in `America/New_York` and a 20-minute collection
window starting now. It refuses a window that crosses local midnight. Source
timestamps remain UTC, so a September 9 UTC capture can belong to September 8 in
New York. Requests are sequential, spaced by at least three seconds, with a hard
120-request / 20-minute ceiling. Access blocks stop collection without retries.

The query plan fixes model years 2020-2026, ZIP 08542 and location filtering off.
This is an operational pilot, not national inventory or a representative sales
sample. Changing the scope/timezone requires a separate register and database;
existing registered history cannot silently acquire a new population.

## Where the data lives

Paths below are relative to `vehicle/`; configuration is
`config/carvana_daily_tracking.json`.

| Path | Contents |
| --- | --- |
| `config/carvana_daily_pilot.json` | Exact seven-query population |
| `data/experiments/carvana_daily/<local-date>/cycle.json` | Date, scope, window, request budget and links to retained query evidence |
| The same cycle folder | Retained response projections, per-query reports and original collector databases |
| `data/analysis/carvana_daily/history.sqlite` | Imported `query_runs`, `captures`, `observations`; existing storage format |
| `data/analysis/carvana_daily/cycles.json` | Explicit date-to-cycle selections, IDs and report hashes |
| `data/analysis/carvana_daily/tables/<timestamp-id>/` | A new CSV export and manifest on each explicit run/import/refresh |

Each VIN observation joins its cycle ID to the selected cycle's local date. The
SQLite rows remain actual observations; missing VINs do not become fabricated
observation rows. The daily calendar and missing-listing events are derived in
pandas. A manifest records the cutoff, configuration/source hashes and output
hashes. Notebook reanalysis uses current code and displays its cutoff.

## Report tables

| Table | Meaning |
| --- | --- |
| `daily_inventory.csv` | One calendar date per row, with coverage, observed VINs, complete-scope inventory, native pending counts, asking prices, new/absent VINs and candidate counts |
| `vehicle_observations.csv` | Actual saved observations joined to dates, with VIN/listing IDs, prices, native flags, source paths and duplicate diagnostics |
| `sale_candidates.csv` | One episode after three consecutive complete absences; later returns and gaps remain visible |
| `detail_followups.csv` | Original URLs for missing/relisted/reappeared listings or native-status changes; saved check status, first-check/change/recheck priorities, and up to 20 selected rows |

New exports also include `listing_checks.csv` and `selected_reviews.csv`: the exact
selected check/review records used by that report. The canonical CSV inputs beside
`history.sqlite` retain all recorded versions. Missing inputs mean no records.
See [the recording guide](listing_checks.md) for `--record-check`, `--record-review`,
cutoff selection and the 48-hour recheck policy. `reviewed_sales_with_known_date`
counts selected dated confirmations, not the total sales of the inventory population.

`coverage_status` is `complete`, `partial`, `missing`, or `invalid` when identity/
analysis validation fails. `observed_vins=0` on a failed capture means none were
observed; `inventory_count` stays missing. Complete queries with zero results can
establish zero inventory in that chosen scope. Skipped dates remain missing.

`pending_true` counts native `isPurchasePending=True` among observed rows, with unknown flags
in `pending_unknown`. Asking-price averages exclude unknown prices and preserve
observed zero/negative values; `missing_prices` shows their missingness. These
counts and prices on partial days describe the captured subset only. VIN/listing
conflicts remain visible and block inventory/change/candidate inference.

New and absent counts require two complete consecutive dates with the same scope.
The first day has no comparable prior day. Collection is a sweep, not an
instantaneous census: pagination and inventory changes during the sweep can cause
coverage failures. Passing local count/identity checks does not prove the source
exposes every eligible vehicle or captures cars that turn over between sweeps.

## Why sales are still unknown

Inventory disappearance is evidence to investigate. A pending flag can clear;
vehicles can disappear, return or receive new listing IDs. Search totals changing
also do not reveal gross sales because additions and removals occur together.

`site_marked_sold` and `estimated_sales` are deliberately unavailable. A saved
detail check must retain the exact listing-specific text, VIN/listing identity,
URL and observation time. An explicit sold label means the site was observed
saying sold; it does not by itself establish the transaction date or a sale net
of returns. "No longer available", a 404, or a challenge is not a sold label.

A direct detail-page probe of `/vehicle/4678187` on September 9 UTC received HTTP
403 / `cf-mitigated: challenge`. That access failure is documented here, not
inserted into the vehicle history as an observation. Detail-page access is not a
dependency of the daily bulk search. The queue uses saved check history to support bounded manual browser
review; the command does not perform those checks or bypass access challenges.
Use [the review format](sales_tracking.md) for evidence-backed analyst outcomes.

## Recovery and repeated runs

A second `--live` for a registered date stops before making requests. Two processes
cannot own the same daily register simultaneously. Evidence changes, duplicate
date selections and mismatched populations are rejected.

If capture finished but import/export was interrupted, use its retained root report:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --import-cycle vehicle/data/experiments/carvana_daily/2026-09-08/cycle.json
```

Use the actual saved date. This makes no requests and imports idempotently. It
cannot silently replace a registered date with a different capture. A partial
cycle remains partial; import does not complete its missing queries. For a new
export from registered evidence, including a visible gap through today's cutoff:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --refresh
```

Opening notebook 20 requires neither command. It reads the register and existing
database. The historical walkthrough uses its separate example database. To
reproduce a prior cutoff, set `AS_OF_OVERRIDE` before running notebook code.

A registered partial date remains partial; `--live` will not replace it. Inspect
the retained cycle and coverage diagnostics before any lower-level recovery in
[daily_cycles.md](daily_cycles.md). An import can recover a completed capture's
interrupted database/export step, but cannot manufacture missing requests. A day
that was never collected remains a calendar gap.

## Back up the evidence, not just the code

The Git checkpoint excludes local captures and databases. When no collector or
recording command is writing, back up `vehicle/data/experiments/carvana_daily/`,
the page-evidence folders referenced by saved checks, and all of
`vehicle/data/analysis/carvana_daily/` (SQLite, register, checks, reviews, and
versioned exports/manifests). Include the tracking/query configuration and the
code revision/diff used for analysis. Do not copy a live SQLite file alone while
it is being written; retain any journal/WAL companions with a consistent backup.

Existing registers and source references contain absolute paths. Copying the
folder to another machine does not automatically make those bindings portable.
Restore the original paths for a like-for-like recovery; do not silently edit
historical evidence references or hashes.

At checkpoint `052ed0b`, shared root package configuration and the gaming folder
relocation remain uncommitted. The working setup discovers `gaming/src` and
`vehicle/src`, while the committed root configuration still points at `src`.
The working root notebook checker also uses `gaming/scripts/check_notebooks.py`.
This checkout works with the shared environment; the Carvana checkpoint alone
is not a complete fresh-checkout setup. Resolve that separate repository change
before claiming portable reproduction. No installation is needed in this checkout.

## Vendor workbook reference

The supplied `C:\Users\Sean\Downloads\CVNA_Q3_2026_Summary_Free (1).xlsx` was
inspected read-only. SHA256:
`d9c7caa593793126c0f79098545e4ca0881746dfe1c4e950ab85ea9fe37cbed5`.
On `Sheet1`, row 1 labels A as Date, C as Current Year Daily Sales, O as Orders,
S as Listings, W as Inventory Price, AL as Sold Price, BK as Pending and BL as
Inventory. Those headings do not disclose the vendor's classification method.
Column C has 40 populated daily observations, July 1-August 9, 2026 (C2:C41).
Later calendar dates in column A are not populated current-year sales observations.
This file therefore cannot validate the September daily baseline.

Our first table adopts the useful date/inventory/pending/price structure. No vendor
values were imported, no workbook bytes changed, and its inventory/pending/price
definitions are not assumed equivalent to our fixed pilot/native observations.

## What to do next

1. Repeat once per day for 7-10 days, preserving the same population and similar
   collection time. Check coverage, runtime, native fields and duplicate diagnostics.
2. Review a small sample of missing/changed listings and unchanged controls in
   the browser. Preserve outcomes, uncertainty and reappearances in the candidate
   review ledger. This tests the absence screen instead of assuming it measures sales.
3. Test a broader complete partition plan with its own history. Audit source totals,
   overlap, geographic delivery context and pagination drift before claiming broad
   daily inventory. The earlier 10,019-vehicle trial was a sample, not that proof.
4. Only after sufficient independent labeled evidence, assess precision, missed
   sales, observation lag and returns; then consider a separately labeled estimate.

The first deliverable is a dependable daily inventory history. Exact vendor-style
daily sales, orders and transaction ASP remain separate measurement problems.
