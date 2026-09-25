# Vehicle research

- The September 19 user-directed expansion is described in
  [the full-inventory goal](docs/full_inventory_goal.md) and
  [operating guide](docs/full_inventory.md). The daily config is
  `config/carvana_full_inventory_adaptive.json`. It allows at most 7,000
  requests/six hours. Make/year cells of at most 480 vehicles are collected as
  one query. Larger cells are probed, then split into models. The first attempt
  of a clean day uses three-second spacing; a later attempt the same day, or the
  day after an access stop, uses the next slower spacing. A day starts only when
  the estimated plan fits the remaining window at that spacing. Page-level
  schema, pagination, identity, transport and server failures (HTTP 408 and 5xx,
  including 520) retry twice, then isolate the leaf. Five consecutive isolated
  leaves end that attempt. HTTP 401/403, 429 and Cloudflare challenges end the
  attempt, record a cooldown, and a later attempt runs more slowly. HTTP 520 is
  not an access stop. A finished plan whose unverified leaves are at most half
  of one percent of the opening count is `complete_with_gaps`. Do not substitute
  this scope into the frozen 101-query trial or status study.
  Software/configuration updates alone do not establish a live baseline.
- Daily adaptive full inventory is scheduled on this PC via Windows Task
  Scheduler task `researchOS-CarvanaFullInventoryDaily` (12:01 AM local Eastern,
  then hourly through 8:01 PM, and at logon), wrapper
  `scripts/run_carvana_full_inventory_daily.ps1`. Requires the machine
  awake/logged on and VPN off. The wrapper asks `daily_decision` and starts
  only when today's attempt should run. A finished attempt, including
  `complete_with_gaps` and `infeasible`, is not repeated. An access cooldown or
  a held root lock still blocks. A terminal failure with no active cooldown does
  not block the next local date.

- Live Carvana access is stopped after HTTP 403 on September 18, 2026. Read
  [the retained outcome](docs/facet_outcome_20260918.md) before any daily or browser
  collection. Scheduled wakes do not clear this stop; continue offline audits only.
- The user's September 19 morning daily attempt also stopped on HTTP 403. Its
  [recorded outcome](docs/daily_outcome_20260919.md) consumes that day's attempt;
  the evening wake must not collect again. The global access stop remains active.
- The separately scoped [September 19 access diagnosis](docs/access_diagnosis_20260919.md)
  received HTTP 200 after the user disconnected their VPN. Its single-request
  allowance is consumed. Preserve this result without treating it as a replacement
  daily cycle or automatic clearance of the existing live-collection stop.
- The user's subsequent direct instruction authorized a separate
  [full capture and sales review](docs/full_capture_sales_20260919.md). It completed
  all 101 queries, 468 requests and 10,085 VINs without access failures. Its allowance
  is consumed; keep the original failed daily attempt and seven-date denominator.
  Further live work requires its own applicable authorization and preflight.
- Start with Carvana; add other retailers only for a concrete research question and verified source.
- Preserve retailer names, listing IDs, native statuses, source references, and observation timestamps.
- VIN/vehicle identity does not merge listing histories across retailers. Include retailer in observation keys.
- A pending or missing listing is not a confirmed sale. Show collection gaps, cancellations, and reappearances separately.
- Keep asking prices distinct from transaction prices, observations from estimates, and synthetic examples from real data.
- Read-only notebooks are the default. Confirm data access and coverage before building a collector.
- Add carvana.py or carmax.py when their actual parsing rules are known. Share small helpers only after real duplication exists.
- Do not create a database or invent live data to make a starter notebook appear complete.
