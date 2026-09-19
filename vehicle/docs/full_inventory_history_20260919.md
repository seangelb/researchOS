# September 19 retained inventory history

The successful offline publication combines **68,908 physical observation
memberships across 19,003 VINs**. A membership is a retained vehicle sighting in
one physical capture; repeated copies share provenance aliases rather than
becoming extra sightings. Observations span September 8–19, 2026.

The selection includes six legacy cycle/database groups, **407 query reports,
12 browser DOM samples and four parent-bound search projections**. Original
evidence availability remains unknown for **14,454 memberships**. Samples and
partial queries establish positive sightings only: no invented cycles, listing
dates, complete inventory coverage, absences or sales.

Among the latest complete **101-query panel's 10,085 VINs**, **9,365** have earlier
selected sightings and **720** are newly observed within this selected history.
“Newly observed” does not mean newly listed. The **8,918 prior VINs not observed
in that narrower latest panel are not inventory exits**; the historical scopes
differ. This publication excludes the live all-year sweep.

## Review order

1. Read [the independent check](C:/Users/Sean/Documents/ChatGPT/ResearchOS/carvana_full_inventory_20260919/expanded_history_independent_check_20260919.json)
   for verified counts, clock checks and interpretation limits.
2. Open [history.csv](C:/Users/Sean/Documents/ChatGPT/ResearchOS/carvana_full_inventory_20260919/expanded_history_20260919T151551980351Z/history.csv)
   for one row per VIN, then `cohorts.csv` in the same directory for first-observed
   groups. Use `memberships.csv` to inspect individual sightings and their aliases;
   `retained_memberships.csv` contains the standalone retained-source inputs.
3. Consult [result.json](C:/Users/Sean/Documents/ChatGPT/ResearchOS/carvana_full_inventory_20260919/expanded_history_20260919T151551980351Z/result.json)
   and [manifest.json](C:/Users/Sean/Documents/ChatGPT/ResearchOS/carvana_full_inventory_20260919/expanded_history_20260919T151551980351Z/manifest.json)
   for output hashes, exact source selection and exclusions.

Excluded evidence includes simulated-clock mock cycles, source-less failures,
the incompatible 27-row search pilot, the zero-row HTTP 403 facet attempt,
sparse HTTP projections lacking the capture contract, DOM captures failing
identity/card agreement, and detail/status/title-history material outside
inventory membership scope. The manifest records the individual reasons.
The original blocked September 12 trial retains its storage failure and incomplete
status; its explicitly audited durable positives preserve separate report and
database error states. Neither prior failed publication was overwritten.

## Notebook 25 inputs and clocks

Notebook 25 remains offline with its defaults unchanged. For this selection,
set `RETAINED_MANIFESTS` to one `{path, sha256}` entry using:

```text
path: C:/Users/Sean/Documents/ChatGPT/ResearchOS/carvana_full_inventory_20260919/expanded_history_20260919T151551980351Z/manifest.json
sha256: 17b201d71acfa7bd30204bd9f6ff9393ef1add57d023e891e253f333a67894dd
```

Also populate `LEGACY_SOURCES` with the six `legacy_sources` entries from that
manifest; selecting the retained manifest alone does not select those cycle
groups. Leave `CATALOG_EXPORTS` empty for this publication's scope.

The manifest's **2026-09-19 15:15:51.980351 UTC** timestamp publishes the source
selection. It does **not** establish that the derived CSVs existed then. Their
completed outputs were independently verified at **15:26:36.844893 UTC**; use
`HISTORY_AS_OF = '2026-09-19T15:26:36.844893+00:00'` or a later explicit cutoff
when reviewing this completed result. Original observation and availability
clocks remain intact; no derived output is backdated.

The [backup verification](C:/Users/Sean/Documents/ChatGPT/ResearchOS/carvana_full_inventory_20260919/expanded_history_backup_verification_20260919.json)
records **6,863 restored files with matching hashes and 189 SQLite checks**,
unchanged originals, and preservation of prior failed publications. This is a
same-device archive/restore check, not disaster recovery; the live sweep is excluded.
