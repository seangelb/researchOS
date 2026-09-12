# Daily sales research — September 11, 2026

## Continuation review at 2026-09-11 16:41:25 UTC

The continuation verified the exact requested checkout, **`codex/carvana-daily-sales-research` at `6053ab5274100dd290877a98feb0046ff870fb2f`**, with the intentional uncommitted research changes present. That state was preserved: no reset, branch switch, commit or push. The earlier baseline account below describes the preceding task, not this continuation's starting state.

**Recommendation remains the existing search POST for inventory, with targeted public detail checks for native status.** The current frozen-cohort proxy remains **one first native non-Sold-to-Sold event**, provisional contribution 1, current contribution 1, revision 0. Ten newly checked inventory exits were already Sold at their first native check; they are separate corroborating observations, not ten new cohort transitions or confirmed transactions.

### Implemented

- `sales_proxy.reappearance_summary` reports episode returns and unique retailer/VIN returns against matching denominators. Actual retained results are 9/54 for both; a synthetic repeated-VIN test demonstrates why these cannot generally be mixed.
- `inventory_followup_queue` retains all 33 frozen members, adds recent exits, pending changes, relistings and conflicts, and reserves seeded noncandidate controls. It follows the latest complete inventory listing when a later visit to an obsolete page disagrees. VIN-level 24-hour spacing includes failed checks and listing-ID changes; selected-listing native freshness remains separate. Existing native Sold evidence excludes a VIN from noncandidate controls.
- `native_estimate_revisions` replays evidence-availability vintages and preserves contributions even when a later source correction removes an earlier event entirely. Notebook 23 now shows this bridge. Immediate, three- and seven-day absence comparisons stay unavailable on missing current dates.
- The search projection preserves demonstrated optional **`previousPrice` and `priceUpdateDate`**. The UTC timestamp is checked as an actual date/time. Missing fields retain the old projection shape, normalized SQLite rows are unchanged, and no database migration was added. This is an offline-tested retention change, not a newly collected bulk field series.
- Notebook 23 exposes the vendor hypotheses, workbook arithmetic, pending-stock identity, source-field coverage, readable VIN histories, both measured detail batches, next-check selection and remaining limits. Its explicit export includes the vendor-audit script hash and retained source hashes.

### What was collected successfully

New local evidence is in [the continuation folder](../data/experiments/vendor_review/20260911T162600Z/). Four dated public vendor sample pages were retained separately in [the vendor audit](../data/experiments/vendor_method_audit/20260911T162442Z/). Twenty previously retained, page-referenced JavaScript assets were hash-checked; no new asset requests were needed.

| Actual detail batch | Frozen selection | Result | Elapsed span / minimum start spacing |
|---|---|---|---|
| First | Nine overdue members of the original cohort, plus three seeded controls from a 379-VIN endpoint frame | 12/12 identity-matched native captures. One pending-to-purchasable change; three repeated Sold observations; two retained pre-order members; one native Available/NotPurchasable historical page. Controls: two Purchasable and one WIP/Reservable, all Available. | 502.363 seconds / 21.411 seconds |
| Second | Ten recent exits selected by queue rank, plus two seeded noncandidate controls from a 590-VIN frame | 12/12 identity-matched native captures. **10/10 selected exits native Sold; 0/2 controls Sold**. One control Purchasable, one WIP/Reservable. | 225.545 seconds / 15.004 seconds |

Both plans were retained before navigation. Each batch stayed within 12 visits. Total: **24/36 detail visits**, no access challenges, no navigation retries, and **12 detail visits remaining**. The first batch's elapsed span includes tool-inspection/analysis overhead, so it is not pure page latency. Browser subrequests and HTTP response codes were not measured. All actions were ordinary page navigation and read-only extraction; no reservation, checkout, account or purchase action occurred.

Nine existing-cohort observations were imported through the existing experimental importer into two fresh dated runs, preserving the 33-VIN definition and operating databases. The other 15 observations remain separate diagnostic evidence. They inform subsequent check selection, without entering the cohort's native-event numerator.

**New inventory requests: zero.** The preceding task already used one explicit request, returning HTTP 200 but retaining zero rows because of the documented local path failure. Its fixed 900-second window ran **12:42:34.331016?12:57:34.331016 UTC** and is expired. The nominal 119 unused request slots do not reopen it. Older September 10/11 follow-up passes preceded this same-task experiment and are identified separately in the retained budget ledger; the newer 36-visit ceiling supersedes the older 48-visit setting. No inventory retry, new national crawl, competing collector or database was launched.

### Vendor reconstruction and measured comparison

See [the vendor hypothesis/evidence table and dated forecast checks](carvana_daily_sales_external_research.md). The supplied workbook is unchanged; a byte-identical source copy and its SHA-256 bind the notebook's extracted rows.

- **Run rate:** `91,901 / 65,581.14985868326 ? 155,941 = 218,525.1993`, using the prior year's seasonal pattern. Flat `91,901 / 40 ? 92 = 211,372.3` is a different calculation. The notebook reconstructs formulas in pandas; it does not claim an Excel recalculation engine ran.
- **Fractional prior-year counts:** all 92 values reconstruct as integer counts multiplied by `155941/158863`. This strongly supports common normalization, but the unadjusted counts' original provenance and vintage remain unknown.
- **Workbook stock flow:** July 2 implies `74,347 + 3,941 ? 75,792 = 2,496` exits versus 2,376 sales. Across 39 numeric intervals, exits exceed sales by **12,171**. July 6 repeats inventory/pending and has zero additions despite 2,152 sales, followed by a large July 7 residual. Timing, missing updates and population differences remain competing explanations; this is not a cancellation series.
- **Population mismatch:** reported model-year shares cannot be generated by the same headline inventory denominator at retained precision. The smallest compatible tested denominator is 111, not a proven sample size. A 50/50 sold-year split is incompatible with that day's odd 2,159-unit sales total under a common full population.
- **Sold Price:** its transaction-price meaning remains unverified. JXCE explicitly uses list prices in its projected ASP inputs. Last asking price is a plausible reconstruction; it is not realized revenue.

Retained search/visible-card evidence agrees on **21/21 VIN/listing identities and asking prices**, while JSON-LD agrees on only **20/21** despite containing 21 rows. Exact native `saleStatus` is absent in that search sample but present in all 24 new detail captures. A previous ask of **$49,990**, current ask **$49,590**, and native update **2026-09-02T15:02:19.188Z** occur in one of 21 retained rows; the other 20 missing histories do not prove no repricing. Specifications and delivery-related fields are demonstrated, but seller/Marketplace attribution and paired-VIN delivery estimates across ZIPs remain unvalidated.

The retained inventory reconciles at the endpoints: `674 + 8 ? 1 = 681`; `681 + 98 ? 73 = 706`. Pending stock also reconciles: `215 + 12 ? 22 + 0 ? 0 = 205`; `205 + 75 ? 15 + 22 ? 60 = 227`. These equations count pending entries/clears and pending inventory entrants/exits. They do not identify orders completed, canceled or returned. September 10 remains missing; no daily flow is allocated across that gap by these identities.

### What was validated prospectively, and what remains a hypothesis

The new plans precede their new page observations. They prospectively test later **website** outcomes: the pending-clear case remains native Available/Purchasable, repeated Sold controls create no extra events, and the ten deliberately selected exits all have later native Sold labels. This is useful conditional corroboration. Selection was enriched, only 10 of 73 recent exits were checked, and both signals come from Carvana's website. No transaction precision, representative conversion rate, actual delivery day, net-return adjustment or national scaling was validated.

The nine earlier reappearances remain chronological retained-evidence analysis, not a historical point-in-time backtest. Later observations must still test three-/seven-day absence and reappearance, with rules frozen before outcomes. Dated vendor forecast pages support limited pre-result quarterly comparisons; their present mutable publication metadata does not prove immutable historical vintages or daily accuracy. Registration/title/ownership sources offer a different evidence lineage but have unresolved lag, backfill, seller attribution and permitted-retention questions. No data was purchased and no vendor was contacted.

### Measured expansion prerequisites and next informative dates

The latest retained seven-query sweep needed **35 explicit POSTs** and **100.435 seconds between capture endpoints** for 706 VINs. The older broad trial required 500 requests over 1,500.754 seconds; 10,019 admitted VINs included only 9,107 from complete queries. Duplicate/missing identities and changing totals caused real failures. An older displayed 80,576-unit population implies only a conditional lower bound of 3,358 requests / 2.80 hours at 24 rows and three-second spacing; partition overhead, failures and population drift add cost. No complete national enumeration was demonstrated. ZIP-sensitive ranking is not a separate population.

Expansion first requires repeat complete comparable partitions, a measured candidate/control miss rate, documented inclusion probabilities for any population estimate, explicit Marketplace attribution and retention of unknown periods. The second batch took 225.545 seconds for 12 visits; covering all 73 exits, let alone pending changes, costs several such batches and was not authorized within the remaining allowance. The current 36-visit task budget cannot provide a national validation sample.

The existing first-Sold VIN `5YJ3E1EA7NF288274` / listing `4567173` reaches its 24-hour threshold at **September 12 02:19:09.912 UTC** (September 11 22:19 New York). It was not revisited early. The second batch reaches 24 hours by **September 12 16:40:18.662 UTC** and seven days by **September 18 16:40:18.662 UTC**. Those repeat observations become informative about persistence/reappearance, not transaction completion. If a newly authorized unchanged daily inventory series begins with a September 12 baseline, three complete absent dates can first qualify on **September 15**, seven on **September 19**, with a later holdout check on **September 26**. A missed or failed date postpones maturity. Nothing was scheduled and the expired inventory window was not reset.

### Continuation validation and review files

Final checks: **641 tests passed** (five existing noninteractive-plot warnings), **all seven notebooks passed** with network/exports/SQLite writes blocked, and `git diff --check` passed. Two independent exports at **2026-09-11T16:41:25.820875+00:00** produced identical hashes for all **39 outputs** (36 CSV tables, two figures and execution text), with 438 identical input hashes and identical code hashes. Both figures were visually inspected. All **1,783 pre-existing data/configuration files** were byte-identical afterward; the original workbook also retains its recorded SHA-256.

Independent review reproduced and resolved obsolete-listing selection, corrected-away event revisions, missing-current-day false zero, timestamp validation and independent expected-identity binding. Additional guarded replays confirm that September 12's missing inventory leaves all three absence estimates NA, and a September 1 pre-cohort cutoff produces coverage only. No independent economic-sale labels were acquired.

Review [Notebook 23](../notebooks/23_carvana_daily_sales_research.ipynb), [final tables/figures](../data/experiments/vendor_review/20260911T162600Z/final_export/), [validation record](../data/experiments/vendor_review/20260911T162600Z/validation.json), and the two frozen plans/pass manifests in the continuation folder. The sources are local and Git-ignored; retain them with the research outputs. Exact branch/HEAD remain the supplied reference and all changes remain uncommitted.

## Earlier task record (preserved)


**Use the first native non-Sold-to-Sold transition as the current tracked-cohort proxy.** Count each prospective VIN once, retain its observation interval and first-available clock, and withdraw its current proxy contribution if later native Available or inventory presence contradicts it. A one-for-one interpretation is an explicit gross sale-like-event assumption, not calibrated completed sales or a lower bound on Carvana's net retail units.

Review [Notebook 23](../notebooks/23_carvana_daily_sales_research.ipynb), then the [source comparison and vendor questions](carvana_daily_sales_external_research.md). The notebook contains the actual input rows, two source-to-SQLite traces, method comparisons, holdouts, prices, allocation and continuation queue. Normal Run All is offline/read-only. [sales_proxy.py](../src/vehicle_tracker/sales_proxy.py) contains only plain pandas calculations; the existing readers retain authority over source hashes, identities and SQLite reconciliation.

## Baseline and preserved work

The exact checkout is `C:\Users\Sean\VscProjects\researchOS`, initially clean `main` at `6053ab5274100dd290877a98feb0046ff870fb2f`, identical to the supplied baseline. No intervening commits needed inspection. Created `codex/carvana-daily-sales-research`. Read `vehicle/AGENTS.md`; no applicable parent AGENTS file was present. Existing configuration, 33-VIN membership, retained captures and operating databases remain unchanged. No commit, push, purchase, vendor contact, subscription or scheduling occurred.

New evidence and explicit exports are isolated under [the dated study](../data/experiments/daily_sales_research/20260911T124200Z/). `preservation_before.json` fingerprints all 1,640 pre-existing vehicle data/configuration files. New notebook cells have stable IDs; no existing notebook was edited.

## Empirical results at 2026-09-11 12:48:13 UTC

| Method / observation | Result | Interpretation |
|---|---:|---|
| Native physical checks / matched native checks | 74 / 73 across 33 VINs | One access-failed historical check remains visible |
| Prospective / initially non-Sold prospective VINs | 30 / 29 | Three historical controls; one prospective VIN already Sold initially |
| First qualifying native Sold transition | 1 | VIN `5YJ3E1EA7NF288274`, listing `4567173` |
| Conservative ≥24-hour repeat / central / combined / expansive | 0 / 1 / 1 / 1 | Zero strict qualifiers; absence branches not mature, so agreement does not validate them |
| Native pairs | 35 Available→Available, 1 Available→Sold, 4 Sold→Sold | Four Sold repeats are historical controls; the new transition has no later check |
| Complete daily inventory | 674 / 681 / 706 on Sep 8 / 9 / 11 | Sep 10 missing; partial 97 rows excluded |
| Comparable endpoint flows | 674 + 8 − 1 = 681; 681 + 98 − 73 = 706 | Actual start intervals 9.256 / 47.900 hours; no unexplained count residual |
| Three-/seven-day persistent absence | 0 qualifiers; estimates unavailable | Only two consecutive complete dates; insufficient history |
| Earlier-departure holdout | 9 of 54 VINs later reappear | Five same-query sweeps; same listing IDs; not a transaction false-positive rate |
| Later native holdout | 24 usable later outcomes; 1 Sold | Of 15 initially pending, 13 still pending, 1 available, 1 Sold |

The transition is bounded by September 10 **00:26:18.172 UTC** and September 11 **02:19:09.912 UTC**, a **25.881-hour** interval. It became available at **02:23:02.872973 UTC** on September 11. Uniform elapsed-seconds allocation gives **0.137615 September 9 + 0.862385 September 10**, New York dates. The endpoint alternative puts the unit on September 10. Neither identifies the actual sale/delivery date. Dates outside detected-event intervals stay missing; a complete daily company estimate or seven-day average is unsupported.

The nine reappearances use two independently replayed older same-query sweeps (694 and 693 VINs) plus the three registered sweeps. Their legacy availability timestamps are absent, so this is chronological retrospective validation, first verified for this study at its cutoff, not a historical point-in-time backtest. Seventy-three latest departures remain right-censored. The latest 98 endpoint additions include seven previously observed VINs when this longer history is used.

The extension's selection was reproduced from the retained snapshot and frozen seed: 18/204 pending and 8/474 nonpending eligible vehicles. Pending is 69.23% of the sample versus 30.09% of the frame. Design weights are displayed but not applied to national sales. Four deliberately selected model families leave unobserved segments with zero inclusion probability.

Among the latest 608 matched VINs, 50 asking prices decrease, one increases and 557 are unchanged: total −$23,000, average **−$37.829**. Overall inventory mean rises **$79.100** because composition changes. Asking prices are not transaction prices. Bulk native inventory codes remain unmapped; Carvana's reported retail-unit definition includes Marketplace partners and is net of returns, unlike this uncalibrated gross event proxy. The external-source note retains primary citations and the material licensing/lag gaps.

## Experiments that failed or remain limited

The active-collector check found none. One isolated 16-query inventory trial used the prepared four-model plan, sequential spacing ≥3 seconds and hard limits of 120 requests / 900 seconds. It made **one explicit POST**: HTTP 200, 230,152 recorded response-content bytes, then failed to retain source data. There are **zero admitted vehicle rows**, no usable broad inventory estimate, and 15 unattempted queries. The failed journal is retained; no second trial or recovery was attempted.

Offline reproduction identified a Windows path failure: the directory is 183 characters, the final hash-named JSON path 253, but the temporary filename expanded to 261. The one-line `storage.py` change uses the short temporary prefix `capture.` while preserving final hashes and atomic publication. Both original-response and selected-response retention pass the new regression test. The original journal lacked a detailed traceback; the matching failure was reproduced independently rather than fabricated from the HTTP response.

Ordinary browser discovery returned no connected browser. **Zero new detail-page visits** were made; browser network requests were not measured. Nine due VINs are retained in the exported queue. No direct HTTP detail-page fallback or access-control workaround was used.

Immediate disappearance was rejected as the recommended sale rule because actual same-ID returns were observed. Three-/seven-day and combined approaches are implemented and tested for edge cases but lack mature real history. Registration/title/ownership sources are the strongest independent corroboration candidate; no suitable licensed VIN-level outcomes were available. MarketCheck's inferred sales share disappearance logic and are not transaction ground truth. No precision, recall or invented conversion probability is reported.

## Validation and next actions

The new synthetic tests cover initially Sold and controls, repeated checks, corrections, conflicting identities/statuses, delayed evidence, reappearances, absent/gapped/changed-scope collections, 3/7-day qualification, deduplicated combined rules and DST/seconds allocation. Real-data holdouts above are kept separate.

Final validation: **615 tests passed**, with five existing noninteractive-plot warnings; **all seven notebooks passed** with network, exports and SQLite writes blocked. Two independent exports have identical hashes for all **23 output files** (20 CSV tables, two chart PNGs and execution text), with 357 identical input hashes and identical code hashes. Both charts were visually inspected. A pre-cohort cutoff exports coverage only, and a pre-Sold cutoff excludes the later event. All **1,640 original data/configuration files remain byte-identical**. `git diff --check` passes. Detailed records are in the dated study's `validation.json` and `preservation_after.json`. Run from the Git root:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider vehicle/tests
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py
git diff --check
.\.venv\Scripts\python.exe -B vehicle/scripts/export_sales_proxy.py --as-of 2026-09-11T12:48:13Z --destination vehicle/data/experiments/daily_sales_research/20260911T124200Z/my_review
```

The export destination must be new. It contains tables, chart PNGs, and a manifest binding the explicit cutoff, input files, current code, Python/pandas versions and output hashes. It does not collect or change SQLite. Local evidence is ignored by Git and needs backup with its existing absolute-path source dependencies.

**Smallest next experiment:** once ordinary browser access is connected, recheck the nine already-due VINs plus listing `4567173` after **September 11, 22:19:09.912 New York** (September 12 02:19:09.912 UTC), in a batch of at most 12. Use the existing capture/import workflow, preserve failed attempts and stop on access blocks. Follow the seven-query inventory at the same local time for eight complete dates, then inspect candidates and noncandidate controls seven days later. A later separate four-model trial should use a short fresh dated destination and this retention fix. Do not resume the failed evidence or schedule a collector. Vendor questions are prepared in the external note; acquisition/contact remains a separate user decision.
