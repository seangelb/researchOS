# FLUT current-quarter review — September 12, 2026

**Massachusetts wagering growth is healthy; Michigan shows FanDuel growing more slowly than its market.** Review market-share and sportsbook-outcome assumptions after another comparable month.

The new `refresh_20260912T201854Z` snapshot contains **19,806 observations**, 1,035 verified source files and 34 state/product series. Current code reproduces `current_scorecard.json`; only the supported MA/MI subset qualifies for this scorecard.

## July evidence: one of three Q3 months

Every comparison uses **July 2026 against July 2025: one of three Q3 months**. August and September are absent from this panel. Amounts are FanDuel native USD millions; shares use each metric's printed online statewide denominator.

| State/product | Native metric | July 2025 | July 2026 | Growth | FanDuel share: 2025 → 2026 |
|---|---|---:|---:|---:|---:|
| MA sportsbook | Handle | $121.57m | $150.59m | +23.9% | 25.16% → 25.65% |
| MA sportsbook | Accrual Win | $12.55m | $17.17m | +36.8% | 25.80% → 25.80% |
| MA sportsbook | Taxable Gaming Revenue | $12.25m | $16.80m | +37.1% | 25.82% → 25.81% |
| MI sportsbook | Total Handle | $90.42m | $107.02m | +18.4% | 31.98% → 30.13% |
| MI sportsbook | Gross Receipts | $14.12m | $14.93m | +5.8% | 41.43% → 34.44% |
| MI sportsbook | Adjusted Gross | $10.92m | $11.76m | +7.7% | 45.68% → 38.21% |
| MI casino | Gross Receipts | $63.06m | $74.58m | +18.3% | 25.17% → 24.63% |
| MI casino | Adjusted Gross | $59.27m | $71.59m | +20.8% | 25.20% → 24.63% |

MA gross revenue is **Accrual Win**; MI separately reports **Gross Receipts** and **Adjusted Gross**. Taxable, adjusted and gross amounts remain distinct, including beneath Michigan's older generic database label. [Notebook 95](../notebooks/95_flut_quarterly_scorecard.ipynb) shows native definitions, inputs, sources and reconciliation.

Three questions follow from these observations:

- **Is MA revenue growth being mistaken for a major competitive gain?** Handle share improved 0.49 percentage points, but Accrual Win share was flat. FanDuel gross hold rose from 10.32% to 11.40%; statewide Accrual Win grew 36.8%. Review any attribution principally to share gains.
- **Does MI support stable sportsbook share and gross hold?** Handle grew 18.4% against market growth of 25.6%; handle share fell 1.85 points. Gross hold fell from 15.61% to 13.95%, and gross-receipts share fell 6.99 points. Review competition separately from wagering outcomes; causes and company net margin remain unestablished.
- **Is MI casino growth being treated as a share gain?** FanDuel gross receipts grew 18.3%, versus 20.9% for the market; share declined 0.54 points. Absolute growth and a relative shortfall coexist. Profitability is not measured here.

## Kentucky adds Q2 context

The recovered online tables explicitly pair **Fanduel with Turfway Park**. Q2 handle increased from **$194.00m to $205.51m (+5.9%)**, while handle share declined from **32.84% to 30.11%**. Native adjusted revenue fell from **$26.92m to $25.29m (−6.0%)**. This supports a historical question about relative wagering growth and revenue conversion, not a conclusion about current Q3.

The 57 rows were independently checked against report images but remain **manual transcriptions without analyst approval**, outside the scorecard contract. Kentucky AGR deducts winnings and federal excise; gross revenue stays missing. [Recovery evidence](kentucky_recovery_20260912.md)

## Company reference and analyst judgment

The separate Q3 US management reference is **$1,480m**, calculated as **$7,400m FY US revenue guidance × approximately 20% Q3 phasing**. Applying that approximate factor to FY endpoints produces $1,425m–$1,535m; management did not separately disclose this calculated Q3 range. It is not a probability interval, an analyst scenario or an approved house forecast. [Flutter Q2 2026 release](https://flutter.com/media/g23an0ae/flutter-q2-2026-earnings-release.pdf)

The dated expectations record is `pending_actual`; no scenario or approval was created. State growth does not mechanically move the reference. An analyst scenario requires full-quarter US sportsbook handle, **net** revenue margin, iGaming and other revenue, with explicit geography, timing and promotion assumptions. [Notebook 96](../notebooks/96_flut_expectations_review.ipynb) preserves that separation and eventual evaluation.

## Next goals, in priority order

1. **Complete Q3 coverage.** Capture August and September reports into new dated snapshots, verify source bytes and test backup restoration. Preserve July's evidence vintage.
2. **Write an analyst scenario.** Decide whether MI share/hold changes challenge US assumptions; document support for extrapolation. Never assign MA/MI a guessed national weight.
3. **Expand decision-relevant coverage.** Validate Ohio's two FanDuel licensee rows, then Kentucky's AGR/rounding contract. Kansas needs a settled-wagers/Net Revenues contract. PA/NJ mappings and Colorado access remain evidence tasks.
4. **Evaluate after results.** Attach official Q3 actuals to the dated reference and any independent scenario. Keep their errors separate; assess signal value over multiple prospective quarters.
