# Nationwide coverage

Source research checked 2026-09-05 UTC. Counts and ranges below refer to `data/staging/gaming_nationwide.sqlite`.

19,796 retained observations cover 34 state/product pairs. Repeated source versions are observations, not extra months.

Collected means retained reports have parser checks and representative fixture validation. It does not certify every historical definition, operator, or month since launch. Partial means the stated range or usable metrics still have limitations.

The inventory documents all 102 state/product combinations, official URLs, dated checks, evidence, limitations, and next steps. No series found is a bounded source-research result, not a legal-market conclusion. Not offered applies only to the checked official scope.

- [Full source inventory](../config/state_gaming_source_inventory.csv)
- [Metric definitions](../config/state_metric_notes.csv)
- [Main notebook](../notebooks/90_consolidated_ggr.ipynb)

| State | Online sports betting | Online casino |
| --- | --- | --- |
| AK | No series found | No series found |
| AL | No series found | No series found |
| AR | No series found | No series found |
| AZ | Access blocked | No series found |
| CA | No series found | No series found |
| CO | Partial; 2021-07-01 to 2026-02-28; 55 monthly periods | No series found |
| CT | Collected; 2021-10-01 to 2026-07-31; 58 monthly periods | Collected; 2021-10-01 to 2026-07-31; 58 monthly periods |
| DC | Partial; 2024-07-01 to 2026-07-31; 25 monthly periods | No series found |
| DE | Combined only | Collected; 2013-11-01 to 2026-07-31; 153 monthly periods |
| FL | No series found | No series found |
| GA | Not offered | Not offered |
| HI | No series found | No series found |
| IA | Collected; 2019-08-01 to 2026-07-31; 84 monthly periods | No series found |
| ID | No series found | No series found |
| IL | Collected; 2020-03-01 to 2026-06-30; 76 monthly periods | No series found |
| IN | Collected; 2019-10-01 to 2026-07-31; 82 monthly periods | No series found |
| KS | Partial; 2023-01-01 to 2026-07-31; 43 monthly periods | No series found |
| KY | Partial; 2025-03-01 to 2025-04-30; 2 monthly periods | No series found |
| LA | Partial; 2022-01-01 to 2026-07-31; 53 monthly periods | No series found |
| MA | Collected; 2023-03-01 to 2026-07-31; 41 monthly periods | No series found |
| MD | Collected; 2022-11-01 to 2026-07-31; 45 monthly periods | No series found |
| ME | Collected; 2023-11-01 to 2026-07-31; 33 monthly periods | Reporting pending |
| MI | Collected; 2021-01-01 to 2026-07-31; 67 monthly periods | Collected; 2021-01-01 to 2026-07-31; 67 monthly periods |
| MN | Not offered | Not offered |
| MO | Collected; 2025-12-01 to 2026-07-31; 8 monthly periods | No series found |
| MS | Combined only | Not offered |
| MT | Combined only | No series found |
| NC | Collected; 2024-03-01 to 2026-07-31; 29 monthly periods | No series found |
| ND | Not offered | Not offered |
| NE | Not offered | No series found |
| NH | Collected; 2019-12-01 to 2026-07-31; 80 monthly periods | No series found |
| NJ | Partial; 2018-06-01 to 2026-07-31; 98 monthly periods | Partial; 2016-01-01 to 2026-07-31; 127 monthly periods |
| NM | No series found | No series found |
| NV | Partial; 2020-01-01 to 2026-07-31; 77 monthly periods | Combined only |
| NY | Collected; 2022-01-03 to 2026-08-23; 242 weekly periods | No series found |
| OH | Collected; 2023-01-01 to 2026-07-31; 43 monthly periods | No series found |
| OK | No series found | No series found |
| OR | Partial; 2025-07-01 to 2026-07-31; 13 monthly periods | No series found |
| PA | Collected; 2019-07-01 to 2026-07-31; 85 monthly periods | Collected; 2019-07-01 to 2026-07-31; 85 monthly periods |
| RI | Collected; 2019-09-01 to 2026-07-31; 83 monthly periods | Collected; 2024-03-01 to 2026-07-31; 29 monthly periods |
| SC | No series found | No series found |
| SD | Not offered | Not offered |
| TN | Collected; 2023-11-01 to 2026-07-31; 33 monthly periods | No series found |
| TX | Not offered | No series found |
| UT | No series found | No series found |
| VA | Combined only | No series found |
| VT | Collected; 2024-01-01 to 2026-07-31; 31 monthly periods | No series found |
| WA | Combined only | No series found |
| WI | Reporting pending | No series found |
| WV | Collected; 2020-07-01 to 2026-08-22; 326 weekly periods | Collected; 2020-07-12 to 2026-08-22; 325 weekly periods |
| WY | Partial; 2021-09-01 to 2026-07-31; 59 monthly periods | No series found |

## New collections and checks

Nine new state modules collect Colorado, Kansas, Kentucky, Maine, Nevada, Oregon, Rhode Island, Vermont, and Wyoming. Rhode Island includes both products. Maryland now follows older archive pages and accepts the accessible PDF replacement for February 2026.

Historical examples cover Kansas column/layout changes and scans; Nevada suppressed/image tables; Oregon monthly actuals versus fiscal totals; Rhode Island fiscal-year layouts; Vermont wrapped/broken-font reports; Wyoming statewide/operator and missing-tax layouts; Colorado richer summaries; and Maryland launch-period negative tax bases and PDF reporting.

Connecticut and Pennsylvania retained reports were replayed into staging with separate financial fields. All 98 retained NJ sports PDFs were replayed. The original database was not changed. See README for the mapping repairs and a later reviewed-import example.

## Gaps and reporting limits

- **AZ / online_sports_betting:** Landing returned 200, but June/May 2026 and December 2025 event-wagering PDF downloads returned HTTP 403. No PDF bytes available for a validated parser. Native adjusted columns must not be relabeled GGR. Next: Save an official report manually if accessible, then inspect and implement its mobile columns before staging. Keep TLS/access controls intact.
- **CO / online_sports_betting:** Archive is July 2021-February 2026 with July 2025 absent. Most files only report internet NSBP/tax; GGR/handle appear in four retained summaries. Regulator landing and direct current report downloads returned HTTP 403. No net-to-gross substitution. Next: Use notebook 90 to check the requested metric, missing periods, source versions and operator coverage before analysis.
- **DC / online_sports_betting:** Stored official history starts July 2024; earlier GambetDC operator history is not represented. Complete-market comparisons across the transition are not supported. Next: Use notebook 90 to check the requested metric, missing periods, source versions and operator coverage before analysis.
- **DE / online_sports_betting:** Official lottery sportsbook tables group casino licensees and retailers without an isolated online column. Accounting periods can end before calendar month-end; sales/net proceeds are not automatically calendar-month GGR. Next: Obtain separately labeled online activity and exact period dates before upsert.
- **KS / online_sports_betting:** Current detail archive starts January 2023; September-December 2022 detail not linked. Two 2025 image reports are transcribed by exact hash. Dashes remain missing; net carryover is separate from Net Revenues. Next: Use notebook 90 to check the requested metric, missing periods, source versions and operator coverage before analysis.
- **KY / online_sports_betting:** Only March-April 2025 statewide online figures transcribed from official June 2025 meeting packet, PDF pages 123-124. Tableau view returned UnexpectedError and CSV export HTTP 404. Other packet images/operator cells are not transcribed; no claim of complete history. Next: Use notebook 90 to check the requested metric, missing periods, source versions and operator coverage before analysis.
- **LA / online_sports_betting:** FY22 workbook final two rows print July/August 2021 where May/June 2022 are expected. Those conflicting dates are excluded in staging; no replacement months invented. January 2022 is a partial launch month. Next: Use notebook 90 to check the requested metric, missing periods, source versions and operator coverage before analysis.
- **ME / online_casino:** Official iGaming Revenue Distribution page lists tribal monthly reports as TBA; no operating numeric reports were posted at the check. Next: Check the TBA links after official reporting starts; do not fabricate pre-reporting zeros.
- **MS / online_sports_betting:** MGC FAQ limits sports betting to casinos. Regional sports reports combine on-premises activity; no separate mobile-only values identified. Next: Keep location-based activity separate from statewide online. Use separately identified mobile values only if an official report supplies them.
- **MT / online_sports_betting:** Lottery sportsbook is location based; aggregate Sports Bet reports do not isolate mobile wagers from terminals. Annual financial statements are not statewide remote-online GGR. Next: Keep location-based activity separate from statewide online. Use separately identified mobile values only if an official report supplies them.
- **NJ / online_sports_betting:** Sports handle is missing. The 98 retained sports PDFs were replayed into staging using the repaired parser; missing tax/taxable cells remain missing. Original database keeps its older fields. Native reporting names are not a proven complete brand denominator. Next: Use notebook 90 to check the requested metric, missing periods, source versions and operator coverage before analysis.
- **NJ / online_casino:** Sports handle is missing. The 98 retained sports PDFs were replayed into staging using the repaired parser; missing tax/taxable cells remain missing. Original database keeps its older fields. Native reporting names are not a proven complete brand denominator. Next: Use notebook 90 to check the requested metric, missing periods, source versions and operator coverage before analysis.
- **NV / online_sports_betting:** Mobile-only win starts January 2020 in this archive. April/May 2020 sports rows are visibly suppressed in the official PDFs. June/July 2025 are hash-bound visual transcriptions. Handle cannot be recovered from rounded hold. Next: Use notebook 90 to check the requested metric, missing periods, source versions and operator coverage before analysis.
- **NV / online_casino:** The published GRI reports card-game revenue without an online-only poker split. Interactive gaming is not a broad online slots/table-game series. No public online casino denominator is established. Next: Require a separately published interactive-game revenue figure; do not use the combined Card Games line.
- **OR / online_sports_betting:** Public commission archive is rolling; currently retained monthly statements start July 2025. Older meeting materials require a public-records request. Draft/approved copies retained; any financial disagreement remains a conflict. Non-GAAP; annual reports not disaggregated into months. Next: Use notebook 90 to check the requested metric, missing periods, source versions and operator coverage before analysis.
- **VA / online_sports_betting:** Lottery release archive and legislative report RD532 were inspected. July 2026 totals cover 11 mobile and 3 land-based sportsbooks together; no separate online AGR/tax table found. News pages load release bodies through JavaScript. Next: Find a regulator mobile-only breakdown; retain combined values outside online comparisons. Evidence: https://rga.lis.virginia.gov/Published/2026/RD532/PDF
- **WA / online_sports_betting:** WSGC requires mobile sports wagers to be geofenced to tribal casino premises. No separately published mobile-only revenue series identified. Next: Keep location-based activity separate from statewide online. Use separately identified mobile values only if an official report supplies them.
- **WI / online_sports_betting:** DOA FAQ describes tribal reservation/facility sports wagering; Act 247 enables further compact work. No statewide online operating series or confirmed launch report found. Do not infer a launch from passage of the Act. Next: Keep location-based activity separate from statewide online. Use separately identified mobile values only if an official report supplies them.
- **WY / online_sports_betting:** Historical statewide summaries become operator tables. April/May 2023 retained copies differ by one cent and match under cent rounding. December 2023 copies differ in tax availability: matching GGR/taxable stay visible and tax is cleared, labeled `conflicting_sources`. Original net proceeds and later taxable revenue stay separate. Next: Use notebook 90 to check the requested metric, missing periods, source versions and operator coverage before analysis.
- **AR / online_sports_betting:** Checked DFA Casino Gaming Section, FAQ, tax descriptions and site report search. Public tax collections are combined casino receipts; no downloadable mobile activity archive found. Mobile reports are known to be reported to DFA; absence from these pages is not proof they do not exist. Next: Obtain the DFA monthly mobile activity report or its public archive URL; do not treat aggregate casino taxes as online GGR.
- **FL / online_sports_betting:** FGCC statistics cover licensed slots, cardrooms and pari-mutuel handle. Annual reporting includes aggregate tribal revenue-share receipts, not an isolated Seminole online sports GGR series. Next: Use an official tribal sports breakdown if published; do not infer sports GGR from aggregate compact payments.

Monthly gaps inside retained ranges:
- CO / online_sports_betting: 2025-07
- LA / online_sports_betting: 2022-05, 2022-06
- NV / online_sports_betting: 2020-04, 2020-05

Historical boundaries are also gaps: early NJ casino, pre-transition DC, Kansas 2022, older Oregon, pre-current-regime Tennessee, and any period before the table begins are not certified by this work. No weekly reports are allocated into calendar months.

Collection CSVs record download attempts at the time they ran. Some initial PDF failures were later resolved by parser repairs or checked transcriptions. This report and the final staging coverage counts describe the resulting data, not just those initial attempts.

## Rules for analysis

Select one metric; missing gross never falls back to adjusted, taxable, or net revenue. Tax payments and contractual state shares need separate interpretation. Read timing, promotions, carryforward, channel, and effective-date notes before growth or hold calculations. Unknown definition dates are deliberately left unknown.

Use published statewide totals once. Operator sums remain labeled as unverified coverage; a reporting licensee is not necessarily a brand. No cross-state aggregate, automatic hold, or market-share denominator is constructed. Notebook growth calculations require a specific manual review of the selected periods.

Retained source conflicts remain visible. Wyoming April/May 2023 retained copies differ by one cent and match under cent rounding. December 2023 keeps matching GGR/taxable and clears tax where one copy omits it, still labeled `conflicting_sources`.
