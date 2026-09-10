# September 8 auditable-workflow results

The workflow remains Python, pandas, SQLite and Jupyter. No collector ran, no
dependencies were installed, and no existing evidence/database or gaming calculation
was changed. Work is local and uncommitted on `codex/notebook-reliability`, HEAD
`66493e2e5e3fa745b3c734f398c555e376606d9b`.

## Changes and audit path

See [the file/function map, reading order, worked VIN example and pilot commands](audit_guide.md).
Open vehicle notebooks **00 → 10 → 20 → 30**. The visible path is retained source,
parsed DataFrame, validation, read-only SQL selection, matched joins, event diagnostics
and quarter coverage. Synthetic teaching examples are separate from retained evidence.

Correctness repairs preserve known native-status changes when another field is
missing, accept valid ISO clocks with mixed second precision, and exclude later
cycle attempts before selecting as-of evidence. Readiness now exposes both directions
of VIN/listing ambiguity and distinguishes verified empty queries from unknown ones.
Failing regressions reproduced these cases before fixes. The event memory improvement
retains tuples rather than a second large dictionary per VIN/day; 72 varied histories
matched the previous implementation exactly, including DataFrame values, dtypes and counts.

No historical parser replay was performed. Existing imported source references and
original/current normalizer hashes remain distinct. A new reviewed analysis build
would be required to change stored historical normalization.

## Verification

The relevant initial vehicle baseline passed **218 tests in 17.56 seconds**. The
pre-review full suite passed **608 tests in 116.98 seconds**, with plotting warnings.
After reviewer repairs, the focused calculation/notebook suite passed **62 tests in
5.65 seconds**. The independent reviewer separately reran readiness regressions:
**5 passed, 18 deselected, 1.09 seconds**, and confirmed no outstanding actionable findings.

Final results after the reviewer repairs:

- **609 tests passed in 116.84 seconds**, with 15 plotting/backend/layout warnings
  and no failures, using `.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider`.
- **14 active notebooks passed** the guarded checker: ten gaming and four vehicle.
- `git diff --check` passed. Git printed existing CRLF-normalization notices.
  The implementation-only diff, including untracked vehicle files, also passed a
  trailing-whitespace check.
- All **2,970 starting files** were checked: **2,959 remained byte-identical**;
  the other 11 are the intended code/notebook/documentation changes. All existing
  raw files and databases, all gaming files/approval bindings, and every unrelated
  existing notebook cell remained unchanged. No unexpected new files appeared.
- **409 net additional executable lines**, excluding tests, Markdown explanations,
  comments, blank lines and docstrings. This includes 87 lines in the standalone
  slow benchmark. No abstraction was introduced to conceal the code budget.

The task snapshot, hash audit and implementation-only diff are saved under
`C:\Users\Sean\Documents\ChatGPT\ResearchOS\auditable_vehicle_20260908`.
The original preflight matched the supplied 2,970-file manifest and its expected
SHA-256 before any edit. Source/normalizer preservation was checked again after
the full offline suite.

The active notebook guard runs ten gaming notebooks and four vehicle notebooks. It
blocks network, CSV exports and writable SQLite connections. Tests exercise deliberately
invalid approval bindings as understandable blocked results; passing tests does not
renew approval. Gaming notebook/source/approval files match the starting snapshot.

Coverage includes interrupted recovery and persistent budgets, access/rate-limit/parser
failures, empty/missing/partial inputs, ZIP overlap, identity ambiguity, calendar gaps,
scope changes, native nulls, later reappearances and future attempts/inputs, quarter
boundaries, assumption versions, stale benchmarks, idempotent temporary imports and
actual notebook arithmetic. No normal test runs the large benchmark.

## Synthetic scale measurements

One local run of each final configuration; seconds are elapsed time. Peak memory is
the process lifetime peak working set through that phase, in MiB, not a phase allocation
or the whole machine's memory. Evidence generation and SQLite storage use temporary
directories, removed after the run. Cache/machine conditions can affect timings.

| Synthetic input | Evidence generation | Import | History read | Events + daily summary | Process peak |
|---|---:|---:|---:|---:|---:|
| 10,000 VINs × 1 day | 0.243487 s | 1.982339 s | 0.062582 s | 0.433621 s | 139.230469 MiB |
| 80,000 VINs × 1 day | 1.908346 s | 16.896893 s | 0.523081 s | 3.987480 s | 432.050781 MiB |
| 10,000 VINs × 30 days (300,000 observations) | 7.362268 s | 70.438638 s | 2.072168 s | 18.648574 s | 1009.125000 MiB |

Before the tuple change, 80,000 VINs × 1 day peaked at **572.574219 MiB**;
10,000 × 30 days peaked at **1517.503906 MiB**. The latter fell about 33.5%, with
equivalent event results. This establishes a useful local scale measurement, not
80,000 VINs × 30 days or unlimited history. The 30-day workload still approaches
1 GiB. Select bounded research histories; measure a larger panel before promising
its capacity. No new database platform or parallel collector was introduced.

## Retained readiness and live capacity

The 951-query candidate manifest has **158 complete query attempts, 3 blocked,
1 partial and 789 unattempted**, based on the explicit retained report selection.
There are no complete empty queries in that selection and no multiple attempt rows.
No completed selected query exceeds 20 estimated pages; unattempted partitions remain
unestimated. Changed totals/repeated identities are retained as blocked attempts.
The original 261-query trial and this 951-query candidate are different declared plans.

The retained facet at **2026-09-08 12:23:02.688996 UTC** reported **80,576** results.
At 24 per page that gives a **3,358-request lower bound**, about **2.8 hours** of
three-second pacing alone. It omits partition rounding, empty partitions, audit requests,
failures and within-window changes. Native make/year/model facet reconciliation is
visible in notebook 10; it cannot verify unknown/out-of-range categories or national
retail/marketplace population. It is historical planning evidence, not current inventory.

The documented September 9 preview passed: 951 queries, a new
`vehicle/data/experiments/carvana-audit-20260909` destination, 07:00–13:00 New York
window, 6,000-request and 21,600-second limits. **Only preview ran.** The separate
`--live` command is documented, with stop conditions and explicit same-window recovery.

## Research limitations and next evidence

Observed inventory, native pending changes, asking prices and source references are
usable scoped observations. Absence horizons are assumptions. Analyst scenarios are
explicitly exploratory. Actual estimated sales, national market coverage and any
unsupplied guidance/consensus comparison remain unavailable.

Next, run a separately authorized bounded full-plan pilot, inspect every failed or
unattempted query, reconcile category/population scope and repeat consistent daily
windows. Preserve vintages. Evaluate marketplace/return/relisting semantics and
between-scan losses, review individual events, and compare overlapping sourced vendor
and company results. Freeze a conversion policy before a later validation period not
used to tune it. A quarterly match alone cannot validate daily transaction timing.

There is no revenue or earnings forecast from asking prices, no convenience-sample
national extrapolation, and no claim of continuous real-time observation.
