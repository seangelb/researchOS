# Notebook-first workflow

This project already has reusable collectors under `src/variant_gaming/`.
Notebooks explain the sources; modules hold proven download and parse logic.

## Active notebooks

| Notebook | Role |
| --- | --- |
| `notebooks/00_source_inventory.ipynb` | Inventory waves and coverage statuses |
| `notebooks/10_new_york_walkthrough.ipynb` | Teach one NY weekly Handle/GGR path |
| `notebooks/11_illinois_walkthrough.ipynb` | Teach IL handle/AGR join and online-only rows |
| `notebooks/20_run_all_collectors.ipynb` | Run `run_all_collectors()` sequentially |
| `notebooks/90_consolidated_ggr.ipynb` | Read SQLite only; analyze labeled revenue |

Older numbered NY/IL notebooks and per-state wrappers remain in git history from the checkpoint commit.

## Reusable layer

```text
src/variant_gaming/
    common.py
    storage.py
    collect.py          # explicit COLLECTORS map + run_all_collectors()
    consolidate.py
    states/             # one module per distinct official format
```

Rules:

- Ordinary functions only
- Upsert into `gaming_results`; never replace the whole table
- Official regulator sources; TLS on
- Preserve native frequency and negative revenue
- Do not treat GGR, AGR, taxable revenue, and net proceeds as interchangeable

## Tests

Parser tests live in `tests/` with small official or synthetic fixtures.
Run:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m pytest -q -p no:cacheprovider
```
