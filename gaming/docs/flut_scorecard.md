# Current FanDuel scorecard

Open `notebooks/95_flut_quarterly_scorecard.ipynb` from the repository, gaming directory, or notebook directory. It reads the selected captured database and retained sources without changing them. The default is `data/staging/refresh_20260912T201854Z/gaming_current.sqlite`; changing the path requires a completed capture with its matching `run_manifest.json` and `validation.json`.

The notebook checks the database hash and independently checks the retained source bytes used by the scorecard. It displays source definitions, coverage, excluded observations, monthly inputs, source references, same-window quarterly comparisons and gross hold. These are diagnostic observations. They do not approve or change a valuation or company forecast.

## Supported evidence

| State/product | Exact native FanDuel identity | Separate native measures |
| --- | --- | --- |
| Massachusetts sportsbook | FanDuel | Handle, Accrual Win, Taxable Gaming Revenue |
| Michigan sportsbook | FanDuel (MotorCity Casino) | Total Handle, Gross Receipts, Adjusted Gross |
| Michigan casino | FanDuel (MotorCity Casino) | Gross Receipts, Adjusted Gross |

Michigan's older capture labels sportsbook rows `Adjusted Gross`, but the parser retained the workbook's Gross Receipts and Adjusted Gross columns separately. The scorecard labels these columns explicitly; it never infers gross revenue from adjusted revenue. Massachusetts uses accrual win. States and products are not summed into a national measure.

An official denominator must have the exact state, product, channel, frequency, native total identity and report-status contract. All operators must reconcile to that control within one cent, with no missing financial values. One common source version must cover the operator population and total. Equal copies retain their references; disagreeing values are excluded without choosing the latest capture. An unknown status is excluded even when its numbers add up.

## Calendar and economic rules

Choose `quarter` and `through_month` explicitly for a fixed review, or leave both as `None` to use the latest supported captured month. The output prints the actual window. Every expected quarter-to-date month must qualify in both years. A missing or excluded month prevents aggregate comparisons rather than shrinking the requested window. The table shows current observed months, matched months, expected months, and three months in the full quarter.

Market shares and gross hold use ratios of summed native amounts, never averages of monthly ratios. Zero and negative revenue remain visible. Growth is undefined for a zero or negative prior amount; market share is undefined for a nonpositive market denominator. Negative sportsbook revenue can produce negative gross hold, or a revenue share above 100% if other operators lose. Negative handles are excluded from handle/hold analysis.

On the September 12 capture, Q3 compares July 2026 with July 2025: **one month out of three**, with all eight native metric comparisons available. The full history has 456 eligible metric-months and two excluded metric-months: Massachusetts January 2025 handle has a $0.90 operator/control difference; Michigan August 2023 contains a negative operator handle. These exclusions do not affect July's comparison.

## Explicit exclusions and next work

- New York weeks remain in notebook 94 and are not mixed with calendar months.
- Ohio has two native FanDuel licensee rows in July 2026. Belterra Park has zero handle and negative revenue, while Hollywood Mahoning Valley carries active handle. The transition and adjustment aggregation need validation before inclusion.
- Kansas reports settled wagers and Net Revenues with whole-dollar rounding. It needs its own definition and reconciliation contract; its net proceeds cannot supply gross hold.
- Pennsylvania and New Jersey licensee totals require a supported brand mapping. The scorecard does not invent one.
- Other states, including Kentucky, are outside this initial validated contract. This does not imply that official data is absent. The coverage table and any separate recovery evidence remain visible.

[Notebook 96](../notebooks/96_flut_expectations_review.ipynb) now provides the explicit evidence-to-expectation worksheet. The [current analyst review](flut_current_quarter_review_20260912.md) identifies the assumptions these observations challenge. Native state revenue is not automatically Flutter reported US net revenue.

## Small public function surface

`build_monthly_scorecard(results)` creates long native-metric rows with eligibility reasons and source references. `build_quarterly_scorecard(monthly, quarter=..., through_month=...)` creates same-window rows keyed by state, product and metric. `sportsbook_hold(quarterly)` calculates the separately qualified gross/handle ratios. `scorecard_scope(results)` records all retained state/product/channel/frequency scope decisions.

Regression tests cover missing periods and operators, unknown controls, conflicting/equal revisions, source splicing, native identities, negative/zero economics, gross/adjusted separation and weighted same-window calculations. Notebook execution is also checked under the repository's offline read-only guard.
