# researchOS

One notebook-first research repository with two independent projects:

| Project | Start here | Purpose |
| --- | --- | --- |
| Gaming / FLUT | [Gaming guide](gaming/README.md) | Official state reports, FanDuel sportsbook and casino research |
| Vehicle / Carvana | [Vehicle walkthrough](vehicle/docs/code_walkthrough.md) | Retained inventory, asking prices, listing-status evidence and quarterly assumptions |

Each project has its own modules, notebooks, fixtures, and data. ResearchOSCore and
the older EquityIntelligenceOS investment system remain separate repositories.

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

The latest September 12 gaming capture contains **19,806 observations across 34
state/product series**, including recovered Kentucky operator history. Start with
[FLUT current data](gaming/notebooks/94_flut_current_data.ipynb), then the
[quarterly scorecard](gaming/notebooks/95_flut_quarterly_scorecard.ipynb) and
[expectations review](gaming/notebooks/96_flut_expectations_review.ipynb).
The [current analyst review](gaming/docs/flut_current_quarter_review_20260912.md)
explains the July results and next research priorities. The
[workflow guide](gaming/docs/flut_workflow.md) records refresh, validation, backup,
and the first frozen reference. Notebook 20 previews a fresh capture and performs
validated collection and a tested backup when explicitly enabled.

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
not successful executions. Historical notebooks 40–45 are retained investigations,
not part of the active offline notebook run.

## Keep development together

Start new work from the integrated `main` branch. Use short-lived
branches or isolated worktrees, then merge validated changes back. Do not leave the
only copy of a repair in an uncommitted branch checkout or temporary review folder.

Source code is versioned; captured data and databases are local and ignored. Keep
separate backups of both projects' data, including raw reports and metadata.
The [integration record](gaming/docs/integration_20260912.md) identifies recovered work.
