# Approved corrections — candidate copy

The user's “approve all” instruction authorized all three reviewed correction groups
in a separate candidate copy. The copy was created from the preserved staging database.

Candidate: `data/staging/gaming_candidate_20260907_approved_corrections.sqlite`

SHA-256: `492089beb85c1dd802669fea8f214ea7f1afb77265fd44f44c4ec2ca3d4e8d19`

Applied changes:

- **Delaware:** removed 152 malformed historical observation keys and inserted the
  152 corrected native casino/statewide keys, November 2013–December 2016.
- **Michigan:** changed 1,060 casino labels to Gross Receipts. All monetary columns,
  including adjusted revenue, remain unchanged.
- **New Jersey casino:** changed 324 unsupported tax zeros to SQL NULL. Original-only
  NJ sports missing-value corrections were already present in the staging base.

The candidate contains 19,837 observations. Every observation was compared against
the exact expected result, including all untouched columns and source versions.
SQLite integrity is `ok`; `source_coverage` and `gaming_results_legacy` were preserved.
All 38 corrected DE months select a printed statewide total; 76 handle/Net checks
reconcile within $0.01. Every MI monetary field matches the staging base.

## Open the candidate

In notebook 90, set:

```python
database_file = "data/staging/gaming_candidate_20260907_approved_corrections.sqlite"
```

For the corrected Delaware history, also select:

```python
states = ["DE"]
product = "online_casino"
metric = "net_proceeds"
start_date = "2013-11-01"
end_date = "2016-12-31"
frequency = "monthly"
```

Restart Kernel and Run All. Leave the existing growth-review choices unchanged until
you have reviewed any growth comparison you want to calculate.

The default and Delaware-history selections passed guarded offline execution against
the candidate. The ten existing notebooks also passed on their unchanged snapshots.
Notebooks 91–93 correctly block if pointed at the candidate because its database hash
differs from their approved binding. Correction approval has not been substituted for
a new exact notebook/source/database approval; no approval hash was edited.

Both existing databases, all raw files, existing notebook contents, and code/configuration
files were preserved. Everything remains local and uncommitted. See the companion
candidate JSON manifest for exact row counts and the source/candidate hashes.

Final offline verification: **327 tests passed in 95.55 seconds**, with nine Matplotlib warnings. All ten existing notebooks passed (65 code cells); two candidate notebook 90 selections passed, and all three candidate approval gates blocked as expected. `git diff --check` passed.
