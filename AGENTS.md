# Keep researchOS understandable

- Keep gaming/ and vehicle/ as separate projects in this repository, with separate data and definitions.
- Use the root Python 3.11 environment, pandas, SQLite, Jupyter, and small plain functions.
- Read the project's AGENTS.md before editing it. Preserve existing work and retained source bytes.
- Analysis is offline and read-only by default. Live collection requires an explicit source, destination, and intent.
- Preserve missing values, zeros, negatives, native identities, and source versions. Missing does not mean zero or sold.
- Do not change approval hashes to make a notebook pass. Test success is not analyst approval.
- Keep financial/status definitions and intermediate tables visible. Do not add a shared database or generic ingestion framework.
- Use short-lived feature branches or isolated worktrees and merge reviewed work into the integrated branch.
- Databases and captures are ignored by Git. Back them up separately; a Git backup does not preserve them.

From the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py --project all
powershell -File scripts/start_jupyter.ps1 -Check
```

Missing retained data must be reported as blocked, never replaced by fabricated or automatically refreshed evidence.
