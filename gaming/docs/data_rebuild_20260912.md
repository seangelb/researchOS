# FLUT data rebuild and review — September 12, 2026

The user authorized recollecting the lost gaming database using the latest available
official reports. Collection finished at **2026-09-12 19:32:06 UTC**, retaining
**19,749 observations across 34 state/product series**. All 42 existing registry
entries were processed: 30 reported `ok`, four reported partial coverage, and eight
recorded source/reporting limitations without numeric observations. Some of those
eight entries replay the inventory's September 5 source assessment; they are not
fresh September 12 availability checks.

This is a fresh capture of the history currently served by those sources. It is
unreviewed research evidence. It does not reconstruct the original approved
database hashes or historical publication clocks, and it changes no valuation.

## Use the rebuilt data

- Start with [notebook 94](../notebooks/94_gaming_industry_update.ipynb): coverage and
  observation age, native FanDuel rows, and recent New York weekly handle share.
- Use [notebook 90](../notebooks/90_consolidated_ggr.ipynb) to explore states,
  products, dates, and separate revenue measures. Its stale saved outputs were cleared.
- Database: `data/staging/rebuild_20260912T191234Z/gaming_current.sqlite`.
- In the same directory, `current_source_status.csv` and `needs_attention.csv`
  show current dates and exceptions; `stored_observations.csv` lists all series.
- `run_manifest.json`, `collection_summary.csv`, `source_manifest.json`,
  `validation.json`, collection logs, and the run drivers preserve the audit trail.
- Studies 91–93 retain their historical outputs and original gates. Their exact
  earlier databases are still missing; see [recovery details](data_recovery.md).

## What is current?

These are the latest periods present in this capture, not a claim that all sources
have published the same month or that every market is complete.

| Source / product | Latest reporting period |
| --- | --- |
| New York sportsbook | Week ended September 6, 2026 |
| West Virginia sportsbook and casino | Week ended September 5, 2026 |
| Maryland and Maine sportsbook; Delaware casino | August 2026 |
| Massachusetts, Michigan, New Jersey, Pennsylvania, Ohio and Kansas sportsbook | July 2026 |
| Michigan, New Jersey and Pennsylvania casino | July 2026 |
| Illinois sportsbook | June 2026; July/August detail CSVs were unavailable |
| Colorado sportsbook | February 2026 in the collected state-library archive |
| Kentucky sportsbook | April 2025; only two manually supported monthly rows |

For the New York week ended September 6, FanDuel reported **$167.95m handle**
against **$483.31m statewide**, a **34.75% handle share**. FanDuel cash GGR was
**$16.89m**. The new notebook reconciles all operators against the printed total
within one cent for every displayed week. These are descriptive weekly figures;
they do not establish customer migration, sustainable hold, or Flutter net revenue.
Sources: [FanDuel workbook](https://gaming.ny.gov/system/files/documents/2026/09/weekly-mobile-sports-wagering-report-fanduel_0.xlsx)
and [statewide workbook](https://gaming.ny.gov/system/files/documents/2026/09/weekly-mobile-sports-wagering-report-statewide_0.xlsx).

The partial collectors retain valid rows and explain the missing reports:
Indiana's early workbooks lacked online brand rows; one older Maryland report had
no mobile rows; Illinois lacked the listed monthly CSVs; two Nevada reports lacked
statewide mobile detail. Missing values remain missing. Kentucky and Colorado
demonstrate why a successful collection status is not a freshness guarantee.

## Verification and backup

- SQLite integrity and the normalized observation schema passed for all 19,749 rows.
- Every stored source binding was checked against retained bytes: **1,032 distinct
  source files**, including Illinois's ordered handle/tax file pairs.
- No observation has a future period end. The 38 pre-existing gaming data files
  matched their pre-run hashes.
- Separate handle, gross, adjusted, taxable, and net-proceeds consolidations were
  exported. Missing values, incomplete operator sums, and source conflicts remain
  explicit; schema/hash validation does not establish economic comparability.
- The guarded offline notebook run passed **17 notebooks**, including 90 and 94.
  Studies 91–93 were explicitly blocked by the missing historical databases.
  The checker returned a nonzero exit because those blocks are not successes.
- The full repository test run passed 1,450 tests and identified one obsolete
  North Carolina archive-count assertion. Four cumulative PDFs now contain 29
  monthly observations. The test now parses every retained version without assuming
  a fixed live file count; all eight North Carolina fixture/archive tests passed.
  After that repair, the complete gaming suite passed **379 tests**. Vehicle's
  tests had already passed in the full repository run; no vehicle files changed.

The ZIP backup contains the new database, retained raw reports, configuration,
parser source, run drivers and capture/validation manifests:

`C:\Users\Sean\Documents\ChatGPT\ResearchOS\flut_followup_review_20260912\gaming_rebuilt_20260912T191234Z.zip`

The archive is **339,373,749 bytes** with 1,205 entries. Its CRC check passed.
The database was also restored from the ZIP into a separate audit file and reopened
read-only: all 19,749 observations, SQLite integrity, and the exact hash matched.
This is a separate local backup on the same computer, not an off-device backup.

- Database SHA-256: `b9cad7e91ccd06093205a0fab84d8d67723fcdf5ab937657afcb832dd82edc25`
- ZIP SHA-256: `27324540efc208071bd4c3da621a58dab12d887dbc43319c28ccc4dc460534ea`
- `backup_receipt.json` beside the ZIP records the successful restore check.

## What to improve next

The goal should be a dependable FLUT research loop: **what changed, whether the
data is complete and comparable, and which quarterly assumption deserves review**.
Keep the notebook/pandas/SQLite design and focus the next work on four outcomes:

1. **Make recovery routine.** Use a new dated destination for each refresh and
   keep database, source bytes, configuration, and manifests together. Require a
   tested backup before treating a refresh as complete; retain an off-device copy.
   This run establishes the pattern, but no recurring backup was scheduled.
2. **Repair the most useful coverage gaps.** Revisit Colorado and Kentucky's
   discovery limits, then periodically recheck the eight source/reporting gaps.
   Keep reporting-period age separate from last download time. Notebook 94 now
   shows that age and a configurable 90-day review flag, not an invented deadline.
3. **Tighten denominator and metric definitions before a rolling signal.** In
   notebook 92's `panel` function, the unrecognized-provenance branch does nothing.
   A synthetic Michigan case with `unknown_unreviewed_status` was accepted as a
   40% share after numeric reconciliation. Define explicit source/state denominator
   eligibility and reject unknown provenance before reusing this study with fresh
   data. This does not demonstrate an error in the missing historical rows.
   Also make Michigan sportsbook's generic `Adjusted Gross` label distinguish its
   separate gross and adjusted numeric columns. Do not infer a measure from that
   single generic label.
4. **Build one useful quarterly FanDuel scorecard.** Review operator/licensee
   mappings and comparable monthly windows, then show handle share, native revenue,
   hold and coverage alongside the existing quarterly assumptions. Separate casino
   from sportsbook and diagnostic estimates from approved forecasts. A wider set
   of collected states alone will not produce a reliable Flutter revenue bridge.

The current change adds the data view, freshness visibility, documentation, checker
coverage, and the archive-test repair. Collector/parsing code, source definitions,
historical approval hashes, and vehicle code were unchanged.
