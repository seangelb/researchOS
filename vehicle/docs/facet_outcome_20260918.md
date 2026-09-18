# September 18 category-count experiment: access stopped

The approved experiment started at **10:32:00 a.m. New York time** on September
18, 2026. Its opening request returned **HTTP 403**. The original process stopped
with exit code 1 at `2026-09-18T14:32:00.584033+00:00`; no retry or fallback ran.
The request was within the approved start window. Preflight matched clean
`main` and local/remote `origin/main` at
`0c2009889f341745dbda5da5b637ec71dfbbbe20`, all approved implementation hashes,
the unchanged plan and its retained basis. No earlier facet attempt, active
collector, unresolved browser visit or current browser access block was found.

| Planned outcome | Retained result |
| --- | --- |
| Requests | 722 planned; 1 charged and failed; 721 unattempted |
| Make/year counts | All 720 remain missing |
| Broad opening/closing counts | Both unavailable |
| VIN samples and identity reconciliation | No usable samples |
| Native count residuals and new category/year gaps | Unmeasured |
| Inventory enumeration or database import | None |

The [original report](../data/experiments/facets_20260918T143000Z/facet_report.json)
has SHA-256 `71b17ad4e0bddb318f5c16d8ac43cebb2f983c03485046d149ae1efa52e1426e`.
The [durable budget](../data/experiments/facets_20260918T143000Z/facet_budget.json)
records one charged request, stopped=true and pending_request=false: the 403
response is a known outcome. Its 4,544-byte response-content hash is
`c197442c78afc6e559d8755fe07c9e002d2e9820ca77fab61cfad1aeeae5e14d`.
The collector retains the hash and HTTP status, not the access-failure body;
body replay and a more specific blocking diagnosis are unavailable.

The existing replay/export CLI reconciled **all 722 planned rows** at cutoff
`2026-09-18T14:33:00+00:00`. The export has one failed opening request and 721
unattempted rows, without invented zeros, counts or identities. Input, code and
output hashes, ordered query IDs, request budget and source clocks reconciled.
The immutable CSV, manifest and reconciliation record are retained under
`C:\Users\Sean\Documents\ChatGPT\ResearchOS\carvana_facets_20260918`.

## Current operating state

The user's global stop-on-403 instruction now applies to **all live Carvana
collection**, including scheduled daily inventory and browser status checks.
The dated [access-stop record](../data/experiments/research_cycle_resume_20260913/access_stop_20260918.json)
has no automatic expiry. Do not infer clearance from a later wake, switch
transport, retry this destination or create a replacement attempt. Offline
review can establish the next access decision; this result supplies no evidence
that access has recovered.

The same automation was restored to **10:00 and 20:00 New York time**, preserving
its full prompt, name, target thread, active status and notification settings.
These wakes may inspect retained state and conduct offline audits; they do not
override the access stop. Keep all seven original September 13-19 daily dates
in the denominator and keep all 32 frozen study VINs, with missing outcomes
explicit. The September 21 final audit must include this failed experiment and
its unresolved category/coverage gaps. The broader research goal is incomplete.

The September 17 daily transport failure and its uncertain request remain
unchanged. This experiment made no change to daily limits, study selection,
original Tesla evidence, gaming approvals or operator labor estimates.
