# Vehicle inventory research

This project studies public Carvana listings: what inventory and asking prices
changed within selected searches, and which changes need further investigation.
It does not observe customer transactions. A pending flag, disappearance and
website Sold label are different evidence.

Start with the [code walkthrough](docs/code_walkthrough.md). It introduces the
research question, populations, row keys and six core tables, then traces a real
retained source into an inventory and pricing calculation. Follow its links into
the Python files as you go; no vehicle-market knowledge is assumed.

Use the repository `.venv` and start Jupyter from the Git root:

```powershell
powershell -File scripts/start_jupyter.ps1
```

Then work through **00 → 10 → 11 → 20 → 24 → 30**:

| Notebook | Read it to understand |
| --- | --- |
| [00 — source walkthrough](notebooks/00_source_walkthrough.ipynb) | One real source becoming a pandas row; a later example is synthetic. |
| [10 — inventory snapshot](notebooks/10_carvana_inventory.ipynb) | Query coverage before interpreting a retained inventory count. |
| [11 — collection lab](notebooks/11_carvana_live_collection_lab.ipynb) | Search settings, the request preview and offline source replay. |
| [20 — inventory and pricing](notebooks/20_carvana_history_analysis.ipynb) | Comparable dates, VIN joins, asking prices and coverage gaps. |
| [24 — selected status study](notebooks/24_carvana_status_experiment.ipynb) | A frozen sample, native website outcomes and missing checks. |
| [30 — quarterly review](notebooks/30_carvana_sales_expectations.ipynb) | Explicit assumptions, saved forecasts and prospective evaluation. |

Normal **Restart Kernel and Run All is offline and read-only**. Notebook 11
defines a live function but does not call it by default. Routine analysis starts
in 20 and 24; 30 is for quarterly assumptions and forecast review.

The expanded [full-inventory workflow](docs/full_inventory.md) discovers all current
make/model categories with a separate 6,000-request/six-hour ceiling. Start with
Notebook 25 to review its saved collection and coverage reports. The
[revised goal](docs/full_inventory_goal.md) records the baseline, historical
comparison and sales-validation requirements that remain to be demonstrated.

Use the [review and operating guide](docs/status_experiment.md) for exact
settings, commands, separate collection authorization, recovery and export
instructions. The code walkthrough explains how the pieces work; that guide
governs how to operate them. Notebooks 21, 22 and 23 are historical or specialist
references, mapped at the end of the walkthrough.
