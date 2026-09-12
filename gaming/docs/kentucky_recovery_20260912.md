# Kentucky online sports wagering recovery

The collector now supports six complete online tables for April-June 2025 and
April-June 2026: **57 operator and printed-total rows** from three official
meeting packets. The two original March-April 2025 statewide rows remain supported
under their original source hash. This is a bounded recovery, not a complete
Kentucky history; collection coverage remains `partial` even if every supported
packet downloads successfully.

The new figures are **AI-checked manual transcriptions, not analyst approved**.
They are in [kentucky_online_transcriptions.csv](../config/kentucky_online_transcriptions.csv),
with source URL and SHA-256, physical PDF page, printed page, native operator and
licensee, reported amount, and review status. Original images were inspected as
full pages and at higher resolution for ambiguous digits. A future report with
different bytes is rejected until its own source review is added.

## Sources and scope

All packets were followed from the [official board archive](https://khrc.ky.gov/new_docs.aspx?cat=31).
Physical page means the one-based page in the PDF, which differs from its printed
page label.

| Official packet | Periods transcribed | Physical pages | Printed pages | Online rows |
|---|---|---|---|---:|
| [August 19, 2025](https://khrc.ky.gov/Documents/August%202025%20Public%20Meeting%20Materials.pdf) | April-June 2025 | 145-147 | 153-155 | 27 |
| [June 9, 2026](https://khrc.ky.gov/Documents/20260609%20Board%20Meeting%20Materials%20-%20Public.pdf) | April 2026 | 89 | 115 | 10 |
| [August 11, 2026](https://khrc.ky.gov/Documents/20260811%20Board%20Meeting%20Materials%20-%20Public.pdf) | May-June 2026 | 47-48 | 50-51 | 20 |

Each 2025 table has eight operators and one printed online total. Each 2026 table
has nine operators and one total. The collector retains both April 2025 source
versions; downstream analysis must examine duplicate source versions rather than
silently replacing the older capture.

As inspected on September 12, the official [gaming reports page](https://khrc.ky.gov/newstatic_info.aspx/utils/newstatic_Info.aspx?menuid=80&static_ID=722)
still links a Tableau view that returns an Unexpected Error page; its direct CSV
export returns HTTP 404. The latest board packet linked under Meeting Materials
is August 11, with monthly sports tables ending June 2026. July and August monthly
observations have not been established. These failures and gaps are not zeros.

## Financial meaning

The source separately reports Handle/Wagers, Winnings, Federal Excise Tax Paid,
Adjusted Gross Revenue (2025) or Approximate AGR (2026), and Kentucky Excise Tax.
The glossary describes AGR as settled wagers less winnings and federal excise.
Its wager measure includes wagers paid out and resolved for the reporting period.
Native labels and the two intermediate amounts remain in the transcription CSV;
normalized storage receives `handle`, `adjusted_revenue`, and `tax`.

**Gross revenue stays missing.** The collector does not relabel AGR or calculate
an unprinted GGR figure. It does not reconstruct tax from AGR: positive AGR and
zero tax coexist in the printed Circa rows, and negative AGR stays negative.
Retail, fiscal-year, and all-time tables are excluded.

The source explicitly pairs the online `Fanduel` label with `Turfway Park`.
Turfway Park's retail table instead shows Kambi. A licensee-only brand mapping
across channels would therefore misidentify the retail business.

## Validation

The values are printed in whole dollars. The parser applies two explicit bounds:

- Handle minus winnings minus federal excise minus AGR: at most **$2**, reflecting
  four independently rounded components. The largest observed difference is $1.
- Sum of n operators minus the independently rounded printed total: at most
  **$(n + 1)/2** for each of the five native money columns. The largest observed
  difference is $2, within the $4.50/$5.00 bounds for eight/nine operators.

All six tables pass both checks. No printed amount is adjusted to force a match.
The June 2026 online tax total is **$2,728,648**: a close render resolved an initial
low-resolution reading of its final digits, and the erroneous reading is covered
by a regression test.

Tests reject missing/duplicate competitors, incorrect source pages or periods,
foreign channels, unknown approval status, missing/nonfinite/fractional amounts,
broken accounting identities, inconsistent totals, and unknown source hashes.
The three retained full PDFs also passed actual hash lookup, parser selection,
and normalized storage validation offline, returning 27, 10, and 20 rows; the
original legacy PDF still returns its two rows.

## Analyst value

The recovered Q2 figures provide a useful historical comparison:

| FanDuel online measure | Q2 2025 | Q2 2026 |
|---|---:|---:|
| Handle | $193,999,138 | $205,513,759 |
| Handle share of printed Kentucky online total | 32.8359% | 30.1123% |
| Native adjusted revenue | $26,917,840 | $25,294,979 |

They support a question about market share and sportsbook outcomes. They do not
establish company net revenue, a current Q3 run rate, or historical point-in-time
availability. Scorecard integration requires an explicit Kentucky metric/source
contract and rounding treatment before these rows contribute to an aggregate.

## Retained review evidence

The three full PDF files, full-page and detailed renders, HTTP responses,
`ky_recovery_validation.json`, and the broader Colorado/Kentucky review are in:

`C:\Users\Sean\Documents\ChatGPT\ResearchOS\flut_goal_20260912\coverage_review`

This repair changes no existing SQLite database or raw capture. The parent
refresh workflow imports supported rows into its new dated snapshot and backs up
those source bytes. The shared legacy transcription file and state inventory
were preserved.
