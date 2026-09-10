# Carvana inventory MVP: overnight handoff

The daily research workflow is ready for use in
[notebook 20](../notebooks/20_carvana_history_analysis.ipynb). It has 25 cells,
including 12 executable cells. Run All reads retained evidence only. The software
supports investigation; daily sales accuracy is still unvalidated.

## Your first five minutes

1. Launch Jupyter from the repository root with
   `powershell -File scripts/start_jupyter.ps1`, open notebook 20, and Run All.
2. Read the opening daily review: population, collection window, coverage, and
   why a daily comparison is or is not available. Inspect query counts before
   interpreting inventory changes.
3. In section 5, copy the retailer, VIN, and listing ID of one displayed vehicle
   into `SELECTED_IDENTITY`. Rerun that cell to see the original URL and its history.
   Empty queues can use inventory controls; no vehicle is silently selected.
4. After a real page check, fill `CHECK_DRAFT` in section 6 with exact wording,
   status, actual check time, evidence reference, reviewer, and uncertainty. Rerun
   that draft cell so the edited values and manual-recording permission take effect.
5. Right-click the notebook tab in JupyterLab and choose **New Console for
   Notebook**. Run the first Markdown command block there to prepare and preview.
   Review the identity, timestamps and destination, then run the second block to
   intentionally save and reread the record. Keep these commands outside executable
   notebook cells so ordinary Run All stays read-only.

Save retries use the same `prepared_check`: they do not duplicate the CSV row.
Changing the prepared contents requires preparing and previewing again. Corrections
preserve the original physical check time with later availability; new visits have
new check times. Save uses the existing check CSV and lock, not inventory writes.
Rerun the notebook to reread the evidence; advance a historical cutoff deliberately
if you want later records included.

## What changed

| Files | Purpose |
| --- | --- |
| `notebooks/20_carvana_history_analysis.ipynb` | Opening daily review, query and queue diagnostics, explicit selection, manual check entry, one VIN history, and one expanded synthetic lesson; saved offline outputs |
| `src/vehicle_tracker/daily.py` | Pure dictionary preparation with stable content-bound ID; dictionary/JSON share one recording path; placeholders and known Sold boilerplate rejected |
| `src/vehicle_tracker/checks.py` | Revalidate referenced listing-to-VIN ambiguity, including conflicts introduced after preview |
| `src/vehicle_tracker/timeline.py` | One small pure helper combining inventory, derived absence, physical page checks/corrections, and missing-date rows |
| `scripts/benchmark_history.py` | Optional reproducible mixed-history benchmark; existing constant-inventory scenario preserved |
| `tests/test_notebook_recording.py`, `test_vin_timeline.py`, `test_notebook_workflow.py` | New behavior and regression coverage, including blocked writes and actual manual command snippets in temporary storage |
| `tests/test_checks.py`, `test_daily_notebook.py` | Narrow fixture/example updates |
| `README.md`, `docs/daily_inventory.md`, `docs/listing_checks.md`, `docs/daily_cycles.md` | Operating steps, corrections, recovery, backup, and accurate notebook references |

No dependencies, schema, service, scheduler, browser collector, or sales conversion
were added. Existing three-day candidate and 48-hour recheck rules are unchanged.
The implementation adds 208 net Python lines across runtime modules and
the benchmark, excluding tests/notebook/documentation.

## Real evidence versus the invented example

The retained register still has one complete September 8, 2026 daily snapshot:
674 Tesla Model 3 VINs, 215 native pending flags, seven year queries, and 31 requests.
The observed sweep spans 89.63 seconds. The five retained page checks show one
available, two pending, and two unknown/pre-order results. None observed a Sold label.
At the saved notebook cutoff, four unresolved listings are in the queue, all up to
date; none is yet selected for a due check. These are sample/operating counts, not
daily sales. There is no second daily comparison or sale estimate.

The optional invented example separately shows listed, pending, missing, site-marked
sold, then reappearance under another listing ID. It includes a missing collection,
a partial day, and a late-entered check. The earlier cutoff excludes that later
entry. None of the invented records was saved to the real history.

## Validation and scale

The full repository suite passed: **726 tests**, with 14 existing plotting warnings.
All **15 notebooks passed** the root offline checker. Notebook 20 was also executed
offline with saved outputs at **2026-09-09T03:48:11.466975+00:00**; its tables and
explanations were inspected. Low-level write guards and temporary-storage tests
exercise Run All, filled drafts, and the actual manual prepare/save/replay commands.
`git diff --check` passed. All **1,199 original Carvana data files** have unchanged
hashes, with no added/deleted data files. Unrelated original workspace files,
staged state, branch and HEAD remain unchanged. The before-state verification is
retained locally in `C:\Users\Sean\AppData\Local\Temp\carvana_overnight_before_8px9h__4`.

The independent review's material findings were fixed: inconsistent override
cutoffs, save-time ambiguous identity, and generic Sold-boilerplate acceptance.
An additional valid-plan display case defaults omitted location filtering to False.

Reproduce the mixed offline benchmark from the repository root:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/benchmark_history.py --vehicles 10000 --days 14 --scenario mixed
```

The final script was run with 10,000 starting VINs across 14 calendar days:
13 retained cycles, 123,300 observations, and 5,143 captures. It includes 200
additions, 200 persistent removals, 100 returning VINs with new listing IDs, pending
changes/unknowns, a missing date, a partial day, and 80 check versions for 60 listings.

| Phase | Seconds |
| --- | ---: |
| Generate temporary source evidence | 2.96 |
| Import retained reports into temporary SQLite | 30.95 |
| Read SQLite history | 1.09 |
| VIN events and daily summary | 8.62 |
| Full daily report with saved checks | 21.99 |

Cumulative process peak working set was 747.18 MiB; it is not an isolated allocation
for a single phase. The report phase recomputes events and is separate from the
standalone event measurement. These are local measurements, not timing guarantees
or the runtime of a normal notebook/collection. They exclude the daily register's
source-revalidation path and establish nothing about live access or national coverage.
Temporary benchmark data were cleaned up. No new infrastructure is justified by
this bounded workload. Selecting a VIN currently validates/calculates events for
the whole supplied population, so large-history selections can cost several seconds.

## Remaining limits and next action

1. Sales classification and transaction dates remain unknown. Collect the unchanged
   pilot on the next actual local date at a similar time, then check missing/changed
   vehicles and controls. Preserve gaps. Continue the initial 7-10 day observation
   trial; more independent labels may still be needed.
2. Page wording and evidence references are supplied by the analyst. Validation
   checks identity/structure and one known boilerplate case; it does not authenticate
   evidence or classify arbitrary text. A Sold label is not a completed, dated,
   net-of-returns sale. There is no automated page collection.
3. This fixed pilot is not national coverage or a representative sales sample.
4. Root package/setup changes and the gaming relocation remain uncommitted and
   untouched. Committed HEAD still expects old `src`/`tests` paths; this working
   checkout uses `gaming/src`, `vehicle/src`, and the relocated notebook checker.
   A fresh checkout of the interim commit alone is not fully reproducible.
5. Captures, SQLite, registers, checks, and exports are ignored by Git. Follow the
   [backup guidance](daily_inventory.md#back-up-the-evidence-not-just-the-code),
   including absolute-path limitations, before moving or restoring the project.

No live requests, new inventory cycles, page checks, staging, commits, or pushes
were performed. Proposed next commit title: **Make Carvana daily review and page
checks usable from the notebook**. Scope: the vehicle files listed above; preserve
the unrelated gaming and shared setup changes for separate review. Include this
handoff document in that proposed scope.
