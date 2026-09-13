# A small monthly gambling fundamentals workflow

Start with [notebook 94](../notebooks/94_gaming_industry_update.ipynb). Read supporting and contrary evidence together, then the latest three-month comparison, 12-month charts, prior-read changes and gaps. The [September 12 monthly review](monthly_fundamentals_20260912.md) is a saved example; the [earlier July-only study](industry_update_20260912.md) remains a historical reference. One month and three months can point differently: July FanDuel MA share rose, while its May–July share declined.

The [September 13 August release check](release_readiness_20260913.md) confirms July remains the common endpoint in the checked official sources. The [September 12 readiness plan](release_readiness_20260912.md) records the conditions for the next company-share comparison.

## Capture, read, investigate

1. [Notebook 20](../notebooks/20_run_all_collectors.ipynb) previews explicit state/product sources, the current base, a new dated destination and separate ZIP backup. Recent mode supports MA/NY sportsbook; other collectors require explicit history mode. Both download/write switches default off. A live refresh preserves its base and validates source bytes, SQLite schema/clocks and backup restoration. Inspect collection exceptions; completion is not complete historical coverage.
2. [Notebook 94](../notebooks/94_gaming_industry_update.ipynb) performs the monthly review offline. `config/current_snapshot.json` selects the exact database path/hash and `through_month`, which becomes the review endpoint. Nothing is selected by latest timestamp. Its source loader rejects unbound WAL/journal changes, incomplete receipts, invalid clocks, changed source bytes and future cutoffs.
3. [Notebook 90](../notebooks/90_consolidated_ggr.ipynb) explores a particular state, native metric, period or source. The broader stored coverage is not a national index.

The selected capture has 19,806 observations across 34 state/product series. It ends the analytical review at July 2026. Changing the endpoint is explicit; future months are rejected and missing historical months remain missing. After a live refresh, inspect its exceptions before deliberately updating the shared selection. Do not repin old approvals or delete earlier evidence.

## What the comparisons mean

| View | Exact calculation | Limit |
| --- | --- | --- |
| 12 monthly observations | August 2025–July 2026 at the July endpoint; each month against the same month one year earlier | Valid current amounts remain visible if the prior-year comparison is unavailable |
| Rolling three months | Every three-month window ending at each displayed month, compared with its identical prior-year months | All requested months must qualify; amounts are summed, shares/hold are ratios of sums |
| Acceleration/deceleration | Latest 3M YoY rate minus preceding non-overlapping 3M YoY rate; at July, May–July versus February–April rates | Requires all months in all four current/prior-year windows; does not establish cause or durability |
| Change from prior monthly read | July endpoint minus June endpoint: May–July versus April–June rolling rates and YoY share changes | Overlapping windows calculated on the same retained capture; not a historical point-in-time signal |
| NY weekly panel | Recent complete Monday–Sunday weeks reconciled to official controls | No monthly proration or blending with the rolling panel |

The monthly review reuses the already reconciled native monthly tables once. It does not rebuild state history for every endpoint. Every row keeps native metric, requested months, missing current/prior months, source references and status. Equal source copies stay traceable; conflicting revisions are not selected by recency. Zero/negative reported revenue stays visible; growth from a nonpositive baseline is undefined, not a false percentage improvement.

## Demand, share and earnings are different questions

Official MA/MI online handle measures wagering volume. Brand handle share measures competitive position. Sportsbook gross hold is source-native gross revenue divided by handle; higher hold is an outcome, not proof of stronger demand, retention or durable margin. MI casino Gross Receipts and Adjusted Gross are displayed separately, with no sportsbook hold measure. Gross receipts, adjusted/taxable state revenue and issuer net revenue are not interchangeable.

The supporting/contrary columns describe individual volume, share and growth-rate observations. They do not vote on an overall company score. The [dated issuer and ownership context](company_context_20260912.md) provides material counterevidence: Q2 2026 issuer profit measures weakened even where activity grew. Its April–June period and issuer scope differ from the state windows. Management explanations are issuer statements, not causal findings from the state data. Q2 context remains explicitly dated when the operating endpoint changes; it is not refreshed by changing a notebook parameter.

## Exact company scope and source gaps

FLUT is native FanDuel evidence in the covered US states, excluding International. DKNG is the native DraftKings brand/license and excludes Golden Nugget. CZR includes MA/NY sportsbook and two specifically reviewed Michigan licenses. Grand Traverse sportsbook starts May 2021, the first full month after the William Hill acquisition. Combined Grand Traverse/Sault casino starts July 2024, the first full month after the Wynn Michigan acquisition. No Sault sportsbook is attributed. The licensed-operation figures are not consolidated Caesars net revenue and cannot describe the land-based group.

Michigan aliases remain visible as printed: William Hill/Caesars-WSOP for Grand Traverse; Wynn/Caesars Horseshoe for Sault casino. Exactly one native alias per required license/month must be present. Each full source population must reconcile before casino numerators are added. The notebook checks the retained regulator/issuer mapping-source hashes and capture cutoffs before using the mapping; earlier ownership-crossing comparisons remain unavailable. [Source receipts](company_context_sources_20260912.json) document the dated ownership and issuer review. Recheck when adding later captures.

MA January 2025 has a genuine $0.90 disagreement within the original PDF: the main online handle table differs from the comparative tables and operator sum. Economic magnitude is immaterial, but source adjudication is pending. The existing one-cent gate is unchanged. January 2026 monthly handle YoY and January–March 2026 rolling handle/hold comparisons remain unavailable; latest May–July comparisons are unaffected. CZR's first displayed rolling casino comparison also crosses the July 2024 ownership boundary and remains unavailable. The gap tables expose these months rather than filling or shrinking them.

MA/MI monthly evidence starts from official statewide controls and the full retained operator population in a common source version. NY has separate operator/statewide workbooks, so it checks each exact identity and complete weekly population separately. Broader coverage and [native metric definitions](../config/state_metric_notes.csv) are available below the main charts.

## Legal evidence and optional notes

The [legal event file](../config/legal_events.json) is a small sourced watchlist, separating observed facts, source-established status/date, effective date, applicable scope, accounting effect, analyst inference and next checks. It is not exhaustive current-law coverage. Existing tax baselines are not new shocks; interim court rulings are not final nationwide bans. The dated context adds primary NJ/IL observations without merging their differing statewide measures into MA/MI.

`capture_legal_source` in `variant_gaming.legal` previews a specific HTTPS source and destination. Explicit `live=True` retains original content-hashed bytes and their receipt. Read the document before manually adding an event; preserve unknown dates and changed versions. Capturing an old document now does not confirm its legal status remains current.

Notebook 94 prepares a Markdown note in memory. To save it, set `export_note=True` and an absolute new dated `.md` filename in an existing folder. The note includes supporting/contrary evidence, exact windows, prior-read meaning, source drilldown and dated context. Existing notes cannot be overwritten. Descriptive research needs no forecast/valuation approval. Back up notes, databases and original sources separately; Git does not preserve ignored captures.

## Verify and retain historical studies

From the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py --project gaming
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py --project gaming --include-reference
```

The checker blocks network and writes. Historical studies 91–93 still report BLOCKED because their exact approved archives are missing; do not change their hashes. Optional 95 is the former FanDuel-only quarterly table; optional 96 retains the old scenario/freeze experiment. They are not daily prerequisites. [Recovery status](data_recovery.md) and the [previous detailed reference](gaming_reference_20260912.md) preserve historical context.
