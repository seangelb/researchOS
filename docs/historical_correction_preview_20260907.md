# Historical correction preview — September 7, 2026

**Review recommendation:** review a scoped Delaware identity/row-type replacement first.
It repairs double-counted historical monthly totals. Review Michigan's label-only
changes and New Jersey's missing-value cleanup separately. No candidate has been
approved or imported, and neither existing database was changed.

## What was compared

At HEAD `66493e2e5e3fa745b3c734f398c555e376606d9b`, branch
`codex/notebook-reliability`, the preview checked 271 unique retained report hashes
against both original and staging: 542 report/database comparisons and 7,369 stored
observations per database. All source references resolved inside `data/raw`, all
hashes matched, and all reports parsed after applying the existing NY collector's
operator-versus-statewide placeholder policy. No unresolved source-version conflicts
were found in this selected scope; synthetic conflicts and failures are tested.

The replay uses existing state parsers and normalizers. Each original snapshot is
compared separately; differences from older stored data are not automatically
attributed to the latest parser repairs. No downloads, live discovery, parser edits,
notebook edits, database writes, or approval changes occurred during this task.

## Results

| Scope | Original snapshot | Staging snapshot | Interpretation |
| --- | --- | --- | --- |
| DE casino, Nov 2013–Dec 2016 | 152 old keys removed; 152 corrected keys added | Same | Restore three native casinos and one printed statewide total per month; 38 monthly aggregates change |
| MI casino, Jan 2021–Jul 2026 | 1,060 label changes | Same | `Adjusted Gross` → `Gross Receipts`; every monetary field remains unchanged |
| NJ casino, Jan 2024–Jul 2026 | 324 tax zeros → unknown | Same | Missing brand-level tax is not reported zero; GGR unchanged |
| NJ sports, Jun 2018–Jul 2026 | 1,422 taxable-revenue zeros → unknown; 565 tax zeros → unknown (tax starts Jul 2019) | No changes | These older missing-value corrections already exist in staging |
| PA casino | 936 observations unchanged | Same | No retained partial-component aggregate change detected |
| NH sports | 80 observations unchanged | Same | No retained signed-amount change detected |
| NY sports | 2,288 observations unchanged | Same | Ten workbooks pass reconciliation and the corrected overlapping-week check |

Counts of changed metric cells are distinct from observation counts. Delaware's
identity corrections deliberately appear as removed/added keys, not an inferred
operator mapping. Both sides retain 7,369 observations overall; that equal count
does not mean the identities or financial meaning are unchanged.

### Delaware: supported historical correction

The old parser stored labels such as `January Amount Played` and `January Net` as
operators. It also stored the statewide triplet as an operator. The current parser
reads the physical header positions as `STATEWIDE`, `Delaware Park`, `Dover Downs`,
and `Harrington`, with the first row typed `official_statewide_total`.

For January 2016, the retained report prints Net of **$190,829.29 statewide**,
**$90,512.94 Delaware Park**, **$63,399.35 Dover Downs**, and **$36,917.00 Harrington**.
The old consolidation includes all four as operators and produces **$381,658.58**.
The candidate uses the printed **$190,829.29** total. This is a classification and
aggregation repair, not an economic decline.

Across all 38 affected months, 76 operator-to-statewide checks for handle and Net
agree within $0.01. The exact monthly bridge, source references, and retained table
excerpt are in `delaware_aggregation_impact.csv`, `delaware_reconciliation.csv`, and
`delaware_source_example.txt` in the artifact folder below. A later import must
remove the malformed old keys as well as add corrected keys; a simple upsert would
leave duplicate counting behind.

### New Jersey: missing-value cleanup, separate from recent sign repairs

The differences are stored zero-to-unknown changes; the replay found no changed NJ
GGR amounts or removed NJ identities from the sign/form-number repairs. The casino
skin parser does not supply brand-level tax. Representative April 2024 casino and
July 2019 sports reports produce identical parsed frames under HEAD and the current
NJ parser, confirming that these examples predate the recent parser edits.
See `nj_prior_behavior_examples.json`. This evidence supports treating the reported
tax/taxable differences as older storage/ingestion cleanup, not new sign corrections.

Review the retained evidence before approving this cleanup; do not derive tax by
applying an assumed rate or allocate licensee tax to brands. Unknown amounts should
remain unknown. No complete-market-share claim is added.

## Review artifacts and readable pandas inspection

All tables are in:

`data/staging/historical_preview_20260907/`

- `source_inventory.csv`: reporting ranges, stored/parsed row counts, source paths,
  URLs, verified hashes, parser status, and errors.
- `comparison.csv`: every selected observation/metric, including unchanged values.
- `differences.csv`: added, changed, removed, or blocked rows only.
- `candidate_observations.csv`: parsed rows with native names, retained references,
  and explicit `exploratory_unapproved` / `needs_analyst_review` labels. It contains
  only the replayed scope, not a complete replacement database.
- `summary.csv`: metric-change counts and reporting ranges.
- `manifest.json`: protected database hashes, code hashes, and execution scope.

No candidate database was created. CSV tables make removals and unknown values
reviewable without presenting a partially reviewed SQLite snapshot as ready to use.
Existing raw HTML landing pages, metadata, and unbound captures were inventoried
separately in `unbound_raw_files.json`; they were not assigned invented observation
identities or replayed as new reports. This preview does not certify archive coverage.

In a Jupyter scratch cell, with `ROOT` set to the repository:

```python
import pandas as pd

folder = ROOT / "data/staging/historical_preview_20260907"
differences = pd.read_csv(folder / "differences.csv", keep_default_na=False).replace("", pd.NA)
de = differences.query("database == 'staging' and state_code == 'DE'")
display(de[["operator", "row_type", "period_start", "metric", "existing_value",
            "candidate_value", "status", "reason", "source_file", "source_sha256"]])
```

`existing_value` and `candidate_value` mix monetary values and native-label text
because labels are audited too. Select a monetary metric before numeric conversion.
Monetary units are USD; missing differences are not zero. `status=blocked` would
prevent promotion even where `proposed_status` records a mechanical change.

To reproduce the core comparison with no network and no database writes, choose a
new output folder; the script refuses an existing destination:

```powershell
.\.venv\Scripts\python.exe -B scripts/preview_historical_corrections.py --output data/staging/historical_preview_next
```

## Downstream effect and approval boundaries

- **Notebook 90:** DE `net_proceeds` and handle history for the 38 affected months
  changes from an unverified operator sum to the printed statewide total. The
  default recent window starts in 2025 and is unaffected. NJ tax/taxable views
  gain missing values; NJ GGR remains unchanged.
- **Notebooks 91–93:** current calculations remain on the protected databases.
  MA inputs were outside replay scope. NY and current sportsbook numbers are
  unchanged; MI Gross Receipts and adjusted revenue remain distinct and numerically
  unchanged. No candidate result inherits approval from these notebooks.
- **Historical notebooks 40–43:** untouched; do not repin them or reuse their old
  aggregation assumptions to approve the candidate.
- Any later changed staging snapshot invalidates exact database approval bindings.
  A separate human review is required; test success does not renew approval.

## Verification and preservation

- Before implementation: **305 tests passed in 92.76 s**, nine Matplotlib warnings.
- Preview tests: **22 passed in 1.08 s**, using temporary storage and in-memory data.
- Full suite: **327 passed in 125.88 s**, nine Matplotlib backend/layout warnings.
- Guarded offline notebooks: **10 passed, 65 code cells** (00, 10, 11, 20, 30, 31,
  90, 91, 92, 93). Network, CSV exports, and writable SQLite connections are blocked.
- The actual MA gate's deliberately invalid binding test still reports an
  understandable blocked result. Conflict, missing-file, parser-failure, removed-key,
  null/zero/negative-value, and read-only integration cases pass.
- Every pre-existing notebook and code/configuration file is byte-for-byte
  unchanged, as are all **1,572 raw files** and both databases. Existing dirty work
  was preserved. `git diff --check` passes.

Protected database hashes:

- Original: `62afd2b97459f151e62fbbf24c7e0d0fe5a52e9dee829931d2554b9b1529d388`
- Staging: `023ca5e8e4c16ff0981a2783dabcedf9399939e0241701b0394bc0277eff6ce9`

All changes and artifacts remain local and uncommitted. The next human decision is
whether to authorize a **new candidate SQLite copy with the scoped DE replacement**,
followed by review of its exact totals and source/approval implications. Treat MI
label cleanup and NJ unknown-value cleanup as separately reviewable changes.
