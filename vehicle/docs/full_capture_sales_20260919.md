# September 19 full capture and sales review

The user-directed full capture completed **all 101 queries, 10,085 VINs and 468
requests**, with no access or collection failures. The original process exited
normally. Observations span September 19, 2026, **11:49:55–12:13:30 UTC**
(7:49–8:13 a.m. New York). Native pending was true for **2,082 vehicles**.

The capture uses the fixed ten-make, model-year 2022–2024 population, ZIP 08542,
with location filtering disabled. It is a complete refresh of that tracked
population; national inventory completeness remains unverified. The user's
reported VPN disconnection preceded both the successful diagnostic and this
full capture. The exact server rule behind the earlier 403s remains unknown.

## What changed

Both endpoints completed the same 101 queries and passed source/SQLite parity.
The comparison starts with the September 14 local-date sweep, observed September
15 at 00:02:06–00:25:07 UTC. The elapsed interval is about 4.5 days, not an
exact five-day or one-day sales denominator.

| Observation | Count |
| --- | ---: |
| Previous tracked inventory | 9,645 |
| New VINs observed | 2,086 |
| Previously observed VINs now absent | 1,646 |
| Current tracked inventory | 10,085 |
| Net inventory change | +440 (+4.6%) |
| Shared VIN/listing identities | 7,999 |
| Absent VINs previously marked pending | 1,208 |
| Absent VINs previously not pending | 438 |
| Shared listings newly marked pending | 1,242 |
| Shared listings whose pending flag cleared | 211 |
| Shared listings with asking-price cuts | 2,960 |
| Shared listings with asking-price increases | 5 |

Inventory reconciles exactly: **9,645 − 1,646 + 2,086 = 10,085**. There were no
changed listing IDs or reused listing-ID conflicts in this comparison. Among
the 7,999 matched listings, 5,034 asking prices were unchanged. Price cuts affected
37.0% of matched listings; the median reduction was **$400**, or **1.64%** when
calculated as the median of individual percentage reductions. Asking prices
are not transaction prices.

## Can we identify likely sales?

**We can prioritize individual candidates; we cannot yet report a validated
sales total.** The 1,208 previously pending VINs now absent form an explicit
follow-up list. Absence can reflect other outcomes, and the interval does not
reveal the exact exit day or intermediate disappearance/reappearance. No sale
probability, transaction count or transaction price has been assigned.

The separate frozen Tesla study provides a useful early signal: among ten
selected pending exits, eight had a native **Sold** label, one was **Available**,
and one was unvisited. Across all 32 selected VINs, the retained primary checks
show ten Sold, sixteen Available, one Unavailable and five unvisited. The first
repeat window opened September 19 at 12:08:46 UTC; no repeat outcomes were
retained at this review's 12:14:30 UTC cutoff.

That small Tesla sample has not calibrated a probability for this ten-make
panel, which contains no Tesla queries. A native Sold label also does not
establish an economically completed transaction. The next analytical priority
is the feasible, already planned repeat checks, followed by validation within
the broader panel before estimating sales totals. Preserve the original 32-VIN
selection and all missing outcomes; this review performs no browser checks.

## Review the notebooks

1. **Notebook 20:** select the September 14 cycle and the new standalone September
   19 cycle explicitly, with `RETAINED_DATABASE = None` and
   `ANALYSIS_CUTOFF = '2026-09-19T12:14:30Z'`. Source replay handles their separate
   databases. Its strict daily-comparison gate should remain withheld across the
   date gap; the saved interval tables provide the separately labelled comparison.
2. **Notebook 24:** set `AS_OF = '2026-09-19T12:14:30Z'` and restart/run offline to
   see all 27 retained primary checks. Keep the frozen study and
   `EXTRA_CYCLE_PATHS = []`; this 101-query capture has a different scope from the
   seven-query Tesla study. Changing the cutoff does not collect new checks.
3. **Notebook 30:** keep actual sales estimates unavailable until the status
   evidence and conversion assumptions support them. This capture does not
   authorize a forecast or analyst-assumption change.

Notebook 20's explicit source paths, relative to its vehicle `ROOT`, are:

```python
RETAINED_CYCLES = [
    ROOT / 'data/experiments/mvp_completion_20260912/scale_acceptance_20260912/operating_validation/captures/2026-09-14/cycle.json',
    ROOT / 'data/experiments/full_capture_20260919/captures/2026-09-19/cycle.json',
]
RETAINED_DATABASE = None
ANALYSIS_CUTOFF = '2026-09-19T12:14:30Z'
```

## Evidence and verification

The new capture/config/database/export live under
`vehicle/data/experiments/full_capture_20260919`. The original failed September
19 attempt and all seven September 13–19 planned dates remain unchanged. This
user-authorized supplemental observation is not substituted into the original
trial denominator. It does not authorize another automatic collection.

The review bundle is
`C:/Users/Sean/Documents/ChatGPT/ResearchOS/carvana_full_capture_20260919`.
Its `interval_analysis` directory contains `summary.json`, `query_coverage.csv`,
`vin_interval.csv`, `same_listing_transitions.csv`, `likely_sale_followup.csv`,
and a source/output hash manifest. `analysis_supplement/by_make.csv` breaks out
inventory, pending flags, candidates and asking-price cuts by make.

Both selected notebooks passed with external setting copies and the existing
offline guards; their originals were unchanged. All **12,882 pre-existing
protected evidence/configuration files** retained their hashes. The complete
evidence backup restored **14,507 files** and passed all **785 SQLite checks**.
This is a verified same-device backup; off-device protection is not claimed.
Your saved outputs in notebooks 10, 11 and 23 are preserved separately from this
review's changes. Operator labor remains unmeasured.
