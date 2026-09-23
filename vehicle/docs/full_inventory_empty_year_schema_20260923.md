# September 23 empty year-tail schema stop

The first live year-first full-inventory attempt on September 23, 2026 stopped after
two HTTP 200 responses. Broad discovery succeeded (~80,062 opening count). The
older-year tail (`year` max 2009) returned zero vehicles and one native page, with
`facetData.year.appliedMax=2009` and **no `facetData.makes` key**. Facet selection
raised `KeyError` on `makes`, recorded as `schema_failure`, and wrote
`access_stop.json`.

This matches the empty layout already established in
[gap recovery](gap_recovery_20260919.md). Year-first discovery now admits that
exact empty layout for year-only probes, retains `makes_present:false`, and
treats the year context as a validated empty total without inventing zero make
categories. Positive year pages still require make counts that sum to the total.
Make/model inventory probes keep the stricter default.

The failed attempt bytes were moved to
`vehicle/data/experiments/retained_schema_failures/carvana_full_inventory_years_20260923_empty_makes/`
so today's corrected attempt can use a fresh destination under the ordinary
capture root. The stop was a client schema bug, not an HTTP access failure.
