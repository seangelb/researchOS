# Variant / researchOS

Learning-first notebooks and small Python modules that collect **official** U.S.
online sports-betting and online-casino revenue from state regulators.

## Active notebooks

1. `notebooks/00_source_inventory.ipynb`
2. `notebooks/10_new_york_walkthrough.ipynb`
3. `notebooks/11_illinois_walkthrough.ipynb`
4. `notebooks/20_run_all_collectors.ipynb`
5. `notebooks/30_pdf_parsing_walkthrough.ipynb`
6. `notebooks/31_massachusetts_pdf_walkthrough.ipynb`
7. `notebooks/40_sqlite_ggr_analysis.ipynb`
8. `notebooks/90_consolidated_ggr.ipynb`

## Setup (Python 3.11)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
```

## Collect and export

```powershell
$env:PYTHONPATH = "$PWD\src"
python -c "from variant_gaming.collect import run_all_collectors; print(run_all_collectors())"
python -m variant_gaming.consolidate
```

Collectors **upsert** into `data/gaming.sqlite`. They never replace the whole table
while processing one state. Blocked official sources are recorded in `source_coverage`.

## Rules of the road

- Official regulator / lottery / gaming-commission sources only
- TLS verification on; immutable hashed raw captures
- Preserve native frequency; do not invent months
- Do not treat GGR, AGR, taxable revenue, and net proceeds as interchangeable
- Preserve negative revenue; never replace missing revenue with zero
