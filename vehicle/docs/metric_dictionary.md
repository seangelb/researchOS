# Vehicle metric dictionary

All observations retain retailer, listing ID, VIN where present, query/ZIP, UTC capture
time and a retained-source reference. An observation interval is not an exact event time.

| Metric | Definition and required evidence | Units / limits |
|---|---|---|
| Observed inventory | Distinct retailer/listing IDs observed in a declared query plan and capture window | Listings. Show incomplete queries; never replace failure with zero. |
| Complete query count | Unique IDs reconcile to a stable displayed total, with valid identity/card checks and verified page ending | Scoped listings during a window. Does not certify national coverage or physical stock. |
| First observed listing | Present now with no earlier retained observation in the available history | First observed date is not original listing date. Left-censored at collection start. |
| Reappearing listing | Absent from a complete comparable run, later present, and observed in older retained history | Could be cancellation, relisting or earlier collection gaps. |
| Asking price / change | Native USD asking price and difference for matched listing identities | USD. Missing stays missing; not a transaction price. VIN conflicts block price changes. |
| Native availability | Schema availability and captured card labels, kept separately | `InStock`, `On hold`, `Purchase in progress`, `Pre-order now` are source text. Pending is not completed. |
| Delivery estimate | Captured native delivery wording plus capture time and actual ZIP | Do not infer a calendar date from relative text or physical location from ZIP. Shipping charges also depend on ZIP. |
| Not observed later | Present before, absent after a complete matched-query capture | Observed search disappearance, not a sale. Incomplete periods block change classification. |
| Inferred removal / possible sale | A future estimate requiring persistent absence, coverage checks and independently reviewed status rules | Not implemented. Cancellations, returns and query movement must be evaluated. |
| Vendor estimates | Values supplied by a named vendor under its own coverage and event rules | Preserve native values/formulas and unknown definitions; do not treat as ground truth. |
| Company-reported results | Company's formally reported retail units for its stated period and definition | Separate from scraped listings and vendor estimates; no current conversion model. |

VIN links observations of a vehicle without merging retailer listing histories. Counts
across ZIPs are unions of identified listings, not sums. A census assembled over many
hours is exposed to inventory changes within the window.

## What the vendor outputs would require

| Vendor output | What our observations can support | Missing research |
|---|---|---|
| Daily inventory | Complete query-plan enumeration plus deduplication | Verified universe, repeatability and capture-window drift |
| Listings | First observations and reappearances | Original listing time and vendor's new-listing convention |
| Orders | Native hold/purchase labels and observed transitions | Whether each native label is an order, cancellations and duplicate attempts |
| Sales | No confirmed transaction observation currently | Delivery/completion evidence, returns, marketplace scope and frozen out-of-sample validation |
| Price changes | Matched USD asking-price observations | Transaction prices, financing, KBB and profitability are deferred |

Compare vendors only over overlapping dates, populations and definitions. The
earlier supplied free workbook ended August 8. The newer `(1).xlsx` supplied with
the daily-inventory request has 40 populated current-year daily sales rows through
August 9, 2026; neither overlaps the September captures. See its exact hash and
cell references in [the daily inventory guide](daily_inventory.md).

## Public search response context (September 8)

`price.total` is the source's USD asking price (checked against paired visible cards).
`transportCost` is separate, native USD transport context. `isPurchasePending`,
`vehicleLockType`, `vehiclePurchaseType`, `vehicleInventoryType` and `isOnDemand` are
retained native values. Their codes are not automatically mapped to orders or sales.
Schema availability is missing for this source; it is not fabricated as `InStock`.

`LocationBasedPrefiltering` is an observed website request feature. It changes the
query population by ZIP. A feature-off search, a ZIP-filtered search, a vendor's
inventory measure and confirmed company retail stock are distinct populations.
Store the setting with each source and keep it fixed for matched comparisons.
The first 1,006-vehicle MVP used feature-off search; ZIP audits use feature-on search.

Page reports retain response status, response-byte hash, projection-file hash, row
counts and failures. Complete enumeration means unique identities match the reported
query total over its recorded interval; it is not proof of an atomic national census.


## Daily VIN diagnostics

The operating [daily inventory table](daily_inventory.md) counts unique retailer/VIN
identities. It reports a full `inventory_count` only on a complete, valid cycle;
`observed_vins` remains a count of actual observations on partial cycles. The
calendar includes missing dates without inventing zero inventory or sale events.

The daily workflow is documented in [daily_cycles.md](daily_cycles.md). A cycle's
target window is distinct from its actual capture window. Resumed request usage and
access-stop state persist. Only fresh, source-reconciled imported observations can
be assigned to that cycle's daily presence. Complete declared queries do not establish
the national retail population.

`relisted` links the same retailer/VIN across changed listing IDs; native listing
histories and references remain separate. `persistent_absence` is a threshold event
after consecutive complete observed days, emitted once per absence episode. Gaps
and incomplete cycles break the streak; absence counts on incomplete days are missing.
`pending_started` and `pending_cleared` refer to native flags, not confirmed orders
or cancellations. Missing native values remain unknown. Absence rows carry the
last presence's evidence, labeled `observed_in_cycle=False`.

Daily events retain `rule_version`, actual capture clocks and `available_at`, the
latest supporting evidence availability. Threshold evidence is not backdated as
information available on the first missing day. 2/3/7-day horizons are sensitivity
parameters, not certified sales definitions. `estimated_sales` remains missing.

## Quarterly research outputs

Sale candidates and reviewed outcomes are now available in notebook 20; see
[sales tracking](sales_tracking.md). `new_candidates` counts threshold detections,
not sales on that date. `reviewed_sales_with_known_date` counts only explicitly
selected analyst confirmations with evidenced sale dates, as known at the cutoff.
Unknown dates remain unassigned. Reappearances and evidence gaps flag follow-up;
they do not silently rewrite reviews. Zero reviewed confirmations is not zero
population sales. A calibrated `estimated_sales` series is still unavailable.

`quarter_coverage` is a calendar of the quarter through the cutoff's local date.
Complete means the declared cycle reconciled within its window, not that every
transaction that day was observed. A missing cycle remains missing. A current-date
cycle cannot observe events occurring after its capture window.

`estimated_retail_units` is unavailable until a sales conversion and relevant
population are validated. `scenario_units` is a separately labeled analyst assumption:
observed persistent-absence events times an explicit conversion, plus remaining
calendar days times an explicit assumed unit rate. It is not a national forecast.
Input identity, source, availability, quarter, scope and units must agree. A seven-day
input freshness limit and a 36-hour observation limit are visible research policies,
not empirically calibrated accuracy claims.

The revision bridge separates revised common dates, newly observed dates, calendar
roll, conversion changes and remaining-day assumptions in that order. Different
coverage blocks attribution. Asking prices do not produce revenue or earnings estimates.

Carvana's [Q2 2026 filing, Key Operating Metrics](https://www.sec.gov/Archives/edgar/data/1690820/000169082026000055/cvna-20260630.htm)
defines website units to include reserved/purchase-in-progress and some vehicles
before reconditioning. Retail units sold include marketplace partners and are net
of returns under the seven-day policy. [Results were published July 29, 2026](https://investors.carvana.com/news-releases/2026/07-29-2026-210525685).
That publication date alone is not an intraday benchmark availability timestamp.
These definitions do not supply a mapping for undocumented native inventory codes.
