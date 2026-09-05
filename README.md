# researchOS: official state gaming revenue

Small Python modules collect official online sports-betting and online-casino reports.
Use pandas in Jupyter to inspect the results. No service or separate application is needed.

## Open these notebooks

- **`notebooks/90_consolidated_ggr.ipynb` — daily analysis.** Change states, product,
  metric, dates, and frequency, then Restart Kernel and Run All. It reads SQLite
  without downloading or writing. Optional CSV export is disabled by default.
- **`notebooks/20_run_all_collectors.ipynb` — updates.** Choose state/product pairs,
  inspect the plan, then set `run_downloads = True` when ready to collect.
  Each selected source runs its existing history routine. Use `selected_sources = None`
  for all registered collectors and gap recorders. This is not an incremental refresh
  and does not mean every inventory row has a working collector.
- **`notebooks/31_massachusetts_pdf_walkthrough.ipynb` — learn one parser.**
  Inspect a saved PDF, call the module parser, and reconcile to the printed total.
  Runs offline and writes nothing.

Notebooks 00, 10, 11, and 30 are optional source examples. Notebook 10 downloads
its discovery page. Notebooks 40–43 are historical investigations with snapshot
assumptions; use notebook 90 for current analysis.

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
| `src/variant_gaming/collect.py` | Explicit source mapping and sequential updates |
| `src/variant_gaming/storage.py` | SQLite reads and writes |
| `src/variant_gaming/consolidate.py` | Source conflicts, labeled sums, CSV exports |
| `tests/fixtures/` | Small saved reports for parser tests |

## What the numbers mean

- Choose `gross_revenue`, `adjusted_revenue`, `taxable_revenue`, or `net_proceeds`
  explicitly. A missing metric is never replaced by another revenue definition.
- `handle` and `tax` are also selectable. Negative revenue and reported zeros are valid.
- Weeks remain weeks. Missing months remain gaps.
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

In either notebook, select one database with:

```python
database_file = "data/staging/gaming_nationwide.sqlite"  # new data and corrected replays
# database_file = "data/gaming.sqlite"                  # preserved original data
```

Notebook 90 opens the selected database read-only. Notebook 20 defaults to staging;
set `selected_sources`, review the displayed plan, and explicitly enable
`run_downloads`. The staging database is local and ignored by Git, like raw captures.
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

## Verify changes

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
```

Tests use retained fixtures and temporary databases. Do not run collectors just to test a parser.

Nationwide validation on 2026-09-05: **184 tests passed**. Notebook 90 executed
against staging with HTTP requests and CSV writes blocked and read-only SQLite
connections enforced. Original database SHA-256 remained
`62afd2b97459f151e62fbbf24c7e0d0fe5a52e9dee829931d2554b9b1529d388`; staging SHA-256 is `5b7c00777123494d089fc13358bf82dc73102d2d03a9c0b0853fbfa7940ebd0d` after the Massachusetts
printed-total repair. Both hashes were unchanged during notebook execution.
`git diff --check` passed. All 1,134 preexisting raw files matched their
before-work hashes. Massachusetts state-period rows display `reported_total`.
Details are in `data/staging/validation_summary.json` and
`data/staging/protected_file_hashes.json`.