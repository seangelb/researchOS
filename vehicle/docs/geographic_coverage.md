# Carvana geographic coverage: implementation and proposed experiment

The goal is a reproducible inventory history with measured geographic/category gaps.
Reaching10,000 VINs does not establish a full website denominator. Existing daily
configuration, original failures and the frozen status study remain unchanged.

## Query failure handling

Fresh full-plan experiments can opt into `--isolate-pagination`. This stops the
affected query, verifies its source/journal/SQLite agreement and valid VIN/listing
relationships, then allows independent queries to proceed under the same budget.
An incomplete query remains incomplete. Nothing resets elapsed time, request count
or request spacing. Durable budgets retain a dated report/hash entry for isolation.

Default collection still stops the shared invocation. The new option is rejected
for existing daily-cycle and resume commands. It is not automatically enabled in
the scheduled 101-query trial. Access failures, wrong contexts, malformed data,
identity conflicts and uncertain storage/transport outcomes continue to stop all
work. Within-page duplicates that cannot pass the existing parser also remain a
global stop; the implemented recovery is deliberately limited to reconciled cases.

The actual September 13 Jeep failure was replayed offline, followed by the retained
Camry responses. Eight mocked requests produced one incomplete and one complete
query, with unchanged original sources. These replay outputs are test evidence,
not new vehicle observations.

Validation: the vehicle suite passed 1,085 tests before the final three additional
failure cases; the final focused search/isolation suite passed 66 tests, including
all 15 isolation cases. All nine vehicle notebooks passed offline. No new live
Carvana requests were made. Final checkpoint receipts are retained outside the
checkout in `C:\Users\Sean\Documents\ChatGPT\ResearchOS\carvana_coverage_implementation_20260913`.

## Concrete proposed ZIP experiment

The canonical [proposed manifest](../config/carvana_geography_proposed_20260914.json)
is **not authorized or executed**. It proposes September 14, 2026, 10:30–11:00 New York,
destination `vehicle/data/experiments/geography_20260914T143000Z`, maximum 300 requests,
1,800 seconds and minimum 3 seconds between starts. An expired proposal requires a new
dated plan; it cannot be silently rescheduled or executed as an old vintage.

| Role | ZIP contexts |
| --- | --- |
| Discovery |08542 Princeton;30303 Atlanta;60601 Chicago;75201 Dallas;85004 Phoenix;90012 Los Angeles|
| Held out for validation |98101 Seattle;33130 Miami|

These are requested delivery contexts, not proven physical vehicle locations.
Live response ZIP validation is required. Their geographic spread is a proposed
design choice, not a verified partition of Carvana inventory.

| Exact cohort | September 12 retained count | Pages at 24/response |
| --- | ---: | ---: |
| Toyota Camry 2022 |42|2|
| Chevrolet Colorado 2022 |51|3|
| Ford Bronco Sport 2023 |55|3|
| Volkswagen Taos 2023 |57|3|

Every cohort references a verified earlier report and hash in the manifest.
There are two passes through each cohort/context combination. Pass 2 reverses the
within-cohort ZIP order, and each block ends with an additional 08542 anchor query.
This makes 72 queries. If earlier 08542 counts held in every context, the estimate
would be 198 pages; they may not hold. The shared 300-request cap still applies and
unattempted/partial comparisons stay visible. These four cohorts are a feasibility
sample, not a representative sales sample or a full-category discovery sweep.

Read-only preview from the repository root:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_search.py --plan vehicle/config/carvana_geography_proposed_20260914.json --experiment geography_20260914T143000Z --full-plan --isolate-pagination --max-requests 300 --max-seconds 1800
```

The preview makes no requests or capture-directory writes. The general CLI does
not enforce this manifest's proposed wall-clock appointment or authorization:
those must be checked before any live invocation. No live invocation is scheduled
by this document or implied by the earlier daily-panel heartbeat.

## Work still required

Prepare notebook-visible comparisons before authorizing the pilot: per-query
coverage, pass/ZIP intersections and unions, incremental VINs, held-out additions,
price versus delivery-cost differences, anchor drift and capture time spans.
An incomplete query cannot support absence or geographic exclusion. Identical
counts alone cannot establish identical membership or an atomic snapshot.

Use the results to choose an economical discovery plan and explicitly size a
full-coverage budget. The current 600-request daily cap is not enlarged. Retain all
context observations while deduplicating analytical inventory counts. Separate
old inventory first discovered in a new context from genuinely newly observed
listing episodes. Keep evidence vintages, observation-age lower bounds and missing
dates visible. The full goal still requires measured geographic results, a baseline
and a separately declared operating trial; this change does not establish them.
