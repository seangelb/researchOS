# Vehicle research

- Start with Carvana; add other retailers only for a concrete research question and verified source.
- Preserve retailer names, listing IDs, native statuses, source references, and observation timestamps.
- VIN/vehicle identity does not merge listing histories across retailers. Include retailer in observation keys.
- A pending or missing listing is not a confirmed sale. Show collection gaps, cancellations, and reappearances separately.
- Keep asking prices distinct from transaction prices, observations from estimates, and synthetic examples from real data.
- Read-only notebooks are the default. Confirm data access and coverage before building a collector.
- Add carvana.py or carmax.py when their actual parsing rules are known. Share small helpers only after real duplication exists.
- Do not create a database or invent live data to make a starter notebook appear complete.
