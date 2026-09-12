# Source configuration

`carvana_daily_tracking.json` selects the original seven-query Tesla Model 3
population, its history, register and collection limits. The separate proposed
101-query configuration is described in the [operating guide](../docs/status_experiment.md).
Selecting a configuration makes no requests; collection requires an explicit live action.

Keep a population paired with its own query plan, register and database. Frozen
cohort files retain their original membership. Credentials belong outside the repository.
