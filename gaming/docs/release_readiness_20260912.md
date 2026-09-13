# August release check — September 12, 2026

**August 2026 is not yet available in the checked official sources.** July remains the last common MA/MI review endpoint. The fresh July MA report and both current MI workbooks are byte-for-byte identical to the sources in the selected snapshot, so this check supplies no new operating results or evidence that company share has stabilized.

Reviewed September 12 in America/New_York. Source captures span **September 13, 2026, 00:08:47–00:09:20 UTC** (September 12, 20:08:47–20:09:20 EDT). This is a dated availability finding, not a claim that August is unpublished everywhere or a prediction of its release date. The [evidence receipt](release_readiness_sources_20260912.json) binds all seven retained sources to their capture clocks and hashes.

## What was checked

| Official evidence | Observed result | Decision for August |
| --- | --- | --- |
| MA [revenue page](https://massgaming.com/regulations/revenue/) and [archive](https://massgaming.com/regulations/revenue/revenue-report-archives/) | Latest listed consolidated report is July 2026; no August report found. The [July PDF](https://massgaming.com/wp-content/uploads/MGC-Revenue-Report-July-2026.pdf) confirms July and matches the retained source hash. | Unavailable in checked sources |
| MI [revenue page](https://www.michigan.gov/mgcb/detroit-casinos/resources/revenues-and-wagering-tax-information), current internet sportsbook and casino workbooks | Both 2026 sheets parse January–July only. Their August row 14 has no numeric entries; both files match the retained source hashes. Embedded **2025** August rows are populated and do not establish 2026 availability. | Unavailable in checked sources |
| MI [news page](https://www.michigan.gov/mgcb/news) | Retained as an additional discovery surface; the actual workbook content determines monthly numerical readiness. | No substitute for complete August tables |

Successful downloads, an annual workbook link or a successful collector run do not prove August coverage. Here, the retained landing/archive pages and direct inspection of the current-year workbook rows support the absence finding; it does not rely solely on a parser returning no August observations.

## Next test: are company share losses narrowing?

When August is complete, compare **June–August 2026 with June–August 2025**, separately for FLUT, DKNG and CZR in each covered state/product. Recompute the prior July read, **May–July 2026 versus May–July 2025**, on the same new retained capture. This measures a change in the monthly read, not what was historically knowable in July.

If reporting demand acceleration, compare the June–August YoY growth rate with **March–May 2026 versus March–May 2025**, the preceding non-overlapping window. This requires complete coverage in all four windows and is separate from the overlapping prior-read share test.

For each window, share is the sum of company amounts divided by the sum of the comparable market amounts. Show the August monthly share comparison, the latest three-month YoY share change in percentage points, and its change from the prior July endpoint. A less-negative YoY share change means **losses narrowed**; it is not a positive YoY share gain or proof of durable stabilization. A more-negative change means losses widened. Preserve differences between MA and MI and between sportsbook and casino; do not combine them into a company score.

Use MA online wagers settled and MI online handle for sportsbook demand/share. Keep MA Accrual Win, MA Taxable Gaming Revenue, MI Gross Receipts and MI Adjusted Gross separately labeled. Calculate sportsbook gross hold separately as gross revenue divided by handle; keep MI casino gross and adjusted comparisons separate. Higher hold or growing market volume does not establish stronger company earnings. Apply the existing reviewed native company/license identities, including the bounded CZR Michigan mapping.

## Decision on the next explicit check

| Outcome | Required evidence and action |
| --- | --- |
| **Ready** | August MA online sportsbook and both MI online products are published, retained and validated. Required company identities and full market controls reconcile in every June–August current/prior-year month and every May–July prior-read month, using comparable native metrics. Preserve any conflicting revisions for review. Only then select the reviewed capture and August endpoint in the existing workflow. |
| **Unavailable** | Official pages load successfully and retained source inspection still finds no August report or populated August current-year table. Keep July as the common review. If only some products have August, show their availability separately and leave the common August test pending. |
| **Check failed** | Retrieval is blocked/incomplete, discovery disagrees with visible links, a populated August table fails parsing, the report heading disagrees with its link, or required controls/identities/comparisons fail validation. Report the specific problem; do not describe published but unusable data as unavailable. |

Require complete requested windows; do not zero-fill missing months, shorten windows, silently mix July and August endpoints, or treat an unchanged file as a new release. The existing January 2025 MA discrepancy does not enter these target windows. Recheck the same official pages and actual current-year reports on the next explicit run; no release date or recurring schedule is assumed.

The [monthly workflow](industry_workflow.md) and [saved July-endpoint review](monthly_fundamentals_20260912.md) remain the working baseline. Database selection, notebooks and operating data are unchanged by this check.
