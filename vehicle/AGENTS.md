# Vehicle research

- Live Carvana access is stopped after HTTP 403 on September 18, 2026. Read
  [the retained outcome](docs/facet_outcome_20260918.md) before any daily or browser
  collection. Scheduled wakes do not clear this stop; continue offline audits only.
- The user's September 19 morning daily attempt also stopped on HTTP 403. Its
  [recorded outcome](docs/daily_outcome_20260919.md) consumes that day's attempt;
  the evening wake must not collect again. The global access stop remains active.
- Start with Carvana; add other retailers only for a concrete research question and verified source.
- Preserve retailer names, listing IDs, native statuses, source references, and observation timestamps.
- VIN/vehicle identity does not merge listing histories across retailers. Include retailer in observation keys.
- A pending or missing listing is not a confirmed sale. Show collection gaps, cancellations, and reappearances separately.
- Keep asking prices distinct from transaction prices, observations from estimates, and synthetic examples from real data.
- Read-only notebooks are the default. Confirm data access and coverage before building a collector.
- Add carvana.py or carmax.py when their actual parsing rules are known. Share small helpers only after real duplication exists.
- Do not create a database or invent live data to make a starter notebook appear complete.
