# FLUT research workflow — September 12, 2026

Use **94 → 95 → 96**: inspect current coverage, compare FanDuel with the same
months last year, then review the observations alongside company expectations.
The [current analyst review](flut_current_quarter_review_20260912.md) gives the
findings and next research priorities. All three notebooks default to offline,
read-only analysis.

## Current capture

`data/staging/refresh_20260912T201854Z/gaming_current.sqlite` contains **19,806
observations across 34 state/product series**, with **1,035 referenced source
files verified**. Its SHA-256 is
`751e135c083f66d025ca4d03116021215ac136518ba24b2093f135af044df3ee`.

The run copied the initial September 12 rebuild, then collected MA sportsbook,
MI sportsbook and casino, NY sportsbook and KY sportsbook history. All 19,749
prior observations retain their financial values; the increase is 57 Kentucky
operator/total rows. Other states retain the earlier broad collection's evidence
and coverage limitations. A selected refresh does not certify those states anew.

The run is `complete_with_exceptions`: Kentucky's supported history is deliberately
partial. The collector completed; this status is not a claim of complete coverage.
Review `collection_summary.csv`, `validation.json` and `run_manifest.json` beside
the database. The [Kentucky record](kentucky_recovery_20260912.md) explains the
native AGR definition, exact pages, manual review status and rounding tolerances.
Colorado's accessible official archive still ends in February 2026; the regulator
page returned HTTP 403. Later archive month labels had no report links.

## Refresh and preserve the evidence

1. Open [notebook 20](../notebooks/20_run_all_collectors.ipynb). Choose the explicit
   state/product sources and `recent` or `history` mode. Recent mode supports MA/NY
   sportsbook; use history for the other supported collectors.
2. Select the current `base_database_file`, a new dated `run_name`, and a new
   `backup_file` outside this repository. Defaults generate new names; inspect the
   plan before enabling collection. The base database is never overwritten.
3. Enable both `run_downloads` and `allow_database_writes`, then run the notebook.
   It collects into the new directory, validates SQLite/schema/dates/source hashes,
   writes run receipts, creates the ZIP, checks every member and restores the
   database to verify its hash, integrity and observation count.
4. Read collection exceptions and the completion record. Failed validation or
   backup leaves a failure record rather than a completed capture. Valid partial
   coverage remains visible as an exception. On the command line this returns
   exit 2, not a failed-download instruction to rerun the same destination.
5. Point notebooks 90 and 94–96 at the new validated database deliberately. Keep
   the prior capture for comparison. Save an expectation only after inspecting
   the evidence and assumptions, then back up that new vintage as well.

The command-line equivalent is `gaming/scripts/refresh_gaming.py`; `--help` lists
its explicit source, base, new run directory and external backup arguments. It
previews by default. `--live` performs collection, validation and backup.

The wrapper uses the existing collectors and plain SQLite/pandas functions.
It does not install a scheduler or update an approved valuation. Changed source
versions remain available; conflicting values require review.

## Backup and recovery

The final verified backup is outside the Git repository:

`C:\Users\Sean\Documents\ChatGPT\ResearchOS\flut_goal_20260912\gaming_final_20260912T202914Z.zip`

- ZIP SHA-256: `78563391f20404c4cb62f4fa1e7e7edba0d866c5173757cafcd20ea24e0f4b91`.
- 381,318,484 bytes; 1,198 archive entries, including `archive_hashes.json`.
- Restored database: 19,806 observations, matching the current database hash.
- Includes raw reports/metadata, capture receipts, configuration, parser source,
  and the first frozen expectation with its manifest and outcome status.
- Every member hash and archive CRC passed; the restored SQLite file opened and
  passed its integrity/count checks. Details are in `final_backup_receipt.json`
  in the same audit directory.

To recover, first verify the ZIP hash, then extract into a **new empty folder**.
Check the extracted files against `archive_hashes.json`, open the restored database
read-only and verify its recorded hash/count, and use its accompanying `gaming/`
source tree to resolve raw paths. Do not overwrite an existing research folder.
Code and notebooks are also saved in Git; Git alone does not preserve ignored
captures or frozen expectations. This backup is on the same computer; a separate
device or independent storage location remains the next durability improvement.

The original historical approved databases remain missing. Their exact hashes
and recovery requirements remain in [data recovery](data_recovery.md). Fresh data
does not recreate historical approval or historical public-availability clocks.

## First dated expectation

`data/expectations/flut_2026Q3_20260912T202508Z/expectation.json` was frozen at
**2026-09-12 20:25:11 UTC**. Its SHA-256 is
`a1648b7f51feb3a6d4837bb0ecc305adbe40f747e7d7c3c7b886e484ba13c9f7`.

It retains July state evidence and the **$1,480m derived management reference**
for Q3 2026 Flutter US revenue. No analyst scenario or approval was created.
Evaluation currently returns `pending_actual`, with actual and error fields
missing. The [expectations guide](flut_expectations.md) explains the official
source calculation and the full-quarter scenario inputs.

When Q3 results are public, retain the official source and its publication and
capture timestamps, then evaluate the exact US segment/revenue/USD-million scope.
The API rejects mismatched scope and vintages created after results. A management
reference comparison is not evidence that state data predicted company results;
that requires separate prospective analyst scenarios over multiple quarters.

## Completion evidence

| Requested outcome | Delivered evidence |
| --- | --- |
| Repeatable refresh and recoverable backup | Notebook 20, refresh CLI, validated live capture, verified ZIP and restored database |
| Correct denominator and native definitions | Explicit source contracts; unknown controls excluded; MI gross and adjusted fields separately labeled |
| Comparable FanDuel quarterly scorecard | Notebook 95; eight July-versus-July native metric rows; one of three quarter months |
| Investigate valuable coverage gaps | 57 KY rows recovered and ingested; all 285 printed financial cells checked; CO access/archive limits documented |
| Connect evidence to expectations for review | Notebook 96 plus current analyst review; management reference and optional scenarios separate; no automatic US mapping |
| Freeze and evaluate dated cases | First real snapshot saved and backed up; exact-scope evaluation tested; future actual explicitly pending |

Final gaming validation: **531 tests passed**. The full repository suite passed
**1,593 tests** before the final gaming-only capture-binding hardening, which is
covered by that final 531-test run. The final guarded notebook run produced
**19 PASS and 3 BLOCKED**: all ten current gaming/source notebooks and all nine
vehicle notebooks passed; historical studies 91–93 require their missing exact
database archive. A blocked historical study is not counted as a pass.

Audit logs and receipts are retained in
`C:\Users\Sean\Documents\ChatGPT\ResearchOS\flut_goal_20260912`.
The capture manifest records collection-start code hashes; the final backup and
Git commit preserve subsequent analytical hardening. Do not interpret the
collection-start hashes as the final analysis version.
