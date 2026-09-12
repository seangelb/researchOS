# ResearchOS integration record

- Preserved the latest uncommitted Carvana changes in checkpoint `5102b2b`.
- Merged `codex/notebook-reliability` (`052ed0b`) with the latest Carvana history.
  All older vehicle paths already existed in the newer vehicle tree, which was preserved.
- Merged the reviewed Pennsylvania (`4e7248a`) and Colorado (`968f559`) notebooks.
- Recovered the newer uncommitted repairs in both state-review worktrees, including
  Pennsylvania template/missing-value checks and Colorado offline/source-sign controls.
  Preserved 20 associated sample reports and manifests byte-for-byte under gaming/data/raw/.
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

## Working location and recovery copies

The active checkout is `C:\Users\Sean\VscProjects\researchOS`, on local `main`.
ResearchOSCore and EquityIntelligenceOS remain separate. No remote was pushed.

The audit folder is
`C:\Users\Sean\Documents\ChatGPT\ResearchOS\repo_integration_20260912`.
It contains the original branch bundle, working-file backups, before/after data
hash checks, notebook source diffs, and validation logs. The original review
worktrees were not modified. The integration worktree remains available at
`C:\Users\Sean\VscProjects\researchOS-integration`.

The old unrelated full-stack scaffold remains in its remote branch history; it
was not mixed into this notebook research project.

## Validation

- Full regression run from the canonical checkout: **1,441 passed, 1 skipped**
  in 290.40 seconds. The skip is the unavailable 29-report local NC archive;
  the committed NC PDF fixtures still pass. Fourteen plotting warnings remain.
- Expanded root/notebook path checks, including PA and CO: **42 passed**.
- Active offline notebook check: all nine vehicle notebooks and six gaming
  walkthroughs passed. Gaming 90–93 report **BLOCKED** because the exact original
  and staging databases are absent; the command correctly exits nonzero.
- The recovered PA and CO notebooks separately passed all 26 code cells with
  network access, exports, and database writes blocked. Saved outputs were not regenerated.
- All **6,314** original data files matched their pre-integration SHA-256 values
  after accounting for the gaming folder move. All **20** recovered state sample
  files matched their review-worktree bytes.
- The vehicle Git tree matches checkpoint `5102b2b`. All five development/checkpoint
  branches are ancestors of the combined main branch. The approved MA source/config
  Git blobs and notebook approval values remain unchanged.
- Built the combined package and installed its editable wheel into the existing
  Python 3.11 environment without downloading or replacing dependencies. Both
  packages import from the canonical checkout; the shared launcher check passes.

Full database-backed FLUT validation remains blocked on archive recovery. No live
collection, research-database replacement, valuation change, or approval rebinding
was performed.
