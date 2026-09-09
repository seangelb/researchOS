# Implementation prompt: start a daily Carvana inventory history

Work in `C:\Users\Sean\VscProjects\researchOS`, HEAD
`66493e2e5e3fa745b3c734f398c555e376606d9b`. Preserve the existing dirty worktree,
retained experiments, databases, gaming files and analyst bindings. Read root and
vehicle AGENTS.md. Use the shared Python 3.11 environment, pandas and SQLite.

Objective: give the analyst one repeatable command and one understandable daily
inventory table, then collect the first fresh baseline. Reuse existing search,
cycle-budget, retention, import, event and candidate functions. Do not create a
service, scheduler, ORM or second inventory storage model.

Use the seven queries in `vehicle/config/carvana_daily_pilot.json`, ZIP 08542,
location filtering disabled. This is a fixed pilot population, not national coverage.
Use `vehicle/data/experiments/carvana_daily/<local-date>` for new raw cycles and
`vehicle/data/analysis/carvana_daily/history.sqlite` for imported observations.
Record selected cycle paths explicitly in a small date-indexed JSON register. Reject
changed scope, duplicate dates, changed registered evidence and concurrent owners.

Implement a preview-by-default command. Explicit live mode collects the full plan
within 120 requests / 20 minutes, imports complete/partial evidence and exports
versioned tables. An import-only recovery path must reuse retained evidence without
new requests. Re-running the same day must not silently make a second collection.
Keep failures visible; do not retry access blocks or turn gaps into zero inventory.

Show daily coverage, observed VINs, complete-scope inventory, pending counts, asking
prices, new/absent identities for comparable consecutive days, and sales candidates.
Keep one row per actual VIN observation per cycle with source links. Use known
native statuses; no inferred transaction prices or invented sale dates.

Generate a small explicit follow-up queue for disappeared listings and native-status
changes, with VIN, original listing URL, reason and observation time. Detail checks
are supplementary: a direct detail-page probe returned a Cloudflare challenge on
September 9 UTC, so do not make that access path a dependency of daily collection.
Do not convert "no longer available" or failed page access into "sold". Explicit
site-sold evidence and inferred sales remain separate, unavailable until supported.

Put the operating daily tables first in notebook 20; keep the prior walkthrough and
synthetic examples below. Use the new register by default while preserving explicit
cycle/cutoff overrides for research. Notebook execution stays offline/read-only.

The supplied `CVNA_Q3_2026_Summary_Free (1).xlsx` is a read-only reference for daily
inventory/pending/price layout. Preserve its original bytes and vendor definitions.
Do not populate our orders, sales, ASP, nationwide inventory or YoY columns from it.

Keep the implementation small: aim for about 250 executable lines across the daily
module/command, plus focused offline tests and notebook explanations. Verify fresh
and empty histories, complete/partial cycles, missing dates, duplicates, scope drift,
idempotent import, evidence changes and offline notebook execution. Then run the
first explicitly bounded live baseline, inspect its real coverage and report the
actual result. Never claim a complete daily inventory when collection fails.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py
git diff --check
```

Deliver the prompt, daily command, visible table, preserved evidence and exact next
day instruction. No commit, push, deployment or scheduled job is requested.
