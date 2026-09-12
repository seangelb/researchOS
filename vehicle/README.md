# Carvana: learn, inspect, then operate

Use the repository `.venv` and start Jupyter from the Git root:

```powershell
powershell -File scripts/start_jupyter.ps1
```

**First-time learning: 00 → 10 → 11 → 20 → 24 → 30.**
**Routine review: 20 for inventory/pricing, 24 for the selected status study.**
Open 30 when reviewing quarterly assumptions or saved forecasts. Notebooks 21,
22 and 23 remain intraday, original-cohort and specialist research references.

Normal **Restart Kernel and Run All is offline and read-only**, including the
new live-collection lab. The lab defines a deliberate one-page action; calling
it requires fresh confirmation and consumes its separate three-attempt teaching
budget. Teaching evidence never enters operating history.

| Notebook | Question and ordinary settings |
| --- | --- |
| [00 — source walkthrough](notebooks/00_source_walkthrough.ipynb) | How does retained source become a normalized row? Start with the real VIN, then the labelled synthetic example. |
| [10 — inventory snapshot](notebooks/10_carvana_inventory.ipynb) | What did one retained query observe? Select its report and evidence together. |
| [11 — live collection lab](notebooks/11_carvana_live_collection_lab.ipynb) | How do search settings become a request and retained pandas rows? Edit make/model/year/ZIP; read saved evidence before choosing a live action. |
| [20 — inventory and pricing](notebooks/20_carvana_history_analysis.ipynb) | What changed within the selected population? Edit `TRACKING_CONFIG`, `ANALYSIS_CUTOFF` and `EXAMPLE_IDENTITY`; use `RETAINED_CYCLES` for the commented offline comparison. The original Tesla panel remains the default. |
| [24 — selected status study](notebooks/24_carvana_status_experiment.ipynb) | What do the predefined checks show, including missing outcomes? Select `STUDY`; the frozen 32-VIN study remains the default. |
| [30 — quarterly review](notebooks/30_carvana_sales_expectations.ipynb) | What do dated assumptions and saved forecast vintages support? Empty analyst inputs are intentional; synthetic examples are separate. |
| [21](notebooks/21_carvana_intraday_reference.ipynb), [22](notebooks/22_carvana_sale_status_validation.ipynb), [23](notebooks/23_carvana_daily_sales_research.ipynb) | Historical/specialist references. 22 reviews the original legacy captures; newer browser batches appear in 23/24. Older operating proposals are historical. |

Read the [review and operating guide](docs/status_experiment.md) for the data flow,
real examples, exercises, selected populations, exact commands, failure/recovery
instructions and validation evidence. The collector remains ordinary Python,
pandas, SQLite and retained JSON; important joins and calculations stay visible
in notebook cells.

The earlier authorized scale run demonstrated **10,000 VINs in 466 requests over
23.44 minutes**, with 101 complete queries. Its evidence and prior failed attempt
are preserved. The [fixed seven-date operating proposal](data/experiments/mvp_completion_20260912/scale_acceptance_20260912/proposed_seven_date_validation.md)
uses those 101 completed query definitions in the original order, with at most
600 requests/60 minutes per date. It remains unexecuted and unscheduled. The
first collected date will establish that panel's daily baseline.

A pending flag, disappearance, native Sold label and economic sale are different
things. Asking prices are not transaction prices. Seven inventory dates do not
mature every seven-day absence event. Neither the selected panel nor frozen
cohorts can be scaled into national sales without supported calibration. The
**1–3% quarterly error target remains a future prospective validation goal**.

Start every new browser check through the [reserved browser batch workflow](docs/browser_detail_batches.md).
For explicit exports, save the notebook's ordinary settings first: the exporter
reruns the file on disk, not the current kernel. Existing evidence is never replaced.
