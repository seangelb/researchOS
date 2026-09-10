# Recovery onto main

The user authorized recovering and publishing the Carvana work omitted from
`119b42b52a4b9be46641b35865bda8f75e2b50bc`. The original implementation handoffs
describe work on `codex/notebook-reliability`; they remain historical records.

Source, tests and documentation were restored from the retained pre-simplification
backup and final source diffs. Frozen cohort definitions, the browser fixture,
capture helper and import script were checked against the recorded SHA-256 hashes.
Notebooks 20 and 22 preserve the final source and cell order; their saved outputs
were cleared and their code was validated offline against retained evidence.

The current checkout keeps gaming at the repository root. Carvana tests therefore
use `scripts/check_notebooks.py` directly. That command now executes all six Carvana
notebooks with network, CSV exports and writable SQLite connections blocked,
instead of only checking notebook structure. It is a regression guard for trusted
notebooks, not a security sandbox. The Jupyter launcher uses the existing root
environment without installing anything.

Validation: 499 vehicle tests passed; all six Carvana notebooks executed successfully.
A separate execution attempt on the existing gaming notebooks exposed missing
gaming data and cells that attempt writes or network access. Those notebooks were
not changed or included in the Carvana checker. No live collection, operating
database migration or retained-data rewrite was performed during this recovery.
