# Applied vehicle workflow review — September 11, 2026

Implemented sequentially in `C:\Users\Sean\VscProjects\researchOS\vehicle`.
Actual HEAD matched the supplied `78b67842db32bd10daf796ee0470e508b2e7dec2`, on
`main`. Before editing, six tracked modifications and nine untracked files were
recorded and copied to a temporary review folder. Existing work was preserved.
No collection, historical-data rewrite, migration, dependency, commit or push.

## Refactoring

- Notebook 20: one settings section; population/completeness first, inventory and
  the existing matched-VIN comparison next, pricing next, source walkthrough last.
  Manual checks/recording and synthetic lessons remain optional. The check-history
  preparation is a short visible cell shared by the walkthrough and optional audit.
- Notebook 22: cohort coverage, native statuses, transitions/repeats, attention,
  then one native evidence timeline. Current follow-up identity selection no longer
  depends on loading the historical CARFAX/title study. Old study, sensitivity and
  batch-accounting calculations remain intact in labelled optional sections.
- Notebook 30 is explicitly titled experimental assumptions/scenarios; calculations
  are unchanged. All 40 original Notebook 20 and 28 original Notebook 22 cell IDs
  were retained. No helper module or additional notebook was added.
  Stale saved Notebook 22 output displays were cleared; the preflight copy retains
  them, and the results below come from fresh offline execution at fixed cutoffs.
- Collection/storage remain unchanged: `search.py` retains response evidence and
  projects the search fields; `carvana.py` validates native values; `storage.py`
  saves captures/observations; `history.py` replays retained evidence into SQLite;
  `cycles.py` checks identity, scope, clocks and completeness before notebook use.
  Source-less partial checkpoints stay diagnostic-only and missing dates stay missing.

## New behavior

1. **Intentional status correction:** only a complete target `hero_badge` matching
   `On Hold\n[0-9]{2}:[0-5][0-9]` adds pending evidence. Retained examples are
   `On Hold\n00:00` (listing 4681095) and `On Hold\n19:08` (listing 4682416).
   Original wording is preserved. Native Available/Purchasable and identity/UI
   consistency remain required. A zero countdown does not establish expiry.
   The earlier [source study](status_validation_20260908.md) independently described
   a hold as another customer beginning the purchasing process. No transaction is inferred.
2. **Days since first observed:** visible pandas joins use the existing valid pair
   and matched retailer/VINs with both asking prices known. Age is elapsed seconds
   from the first observation in selected complete collections to the later vehicle
   observation, divided by 86,400. Both source clocks and cycle availability are
   retained. Initial-cohort cars may have been listed earlier; gaps do not prove
   continuous listing. Model/age tables show denominators, reduction counts/percent,
   and median positive-dollar reduction among reduced cars only. No causal claim.
3. **Expansion preview:** separate tracking configuration points to the original
   frozen proposal. The existing CLI now displays every query and destination.

## Review these cells first

| Notebook | Cell IDs in reading order |
| --- | --- |
| [20](../notebooks/20_carvana_history_analysis.ipynb) | `daily-operating-view` (settings), `daily-cycle-data`, `daily-query-quality`, `daily-vin-analysis`, `daily-asking-prices`, `daily-price-bridge`, `daily-observed-age-prices`, `daily-source-trace` |
| [22](../notebooks/22_carvana_sale_status_validation.ipynb) | `pilot-settings`, `pilot-coverage`, `pilot-source-rows`, `pilot-results`, `pilot-freshness`, `pilot-evidence-timeline` |

For the earlier comparable inventory example, set `AS_OF_OVERRIDE` to
`2026-09-10T02:00:00Z`. For the reviewed latest evidence use `2026-09-11T12:00:00Z`.
Normal Run All uses the current cutoff and remains offline/read-only.

## Same-cutoff before/after results

Every pre-existing Notebook 20 DataFrame is exactly unchanged at both cutoffs.
Inventory is 674 on September 8, 681 on September 9, missing on September 10, and
706 on September 11. Latest-period price comparison remains unavailable; the
notebook does not bridge September 10 or silently reuse September 8/9.

The earlier pair has **673 eligible matched VINs, zero reductions, 0%, undefined
median reduction**. All are Tesla Model 3 in the `<1 day` observed-age group.
The existing mean-price increase remains $72.83, entirely inventory composition
under the displayed sequence, with zero common-sample repricing.

Only the hold correction changes existing Notebook 22 results:

| Metric | September 10 02:00 UTC, before → after | September 11 12:00 UTC, before → after |
| --- | --- | --- |
| Physical checks / matched native checks | 38 / 37, unchanged | 74 / 73, unchanged |
| Latest pending / unknown statuses | 8 / 8 → 9 / 7 | 15 / 9 → 17 / 7 |
| Usable resolved checks / unresolved checks | 28 / 9 → 29 / 8 | 56 / 17 → 58 / 15 |
| First qualifying Sold transitions | 0 → 0 | 1 → 1 |
| Repeated Sold observations after first Sold | 4, newly displayed; unchanged evidence | 4, newly displayed; unchanged evidence |
| Attention-list membership | 19 → 19 | 19 → 19 |

These are the same one/two corrected rows propagated through status, cohort,
inventory-page comparison, study comparison and attention tables. The optional
old-pass accounting's unresolved-status row changes from 8 to 7 at its baseline,
and from 9 to 7 at the latest cutoff. Its other numbers, batch membership, physical
times, native fields, first-Sold bounds, reappearances and measured-sales nulls are
unchanged. Later-cutoff coverage includes later passes; it is not all attributable
to the historical batch. All other existing DataFrame values matched exactly.

## One source-to-result walkthrough

At the earlier cutoff, retailer `carvana`, VIN `5YJ3E1EA0RF763544`, listing `4712757`:

1. The retained [September 8 projection](../data/experiments/carvana_daily/2026-09-08/attempt_0001/tesla_model3_2024/raw/0042a4573188db9fa4a2f0738e4e47735174cfcc302b1fdc5ccda072117033f6.json)
   records `price.total=34990`, `isPurchasePending=false` at
   `2026-09-09T01:44:11.074146Z`. The cycle was available at `01:45:40.724750Z`.
2. SQLite stores `asking_price_usd=34990`, `purchase_pending=0`; the notebook joins
   `observations.capture_id` to `captures` and verifies the retained file's SHA-256.
3. The same retailer/VIN/listing was observed at `2026-09-09T10:59:31.570451Z`, again
   at $34,990; that cycle was available at `11:01:07.378901Z`. Thus the asking-price
   change is **$0** across **9.255693 hours**, despite consecutive local dates.
4. This is its first selected collection, so days since first observed at the later
   observation is `9.255693 / 24 = 0.385654`: the `<1 day` group. It contributes one
   eligible vehicle and zero reductions. Its actual listing start is unknown.

## Four-model preview ready for review

From `C:\Users\Sean\VscProjects\researchOS`:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --config vehicle/config/carvana_four_model_tracking.json
```

Frozen query order (all ZIP `08542`, location prefilter omitted):

| # | Query ID | Make / parent model | Year |
| --- | --- | --- | --- |
| 1 | tesla_model3_2024 | Tesla Model 3 | 2024 |
| 2 | tesla_model3_2023 | Tesla Model 3 | 2023 |
| 3 | tesla_model3_2022 | Tesla Model 3 | 2022 |
| 4 | tesla_model3_2021 | Tesla Model 3 | 2021 |
| 5 | tesla_model3_2020 | Tesla Model 3 | 2020 |
| 6 | tesla_model3_2025 | Tesla Model 3 | 2025 |
| 7 | tesla_model3_2026 | Tesla Model 3 | 2026 |
| 8 | chevrolet_equinox_2022 | Chevrolet Equinox | 2022 |
| 9 | chevrolet_equinox_2023 | Chevrolet Equinox | 2023 |
| 10 | chevrolet_equinox_2024 | Chevrolet Equinox | 2024 |
| 11 | ford_escape_2022 | Ford Escape | 2022 |
| 12 | ford_escape_2023 | Ford Escape | 2023 |
| 13 | ford_escape_2024 | Ford Escape | 2024 |
| 14 | toyota_corolla_2022 | Toyota Corolla | 2022 |
| 15 | toyota_corolla_2023 | Toyota Corolla | 2023 |
| 16 | toyota_corolla_2024 | Toyota Corolla | 2024 |

Limits: **120 explicit requests, 900 seconds, at least 3 seconds between request
starts, 24 rows/page, MostPopular ordering**, existing sequential full-plan runner.
No resume of old captures is selected. Dated counts in the proposal are planning
evidence only; the limits can stop an incomplete sweep and do not guarantee coverage.

Resolved destinations, under the absolute root
`C:\Users\Sean\VscProjects\researchOS\vehicle\`:

| Purpose | Resolved suffix |
| --- | --- |
| Capture root | `data\experiments\carvana_four_model_daily` |
| Previewed date folder | `data\experiments\carvana_four_model_daily\2026-09-11` |
| Database | `data\analysis\carvana_four_model_daily\history.sqlite` |
| Register | `data\analysis\carvana_four_model_daily\cycles.json` |
| Exports | `data\analysis\carvana_four_model_daily\tables` |
| Checks | `data\analysis\carvana_four_model_daily\listing_checks.csv` |
| Reviews | `data\analysis\carvana_four_model_daily\candidate_reviews.csv` |

The preview computes a fresh 900-second window each time; it does not reserve it.
The unchanged operating seven-query configuration and 33-VIN cohort remain separate.
**Ready for a separately authorized bounded collection trial**, with combined live
coverage/access reliability still untested. No live trial was performed here.

## Validation

From the repository root, using the existing `.venv`:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider vehicle/tests
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py
```

- Baseline: **542 passed**, five existing headless-plot warnings, 69.55 seconds.
- Final: **568 passed**, the same five warnings, 83.49 seconds; no unresolved failures.
- Notebook check: **all six PASS** (00, 10, 20, 21, 22, 30), with network, exports
  and writable SQLite blocked. Notebook 20 executes 21 code cells; Notebook 22, 17.
- Focused tests exercise the observed hold formats, malformed/unrelated text,
  native/UI and identity conflicts, saved-capture replay, unchanged first-Sold
  counting, missing/zero denominators, partial/gapped comparisons, 9.25-hour
  intervals, model grouping and evidence-availability cutoffs. Existing notebook
  assertions were retained; the moved check-history setup is executed explicitly.
- The actual 16-query CLI preview passed both normally and with transport/file-write
  guards. Neither experimental capture nor analysis destination was created.
- **All 1,639 pre-existing data/configuration files are SHA-256 unchanged.** The
  only added file in those trees is the new experimental tracking configuration.
  All pre-existing changed/untracked non-notebook files remain byte-identical.
- `git diff --check` passes; HEAD/branch are unchanged. No commit or push.

Preflight originals, per-file hashes, before/after DataFrames, difference inventory,
preview output and test logs are retained locally in
`C:\Users\Sean\AppData\Local\Temp\researchos-review-20260911-a4mmkpkq`.
The reviewable code/configuration and this result summary live in the workspace.

## Correction: due-time selection for the follow-up batch

The follow-up plan now calculates `next_due_at = last_usable_native_at +
PILOT_RECHECK_HOURS`. It selects first checks, then repeats due at or before the
cutoff, oldest due time first across all cohorts/control groups. Retailer, VIN and
listing ID break ties. Group labels remain available for analysis. The default
single-pass limit remains 12 and now accepts only 1–12, matching the unchanged importer.

Latest failed/conflicting attempts and attempts without usable native evidence
remain explicitly marked for review and deferred from the batch; they never reset
the earlier native clock or authorize retries. Missing verified URLs also require
review. The full plan displays due time, eligibility and the reason for deferral.
Eligible overflow is distinguished from vehicles that are not yet due.

Saved-evidence replay at **2026-09-11T12:00:00Z**:

| Selection | Before correction | After correction |
| --- | ---: | ---: |
| Batch size | 12 | 9 |
| Overdue vehicles selected | 5 | 9 |
| Not-yet-due vehicles selected | 7 | 0 |
| Previously deferred overdue controls selected | 0 | 4 |
| Remainder | 21 | 24 |

The four newly included controls have listing IDs **4465259, 4678187, 4436696 and
4674263**. All nine overdue vehicles are selected from the retained evidence;
identities/counts are not hardcoded in selection logic. All 24 remainder vehicles
are not yet due. Batch and remainder are disjoint and account for all 33 cohort VINs.

The 74 physical checks, native statuses, one first-Sold transition, zero observed
reappearances, and all inventory/pricing results are unchanged. Before/after
comparison verified every other existing DataFrame, allowing only display-order
normalization for the unchanged group counts.

Review Notebook 22 cells **`pilot-settings`**, **`prospective-followup-plan-intro`**
and **`prospective-followup-plan`**. These are the only changed cells; all cell IDs
and their order are preserved. The earlier sections above describe the initial
implementation; this note supersedes its follow-up selection policy.

Correction validation: the required vehicle suite passed **586 tests** in 78.38
seconds with the same five existing plotting warnings; **all six notebooks PASS**
under the offline guards. The focused follow-up tests passed 22 cases, covering
inclusive due times, empty/short batches, overdue controls, first versus failed
checks, preserved native clocks, shuffled inputs, limits and complete accounting.
All **1,640 existing data/configuration files** and all other files outside the
three-file correction scope remain unchanged. `git diff --check` passes. HEAD
remains `78b67842db32bd10daf796ee0470e508b2e7dec2` on `main`. No live collection,
imports, database changes, configuration edits, commit or push were performed.
Preflight copies, hashes, replay tables and validation logs are retained in
`C:\Users\Sean\AppData\Local\Temp\researchos-followup-correction-fc9hl_0s`.
