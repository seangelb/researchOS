# ResearchOS integration record

- Preserved the latest uncommitted Carvana changes in checkpoint `5102b2b`.
- Merged `codex/notebook-reliability` (`052ed0b`) with the latest Carvana history.
  All older vehicle paths already existed in the newer vehicle tree, which was preserved.
- Merged the reviewed Pennsylvania (`4e7248a`) and Colorado (`968f559`) notebooks.
- Recovered 160 gaming source, notebook, fixture, test, and documentation files from
  the September 7 layout review copy, checking every file against its saved SHA-256.
- Restored later parser repairs, missing-value handling, first-capture preservation,
  notebook coverage checks, historical correction preview, and their regression tests.
- Organized projects under gaming/ and vehicle/ with shared package configuration
  and launch/check commands. Financial definitions and approval hashes were retained.

Original branch histories remain available. A separate pre-integration branch and
Git bundle preserve the starting code; a file backup preserves the 24 working edits.
Data is excluded from Git and requires its own backup. The gaming data recovery
limitation is recorded in [data_recovery.md](data_recovery.md).
