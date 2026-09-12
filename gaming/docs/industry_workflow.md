# A small gambling industry workflow

Start with [notebook 94](../notebooks/94_gaming_industry_update.ipynb). Its opening table answers four questions: what changed, what that suggests, what is uncertain, and what to check next. The [September 12 note](industry_update_20260912.md) is a saved example. A short research note is descriptive work; it does not need a forecast or valuation approval step.

## Capture public evidence

[Notebook 20](../notebooks/20_run_all_collectors.ipynb) previews the exact state/product sources, collection mode, base database, new dated run directory and separate ZIP backup. Recent mode supports MA and NY sportsbook; other supported sources use explicit history mode. Enable both download/write switches only for an intended live capture. The refresh preserves its base, verifies SQLite/schema/clocks/raw source hashes, records exceptions and tests its backup. Inspect `collection_summary.csv` and `completion.json`; completion does not mean complete historical coverage.

Legal evidence stays small: one [event file](../config/legal_events.json), with observed fact, source-established status/date, effective date, applicable tickers/scope, statutory accounting effect, analyst interpretation, limitations and next check. The current seed covers two existing 2025 tax changes and an August 2026 interim court disposition. It is not exhaustive current-law coverage.

To retain another official document, call `capture_legal_source(root=ROOT, url=..., filename=...)` from `variant_gaming.legal`. It previews by default. An explicit `live=True` saves original bytes and their URL/hash/capture receipt under a dated `data/raw/GAMING_LEGAL/` directory, preserving an existing identical capture. Read the document before manually adding its event row. Unknown publication or effective dates stay null. Capture time is not publication time or proof that an older status remains current. A changed rule or ruling should be a new event/source record; retain competing outcomes separately.

## Select evidence deliberately

[config/current_snapshot.json](../config/current_snapshot.json) selects one database path, its content hash, and an explicit quarter/through-month window. Notebooks 90, 94 and optional 95/96 share this selection. After a completed refresh, inspect its exceptions and source evidence before changing this setting to the new capture. Never select a file by newest timestamp, repin a historical approval, or delete older evidence to make a check pass.

The selected snapshot is `refresh_20260912T201854Z`, with 19,806 rows and 34 stored state/product series. Q3 is explicitly through July 2026. The common read-only loader checks the original completion/validation receipts, their observation counts, actual SQLite schema and integrity, row capture clocks and retained source hashes. A nonempty SQLite WAL or journal is rejected, since a hash of the main database cannot bind those changes. Connections close after reads. Missing archives remain missing.

## Read the evidence in separate pieces

| Question | Evidence | Interpretation limit |
| --- | --- | --- |
| Is covered wagering demand growing? | MA and MI official online handle, same calendar months versus prior year | Volume, not customer counts; one positive month does not establish acceleration or durability |
| Is a brand gaining position? | Native brand handle / official state handle, and change in percentage points | Specific state/brand coverage, not all consolidated company operations |
| Did gross outcomes change? | Source-native sportsbook gross revenue / handle | Gross hold has many possible causes; not proof of retention, pricing or company EBITDA |
| Is casino activity improving? | MI Gross Receipts and Adjusted Gross, displayed separately | No sportsbook handle/hold; no substitution of gross receipts for issuer net revenue |
| What is timely? | Reconciled NY complete Monday–Sunday weeks | No monthly proration, no blending with the monthly panel; sports calendars matter |
| Did law or regulation change? | Retained official text/status/effective date and explicit inference | Enacted baselines are not new shocks; proposals/interim rulings are not final nationwide outcomes |

Each comparison requires every requested month in both years. Missing/invalid months block the comparison rather than being zero-filled or removed. Shares use ratios of summed amounts. Zero/negative reported revenue remains visible; growth from a nonpositive baseline and ratios with invalid denominators are unavailable. Conflicting source versions never resolve by newest capture.

MA and MI monthly totals must reconcile against the full retained operator population in one common source version. NY uses separate operator/statewide workbooks, so agreement within each exact identity and reconciliation of its complete weekly population are checked separately. Both preserve the original URLs/files/hashes. The notebook shows native monthly inputs, omitted mappings, failed metrics and broader coverage below its main business table.

The exact mappings are visible in `operator_scope()`: FLUT's US FanDuel labels, DKNG's DraftKings brand/license labels, and CZR's MA/NY sportsbook labels. DKNG excludes Golden Nugget. CZR's Michigan multi-license aggregation has not been reviewed, and digital observations cannot stand for its land-based business. Flutter International is outside this panel. PA/NJ licensees are not assigned by fuzzy name matching. [Notebook 90](../notebooks/90_consolidated_ggr.ipynb) and [metric definitions](../config/state_metric_notes.csv) support further state-level exploration.

## Save a note and verify work

Notebook 94 prepares a Markdown note in memory. To save it, set `export_note=True` and an absolute new dated `.md` filename in an existing folder. It records the comparison window, capture, database hash, observed changes, interpretations, limitations, next checks and source drilldown. Existing notes cannot be overwritten. Back up notes together with the raw sources and database; Git stores code/config/docs but not ignored captures.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py --project gaming
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py --project gaming --include-reference
```

The checker blocks network and writes. Historical approval studies 91–93 continue to report BLOCKED for their missing exact archives, so a nonzero exit is expected until those archives are restored. Do not alter their approval hashes. Optional 95 is the former FanDuel-only table; optional 96 is the old scenario/freeze/evaluation experiment. Its native quarterly input is recomputed from the bound database before validation, so editing a displayed amount cannot inherit verification. Neither experiment is required for the everyday business update. [Historical reference](gaming_reference_20260912.md) preserves previous collector detail and [recovery status](data_recovery.md) records the missing archives.
