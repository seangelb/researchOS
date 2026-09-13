# researchOS

One notebook-first research repository with two independent projects:

| Project | Start here | Purpose |
| --- | --- | --- |
| Gambling industry | [Gaming guide](gaming/README.md) | Public gambling data, legal developments and FLUT/DKNG/CZR research |
| Vehicle / Carvana | [Vehicle walkthrough](vehicle/docs/code_walkthrough.md) | Retained inventory, asking prices, listing-status evidence and quarterly assumptions |

Each project has its own modules, notebooks, fixtures, and data. ResearchOSCore and
the older EquityIntelligenceOS investment system remain separate repositories.

For a guided review of the current work, follow [the notebook reading order](REVIEW.md).

## Open the notebooks

Open this repository in Cursor or VS Code and use its root Python 3.11 environment.

```powershell
powershell -File scripts/start_jupyter.ps1 -Check
powershell -File scripts/start_jupyter.ps1
```

For a new environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Both Python packages are included in the root package configuration. Notebook
source references resolve within their own project, not relative to the Git root.

## Current data availability

Gaming uses three steps: [20 — capture official reports](gaming/notebooks/20_run_all_collectors.ipynb),
[94 — read the monthly fundamentals review](gaming/notebooks/94_gaming_industry_update.ipynb), and
[90 — investigate sources](gaming/notebooks/90_consolidated_ggr.ipynb). The selected
September 12 capture has **19,806 observations across 34 state/product series**;
the comparable MA/MI monthly panel and separate NY weekly panel have narrower
coverage. The [industry guide](gaming/docs/industry_workflow.md) explains the
single snapshot setting, 12-month/rolling-three-month comparisons, legal-event watchlist and optional dated note export.
Read the [September 12 monthly review](gaming/docs/monthly_fundamentals_20260912.md) for the
business conclusions and limitations. FLUT expectations notebooks 95/96 remain
optional historical experiments.

The exact historical original, staging, and approved-candidate databases remain
missing. Studies 91–93 retain their original gates and historical outputs; fresh
downloads do not recreate those approved snapshots. See [recovery status](gaming/docs/data_recovery.md).
The Pennsylvania and Colorado walkthroughs retain their reviewed sample reports.

Vehicle evidence remains in `vehicle/data/`. Neither branch merging nor installation
runs collectors, refreshes data, changes approved snapshots, or promotes a valuation.

## Validate

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py --project all
```

Use `--project gaming` or `--project vehicle` to check one notebook workflow.
The checker blocks network access and writes; missing data and approval blocks are
not successful executions. The daily gaming path and historical approval studies 91–93 are checked by default;
91–93 remain BLOCKED on missing archives. Add `--include-reference` for optional
source examples and FLUT experiments 95/96. Older 40–45 remain retained investigations.

## Keep development together

Start new work from the integrated `main` branch. Use short-lived
branches or isolated worktrees, then merge validated changes back. Do not leave the
only copy of a repair in an uncommitted branch checkout or temporary review folder.

Source code is versioned; captured data and databases are local and ignored. Keep
separate backups of both projects' data, including raw reports and metadata.
The [integration record](gaming/docs/integration_20260912.md) identifies recovered work.
