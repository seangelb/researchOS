# Notebook workflow

See README.md for the current workflow and data limitations.

- 90: daily analysis, read-only by default; CSV export is optional.
- 20: selected-source updates; downloading and database writes are explicit.
- 31: saved Massachusetts PDF → module parser → reconciliation, entirely offline.
- 40–43: historical investigations, not the active workflow.

Modules hold reusable parsing and storage logic. Notebooks keep ordinary analysis visible.
The current cleanup changes code only; existing raw files and the main database are retained.
