# Carvana collection: evidence, working prototype, and scaling plan

Research date: September 7, 2026 US Eastern (some captures are September 8 UTC).
The user explicitly authorized website automation for this personal project.

## What the vendors actually disclose

- [JXCE's FAQ](https://www.jxcedata.com/faqs) says it combines automated web gathering
  of public information with proprietary software and forecasting. Its product includes
  pending orders, inventory age, processing times and sales estimates. Its exact capture
  endpoints and conversion of listing events into completed sales are not disclosed there.
- [QuanticData](https://quanticdata.io/collectors/carvana-scraper-api/) describes reading
  structured React Server Component data embedded in search pages, with schema.org
  Vehicle records as a fallback. It discusses stream chunk boundaries and promotional
  tiles. Its advertised 96-listing cap is a limit of that vendor offering, not evidence
  that Carvana itself exposes only 96 listings.
- [Indra's June 2025 weekly analysis](https://indrastocks.substack.com/p/carvana-weekly-update-5262025-612025)
  separates listings, inventory, orders, delivery estimates and sales estimates, including
  CarMax comparisons. It points to Clarity coverage. These are vendor-produced metrics,
  not independently verified completed transactions.
- [Indra's July 2025 post](https://x.com/IndraStocks/status/1947237307318186222) discusses
  maintaining scripts when they break. A [July 2026 post](https://x.com/IndraStocks/status/2082547600524480906)
  gives a quarterly estimate from his data tracking. Neither discloses enough technical
  detail to reproduce the collector or the sales classification. Do not invent that method.

## What we verified directly in Chrome

1. The public search page renders listing cards and schema.org Vehicle JSON, including
   listing URL/ID, VIN, exact odometer miles, year, make/model, asking price and native
   schema availability. The retained three-record fixture is an explicitly truncated
   browser-DOM sample, not an original HTTP response or a complete page.
2. Clicking the next-page button changed the URL to `/cars?page=2`. Pagination can be
   automated; a person does not need to click through every car's detail page.
3. A plain Python GET with an honest research user agent received HTTP 403. Interactive
   Chrome worked. This does not establish unattended browser access or an available bulk API.
4. Client-side navigation left old schema records while the visible cards changed.
   A normal reload produced **23 visible listing cards and 23 matching structured records**.
   The prototype therefore reloads and checks exact identities before storing a page.
5. A Tesla Model 3 query showed **830 cars** in ZIP **08542** and **830 cars** in
   **90210**, but its first pages had **0 overlapping listings out of 21 each**.
   This demonstrates ZIP-sensitive presentation, not two disjoint inventories. Ordering
   can explain the difference. We did not enumerate the full sets. The original ZIP was restored.
6. Carvana's ZIP dialog says location affects delivery options and shipping costs.
   [Carvana's help page](https://www.carvana.com/help/carvana-inventory/where-are-carvana-cars-located/)
   also discusses delivery/pickup context. Shopper ZIP is not a car's physical location.

## How collection can scale

Visible page-by-page browsing is not the only transport. In preferred order:

1. An authorized documented bulk feed or search endpoint, if one is actually available.
2. Structured search responses or embedded page data with supported pagination/cursors.
3. Browser-rendered pages, extracting structured records in batches.
4. Detail pages only for fields/statuses missing from search results or for follow-up checks.

No bulk Carvana endpoint was verified in this work. Do not guess endpoint names, increase
undocumented page-size parameters, enumerate invented VINs/IDs, or claim a vendor API's
limit is the website's limit. The present prototype uses option 3 and stops on access errors.

The data-discovery job and the follow-up job should be separate. Discovery finds new
listing IDs; follow-up rechecks known IDs or refreshes search batches. A known-ID watchlist
alone misses newly listed vehicles. Full rediscovery remains necessary at a measured cadence.

### Prove enumeration before increasing speed

- Start with one explicit ZIP and a small, fully enumerated make/model/year cohort.
- Record query filters, selected sort, actual ZIP, reported count, page range, observation
  times, unique listing/VIN counts, overlaps, failures and source references.
- Check whether a query's next-page traversal reaches all reported results. A page cap,
  repeated result set or missing pagination control is a coverage gap, not completion.
- Only if queries truncate, partition using stable categories such as make/model/year.
  Price bands can split very large cohorts but change as prices change; handle boundary
  overlap and deduplicate while preserving each query's original evidence.
- Repeat the same small complete cohort in several geographically separated ZIPs.
  Compare complete sets and per-listing offers, not just the first page. Record incremental
  unique VINs contributed by each ZIP. This measures the value of additional ZIP coverage.
- Do not scrape every ZIP by default. Shared inventory may make that mostly duplicate work.
  Conversely, equal displayed counts do not prove identical membership or deliverability.

### Keep storage simple

The prototype uses two SQLite tables:

- `vehicle_captures`: each query/page attempt, run ID, timestamp, source URL, ZIP, displayed
  total, status, counts, error, retained path/hash, and explicitly unverified coverage.
- `vehicle_observations`: listing ID, retailer, VIN, timestamp, price, exact mileage,
  year/make/model, native availability, card text, and source URL, linked to the attempt.

Retain immutable extracted JSON separately. A VIN identifies a physical vehicle; a
retailer listing ID identifies a listing. Keep repeat observations and cross-retailer
appearances. Union/deduplicate for a specific analytical question, not during raw capture.
Do not multiply counts by the number of ZIPs. A current-vehicle summary can initially
be derived with pandas; a separate vehicle-master service is unnecessary.

For scale, introduce an explicit query manifest and query-result coverage table only
when the cohort experiment establishes the necessary partitions. Run independent ZIPs
in isolated browser contexts: shared location settings can contaminate another query.
Use a small bounded worker count only after measuring latency and permitted request rates;
keep one SQLite writer. Do not begin with distributed workers, Redis, proxies, or a warehouse.

As an order-of-magnitude illustration, 70,000 listings at 21 records per response require
roughly 3,334 responses for one complete pass, before overlaps, failures and detail checks.
That is not 70,000 detail-page requests. More ZIPs do not automatically multiply useful
coverage. Avoid images/video in a future optimized transport when they are not inputs.
Compress retained captures and choose a retention policy after actual size measurements.

## Sales estimation remains a separate research step

Pending, disappearance and sale are different events. Carvana defines reported retail
units as vehicles sold, including marketplace partner vehicles, net of returns under its
seven-day policy ([2025 10-K](https://www.sec.gov/Archives/edgar/data/1690820/000169082026000009/cvna-20251231.htm)).
Therefore, neither `InStock` nor a missing listing produces a confirmed transaction.

Build and review lifecycle cases: new listing, pending card text, price change, missing
from one complete query, present elsewhere, reappearance, and later evidence of sale.
Use repeated observations to calibrate any disappearance delay; do not arbitrarily call
seven days a sale. Validate frozen estimates against reported quarters without hindsight.
Market/retailer coverage, marketplace mix, cancellations and returns remain limitations.

## Current scope and next acceptance test

Implemented: a capped sequential browser collector, parser, explicit snapshot storage,
real retained sample, offline tests and read-only notebook. Every run is still labeled
unverified. There is no scheduler, full-inventory claim or sales estimator.

Standalone collection requires optional Playwright, which was not installed during this
work. The live Chrome inspection verified the DOM extraction and navigation, while
transport orchestration is covered with mocks. Do not describe the standalone runner
as proven against Carvana until it has passed an explicit live pilot in its own context.

The next acceptance test is one fully enumerated small cohort, captured twice in the
same ZIP and once in a second ZIP, with visible reconciliation of counts and unique IDs.
Scale only when this explains coverage and preserves the actual financial meaning.


## Delivered verification

- Full offline suite: **389 passed**, 9 pre-existing Matplotlib warnings, **98.11 seconds**.
- Vehicle tests: **26 passed**; real fixture fields, missing/zero prices, invalid IDs and
  currency, duplicate captures, unrelated-database protection, page caps, wrong ZIP,
  access denial and parser failures are exercised without network.
- Guarded notebook execution: **12 notebooks / 72 code cells passed**.
- Collector dry-run command opened no browser and wrote no database.
- A separate `vehicle/data/vehicle.sqlite` contains one real, truncated three-listing
  browser sample. Its raw extracted JSON is retained by hash. Synthetic rows were not ingested.
- All **1,780 pre-existing gaming files** in the preflight manifest are byte-identical.
- `git diff --check` passed. New vehicle source/data remains local and uncommitted;
  raw captures and the vehicle database are ignored by Git.
- Playwright is an optional declared dependency, not installed during this task.
  Browser orchestration uses mocks in tests; interactive Chrome confirmed the public
  extraction, pagination, ZIP changes/restoration, and stale-schema reload behavior.
