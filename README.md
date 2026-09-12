# researchOS: official state gaming revenue

Small Python modules collect official online sports-betting and online-casino reports.
Use pandas in Jupyter to inspect the results. No service or separate application is needed.

## Open these notebooks

- **`notebooks/90_consolidated_ggr.ipynb` — daily analysis.** Change states, product,
  metric, dates, and frequency, then Restart Kernel and Run All. It reads SQLite
  without downloading or writing. Optional CSV export is disabled by default.
- **`notebooks/91_flut_ma_sportsbook_signal.ipynb` — FanDuel MA signal.** Read-only
  Accrual Win / handle check for Jan–Jul 2026 vs 2025; fail-closed gates before YoY.
- **`notebooks/92_flut_multistate_sportsbook_signal.ipynb` — FanDuel multi-state check.**
  Inventories sportsbook evidence across states; conclusions only where approved (MA today).
- **`notebooks/93_flut_online_casino_signal.ipynb` — FanDuel online casino / cross-product.**
  MI iGaming revenue-share exploratory analysis and OSB vs casino direction table.
- **`notebooks/20_run_all_collectors.ipynb` — updates.** Choose explicit `recent` or
  `history` mode and inspect the database destination. Recent mode supports MA monthly
  PDFs and NY weekly workbooks. Both `run_downloads` and `allow_database_writes` must
  be true to collect; both default to false. Unsupported recent sources raise a clear
  error. Use `selected_sources = None` only with explicit history mode for all registered
  collectors and gap recorders; that is not a claim of complete nationwide collection.
- **`notebooks/31_massachusetts_pdf_walkthrough.ipynb` — learn one parser.**
  Inspect a saved PDF, call the module parser, and reconcile to the printed total.
  Runs offline and writes nothing.

Notebooks 00, 10, 11, and 30 are optional offline source examples. Notebooks 00,
10, and 11 explicitly select staging and inspect it read-only. Notebook 10 fetches
its discovery page only when `run_live_discovery = True`; its retained workbook
example runs offline. Notebook 30 checks expected months and handle amounts, not
GGR/tax reconciliation. Notebooks 40–43 remain historical investigations with
snapshot assumptions; use notebook 90 for current analysis.

## Setup (Python 3.11)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m jupyter lab
```

Select the repository's `.venv` Python kernel. Existing environments need no reinstall.

## Follow the data

Official report → saved file in `data/raw/` → state parser → DataFrame → SQLite.
Notebook 90 reads SQLite, consolidates observations, and displays tables and charts.

| File | Responsibility |
| --- | --- |
| `config/state_gaming_source_inventory.csv` | Official sources and known limitations |
| `config/state_metric_notes.csv` | Native labels, timing, deductions, and definition limits |
| `config/transcribed_report_rows.csv` | Visually checked scan values, bound to exact report hashes/pages |
| `src/variant_gaming/states/` | One module per state format |
| `src/variant_gaming/common.py` | Download, file hashing, dates, money parsing |
| `src/variant_gaming/collect.py` | Explicit source mapping and full-history updates |
| `src/variant_gaming/recent.py` | Small MA/NY recent refresh and per-report results |
| `src/variant_gaming/storage.py` | SQLite reads and writes |
| `src/variant_gaming/consolidate.py` | Source conflicts, labeled sums, CSV exports |
| `tests/fixtures/` | Small saved reports for parser tests |

## What the numbers mean

- Choose `gross_revenue`, `adjusted_revenue`, `taxable_revenue`, or `net_proceeds`
  explicitly. A missing metric is never replaced by another revenue definition.
- `handle` and `tax` are also selectable. Negative revenue and reported zeros are valid.
- Weeks remain weeks. Missing months and weeks remain gaps on their reporting grids.
- A published statewide total is labeled `reported_total`. An operator sum is
  `operator_coverage_unverified`: known values alone do not prove every operator is present.
  If a contributing value is missing, its sum is missing too.
- Legacy IL/IN calculated totals are interpreted as derived, not official.
  Their original database rows remain untouched.
- Equal copies of an observation collapse with source references preserved.
  Different values or definitions produce `conflicting_sources`. Agreeing money
  fields stay visible; only disagreeing fields are cleared. One-cent retained-copy
  differences count as matches. Inspect the original reports using notebook 90's
  trace table. There is no automated revision-authority decision for true conflicts.
- Future re-reads preserve a source's first capture metadata. Coverage records the latest
  refresh. Retrieval time is not publication time, and earlier overwritten clocks cannot
  be reconstructed by this cleanup.
- Shared column names do not establish economic comparability. Notebook 90 labels
  observed-operator trends and limits year-over-year comparisons to published monthly
  totals with matching source labels, only after you explicitly list a state in
  `growth_states_reviewed` for the dates being compared. Mechanically eligible
  candidates are shown as pending analyst review first. It does not sum states
  into a national total.

The original database is preserved. Staging contains new collections and reviewed
parser replays; its corrections are described below. Source-check dates describe
the inventory research, not the publication date or historical availability of a number.

## Nationwide coverage and staging

Open [the coverage report](docs/nationwide_coverage.md) for every state/product,
observed ranges, missing periods, and exact reasons for gaps. The inventory has 102
rows (50 states plus DC, two products). It is a coverage map, not a claim of 102
collected series or complete national GGR. Collection logs are in `data/staging/`.

In notebooks 00, 10, 11, 20, and 90, select one database with:

```python
database_file = "data/staging/gaming_nationwide.sqlite"  # new data and corrected replays
# database_file = "data/gaming.sqlite"                  # preserved original data
```

Notebooks 00, 10, 11, and 90 open the selected database read-only. Notebook 20 defaults to staging;
set `selected_sources` and `collection_mode`, review the displayed plan, and explicitly
enable both `run_downloads` and `allow_database_writes`. The staging database is local and ignored by Git, like raw captures.
To create another staging copy, use SQLite's backup API with a read-only source;
do not overwrite an existing staging file that contains work.

Staging corrections matter for analysis:

- Connecticut's label **Total Gross Gaming Revenue** is its post-deduction base.
  Printed **Win/(Loss)** is now `gross_revenue`; sports revenue after excise is
  `adjusted_revenue`; the post-promotion amount is `taxable_revenue`.
- Pennsylvania sports now retains printed **Revenue** as gross, separately from
  **Gross Revenue (Taxable)**. Missing component taxes remain missing.
- New Jersey sports is replayed from the retained PDFs using the repaired parser;
  missing tax fields remain missing. Original NJ tax/taxable fields need this review.
- Louisiana FY22 has two printed dates inconsistent with their fiscal-month positions.
  Those rows are excluded in staging. May/June 2022 remain gaps; no dates were invented.
- Wyoming has three periods with disagreements between retained copies. April/May
  2023 differ by one cent and now match under cent rounding. December 2023 keeps
  matching GGR/taxable and clears tax where one copy omits it, labeled
  `conflicting_sources`.
- Maryland now follows the archive's older pages and reads its February 2026 PDF.
  Staging covers November 2022 through July 2026. Early negative taxable win is
  preserved; later loss-floor and carryforward rules need period-specific review.

Several sources report only adjusted revenue, net proceeds, handle, or state share.
Read `state_metric_notes.csv` for the selected field before comparing states.
Unknown definition dates remain unknown. A checked recent example does not establish
the same promotion rules throughout the historical range. Tax columns can contain
payments or contractual state shares, which are not interchangeable with tax accruals.

Eight image/broken-font reports have explicit visual transcriptions. The CSV records
the page, native figures, source URL, and hash. Changed bytes require another check.
Kentucky currently uses two monthly totals from one meeting packet; it is a partial
manual collection, not a general Kentucky report parser.

## Trace or repair one observation

In notebook 90, choose `trace_state` and `trace_period`. Inspect the displayed
`source_file`, URL, hash, native label, and competing versions. Open the saved file
relative to the repository, find the printed row, and read the metric-note entry.
For a scan, the transcription CSV also gives its PDF page and the visual check.
Fixture provenance and PDF excerpt pages are listed in `tests/fixtures/nationwide_sources.csv`.

For a manually obtained official file, save it under a **new** raw path, inspect it,
then call the state's `parse_report(path)` in a notebook. Inspect the DataFrame before
adding source metadata and calling `upsert_gaming_results` on a staging connection.
An Arizona download still needs a parser; saving a file alone does not make its
mobile/adjusted fields validated. Do not overwrite an earlier raw capture.

## Later reviewed import

No import into `data/gaming.sqlite` was performed. After reviewing a chosen scope in
notebook 90, back up the original and run the following in a separate notebook cell.
This example imports only new Rhode Island observations. Change the explicit scope
only after reviewing its numbers, definitions, missing periods, and source conflicts.

```python
import sqlite3
from pathlib import Path
import pandas as pd
from variant_gaming.storage import connect, connect_readonly, upsert_gaming_results

backup_path = ROOT / "data/staging/gaming_before_reviewed_import.sqlite"
if backup_path.exists():
    raise FileExistsError(backup_path)
original = connect_readonly(ROOT / "data/gaming.sqlite")
backup = sqlite3.connect(backup_path)
try:
    original.backup(backup)
finally:
    backup.close()
    original.close()

staged = connect_readonly(ROOT / "data/staging/gaming_nationwide.sqlite")
try:
    approved_rows = pd.read_sql_query(
        "SELECT * FROM gaming_results WHERE state_code = ? AND vertical = ?",
        staged, params=("RI", "online_sports_betting"),
    )
finally:
    staged.close()

# Run only after reviewing approved_rows and the backup.
destination = connect(ROOT / "data/gaming.sqlite")
try:
    upsert_gaming_results(destination, approved_rows)
finally:
    destination.close()
```

Do not copy the entire staging database over the original. A corrected replay can
remove or rename rows as well as update values; a simple upsert does not remove old
rows. Review a separate, explicitly scoped replacement for NJ sports or the two
rejected Louisiana dates. Recheck the imported scope in notebook 90 afterward;
stored coverage notes may also need a reviewed update.

## Add one state when needed

1. Save a small official report and inspect its raw table in a walkthrough.
2. Write a plain `parse_report(...)` function in the state module. Return a DataFrame;
   parsing a saved file must not download or write anything.
3. Explain the output row and source-column mappings in a short docstring.
4. Check negatives, missing cells, dates, online/retail separation, and printed totals.
5. Add a focused fixture test, then connect discovery/download/save logic to `collect.py`.

Use existing helpers. Keep state-specific quirks in that state's module.
A manually downloaded official report is a valid starting point.

## Verification — September 7, 2026

The notebook-review repairs passed **305 tests in 91.28 seconds**, including
31 focused notebook regressions (nine Matplotlib backend/layout warnings). Before
repair, those regressions produced 30 failures and one valid-input pass. The guarded
offline checker passed all ten active/source notebooks: 00, 10, 11, 20, 30, 31,
and 90–93, totaling 65 code cells. An intentionally changed MA approval binding
correctly produced a blocked result. Five additional notebook 90 selections passed,
including NY weekly, missing data, and MI adjusted revenue.

NY comparisons now require complete, unique, matching weekly grids. NJ requires
every requested month and one FanDuel observation; its denominator remains explicitly
unverified because the retained operator rows do not prove a complete roster. Weekly
charts show missing weeks as gaps. Michigan's share-direction and dominant-component
wording follows calculated results while retaining Gross Receipts as the measure.

MA result tables and MI monthly shares, coverage checks, cross-product table, and
decomposition matched the pre-edit results exactly. Both protected databases and all
1,572 raw files remained unchanged. Notebooks 20, 31, 40–43, and 91 were byte-for-byte
preserved; notebook 90 changed only coverage/chart cells and their explanation.
The previous uncommitted parser/collection work and approval-bound source/configuration
files were preserved. No approval hashes were replaced. `git diff --check` passed.

These notebook-review repairs add 128 executable lines (64 net), including the expanded
checker list, excluding tests and explanations. Changes remain local and uncommitted.

| Protected database | SHA-256 |
| --- | --- |
| Original `data/gaming.sqlite` | `62afd2b97459f151e62fbbf24c7e0d0fe5a52e9dee829931d2554b9b1529d388` |
| Staging `data/staging/gaming_nationwide.sqlite` | `023ca5e8e4c16ff0981a2783dabcedf9399939e0241701b0394bc0277eff6ce9` |

No live collection, installation, or historical database replay was performed.
The preceding parser/collection milestone added 82 repair source lines and about 495 other lines,
including the offline checker and executable notebook cells, excluding tests and
explanatory text. The existing Python/pandas/SQLite/Jupyter architecture remains.

## Learn, refresh, analyze

Start with **31 → 20 → 90 → 91 → 92 → 93**. Notebook 31 shows the retained official
MA report, extracted rows, normalized columns, missing values, and reconciliation
differences before analysis. Notebook 20 explains collection without executing it by
default. Notebook 90 exposes metric selection, coverage, conflicts, and source tracing.
Notebooks 91–93 separate FanDuel market growth, share changes, and sportsbook hold;
casino revenue alone cannot identify hold or profitability. Michigan casino uses
**Gross Receipts (`gross_revenue`)**; Adjusted Gross Receipts remains a distinct field.

MA recent collection discovers available months and fetches only the latest
`recent_report_limit` PDFs (two by default), rather than every historical PDF. The
index pages still need to be fetched. NY's current statewide and optional operator
workbooks contain fiscal-year sheets of weekly observations: download bytes to check
for changes, then skip parsing only when identical bytes and a verified complete ingestion are already
retained. Missing observations or missing retained bytes require parsing. A small
`recent_ingestions` table in the selected database records the row count after a
successful ingest. Legacy versions without this receipt parse once before skipping. Use explicit
full-history collection when older MA months need review; recent mode does not certify
the archive is complete and can miss older revised months outside its selected window.

`collect_recent(..., db_path=...)` requires a database destination and returns a
DataFrame with report identity, observation dates, URL, download status, byte-change
status, parsed/stored/existing row counts, validation, reason, and retained file/hash.
Counts are rows, monetary observations remain USD. `stored_rows` counts upserted rows,
not net additions; `bytes_changed` is unknown with no stored comparison. The result is
an in-memory run log; only successful-ingestion counts persist in the same SQLite
database. It does not overwrite the existing full-history coverage table.
No extra scheduler, separate log database, or background process is involved.

Changed hashes are kept separately and may create conflicts visible in notebook 90.
Identical-byte skips preserve the first capture metadata. `force_reparse=True` is for a
deliberate parser replay and can update parsed values for the same source hash: prepare
a candidate database and review differences separately before replacing any data.
It does not confer approval or automatically pick an authoritative source version.

The September 2026 repairs prevent Delaware column shifts, partial Pennsylvania
casino totals, New Jersey form-number and sign errors, and New Hampshire sign loss.
They have **not** been replayed into existing databases. The extent of any historical
impact requires a separate retained-source audit and candidate replay. Michigan's
label correction preserves the selected gross-receipts calculations. Existing stored
Michigan generic labels can remain old; analysis uses metric-specific definitions.

## Small changes and offline verification

Use plain functions, explicit arguments, state parsers, documented DataFrame columns
and units, and visible pandas analysis. Test missing cells, reported zeros, negatives,
dates, and duplicate keys with retained fixtures. Parsers do no downloads or writes.
See `AGENTS.md` for the short development rules.

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py
```

Collector tests use mocked HTTP and temporary files/databases. The notebook check
executes active notebooks in memory and blocks network requests, CSV exports, and
writable SQLite connections. It does not save notebook outputs. Local retained data
is needed for the full notebook check; fixture-based tests remain runnable offline.

MA approval is bound to exact source/configuration files and the staging database.
Those approval values are not refreshed automatically. A mismatch must block the
approved result with an explanation, even when exploratory calculations are otherwise
possible. Re-running tests is not renewed human approval. Keep notebook analysis and
any live refresh or historical replay as separate decisions.
