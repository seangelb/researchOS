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

That corrected attempt also stopped after one HTTP 200 response, with zero rows
accepted. Empty pagination now replays successfully; a required field access
inside facet selection failed. The previous source contract omitted facet data,
so the exact missing field cannot be inferred from either retained response.
Both attempts and their stop records remain immutable and restore-verified.

The source contract now retains only the public make/model/year facet fields
before strict validation. Unknown fields are omitted, unsafe strings are redacted,
and missing values remain missing. Collection and replay select facets from those
same verified source bytes. A separately reviewed one-request diagnostic of the
original first query is needed to establish the actual empty-response layout;
this consumes one of the remaining recovery requests within the original deadline.
It does not resume the 500-query run or claim that an unverified empty result is
complete coverage. The overall recovery has no reliable finish time until this
live schema uncertainty is resolved.

The one-request diagnostic established the actual omission: the native year
metadata matched the requested older-year bound, but `facetData.makes` was absent.
It returned HTTP 200, zero vehicles and one native page. The original response
metadata is retained and replay reproduces the missing `makes` field; older
responses' omitted facets remain unknown.

The next recovery configuration, `carvana_gap_empty_context_20260919.json`, uses
the remaining **5,997 requests** and the same original deadline. Its explicit
gap-only parser option recognizes this empty layout, retains `makes_present:false`,
and leaves its make/model context unverified. Such a child accepts no vehicle
rows and does **not** count toward child or parent completeness. Independent
year groups may continue. Populated responses still require native make, model,
year and ZIP validation; unexpected schema or context failures still stop globally.
Other facet/discovery consumers retain their strict behavior. All 25 parents and
500 children, including unverified empty groups, remain in the denominator.

## Fourth attempt: a second empty layout stopped the run

That configuration ran at `16:59:47.199409 UTC` and charged **nine requests**
against the first parent only, `make_002_model_002` (Audi A5). Its lower-year
tail returned the known missing-`makes` empty layout and stayed unverified.
Exact years **2010 through 2016** completed with validated native contexts and
**21 retained VINs**. The run then stopped on `make_002_model_002_year_2017`.

That response was **HTTP 200**, 7,493 bytes, with zero inventory, one native
page and `appliedMin`/`appliedMax` both 2017. Its facets **did** contain makes:
Audi was `isApplied: true` with 61 vehicles and ten parent models. **A5 was
absent from that list.** Carvana omits a zero-count model from the applied
make's native children, so `validate_context` found no applied A5, raised, and
the shared invocation stopped with `schema_failure`. Its 491 remaining children
and all 24 other parents stayed unattempted. Zero inventory here is not an
observed A5 absence: the response cannot confirm that the requested model
filter was applied as sent.

This differs from the original 25 gaps. Those were `pagination_unstable` stops
on populated multi-page queries; this was a client contract gap on an empty
one. Year partitioning still has not re-enumerated any original gap.

The [retained capture](C:/Users/Sean/VscProjects/researchOS-gap-empty-context/vehicle/data/experiments/carvana_gap_empty_context/2026-09-19/catalog_report.json)
records `stopped=true`, `pending_request=false` and nine reconciled requests.
Its export reports 25 parents, 500 children and **zero recovery-complete
parents**. The September 19 deadline and that authorization have both expired;
any further recovery needs its own reviewed configuration and fresh destination.

## Reviewed failure dispositions

All four attempts remain immutable, each keeping its own `access_stop.json`.
Because they are genuine retained evidence rather than resolvable faults, the
full-inventory configuration now binds them as **reviewed peer failures** by
exact report, budget and stop hash. A reviewed disposition never deletes or
edits a stop marker; it records that the failure was inspected so an unrelated
fresh collection is not blocked forever by a closed investigation. Any changed
hash, added attempt, pending request, or access, transport, identity, storage
or pagination outcome in those roots blocks collection again.

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
