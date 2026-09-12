# Clarity methodology experiment

The experiment asks which public website observations can reproduce Clarity's stated metric definitions. It does not claim to have recovered Clarity's private scraper or validated completed customer transactions.

## What Clarity actually discloses

The [CVNA factsheet](https://zwexremjcszwnbaldhkh.supabase.co/storage/v1/object/public/faq-sheets/Clarity_CVNA_Coverage_FAQ.pdf), linked by **Learn more** on [Clarity's coverage page](https://www.clarity-markets.com/data/CVNA), identifies Carvana.com as its source. Page 1 contains the following definitions; page 2 is blank.

| Metric | Stated definition and practical implication |
| --- | --- |
| Orders | Vehicles entering Purchase Pending for the first time within the preceding week. Pending can represent a short reservation or a vehicle being shipped. Count entries separately from the stock of pending vehicles. |
| Pending | Total vehicles classified as ordered. Clarity describes this as backlog, with approximately 70% conversion to sales. This is a vendor statement, not a validated conversion factor for our sample. |
| Inventory | Website-listed vehicles, including pre-orders and already ordered vehicles; active reconditioning inventory is excluded. A public Reservable/pre-order record is not automatically excluded. |
| Listings | First inventory appearances, including partner-sourced vehicles. First observed in our limited history is not necessarily first listed by Carvana. |
| Sales | Intended to match Carvana's retail-unit KPI; Clarity attributes much of the residual error to quarter-end returns/cancellations. The actual website event or classification rule is undisclosed. |
| Price | Asking prices captured at listing, sale classification and across daily inventory. Partner accounting prevents direct equivalence to Carvana's reported ASP. |
| Other fields | Delivery estimates for five leading metros per state, Carvana's per-vehicle KBB metric and advertised financing terms before customer credit inputs. These are estimates, not realized delivery or financing outcomes. |

The coverage page advertises daily report updates; it does not establish the scraper's intraday frequency. The factsheet also leaves timezone, repeat-order reset rules and the exact sale detector unspecified. Its historical accuracy figures are self-reported, without the evaluated vintages needed for an independent audit.

Clarity's [May 2, 2025 pricing discussion](https://claritymarkets.substack.com/p/used-car-market-trends-coverage-analyzing) explains that sold ASP includes earlier prices because delivery timing varies. This supports preserving vehicle price histories and comparing event lags; it does not establish direct access to delivery records.

The plausible workflow is repeated VIN/listing observations, pending entries, subsequent sale qualification, reappearance checks and lagged price/count estimates. Public search records plus targeted detail checks could supply many of these ingredients. Clarity's actual request endpoints and implementation remain unknown.

## What the supplied workbooks establish

The six supplied paths, including the earlier `(2)` file, contain three unique files. `(2)`, `(2) (1)`, `(1)` and `(3)` are byte-identical. The full versions contain sales through May 2 and May 7; the Free file contains only April 1 headline sales. Blank cells remain missing.

The newer full version preserves earlier sales and adds 7,339 units over five dates. This pair does not show retrospective sale revisions; it cannot exclude adjustments made before publication or in other versions. The comment at `Sheet1!A7` describes manually smoothing sales after a late script run, while orders and listings remained harder to adjust. Published daily sales therefore need not equal raw daily observations.

For May 7, `50,206 + 2,574 - 44,454 = 8,326` implied exits, versus 1,560 published sales: a residual of 6,766. This calculation assumes first listings, inventory and sales use the same population and interval. Reappearances, changed coverage, timing and classification can violate that assumption; the residual is neither confirmed sales nor identified cancellations. Source cells are `BJ37`, `Q38`, `BJ38` and `C38`.

The workbook run rate scales the prior-year same-quarter seasonal series by current-to-prior cumulative sales. It is not simply daily sales multiplied by remaining days. The audit reconstructs arithmetic from populated cells without executing Excel formulas. Local file timestamps and row dates do not prove publication availability, so these 2025 sheets cannot directly score a September 2026 vehicle sample.

## What is implemented

Open [Notebook 23](../notebooks/23_carvana_daily_sales_research.ipynb), restart the repository kernel and Run All. The optional Clarity section reads retained evidence only:

1. `clarity-method-settings`: inspect source paths and the evidence cutoff.
2. `clarity-orders`: review observed pending entries, uncertain initial baselines, complete prior-week coverage and repeat suppression.
3. `clarity-exit-rules`: compare all exits, pending-preceded exits, and three-/seven-day absence on the same exit episodes.
4. `clarity-transport`: inspect the frozen detail plan and the stopped transport result.
5. `clarity-workbooks`: inspect distinct workbook versions, missing values, changed formulas and inventory residuals. The linked audit also retains the run-rate reconstruction.

[clarity_experiment.py](../src/vehicle_tracker/clarity_experiment.py) contains small pandas functions shared by the notebook and tests. The pending experiment treats a same-listing false-to-true change across consecutive complete days as an observed entry. It then requires a complete prior week and suppresses entries observed within seven calendar days of the previous entry. This cooldown is an explicit interpretation of Clarity's wording, not proof of its private reset rule. Remaining pending does not create a fresh order each week. Daily snapshots can still miss short holds.

Exit rules have their own decision times. Native Sold already known at a rule's decision is separated from later corroboration. Later matched checks must refer to the same retailer, VIN and listing, and occur before a recorded reappearance. A first observed Sold label is distinct from an observed non-Sold-to-Sold transition. Unchecked episodes remain visible; selecting only successful follow-ups would overstate reliability.

At the study cutoff `2026-09-12T03:29:50.837558+00:00`, the offline replay has 74 exit episodes:

| Rule | Selected | Already-known Sold excluded | Eligible for follow-up | Episodes with later matched checks | Later native Sold | Unchecked eligible episodes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| All exits | 74 | 2 | 72 | 12 | 10 | 60 |
| Pending before exit | 60 | 2 | 58 | 9 | 8 | 49 |
| Three-day absence | 0 | 0 | 0 | 0 | 0 | 0 |
| Seven-day absence | 0 | 0 | 0 | 0 | 0 | 0 |

All four rules are evaluated against the same 74 episodes. The rules overlap: do not add their counts. The later native checks are existing retained observations, not successful captures from the new HTTP trial. No three-/seven-day candidates have matured. The checked subsets were not a representative transaction-label sample; 10/12 is a selected website-corroboration fraction, not estimated sale precision. These are candidate denominators and observed statuses, not sales counts.

Pending replay contains 237 first/unknown baselines and 87 observed false-to-true changes. Of the changes, 75 have an incomplete interval or changed listing, and 12 lack a complete prior week. No entries qualify for the weekly proxy yet. The weekly result is **unavailable**, not evidence of zero orders.

## The bounded live transport test

A frozen plan selected six diagnostic targets: two continuity checks, two randomized exits and two randomized still-listed controls. The question was whether anonymous public detail HTML exposes the same selected native fields as the existing Chrome helper.

The first request, at `2026-09-12T03:26:15Z`, received HTTP 403. The run stopped with **one request, zero native captures and five unattempted targets**. There were no retries, browser fallbacks or inventory requests. This test did not establish an alternative production transport. It also did not establish that Clarity uses a different data source; access can vary by client and context.

[probe_carvana_details.py](../scripts/probe_carvana_details.py) previews by default. Explicit live mode uses a new destination, at most six targets, a bounded plan window and a stop on the first access/identity/parse failure. Treat the recorded `status="stopped"` as authoritative: the original recorded invocation returned shell exit 0 before the later CLI correction. A corrected CLI does not change that historical run. Do not resume the expired plan.

From the repository root, this command only previews the retained plan; it makes no requests or files:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/probe_carvana_details.py --plan vehicle/data/experiments/clarity_method/20260912T032143Z/plan.json --destination vehicle/data/experiments/clarity_method/20260912T032143Z/unused_preview
```

The [verification record](../data/experiments/clarity_method/20260912T032143Z/verification.json) records 689 passing vehicle tests and seven passing offline notebooks. Two final offline exports reproduced all 46 tables and their other output files identically. All 1,988 pre-existing data/configuration files and six original workbook paths remained byte-identical. All 37 original Notebook 23 cell IDs remain; only its default cutoff and final table registry changed among the original cells. Replaying the prior cutoff preserves all 38 original tables exactly. The [final exported tables](../data/experiments/clarity_method/20260912T032143Z/export_3/manifest.json) are for review, not imported operating data.

## Next study: fourteen days, then finish follow-ups

1. Freeze one manageable inventory segment, ZIP, query definitions, daily capture window, random seed and request budget. Preserve the existing 33-VIN cohort separately. A seven-day initial lookback is needed before reporting qualified weekly-order proxies; missing days extend the warm-up.
2. Collect complete inventory snapshots for fourteen days at the same time. Preserve raw responses and completion metadata. Missing or partial sweeps do not become zero inventory, zero orders or mass exits.
3. Retain every qualifying exit episode in the analysis denominator. If detail capacity is insufficient to check all exits, freeze a random subset plus random still-listed controls; save the eligible frame, inclusion probabilities, unattempted cases and selection reason before observing their detail outcomes. The study may need a smaller inventory segment to fit the budget.
4. Use the established, accessible capture workflow within its existing limits. Perform the initial planned check and follow-ups after approximately 24 hours and seven days. Reserve request capacity for repeats before adding new targets; late exits require follow-up after day fourteen. Report actual observation intervals and missing scheduled checks.
5. Compare each rule's qualified episodes, matched-check coverage, later native Sold fraction, non-Sold outcomes, reappearances and unresolved cases. Report observed intervals between pending, exit and first Sold, plus the last asking price. Do not infer delivery dates, transaction prices or returns from those fields alone.
6. Publish the first estimate and subsequent revisions side by side. Compare with Clarity only when dates, population, timezone and metric definitions match. Do not scale a Tesla segment to national sales or hardcode the vendor's approximately 70% conversion statement.

A consistent website proxy and manageable request load would justify a broader vehicle panel. They would not by themselves validate economic sales. Establish repeatable coverage and outcome maturity before an all-cars expansion.

## Retained sources

- [Public FAQ manifest](../data/experiments/clarity_method/20260912T031340Z/public_sources/manifest.json), [PDF](../data/experiments/clarity_method/20260912T031340Z/public_sources/Clarity_CVNA_Coverage_FAQ.pdf) and extracted text: retrieved anonymously from the public link, with SHA-256 and retrieval time. The server's modification date is April 11, 2025; it is not proof of when each definition took effect.
- [Workbook manifest](../data/experiments/clarity_method/20260912T031631Z/workbooks/manifest.json) and [audit](../data/experiments/clarity_method/20260912T031631Z/workbooks/audit.json): original-path/hash mappings, deduplicated retained copies, source-cell references and explicit unknown publication clocks.
- [Frozen six-target plan](../data/experiments/clarity_method/20260912T032143Z/plan.json) and [stopped run](../data/experiments/clarity_method/20260912T032143Z/http_probe/run.json): planned identities, sampling context, request count and failure evidence.
