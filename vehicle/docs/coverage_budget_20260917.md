# Coverage budget review, September 17

This is an offline design review, not authorization for another capture. Existing
daily limits, dates, original failures and the frozen status study remain unchanged.
The September 17 connection failure is not retried or cleared by this review.

## Measured costs and conditional alternatives

| Scope | Requests | Minimum spacing time | Interpretation |
| --- | ---: | ---: | --- |
| September 14 complete 101-query panel, ZIP 08542 | 457 | 22.8 minutes | Measured request count; actual collection was about 23 minutes |
| Same panel plus the four pilot cohorts in seven other ZIPs | 534 | 26.7 minutes | Conditional estimate using retained page counts; no repeats included |
| Whole 101-query panel repeated in eight ZIPs | 3,656 | 182.8 minutes | Conditional replication cost; exceeds the 600-request cap |
| September 8 candidate plan, one page for each of 951 queries | 951 | 47.5 minutes | Minimum just to visit every query; not complete enumeration |
| September 8 broad native count at 24 records per page | 3,358 | 167.9 minutes | Conditional payload floor for 80,576 old native matches |

Spacing time is `(requests - 1) * 3 seconds`, excluding the final response and
processing. It is a floor, not a runtime promise. Counts can change, partitions
add partially filled pages, repeats cost requests, and failed attempts still count.
The 534-request example leaves only 66 requests below the current ceiling; it is
not an adopted schedule or a guarantee that a fresh run fits.

The 80,576 count came from a retained September 8 response in ZIP 08542. Its native
pagination reported 3,358 pages at 24 records per page. Neither the age of that
response nor its inventory-category semantics permits treating it as current
national retail inventory. The exact budget for full current inventory remains
unknown. Even the old one-context payload floor is much larger than the present
daily ceiling; slicing it over days would create temporal gaps, not a daily census.

## What the ZIP pilot supports

The September 13 local-date pilot completed 72 queries in 198 requests. Its four
cohorts cost 11 pages per ZIP/pass. All 96 ZIP, anchor and repeated-pass comparisons
had identical VIN membership; both held-out ZIPs added zero VINs. Asking prices
matched, while delivery charges varied for 194 of 201 VINs. Missing delivery costs
remain unknown.

For further design, retain 08542 as the primary collection context and use smaller
geographic checks to test the assumption that other contexts add little membership.
This is a provisional economical choice for the tested population, not permission
to omit geographic validation from new categories. Expand ZIP discovery when a
completed comparable query or unmatched reference listing demonstrates a gap.
The four cohorts were a feasibility sample, not a representative geographic study.

## Partition coverage still needs measurement

The old 951-query candidate is not simply an expanded list of the current panel:
73 of the panel's queries have exact filter matches, and 28 are model subsets of
broader make/year parents. All 101 have an identifiable relationship, but that
does not establish complete child coverage or numerical parent/child parity.
Do not count those 28 mismatches as missing categories or sum overlapping parent
and child counts as inventory.

The next coverage experiment should refresh source-supported facet counts before
enumeration, retain each parent/child relationship and numerical residual, and
separately display unknown/missing/out-of-range native categories. Reconcile native
inventory categories with the intended publicly advertised retail population.
Test new category partitions in the primary and held-out contexts before scaling.
Use only parameters demonstrated by retained requests; no invented sort or cursor.

Only then select a versioned discovery plan and budget, including validation and
failure allowance. Keep the frozen panel for comparable history. Vehicles first
found by wider coverage are newly observed, not necessarily newly listed. A larger
budget, new destination or collection schedule needs its own explicit authorization.
One complete panel date is not a reliable full-inventory baseline; the seven-date
trial retains missing and partial dates in its denominator.

## Evidence

The calculation checked the retained query artifact hashes for the complete
September 14 daily panel and September 13 geographic pilot, matched request sums
to parent reports, and verified the September 8 facet projection's content hash.
Inputs and arithmetic are bound in
`C:\Users\Sean\Documents\ChatGPT\ResearchOS\carvana_coverage_budget_20260917\budget_evidence.json`.
The query relationships are listed in `panel_candidate_relationships.json` beside
it. No new network requests, database imports or evidence rewrites were made.
