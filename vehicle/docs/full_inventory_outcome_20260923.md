# Full-inventory terminal outcome: September 23, 2026 (year-first r4)

The scheduled year-first sweep under
`vehicle/data/experiments/carvana_full_inventory_years_r4/2026-09-23`
started at 4:30 PM Eastern and ended at **2026-09-23T21:34:17Z** with status
`stopped`. The daily task recorded `LastTaskResult 1`. The durable ledger has
`pending_request=false` and 1,266 charged requests. There is no
`attempt_outcome.json`; the stop was labeled `http_access_failure`. The failing
page is HTTP **520**, a Cloudflare origin error, not an access denial. The
automatic offline export still wrote `analysis/`. This is an aborted partial
day, not a reconciled full-inventory baseline and not a sales day.

## Counts

| Field | Value |
|---|---|
| Requests | 1,266 / 7,000 |
| Opening native total | 81,443 |
| Closing native total | none; closing discovery never ran |
| Primary observed VINs | 6,650 |
| Opening residual | 74,793 |
| Discovery complete | true |
| Leaf plan frozen | true |
| Feasibility | fit the remaining allowance before enumeration |
| Declared leaf queries | 3,487 |
| Failure | `CollectionStopped` / `http_access_failure` on HTTP 520 |

The 6,650 VIN figure is how far oldest-year-first enumeration had reached. It
is not a VIN cap. The failing leaf was `year_2016_make_003_model_002`, one
charged request, HTTP 520.

## What this day is not

The HTTP 520 was treated as an access stop by the classifier then in use, so
September 24 started at five-second spacing. A 5xx response is a server
failure. It does not slow the next local date. Missing is not sold.
