# Gambling industry research

Use public state reports and retained legal developments to judge **covered-market demand and competitive position for FLUT, DKNG and CZR**. The work is plain Python, pandas, SQLite and Jupyter, with offline analysis by default.

1. **Capture when needed:** [20 — collect official reports](notebooks/20_run_all_collectors.ipynb). Preview the exact sources, new dated destination and external backup before enabling a live run. Add a legal source/event using the small [industry guide](docs/industry_workflow.md).
2. **Read the business update:** [94 — gambling industry update](notebooks/94_gaming_industry_update.ipynb). One notebook shows what changed, interpretation, uncertainty and next checks, followed by demand, brand share, gross hold, casino and legal evidence.
3. **Investigate a question:** [90 — source explorer](notebooks/90_consolidated_ggr.ipynb). Inspect a particular state, native metric, period and retained source.

The [industry guide](docs/industry_workflow.md) explains the explicit snapshot selection, calculations and optional dated note export. See the [September 12 example update](docs/industry_update_20260912.md).

The selected September 12 capture contains **19,806 observations across 34 state/product series**. This is not nationwide analytical coverage. The comparable monthly panel currently covers MA sportsbook and MI sportsbook/casino; NY remains a separate weekly panel. **Q3 currently has July only.** Legal events are a small sourced watchlist, with historical status dates and recheck needs visible.

FLUT means native FanDuel evidence in the covered US states. DKNG means the native DraftKings brand/license, excluding Golden Nugget. CZR's supported panel covers MA/NY sportsbook; its land-based group and unreviewed Michigan multi-license aggregation remain outside the conclusion. Handle, gross revenue, adjusted revenue, taxable revenue and issuer accounting are different measures.

Use the root [launch and validation commands](../README.md). The default gaming checker runs 20, 90, 94 and the historical approval studies 91–93. **91–93 remain BLOCKED because their exact historical databases are missing.** Those gates and approval hashes have not changed. `--include-reference` also runs source examples and optional FLUT experiments 95/96; they are not daily prerequisites.

Source configuration lives in [the inventory](config/state_gaming_source_inventory.csv) and [metric notes](config/state_metric_notes.csv); state parsers remain in `src/variant_gaming/states/`. See [data recovery](docs/data_recovery.md), [integration history](docs/integration_20260912.md), and [previous detailed documentation](docs/gaming_reference_20260912.md) for retained historical context. Captures and databases are ignored by Git and need their own backup.
