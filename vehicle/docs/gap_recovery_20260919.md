# Recover the September 19 query gaps

The user requested another collection of the 25 incomplete categories from the
[original full sweep](full_inventory_outcome_20260919.md). All 25 were attempted:
20 encountered repeated identities and five encountered changing native counts.
Their opening query counts sum to 30,235 vehicles; 13,368 earlier page rows were
accepted before those failures. These are category counts, not 25 missing pages.

The separate recovery divides each original category into exact model years
2010–2027, plus an older-year and a newer-year range. The audited original broad
response supplies those convenient endpoints; the open ranges preserve years
outside them. All **25 parents and 500 searches** remain in the coverage ledger,
including failures and unattempted searches. Missing/noninteger years remain an
explicit uncertainty.

The new runner verifies that native make, model and year filters match the request
before storing first-page observations. This also applies to empty results and
the Chevrolet whole-make category. Real endpoint support remains subject to this
check; synthetic tests cannot establish it. Pagination failures remain incomplete.
Access, transport, context, identity, storage and uncertain-request failures stop
the invocation without automatic retry.

The configuration authorizes one September 19 attempt in its own capture root,
with at most 6,000 requests and six hours, sequential starts at least three seconds
apart. It locks all declared related catalog roots and preserves their stop state.
A later date or an existing recovery attempt cannot become an automatic retry.
No original pages, failed records, budgets or completeness flags are rewritten.

The first recovery stopped after one HTTP 200 response and accepted zero rows.
Carvana returned an empty first page with zero inventory and one native page;
the client incorrectly required zero pages. The retained source, request ledger,
database and 500-child export reconcile, and the capture and stop record were
backed up and restored. Native applied filters were not reached, so this response
does not establish a validated zero for that category.

The parser correction accepts only empty first-page responses with zero inventory
and native page count zero or one. A separately reviewed fresh configuration,
`carvana_gap_recovery_empty_fix_20260919.json`, binds the exact independent
diagnosis and all 89 retained evidence/code/export hashes. It keeps the failed
root locked and its stop marker unchanged; any other failure or changed evidence
still blocks collection. It uses the remaining 5,999 requests and the original
22:13:10.360920 UTC deadline. It does not restart the failed destination, relax
native filter validation, or make an automatic access-failure retry.

## Preview and inspect

`vehicle/scripts/run_carvana_gap_recovery.py` previews the frozen plan without
requests or writes. Live execution requires its exact reviewed configuration
hash and a fresh destination after process, source and checkout checks. The plan
and configuration are `vehicle/config/carvana_gap_recovery_20260919_plan.json`
and `vehicle/config/carvana_gap_recovery_20260919.json`.

After the original recovery process terminates, its `analysis` directory contains
`parent_coverage.csv`, `child_coverage.csv`, `observations.csv`, a history database,
and a hash manifest. Begin with the parent table to see which of the 25 categories
have all 20 year contexts complete, then inspect the child ledger for gaps.
`--replay <capture> --output <fresh-directory>` independently reconciles retained
evidence without making requests or overwriting an export.

These are later recovery observations. Combining them with the morning sweep
does not create a simultaneous complete baseline, establish national coverage,
or identify sales. The separate full-run baseline, repeat run and reliability
trial remain requirements of the [broader goal](full_inventory_goal.md).
