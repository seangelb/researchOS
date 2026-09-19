# Full-inventory terminal outcome: September 19, 2026

The original all-year make/model sweep finished at **15:42:19 UTC**, retaining
**65,512 distinct primary VINs**. It is an incomplete observed inventory union,
not a successful full-inventory baseline. The automatic offline export succeeded;
`declared_collection_complete` and `primary_scope_reconciled` remain false.

The [terminal process record](C:/Users/Sean/Documents/ChatGPT/ResearchOS/carvana_full_inventory_20260919/terminal_process_20260919.json)
records original session **7251 exiting with code 1 because coverage was partial**,
despite the successful export. A subsequent check found neither original process
(launcher 38572, Python 20740). No restart occurred.

The [terminal catalog report](C:/Users/Sean/VscProjects/researchOS-full-inventory/vehicle/data/experiments/carvana_full_inventory/2026-09-19/catalog_report.json)
records **3,078 requests** across **548 planned and attempted queries**. Of
**498 primary leaf queries, 473 completed and 25 have gaps**. The completed leaf
count includes two reused make probes. Discovery and all declared geographic
checks finished, but that does not repair incomplete primary coverage.

## Counts and geographic limits

The native broad count moved from **82,583 opening to 82,121 closing**, a decline
of **462**, not a transaction or sales count. The observed primary union is
17,071 below the opening count and 16,609 below the closing count. These are
unreconciled count differences across a sequential collection, not estimates of
missing sales or a simultaneous census.

Broad native counts were **81,972 for ZIP 98101** and **77,277 for ZIP 33130**.
Of eight sampled membership comparisons, six matched. Both Audi Q4 e-tron
comparisons each had one additional VIN and one primary VIN not seen. The
comparisons changed both observation time and ZIP; they cannot isolate a causal
geographic effect or establish national completeness.

## Why the 25 queries remain incomplete

The [retained-source diagnosis](C:/Users/Sean/Documents/ChatGPT/ResearchOS/carvana_full_inventory_20260919/terminal_25_failure_diagnosis_20260919T154530892109Z.json)
classifies **14 adjacent page-boundary identity repeats, six other/nonadjacent
identity repeats and five native-count drops**. Each count-drop query fell by
one. All **3,078 responses were HTTP 200**. The 25 failed pages contain 600
source vehicle rows; **zero were admitted**. Earlier accepted pages remain
positive evidence. Deduplicating failed pages would not prove that omitted
vehicles were recovered. Inventory change and ranking movement remain competing
explanations, not established causes.

## Review and preservation

Start with the [terminal audit](C:/Users/Sean/Documents/ChatGPT/ResearchOS/carvana_full_inventory_20260919/v1_terminal_audit_20260919.json),
then the exported [coverage ledger](C:/Users/Sean/VscProjects/researchOS-full-inventory/vehicle/data/experiments/carvana_full_inventory/2026-09-19/analysis/coverage.csv),
`make_reconciliation.csv` and `geographic_checks.json` beside it. The audit
verified **10,416 input hashes** and a **3.00703-second minimum actual request
start gap**. Selected public response JSON is retained; omitted original response
bytes and fields cannot be reconstructed.

The [backup verification](C:/Users/Sean/Documents/ChatGPT/ResearchOS/carvana_full_inventory_20260919/v1_terminal_backup_20260919/verification.json)
records **10,384 files**, matching restored hashes, unchanged source bytes and
**549 successful SQLite checks**. This same-device recovery copy does not
establish disaster recovery or scientific completeness.

The user requested a **separate targeted recovery of the 25 gaps using smaller
year groups**. The [recovery guide](gap_recovery_20260919.md) describes its fixed
plan and review tables. A successful recovery has not yet been established.
It preserves this original incomplete outcome and records its own sources,
scope and coverage. The broader year-first v2 strategy remains
**not live-validated**. Smaller partitions still need pagination and count
reconciliation checks, and cannot resolve geographic coverage by themselves.
Neither a later recovery nor native count movement establishes sales.
