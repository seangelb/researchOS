# Carvana collection evaluation — September 8, 2026

## What worked

The reusable sequential search runner saved **1,006 distinct listing IDs and 1,006
distinct VINs** from 47 requests in **138.94 seconds**, from 11:32:57.954813 to
11:35:16.892452 UTC. The retained result is
`vehicle/data/experiments/20260908_mvp1000_verified/run_report.json`.

| Query | Saved IDs | Query coverage |
|---|---:|---|
| Tesla Model 3 / 2024 | 42 | Complete enumeration against stable reported total |
| Tesla Model 3 / 2023 | 196 | Complete enumeration against stable reported total |
| Tesla Model 3 / 2022 | 169 | Complete enumeration against stable reported total |
| Tesla Model 3 / 2021 | 146 | Complete enumeration against stable reported total |
| Tesla Model 3 / 2020 | 105 | Complete enumeration against stable reported total |
| Tesla Model 3 / 2025 | 27 | Complete enumeration against stable reported total |
| Tesla Model 3 / 2026 | 9 | Complete enumeration against stable reported total |
| Toyota / 2022 | 312 | Target-limited; remaining pages were not collected |

An earlier independent partition pass enumerated the same seven Tesla groups to
694 distinct IDs/VINs in 34 requests. The complete 27-vehicle cohort also succeeded
through the direct endpoint earlier. These are repeated intraday observations, not
proof of repeated daily completeness or a representative sales sample.

## Access comparison and retained evidence

| Approach | Observed outcome | Limitation |
|---|---|---|
| Ordinary visible Chrome | Real inventory displayed | Manual/browser reference, not timed unattended throughput |
| Fresh headed Playwright + installed Chrome | Inventory loaded, 200 response | Initial browser default ZIP was 08540, not requested 08542 |
| Dedicated persistent profile, first launch | Inventory loaded | Same initial ZIP mismatch |
| Dedicated persistent profile, restart | Redirect then 403 with `cf-mitigated: challenge` | Stopped; no challenge solving or profile/cookie copying |
| Browser ZIP update + next page | Search API succeeded; DOM/JSON-LD identities could disagree | Embedded JSON-LD can be stale after client navigation |
| Cookie-free public search POST | Multiple complete cohorts and 1,006-vehicle sample succeeded | Public undocumented source can change; future access remains unproven |

The endpoint is `https://apik.carvana.io/merch/search/api/v2/search`. Its POST body
was observed from a normal website interaction. The collector uses explicit filters,
page/pageSize (24), `MostPopular` sort and `zip5`, optionally the observed location
feature. No cookies, account, analytics identifiers or authentication are sent.

Paired source files in `20260908_search_schema/raw/` match **21/21 API and rendered
card IDs and asking prices**. JSON-LD matches 20/21 IDs and is stale for the other
identity. Common VINs match. The direct first page contains those 21 plus three real
vehicles; the browser request included three promotional tiles. Both cohort totals
were 27. The smaller direct request avoids promotional/financing payload settings.

We retain selected public response fields and response metadata/hash. A projection
hash is not the hash of original HTTP bytes; reports distinguish them. Source-native
lock and pending flags are preserved without sale interpretation. The first inspection
retained a fuller public inventory projection; subsequent reusable collection uses
the smaller allowlist. No credentials are included in retained sources.

The broader API query first reported 80,581 then 80,580 at page 6. Its collector
stopped after 120 stored vehicles; the failed page remains retained. This trial is
`20260908_mvp1000`, not the successful `20260908_mvp1000_verified` experiment.

## Why ZIP totals differ

The normal Chrome request explicitly included `LocationBasedPrefiltering`.
`20260908_location_filter/report.json` records a controlled, cookie-free comparison:

| UTC | ZIP | Location feature | Reported total |
|---|---|---|---:|
| 11:37:14 | 08542 | Off | 80,585 |
| 11:37:17 | 08542 | On | 71,015 |
| 11:37:20 | 90210 | On | 58,993 |
| 11:37:23 | 08542 | Off | 80,585 |

This isolates a material location-setting effect and explains the main API/browser
count difference. It does not prove that every extra vehicle is owned retail stock,
reachable through every ZIP, or part of another vendor's denominator. Feature-off
results are a broader **observed search population**, not a verified national census.

The matching complete Tesla/2025 ZIP audit is retained under `20260908_zip_audit`.
Its notebook shows intersections and incremental identities using 08542, nearby
08540 and distant 90210. Never sum ZIP totals; retain delivery context separately.

## Full-inventory design and realistic capacity

Use ordinary public search batches, one writer and an explicit query manifest.
Start with make/exact-year buckets and split large or unstable buckets by observed
parent model. Review the source facet universe and any missing/unknown residuals
before calling the manifest exhaustive. Do not infer a complete roster from the MVP.

Completed partitions remain separate, hash-checked artifacts. A resume reuses their
references and restarts incomplete queries into new storage. It does not splice old
pages into a new complete query. The full capture interval must remain visible;
stable counts alone cannot prove atomic completeness of a moving inventory.

At 24 vehicles/request, 80,581 observations imply **3,358 batch requests**. Three-second
pacing implies a floor of about **2.80 hours**; the sample's measured seconds/request
also suggests roughly 2.76 hours. Neither includes partition overhead, retries after
review, coverage checks, time variation or ZIP audits. This is a conditional capacity
estimate, not demonstrated daily national collection. The 600-request/60-minute
invocation cap stays below a full census; a reviewed larger plan can use checkpoints
across bounded invocations. No nationwide run or scheduler was activated here.

## Daily sales remains a measurement project

Inventory history is the foundation. Preserve first/last observed times and native
status transitions. Only classify disappearance after complete, comparable captures;
check persistence, relisting, cancellation and return behavior. Resolve VIN aliases
without silently merging listing histories. Freeze an estimated-sales definition,
then compare it over matching dates/populations with company reports and vendor
estimates. Missing is never automatically a sale, and no daily sales estimator was
created or validated in this milestone.

## Development and validation

The existing Python/pandas/SQLite/Jupyter architecture remains. New executable work
is a source-specific search module, a small sequential plan helper and an opt-in CLI.
The existing observation storage is reused. No service, scheduler, framework or
automatic anti-bot fallback was added. Paid solver calls: zero. Vehicle detail-page
requests: zero. Existing gaming and pre-existing raw/database artifacts are protected.

Focused regressions cover wrong/missing ZIPs, malformed identities, duplicate pages,
changing totals, inconsistent pagination, explicit zero versus missing inventory,
timeouts, HTTP errors/challenges, target-limited coverage, identity unions, safe
resume, modified resume artifacts and explicit request/time limits. Tests use mocks
and temporary storage. The final delivery reports full-suite, guarded-notebook and
preservation results.

Official implementation references: [Playwright browser contexts](https://playwright.dev/python/docs/api/class-browsertype),
[Playwright network inspection](https://playwright.dev/python/docs/network), and
[Cloudflare supported browsers](https://developers.cloudflare.com/cloudflare-challenges/reference/supported-browsers/).

## Final verification

- Full offline suite: 464 passed, 9 existing plotting warnings, 104.76 seconds.
- Focused search regressions: 30 passed in 1.16 seconds.
- Guarded execution: all 12 active notebooks passed; requests, exports and writable database connections are blocked.
- Offline reparse: every one of the 1,006 stored observations matches its retained source; no missing VINs or asking prices.
- Preservation: 1,834 pre-existing files checked; six intentional code/documentation edits and 1,828 unchanged files, including all 11 existing databases, existing raw files and the vendor workbook. All 19 earlier notebook cells preserved exactly; nine appended cells.
- Independent review: three findings reproduced and fixed, then rechecked with no remaining actionable finding.
- `git diff --check` passed; HEAD and branch unchanged. All work remains local and uncommitted.

The next milestone is a reviewed complete source-population/query manifest, a bounded
full-enumeration trial using checkpoints, and repeat daily coverage validation. Only
then should a persistent-disappearance/estimated-sales rule be calibrated and tested.
