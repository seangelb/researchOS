# Read the category gaps before expanding collection

Notebook 20 now includes a frozen, offline review of the September 8 facet
projections against the old 951-query candidate and the fixed 101-query panel.
Review the category ledger before the geographic comparisons. The panel and
candidate are query specifications; neither table claims those queries ran today.

The [review manifest](../config/carvana_partition_review_20260917.json) binds both
plans and all 31 source projections by SHA-256. Its publication clock is September
17 local time (September 18 UTC). The old projections have observation clocks but
no retained source-availability clocks. These are retrospective design diagnostics,
available only after this review was published; they cannot be backdated into
inventory history or forecast inputs. Earlier Notebook 20 cutoffs withhold them.

## Follow the three tables

1. **`partition_sources`: one row per source response.** The broad response's
   40 make counts sum to its reported 80,576. In each of 30 filtered make/year
   responses, the requested make's parent-model counts sum to that response's
   total. Other makes in a filtered response are disjunctive facets and are not
   its children. Native category labels overlap and are not inventory partitions.
2. **`partition_parents`: one row per make/year.** The old candidate spans the
   40 × 18 grid implied by native year filter metadata, 2010–2027. Only 30 of its
   720 parent cells have a retained parent facet; 690 parent counts remain unknown.
   `panel_omitted_models` names known native model families outside the panel;
   `panel_unmeasured_models` names selected families without retained count evidence.
   The candidate uses 702 whole make/year queries and 249 model children replacing
   18 other parents. Those query counts total 951 without adding parents to children.
3. **`partition_queries`: one row per declared query.** Each panel query retains
   its exact candidate match or broader parent relationship and the count source,
   observation clock and review clock. There are 73 exact matches and 28 model
   subsets of a broader candidate parent. Those 28 are not missing categories.

Counts and source paths are visible beside the calculation. For example, the
September 8 Ram/2024 response reported 271 matches; the panel's three selected
model-family buckets sum to 266, leaving a **signed count residual of 5** for that
response; the omitted native family is Ram 3500. Every one of the 30 parent cells selected by the panel has a positive
count residual. This establishes exclusions in the old panel specification, not
the number of vehicles missing today. The sources were captured at different times.

The ledger withholds aggregate counts and page floors when a plan contains both
a parent and its children, repeats a child, references an unmeasured model, or
the native model-ID memberships overlap. Signed count residuals remain visible,
including negative values. Unknown counts remain missing. Repeated broad make
counts must not be summed across years. Page floors use 24 records per page and
at least one request per declared query; unknown parent counts prevent a complete
request estimate.

## What still needs collection and review

Zero arithmetic residual is not a VIN-membership reconciliation. Year min/max are
filter metadata, not year-bucket counts proving exhaustive population boundaries.
Missing/out-of-range categories, retail ownership semantics and membership residuals
remain unmeasured. The ledger deliberately provides no national denominator or
full-inventory budget.

The next live proposal must specify fresh broad and parent facet requests, a dated
destination, clocks and a shared request/time budget. Use those counts to choose
partitions, then test actual parent/child VIN unions and new categories in held-out
ZIPs. Preserve the frozen panel for comparable history. The present review neither
authorizes that collection nor changes the 600-request daily cap or scheduled trial.

Notebook Run All is offline/read-only. The existing explicit Notebook 20 exporter
includes these three tables and binds their sources, code and outputs. A new export
is a new review vintage; old exports and source files are retained unchanged.
