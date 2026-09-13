# August release check - September 13, 2026

**August 2026 remains unavailable in the checked official MA/MI sources. July remains the common review endpoint.** The three downloaded numerical reports match the selected snapshot's source hashes. This check adds no August operating results or evidence that company share losses narrowed.

Sources were captured **September 13, 2026, 23:38:47-23:38:49 UTC** (**19:38:47-19:38:49 EDT**). All seven requests returned HTTP 200 with usable content. The [evidence receipt](release_readiness_sources_20260913.json) records source URLs, retained paths, capture clocks, response metadata and hashes.

| Official source checked | Observed content | August decision |
| --- | --- | --- |
| MA [revenue page](https://massgaming.com/regulations/revenue/) and [archive](https://massgaming.com/regulations/revenue/revenue-report-archives/) | Landing-page sportsbook links and the 2026 archive stop at July. No August 2026 report link was found. The [latest consolidated PDF](https://massgaming.com/wp-content/uploads/MGC-Revenue-Report-July-2026.pdf) is headed July 2026, parses seven online operators and matches the selected source bytes. | Unavailable in checked sources |
| MI [revenue page](https://www.michigan.gov/mgcb/detroit-casinos/resources/revenues-and-wagering-tax-information), 2026 Internet Sports Betting workbook | Current-year sheet parses January-July, 112 rows. August is Excel row 14 and has no numeric entries. | Unavailable in checked sources |
| Same MI page, 2026 Internet Gaming workbook | Current-year sheet parses January-July, 112 rows. August is Excel row 14 and has no numeric entries. | Unavailable in checked sources |
| MI [news page](https://www.michigan.gov/mgcb/news) | Retained as a discovery surface. Workbook content establishes numerical readiness. | No substitute for August tables |

The embedded **2025** August rows are populated in both workbooks and do not establish 2026 coverage. Both workbooks are byte-for-byte identical to their selected database sources. Massachusetts landing-page HTML changed since the previous check, but its latest listed month and retained numerical PDF did not.

This is a successful retrieval with target-month data absent in the inspected sources. It is not a retrieval failure, proof of absence everywhere or a forecast of the release date. No operating refresh was run and no partially advanced endpoint was selected.

## Next complete-month review

The [September 12 readiness plan](release_readiness_20260912.md) still applies. Once MA online sportsbook and both MI online products have complete August tables, validate all required company identities and market controls before selecting a new capture. Compare June-August 2026 with June-August 2025, then recompute May-July on that same capture to assess whether share losses narrowed. Demand acceleration uses the separate March-May current/prior-year windows. Missing months cannot be shortened or zero-filled, and conflicting revisions require source adjudication.

The [saved July fundamentals review](monthly_fundamentals_20260912.md) remains current for the selected operating endpoint. Its issuer and legal context remains explicitly dated September 12; this availability check makes no new company earnings or current-law claim.

## Preservation and review

All **1,226 pre-existing gaming data files** and all seven gaming configuration files were hash-checked and preserved. Independent read-only review verified the hashes, source discovery and workbook XML: each 2026 August row contains its month label but no numeric values or formulas. All 33 affected parser and industry tests passed.

The selected database remains `data/staging/refresh_20260912T201854Z/gaming_current.sqlite`, SHA256 `751e135c083f66d025ca4d03116021215ac136518ba24b2093f135af044df3ee`, with `through_month` set to `2026-07`. Approval hashes were unchanged. The January 2025 MA $0.90 discrepancy and missing approved archives for notebooks 91-93 remain blocked.

New evidence is retained under `data/raw/RELEASE_CHECK/2026-09-13/run_20260913T233800Z/`; inspection and preservation records are under `data/staging/release_readiness_20260913T233800Z/`. Paths are relative to `gaming/`. These ignored captures require the separate data backup; committing this report alone does not preserve them.
