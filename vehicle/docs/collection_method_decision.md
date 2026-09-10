# Carvana collection decision

Reviewed September 9, 2026, America/New_York (September 10 UTC). This review uses retained observations from September 8–10 UTC, current source code, and current primary tool documentation. It made **zero new Carvana search requests and zero browser visits**. The optional live benchmark was not needed: the saved experiments already demonstrate field extraction, pagination failures, ZIP effects, and browser access challenges. Another short successful pass would not establish unattended daily reliability.

**Recommendation: use the existing public search POST for inventory and asking prices; use a small, explicit Chrome detail-page cohort for native website-status evidence.** Keep the existing Python → retained JSON → SQLite → pandas/notebook workflow. First validate a modest expansion of the daily population. Do not start a detail-page crawl of every car or claim national inventory coverage.

The search endpoint is an **observed, undocumented website endpoint**, not an officially supported Carvana API. Website `Sold` evidence is useful, but it does not establish delivery, registration, transaction price, or an exact accounting sale date.

## What the evidence actually supports

| Method | Identity and fields demonstrated | Access, cost and recovery | Recommended role |
|---|---|---|---|
| Public search POST | VIN, listing ID, year, make/model/parent model, mileage, asking price, pending/lock/purchase/inventory codes, on-demand flag and optional transport cost. Up to 24 cars per response. | Repeated successful small cohorts; 500-request broader trial. Changed totals and duplicate identities caused real enumeration failures despite HTTP 200. Sequential requests, retained failed pages, no automatic retries. | Inventory and asking-price history. |
| Ordinary Chrome capture | Scoped result cards can validate inventory fields. Detail-page public application data supplies VIN/listing identity and native `saleStatus`/`purchaseType`; visible vehicle badges provide additional context. | Browser visits load unmeasured subrequests. Recent detail pass stopped on a security challenge. Partial evidence survives; remaining cars stay unchecked. | Small reference checks and targeted status validation. |
| Existing headed Playwright/Chrome trials | Nine valid cars on initial fresh and persistent-profile launches. | Actual ZIP was 08540, not expected 08542. Persistent restart redirected and returned a 403 challenge. DOM/JSON-LD could disagree after pagination. | Retained diagnostic tool; no demonstrated reliable unattended replacement. |

Evidence behind the comparison:

- The [paired search/card captures](../data/experiments/20260908_search_schema/search_shape.json) matched **21/21 IDs and asking prices**. JSON-LD matched only 20/21 IDs. The browser response had 21 vehicles plus three promotional tiles; the lean direct POST supplied 24 vehicles. Counting tiles as cars or reading unscoped recommendations would corrupt membership.
- [Playwright access probes](../data/experiments/20260908_access/access_probes.json) record initial loads of 2.688 and 2.563 seconds, both with the wrong ZIP. Restart records a 307 followed by a 403. These are access probes, not comparable whole-query throughput measurements.
- The [September 7 browser experiment](../data/experiments/20260907_approaches/method_comparison.json) produced a 36-car eastern cohort twice. Western pagination returned 36 rows but only 35 IDs; partitioning exposed the missing identity. Its 475-second mixed experiment and 21 browser operations include interaction/inspection overhead; browser network requests were not comprehensively counted.
- The [latest recovered detail pass](../data/experiments/carvana_sale_signals/pass_recovery_20260910T013836Z-437d819a/pass_report.json) has **24 attempted captures, 23 usable native contexts, 16 resolved interpretations, seven unresolved interpretations, one failed capture and nine unvisited cars**. Physical checks span 171.77 seconds. Import happened later and made no new visits. The final challenge is described by the contemporaneous browser observation; the saved failed projection has no measured HTTP status. Do not relabel it a recorded 403. This old pass did not test the present prompt's 15-second browser spacing.

Two older conclusions have been superseded: the September 7 report said Playwright was absent and recommended DOM collection before the POST had been validated; September 8 artifacts demonstrate both installed-browser trials and the working POST. Also, 10,019 is the broader trial's admitted sample, **not its complete-query inventory**: only **9,107 VINs** belong to completed queries. Preserve the older reports as historical evidence.

### How the POST becomes a table

One demonstrated request shape is:

```http
POST https://apik.carvana.io/merch/search/api/v2/search
Content-Type: application/json
```

```json
{
  "filters": {
    "makes": [{"name": "Tesla", "parentModels": [{"name": "Model 3"}]}],
    "year": {"min": 2024, "max": 2024}
  },
  "pagination": {"page": 1, "pageSize": 24},
  "sortBy": "MostPopular",
  "zip5": "08542"
}
```

The response contains `inventory.vehicles` and `inventory.pagination`. Each vehicle becomes one table row. The latest retained 2024 query reported 42 vehicles: page 1 provided 24, page 2 provided 18. Both pages were retained and reconciled before the query was marked complete. The POST sends filters directly; it does not discover inventory by guessing VIN URLs. Browser `cvnaid` filter URLs are a different way the website represents filter choices; the Python collector uses the observed JSON POST contract.

[`search.py`](../src/vehicle_tracker/search.py) saves an allowlisted projection, validates response context and identity, and passes normalized rows to existing storage. `price.total` maps to `asking_price_usd`; mileage is in miles. Transport cost remains separate and may be missing. Each retained projection has a SHA-256; the report separately records the original response-content hash and length. **A projection is not the complete response body**, and cannot recover discarded fields later.

All admitted rows in the six audited search stages had VIN, listing ID, vehicle attributes, asking price and native status fields. Transport cost was missing in 207 of the 10,019 broader-sample rows. That success describes these captures, not a promise about future responses. The present retained search projection does not provide the detail page's exact `saleStatus`; inspecting that field requires the demonstrated detail capture. Its absence from the projection does not prove no future search response could expose additional fields.

Keep native codes as codes. The broader sample contains `vehicleInventoryType` **1: 7,729; 2: 2,289; 4: 1**. Their economic meanings are not verified. Do not silently select only type 1, call type 2 sold, or describe all returned listings as owned, immediately purchasable retail stock. Inventory type is retained in the raw projection even where it is not a normalized SQL column.

## Population, pagination and freshness

The operating daily plan is [seven Tesla Model 3 exact-year queries, 2020–2026](../config/carvana_daily_pilot.json), ZIP 08542, with location prefiltering omitted. It excludes other makes, other Tesla models, and years outside that interval. It is an operational pilot, not a representative Carvana sample.

The [controlled location experiment](../data/experiments/20260908_location_filter/report.json) reported 80,585 with the feature off, 71,015 with it on at 08542, 58,993 with it on at 90210, then 80,585 off again. The observed on-setting is `requestedFeatures: ["LocationBasedPrefiltering"]`. ZIP remains relevant delivery context even with this feature omitted. Equal off-setting totals do not prove equal memberships or universal delivery eligibility. The nearby-ZIP/parent-child audit reconciled just **69 VINs**, with some transport charges differing. **Never add ZIP totals together.**

The existing [full candidate manifest](../config/carvana_full_candidate_20260908.json) is broader but not proven exhaustive:

- A retained unfiltered response reported 80,576; its 40 make counts summed to that total.
- Native year **filter metadata** gave 2010–2027. There are no retained year-bucket counts proving these are exhaustive vehicle-population boundaries. For example, an exact-2022 response still exposes generic minimum/maximum year metadata.
- The initial candidate used 40 makes × 18 years = **720 make/year queries**. Thirty make/year groups were probed. Eighteen parents were replaced by 249 parent-model children: **720 − 18 + 249 = 951 queries**. The broader trial selected 30 − 18 + 249 = **261** of them.
- All 30 selected model-facet sums reconcile to their own parent totals. No exact query duplicates, parent-plus-child double counting within a make/year, or overlapping model IDs within those inspected parent-model facets were found. That is useful structural evidence, not a proof about unseen categories.
- The 261-query trial covers ten makes and 2022–2024 only: Chevrolet, Ford, Honda, Hyundai, Jeep, Kia, Nissan, Ram, Toyota and Volkswagen. It completed 158 queries, stopped four attempts incomplete, and left 99 unattempted. The remaining 793 queries in the full-candidate history plan are historical gaps, not a fresh daily manifest.

Only `makes[].name`, `makes[].parentModels[].name`, `year.min/max`, the observed ZIP context and location feature are verified here. Do not invent inventory-type, unknown-category, status, price-range, cursor, larger-page-size or alternate-sort filters. `categoryIds` such as American and 4WD SUVs overlap; they are not exclusive census partitions.

Four different checks must remain visible:

| Question | Evidence needed | Current position |
|---|---|---|
| Were pages collected successfully? | HTTP/content checks, parsable rows, source bytes/hashes and identities. | Demonstrated, including separately retained failures. |
| Did a query reconcile? | Requested pages, matching context, stable reported totals/pages, unique valid IDs/VINs, union count equal to reported total. | Demonstrated for selected small queries; three broader attempts failed on drift. |
| Did the intended population get covered? | Frozen scope, all required queries, no unexplained overlaps or residuals, verified source exclusions and category boundaries. | Narrow declared scopes can be checked; broad/national population remains unverified. |
| Is this a fresh comparable daily collection? | Every required query complete within the same declared daily window, same scope/context on both dates, physical capture clocks and no stale carry-forward. | Two pilot cycles passed their scope checks; their starts are only 9.256 hours apart. A stable daily cadence is not yet demonstrated. |

`MostPopular` is a changing ranked list, not a snapshot token. Even stable totals and no repeated IDs cannot prove instantaneous membership. **Illustrative example:** page 1 returns A/B; B leaves and D arrives; page 2 returns D/E. The total stays four and the collected union A/B/D/E has four unique IDs, yet C was skipped and no instant necessarily contained the collected set. Counts reconcile while membership drifts.

The existing collector correctly stops a query on changed totals or repeated/missing identities. In the broad trial, Jeep Grand Cherokee 2023 stopped at 432/494 after totals changed; Ram 2023 at 120/232 and Hyundai 2022 at 72/464 after repeated identities. Volkswagen 2022 stopped at 288/289 at the sample target. Failed pages remain evidence, not admitted inventory. Do not weaken these checks or deduplicate away a pagination failure to increase counts.

For later broader collection, keep the existing 951-query manifest as a **candidate specification**, refresh its source facets before use, and record four residual diagnostics separately: make-count residual, per-make/year parent-model residual, identities outside declared year/category boundaries, and unexplained parent/child membership differences. Missing/unaddressable categories are an unresolved gap, not zero. If the source exposes no verified way to enumerate a residual, label broad coverage unverified; do not guess a filter. Fresh before/after parent totals and rotating small parent/child identity audits can detect changes but cannot certify atomic membership. If a facet changes, review a new manifest version for the next cycle rather than silently changing today's scope.

## Concrete next population and measured capacity

The new [proposed 16-query manifest](../data/experiments/collection_method_review/20260910T020000Z/proposed_broader_manifest.json) preserves the seven Tesla queries and adds three exact years for each family below. Every entry includes its retained count source, timestamp and SHA-256. The current operating configuration is unchanged.

| Parent-model family | Years | Retained planning count | Page estimate at 24/page |
|---|---|---:|---:|
| Tesla Model 3 | 2020–2026 | 681 | 33 |
| Chevrolet Equinox | 2022–2024 | 95 + 87 + 129 = 311 | 14 |
| Ford Escape | 2022–2024 | 77 + 41 + 48 = 166 | 8 |
| Toyota Corolla | 2022–2024 | 58 + 62 + 77 = 197 | 10 |
| **Proposed combined scope** | **16 exact-year queries** | **1,355** | **65** |

These are **planning estimates from different retained times**, not a new inventory count. All six added 2022/2023 filters have completed retained enumerations; the three 2024 filters have facet evidence only and need their first enumeration. Escape includes the source's Plug-in Hybrid variants. Corolla includes Hybrid/Hatchback, but excludes the separate Corolla Cross and GR Corolla families. Equinox excludes the separate Equinox EV family. Keep those native family meanings in the notebook.

The [retained coverage preview](../data/experiments/collection_method_review/20260910T020000Z/retained_coverage_preview.csv), produced with the existing `query_readiness` function, shows all 16 queries: 13 with selected complete historical evidence and three unattempted. Their unobserved counts remain missing; facet estimates stay separately identified in the manifest. The mixed-date preview has no identity conflicts and is not a fresh combined cycle.

This selection adds non-Tesla manufacturers and model families while fitting a small experimental budget. It is a deliberate convenience cohort, not a statistical sample or a basis for extrapolating Carvana sales. Query-level completeness can be tested without claiming whole-site coverage.

The [retained measurements](../data/experiments/collection_method_review/20260910T020000Z/retained_measurements.json) are reproduced by the adjacent read-only `analyze_retained.py`. All 195 query reports were audited; its 655 page artifacts pass retained-hash checks. Independent replay confirmed admitted identity counts and query outcomes.

| Retained search run | VINs admitted | Complete queries | Explicit POSTs | Invocation seconds | Response-content MB | Projection MB | Experiment MB |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1,000-car target | 1,006 | 7/8 | 47 | 138.938 | 9.554 | 0.486 | 1.142 |
| Broader sample | 10,019 | 158/261 | 500 | 1,500.754 | 103.527 | 4.936 | 13.548 |
| Intraday Tesla repeat | 693 | 7/7 | 34 | 99.788 | 6.265 | 0.336 | 0.822 |
| Parent/ZIP audit | 69 (207 memberships) | 4/4 | 10 | 27.855 | 1.970 | 0.100 | 0.281 |
| Daily pilot September 8 local | 674 | 7/7 | 31 | 90.724 | 5.982 | 0.325 | 0.797 |
| Daily pilot September 9 local | 681 | 7/7 | 33 | 96.834 | 6.146 | 0.330 | 0.811 |

MB = 1,000,000 bytes. Response-content bytes are the recorded `len(response.content)` (potentially decoded), **not wire traffic**. Projection bytes include retained failed pages. Experiment bytes include reports and per-query SQLite files; daily rows measure `attempt_0001/`, excluding outer cycle metadata and the separate operating analysis DB. Disk allocation, Python memory and browser network bytes were not measured. All 655 recorded responses were HTTP 200; that does not make all 655 pages usable or every query complete.

Across all phases of the September 8 history trial, the separate [request ledger](../data/experiments/20260908_history_trial/task_budget.json) records 576 requests and 120,469,097 response-content bytes. Minimum measured spacing was 3.000079 seconds; first-to-last request starts span 2,326.663 seconds. Discovery, preparation and audits therefore have real costs beyond the 500-request enumeration.

The recovered 24-detail-capture pass retained 22,924 bytes in capture files and 82,418 bytes including its three import manifests. Browser response bytes/subrequests were unmeasured. Its 171.77-second check interval is descriptive, not a reliable scalable rate or a benchmark at the proposed pacing. No paid provider was used; human time, access interruptions and machine/network costs remain real and unpriced.

**Extrapolations, not measurements:**

- At the old 16-query counts, 65 requests imply **192 seconds between first and last starts** at three-second spacing, plus response/processing time. Allow 120 requests/900 seconds for a future bounded experiment, including any explicitly budgeted probes. Totals can grow or drift; partial remains partial.
- Scaling the broader trial's bytes/request to 65 requests gives roughly **13.46 MB response content and 0.64 MB retained projections**. It excludes separate SQL/report overhead and uses an imperfect sample mix.
- At the old 80,576 reported total, the unpartitioned minimum is `ceil(80576/24) = 3358` requests: **2.80 hours of pacing**. Partitions, empty queries, audits, failures and restarting incomplete queries add cost. At the broader trial's bytes/request, approximately **695 MB response content and 33 MB projections per sweep**, or **254 GB and 12.1 GB per 365 identical sweeps**, before database/report/backup overhead. This is an order-of-magnitude capacity scenario, not demonstrated national coverage or daily access reliability.
- At least 15 seconds between 80,576 individual detail-page visits would alone take **335.7 hours**. Even the 1,355-car cohort would take 5.64 hours before overhead. Targeted status checks are essential.

The current generic plan budget caps a call at 600 requests/3,600 seconds. **The newer daily-cycle code already supports explicit larger limits up to 10,000 requests/21,600 seconds** with durable budgets; older reports describing 600 as a universal limit are outdated. The active daily configuration remains 120/1,200. No limit was increased here, and implementation capacity is not evidence that a larger sweep works reliably.

## Architecture and smallest ordered backlog

```text
Frozen explicit query plan
  -> sequential search POSTs -> retained projections + per-query reports
  -> validated daily cycle -> existing SQLite history -> notebook 20
                                                     |
                                      small review queue / frozen cohort
                                                     v
                               Chrome public detail capture -> notebook 22
```

Use retailer + VIN for vehicle tracking and retain listing IDs and query memberships. Across overlapping queries, take identity unions and expose conflicts; do not sum memberships. Keep asking prices, transport, pending, missing, native Sold labels and reviewed sale events separate. Compare prices on matched VINs so changes in inventory mix do not masquerade as price changes.

1. **Show the proposed manifest and coverage in the existing notebook.** Reuse `query_readiness`, `read_query_evidence` and pandas; display every query, source count/date, estimated pages, completed/partial/unattempted state, actual interval and overlaps. The manifest is supplied in this review; notebook edits are the next stage. Expose the four completeness questions above and preserve the old pilot as its own scope. No new framework or database schema is needed.
2. **Validate one fresh expanded experiment, then repeat at a consistent local time.** Reuse `collect_plan(full_plan=True)` with a new experiment destination and no old captures. The specific new uncertainty is the combined four-family scope, especially three unenumerated 2024 queries. Require all 16 queries to reconcile. Subsequently use `collect_cycle` for a reviewed daily scope with a frozen window and durable budget; keep the new scope separate from the Tesla baseline. A week of explicitly run comparable cycles is a useful first operational trial, not proof of permanent reliability. Report scheduled/attempted/complete days, request counts, failures, freshness and duration. No scheduler is needed.
3. **Continue the existing small status-validation cohort.** Reuse the public capture helper, importer and notebook 22; prioritize previously missing/pending vehicles while retaining a few available controls and fixed selection reasons. Cap and space visits; stop the whole live session at an access challenge. Keep failed/unvisited checks missing. A transition from native non-Sold to native Sold establishes an observation interval; initially Sold, repeated Sold, unavailable, missing or pending alone does not establish a new completed sale. Registration changes and daily sales calibration remain outside this collection decision.
4. **Expand beyond the cohort only when reliability and residual evidence justify it.** Reuse the 951-query candidate and existing windowed cycle/checkpoint machinery. Refresh facets, reconcile exclusions and choose a realistic sweep window first. Smaller verified model partitions can reduce drift exposure but add requests. Broad `--resume-from` helps historical recovery; it does not make old observations current. Daily recovery must stay within the identical date/window/plan/budget and must never resume a stopped or uncertain access failure automatically.

Current transport checks stop on access/transport failures and disable redirects; query-data failures leave that query incomplete while other partitions can continue. Requests' `timeout` is not a hard total elapsed-time deadline. Before any benchmark that needs a strict cutoff, retain a separate elapsed-time watchdog/termination procedure and record an uncertain in-flight request as attempted; do not advertise a guaranteed 900-second hard deadline from the socket timeout alone. No operating transport was changed in this documentation stage.

### Exact next commands

Run from `C:\Users\Sean\VscProjects\researchOS`. These commands are **read-only previews or offline checks**, and do not collect, register a cycle or write the operating DB:

```powershell
.\.venv\Scripts\python.exe -B vehicle/data/experiments/collection_method_review/20260910T020000Z/analyze_retained.py
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_search.py --plan vehicle/data/experiments/collection_method_review/20260910T020000Z/proposed_broader_manifest.json --experiment collection-method-expanded-preview --full-plan --max-requests 120 --max-seconds 900
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py
```

To open the notebooks interactively, separately run `powershell -File scripts/start_jupyter.ps1`. This launches Jupyter and can create runtime files; it is not a read-only validation command and was not run in this review.

The experiment preview prints a destination; it does not create it. A future live run needs a new explicit destination and a deliberate `--live` instruction after this review. The CLI's `--experiment` accepts a plain name, not a nested path: for any benchmark required to live specifically under `collection_method_review/`, use the existing Python function with that explicit destination rather than pretending the CLI can accept slashes. No live command is executed or scheduled by this document. Do not use `run_carvana_daily.py --live` for the proposed expansion: it still points to the unchanged Tesla operating configuration.

### Primary documentation and remaining limits

Current primary documentation was consulted for tool behavior, not treated as a guarantee of Carvana access:

- [Requests quickstart](https://requests.readthedocs.io/en/latest/user/quickstart/) documents default redirect following, `allow_redirects=False`, response content and timeout behavior. The collector explicitly disables redirects; a redirect response ends that run rather than adding hidden followed requests.
- [Playwright persistent contexts](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context) documents that automating Chrome's default personal profile is unsupported. A separate profile is a configuration requirement, not an access workaround or reliability promise.
- [Playwright requests](https://playwright.dev/python/docs/api/class-request) distinguishes redirects, request events, HTTP responses and failures. A redirect creates another request; HTTP error responses can finish normally at the transport layer. [Network documentation](https://playwright.dev/python/docs/network) describes monitoring capabilities and service-worker limitations. Earlier browser visit counters did not measure all this traffic.

If a future authorized benchmark is used, count every explicit POST/probe/retry and document redirect toward its applicable cap; separately count top-level visits and disclose unmeasured browser subrequests. Keep at least three seconds between search requests and 15 seconds between browser visits. Stop all live work at a challenge, 403 or 429, preserving partial evidence, without switching methods. This review did not revisit Carvana pages, copy cookies, solve challenges, rotate proxies, install dependencies or use paid services.

Still unresolved: exact economic meaning of inventory codes, true whole-site/national denominator, residual categories and source exclusions, membership drift during sweeps, reliable unattended browser access, sustained daily POST access, and the relationship between website Sold timing and completed economic sales. The next useful milestone is **repeatable, complete coverage of the explicit 16-query cohort**, visible and understandable in the notebook.

## Scope and verification record

Exact checkout verified: `C:\Users\Sean\VscProjects\researchOS`, branch `codex/notebook-reliability`, HEAD `052ed0b6d5b6e9ad0aae1edcb3b283d800ccdc72`. Root and vehicle `AGENTS.md` were read. Existing gaming and vehicle worktree changes were preserved. This stage adds only this document and a timestamped experiment review packet; operating source, notebooks, configurations, retained evidence, databases and daily registrations are unchanged. No commit or push.

The experiment packet remains local under the repository's existing ignore rule for `vehicle/data/experiments/`; the decision document is a new untracked file. No ignore rules were changed.

Final checks: **787 tests passed** in 181.96 seconds (14 existing plotting warnings); **all 16 notebooks passed** guarded execution. Both collector previews passed without creating destinations. All 16 manifest source hashes and 655 search-page hashes passed, local document links resolved, and `git diff --check` passed. All **3,544 pre-existing files** remained byte-for-byte unchanged; the staged diff, branch and HEAD remained unchanged. See the experiment packet's `review_record.json` for the retained scope and verification summary.
