# Review the current work, notebook by notebook

Start from this repository in Cursor, VS Code or JupyterLab. Use the root
`.venv` Python 3.11 environment. For JupyterLab, run from the repository root:

```powershell
powershell -File scripts/start_jupyter.ps1
```

Work through one notebook at a time. Read its question and settings first, then
restart the kernel and run the cells in order. The main notebooks below default
to offline, read-only analysis. Keep collection/write switches off and do not
call the optional live function in vehicle Notebook 11 during this review.
Changing a population or cutoff warrants restarting the kernel.

## First pass: gaming

| Order | Notebook | What to review | A useful question to answer |
| --- | --- | --- | --- |
| 1 | [94 — monthly industry update](gaming/notebooks/94_gaming_industry_update.ipynb) | Selected snapshot and month; coverage; monthly and three-month inputs; demand, company share, gross hold, casino and dated legal/issuer context. | Which observations support or contradict improvement for FLUT, DKNG and CZR? |
| 2 | [90 — source explorer](gaming/notebooks/90_consolidated_ggr.ipynb) | Filter one state/product/native metric and follow a number back to its retained source. | Can I reproduce one number from 94, with the same denominator and period? |
| 3 | [20 — collector preview](gaming/notebooks/20_run_all_collectors.ipynb) | Sources, base snapshot, destination, coverage exceptions and backup checks; leave live/write settings disabled. | What would a new capture add, and what must pass before I select it? |

The selected review is explicitly July 2026. A newer retrieval alone does not
advance the endpoint. Read the [monthly review](gaming/docs/monthly_fundamentals_20260912.md)
beside 94, and use the [industry guide](gaming/docs/industry_workflow.md) for the
latest linked availability check and source definitions. Wider stored coverage
does not make the MA/MI monthly panel national. NY stays a separate weekly view.

For a deeper source lesson, choose [31 — Massachusetts PDF](gaming/notebooks/31_massachusetts_pdf_walkthrough.ipynb),
[10 — New York](gaming/notebooks/10_new_york_walkthrough.ipynb), or
[11 — Illinois](gaming/notebooks/11_illinois_walkthrough.ipynb). Older 40–45 are
retained investigations with their own data/settings; they are not prerequisites.
Notebooks 91–93 are blocked by missing exact historical archives. Do not change
their approval bindings. FLUT experiments 95/96 are optional after the core review.

## Second pass: Carvana

Read the opening population definitions in the
[code walkthrough](vehicle/docs/code_walkthrough.md), then follow this order:

| Order | Notebook | What to review | A useful question to answer |
| --- | --- | --- | --- |
| 1 | [00 — source walkthrough](vehicle/notebooks/00_source_walkthrough.ipynb) | One real listing becoming a pandas row; distinguish the later synthetic example. | Where do the VIN, asking price, pending flag and observation time come from? |
| 2 | [10 — inventory snapshot](vehicle/notebooks/10_carvana_inventory.ipynb) | Selected plan/run, complete versus incomplete queries, identities and observed listings. | Exactly which population does this count describe? |
| 3 | [11 — collection lab](vehicle/notebooks/11_carvana_live_collection_lab.ipynb) | Request preview, retained response, field mapping and one VIN calculation. Do not invoke `fetch_one_page()`. | Can I follow a setting through the request and into a retained row? |
| 4 | [20 — inventory and asking prices](vehicle/notebooks/20_carvana_history_analysis.ipynb) | Coverage before comparisons, VIN joins, additions/disappearances, matched price changes and composition. | Is this a comparable pair of dates, and is a mean-price change caused by repricing or inventory mix? |
| 5 | [24 — frozen status study](vehicle/notebooks/24_carvana_status_experiment.ipynb) | Fixed 32-VIN selection, first qualifying website outcomes, unvisited checks, full-sample bounds and repeat windows. | How much do the missing outcomes limit the conclusion? |
| 6 | [30 — quarterly review](vehicle/notebooks/30_carvana_sales_expectations.ipynb) | Explicit assumptions, source coverage, forecast vintages and reported-result availability. | What is observed, what is assumed, and what remains unavailable? |

Notebook 20 defaults to the original Tesla panel. To reproduce the retained
September 11/12 inventory and price example, use the explicit two-cycle selection
in the [operating guide](vehicle/docs/status_experiment.md#notebook-20-population-inventory-and-asking-prices).
The separate 101-query panel's first operating attempt is partial: six complete
queries and 1,284 observed VINs, with no complete baseline. The earlier 10,000-VIN
trial is capacity evidence. Use the [approved continuation record](vehicle/docs/resume_20260913.md)
for the broader-panel settings and its seven-date calendar. Keep those populations separate.

Notebook 24's ordinary `STUDY` setting selects the frozen study. `AS_OF` controls
which retained checks are visible; an older cutoff will exclude later evidence.
For the latest [approved continuation](vehicle/docs/resume_20260913.md), keep the
existing study and set `AS_OF = '2026-09-14T00:07:09.134064+00:00'` before running.
There are 27 identity-matched primary checks and five unvisited VINs; one matched
check still has an unresolved binary endpoint. The [earlier checkpoint](vehicle/docs/checkpoint_20260913.md)
keeps its original cutoff for historical replay.
Notebook 22 is the original legacy capture reference and does not automatically
incorporate newer detail batches. Notebooks 21–23 can wait until after this pass.

Empty actual forecasts in 30 are intentional. A disappearance, pending flag and
native Sold label are distinct observations; none supplies a transaction price
or a national sales estimate by itself.

## Record feedback as you go

For each notebook, note: the cell or table; what you expected; what you observed;
and the question or change you want. In particular, flag any number you cannot
trace, unclear metric definition, hidden population change or conclusion that
goes beyond the evidence. Save setting edits deliberately; do not overwrite
retained captures or historical approvals during review.

The first goal is to understand and challenge one complete source-to-conclusion
path in each project. The user has approved this review. Remaining dated
observations are scheduled operating work; they are not completed evidence.
