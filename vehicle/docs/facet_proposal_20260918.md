# Proposed category-count experiment, September 18

**Executed once and stopped on HTTP 403.** See the
[September 18 outcome](facet_outcome_20260918.md): one failed request, 721
unattempted, no category counts. The original attempt must not be restarted.

**Approved by the user on September 17 with "approve all".** The
[authorization record](../config/carvana_facets_approved_20260917.json) binds the
unchanged proposal hash, reviewed implementation and exact one-time scope below.
Execution remains limited to the original September 18 start window and fresh
destination; approval is not evidence that collection occurred. It makes no change to the fixed
101-query daily trial, its 600-request limit, or the frozen 32-VIN status study.

The [offline partition ledger](partition_coverage.md) found only 30 retained
make/year parent counts out of the old candidate's 720. Fresh counts are needed
to size a broader inventory sweep and choose smaller model partitions. A *facet*
is a native category/count response: for example, the count for Toyota/2023 and
the model-family counts within that parent. These counts are not VIN enumeration.

## Exact proposed scope

| Item | Proposal |
| --- | --- |
| Manifest | [carvana_facets_proposed_20260918.json](../config/carvana_facets_proposed_20260918.json) |
| SHA-256 | `2605cf52b9c7bf3bf2deda655757e92c008dec9b404c8da24ce58217f368d1bf` |
| Window | September 18, 2026, 10:30–11:30 a.m. America/New_York (14:30–15:30 UTC) |
| Latest start | 10:35 a.m.; an expired start/window is not extended |
| Destination | `vehicle/data/experiments/facets_20260918T143000Z` — must not exist |
| Context | ZIP 08542; location prefiltering omitted |
| Source | Previously observed public `https://apik.carvana.io/merch/search/api/v2/search` request |
| Ordering/page | Native `MostPopular`, page 1, page size 24; no alternate sort or cursor |
| Queries | Broad opening; 40 makes alphabetically × exact years 2010–2027 ascending; broad closing |
| Requests | At most **722** total: 720 parents plus two broad responses; one attempt per request |
| Timing | At least 3 seconds between starts, at most 3,600 seconds and the original window end |

This is **122 requests above the daily panel's 600-request ceiling**, proposed as
a separate one-time research budget. Approval must explicitly cover that scope;
the daily configuration remains unchanged. The spacing floor alone is 2,163
seconds (36 minutes 3 seconds), before the final response and processing. This
is not a runtime guarantee or a full-inventory request budget.

The old year min/max are filter metadata, not proof of exhaustive population
boundaries. If fresh broad metadata shows new/missing makes or changed year
bounds, retain those differences as coverage gaps. Do not add queries, assume
missing categories have zero inventory, or silently revise this manifest.

## What the implementation retains

The new explicit CLI reuses the existing public request builder, cookie-free
transport, immutable source storage and durable shared budget. It has no resume,
retry, second-page, inventory import or scheduler action. The separate budget file
is named `facet_budget.json`; it is not a daily `cycle.json`.

Every request is reserved durably before transport. Retain request/start/response/
evidence-availability clocks, HTTP status, response-content hash, selected public
inventory source and selected make/model/year facets. Other response fields and
original serialization may be unavailable; the records state that limitation.
Native zero, missing and negative numeric values survive retention. Invalid count
types/values stop validation; they never become valid totals through coercion.

Replay produces one row per planned request, including failures and unattempted
parents, with source hashes, clocks, native totals, signed count residuals and
overlapping model IDs. Other make buckets in a filtered response are disjunctive;
they are not added to that parent's children. A zero-result query can have an
unknown model breakdown. New-category/year gaps stay explicit.

The broad opening/closing counts reveal time drift; they are not summed. These
are changing ranked responses across a window, not an atomic snapshot. Even a
722/722 facet run cannot establish complete inventory, VIN membership, Carvana
ownership, national coverage or sales. First-page identities are checked for
conflicts but never imported as complete inventory.

## Stop and preflight rules

Before any authorized live invocation, verify the exact clean reviewed checkout,
manifest/basis hashes, fresh destination, active process handles, unresolved visits
and access-stop state. Do not overlap another Carvana attempt. Preserve today's
failed daily capture and its unresolved request; do not resume or replace it.

Stop globally on challenge/403/429, other HTTP/content failures, uncertain transport,
schema/context/identity mismatch, storage failure or exhausted time. Charge every
reservation, including failed/unresolved ones. There is no transport fallback or
automatic retry. Monitor the original process to terminal completion and enforce
the original deadline; an observation timeout is not permission to restart it.

The CLI refuses a changed plan hash, an existing destination, an early start or
a start after 10:35 a.m. Its flags do not grant authorization. A different date or
destination needs a new immutable proposal and explicit authorization.

## Review and execution commands

Read-only preview, safe now:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_facets.py
```

**Only after approval and preflight, inside the proposed start window:**

```powershell
.\.venv\Scripts\python.exe -u -B vehicle/scripts/collect_carvana_facets.py --live --plan-sha256 2605cf52b9c7bf3bf2deda655757e92c008dec9b404c8da24ce58217f368d1bf
```

After the original process stops, use the existing folder and a timezone-aware
cutoff after its terminal report. Replay is read-only unless an explicit fresh
export directory is supplied:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/collect_carvana_facets.py --replay vehicle/data/experiments/facets_20260918T143000Z --as-of YOUR_UTC_CUTOFF --export YOUR_FRESH_EXPORT_DIRECTORY
```

Check journal/budget parity, all planned rows, source/identity replay, residuals,
new categories, timing and export hashes. Back up and restore-verify the evidence.
Then derive a new inventory-query proposal and its request budget, with parent/child
VIN audits and held-out ZIP checks for new categories. That broader enumeration
needs its own declared scope and authorization; this experiment cannot launch it.
