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

The September 12 integration recovered the gaming code, notebooks, parser repairs,
and tests. Its historical original, staging, and approved-candidate databases and
raw report archive were not found locally. The retained financial studies remain
blocked until their exact data is restored. See [recovery status](gaming/docs/data_recovery.md).
The Pennsylvania and Colorado walkthroughs retain their separate reviewed sample
reports; those samples do not replace the missing full archive.

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
