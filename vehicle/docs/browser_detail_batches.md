# Browser detail batches

This small workflow uses connected Chrome to read public vehicle pages and Python
to save the selected fields. It is browser-assisted: the CLI does not navigate
Chrome or run an unattended scraper. Inventory collection remains a separate daily
command. Each detail batch has a frozen selection, a short collection window and
one checkpoint per planned vehicle. It does not create another database.

Run the commands below from `C:\Users\Sean\VscProjects\researchOS`.

## Preview and prepare

The default action only reads retained evidence and prints a proposed selection:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_details.py
```

Use a fresh name when you intend to collect. Preparing saves the selection and
extractor but makes no browser visits:

```powershell
$batch = 'vehicle/data/experiments/carvana_detail_batches/CHOOSE_A_NEW_NAME'
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_details.py prepare --batch $batch --limit 6 --controls 2 --minutes 45
```

The selection uses the same follow-up queue as Notebook 23. Limits are 1-12
top-level page visits, fewer controls than total visits, and a 1-60 minute window.
Chrome may fetch additional page assets; the visit cap is not a network request
count. A reserved, unresolved vehicle is excluded from fresh selections until
its earlier visit is recovered or explicitly closed, even after that batch expires.

## Read and save one page at a time

1. Reserve the next visit **before** opening its page:

   ```powershell
   .\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_details.py next --batch $batch
   ```

   This prints the exact URL, expected VIN/listing identity, visit directory and
   frozen `capture.js` path. A second `next` cannot repeat an unresolved visit.

2. Open that URL in connected Chrome. Read the public page, then use the frozen
   extractor with the printed identity. Save its returned projection as a JSON
   file in a separate temporary location. The extraction contract is documented
   in [capture_carvana_page.js](../scripts/capture_carvana_page.js); keep the
   returned native fields and physical `checked_at` together.

3. Record that file immediately:

   ```powershell
   .\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_details.py record --batch $batch --input 'C:\path\to\selected_projection.json'
   .\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_details.py status --batch $batch
   ```

   A matched result saves `capture.json` and publishes `run.json` last. Wait at
   least 15 seconds after the saved result before reserving another visit.
   An access, identity or parsing failure stops the batch. The CLI does not retry
   the page or switch collection methods.

If navigation fails or the page is blocked, close the reserved visit explicitly:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_details.py fail --batch $batch --reason access_blocked
```

Other documented reasons are `navigation_failed`, `page_not_ready` and
`interrupted`. A failure records the attempt; it supplies no native vehicle status.

## Recover an interrupted save

If the projection was saved but publication of `run.json` was interrupted, reuse
the local evidence without visiting the page again:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_details.py recover --batch $batch
```

Recovery validates the saved identity, projection and observation clock before
publishing the result. Local availability is the recovery time; it is not
backdated. An expired window prevents new visits but does not prevent recovery
of a saved observation from a visit reserved during that window.

If the saved projection is malformed or the visit produced no usable evidence,
close it explicitly:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_details.py fail --batch $batch --reason interrupted
```

The original partial capture is preserved with its hash. The failure projection
uses a separate file and completes the checkpoint without inventing a status.
Do not delete a checkpoint or reopen its URL to make the old batch look complete.

`status` returns these process exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Every planned visit completed with matched identity and parsing |
| 1 | Unfinished work within the collection window |
| 2 | At least one unsuccessful visit; the batch stopped |
| 3 | Unfinished work after the collection window expired |

## Review in Notebook 23

Run [Notebook 23](../notebooks/23_carvana_daily_sales_research.ipynb) from the top.
Choose an `AS_OF` at or after the saved result's availability to include it.
`browser-batch-review` shows both `browser_health` and `browser_records`; the
following queue excludes unresolved reservations. Both tables are included in
the notebook's explicit export registry. Run All reads local files only.

Browser observations feed follow-up selection and the optional Clarity comparison.
They do not silently change the frozen cohort's original inputs or estimates.
Missing, pending and native Sold remain distinct observations. A website Sold
label does not establish delivery, final transaction price or sales net of returns.

## September 12 validation

The [fresh six-vehicle batch](../data/experiments/carvana_detail_batches/20260912_browser_fix_validation/batch.json)
completed through connected Chrome at `2026-09-12T11:37:13.019826+00:00`.
All six VIN/listing identities and projections matched: two native Sold, four
native Available. The latter include two Reservable/pre-order pages, one pending
purchase and one available vehicle. These are website observations, not six
independently verified transaction outcomes. The previously HTTP-blocked listing
`4659188` was successfully captured through Chrome.

Notebook 23 now defaults to this cutoff. Review `browser-batch-review`,
`followup-selection-table`, then `clarity-exit-rules`. All six checked vehicles
are excluded from immediate repeat selection. The Clarity replay now has 14
checked exit episodes with 12 later Sold labels, out of 72 eligible episodes;
58 remain unchecked. This selected subset does not establish sale accuracy.

The [verification record](../data/experiments/carvana_detail_batches/20260912_browser_fix_validation/verification.json)
binds the saved evidence and code hashes. All 732 vehicle tests and seven offline
notebooks passed. Two exports reproduced all 48 tables identically, including
the browser observations and health table. All 2,155 pre-existing data/configuration
files were preserved. The earlier expired batch remains unattempted; it was not resumed.

The four review fixes cover orphan capture recovery, unresolved reservations
across batches, consistent status exit codes, and notebook input integration.
An additional regression prevents a retained failure from being replaced by a
success after interruption. Recovery and failure writes preserve the earlier
bytes; publication uses the later recovery availability time.
