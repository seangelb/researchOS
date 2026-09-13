# Vehicle review checkpoint — September 13, 2026

Live work stopped when the user requested a clean commit and notebook review.
The review cutoff is **2026-09-13T23:39:33.757066+00:00** (19:39 EDT).

## Retained observation

The existing frozen 32-VIN study received one additional primary check through
connected Chrome: listing **4640427**, VIN **5YJ3E1EA0LF611240**, native
`saleStatus=Sold`, `purchaseType=NotPurchasable`, matched VIN/listing identity.
The physical observation was **2026-09-13T23:38:36.368Z**; local availability was
**2026-09-13T23:38:50.426861+00:00**. This is a website label, not confirmed
delivery, transaction price or a sale net of returns. HTTP status was not measured.

The [new browser batch](../data/experiments/carvana_detail_batches/20260913_primary_followup/plan.json)
has **1 matched and 11 unattempted visits**, with no unresolved reservation or
access block. Its saved expiry is **2026-09-14T00:23:02.100959+00:00**. No further
visits were started after the user's checkpoint instruction. Unattempted entries
remain unattempted; this is not a completed batch.

The [cutoff report](../data/experiments/research_cycle_20260913/study_checkpoint_report.txt)
now shows **16 matched checks, 16 unvisited VINs, 15 resolved binary endpoints**
and one native Unavailable endpoint that remains unresolved. Pending exits have
5 Sold and 1 Available among 10 selected VINs: **5/6 = 83.33%** among resolved
outcomes, conditional Wilson 95% interval **43.65–96.99%**, and **50–90%**
full-selected Sold bounds. Missing outcomes still limit interpretation.

The original primary deadline remains **September 14 at 08:08:46.075745 EDT**.
At the review cutoff, 11 starts remain available for 16 unvisited primary VINs,
so at least five primary endpoints cannot be checked under the retained limit.
The repeat window remains **September 19–21 at 08:08:46.075745 EDT**. Its
32 checks exceed the modeled 24-start capacity by eight. Neither window moved,
and the first qualifying endpoint rule and frozen membership remain unchanged.

## Daily panel remains prospective

The existing 101-query configuration was previewed only. **No API requests,
daily baseline, daily imports or seven-date validation were started.** The source
preview is `https://apik.carvana.io/merch/search/api/v2/search`, with the original
query order, ZIP 08542, location filtering disabled, and caps of 600 requests and
3,600 seconds. The retained [preview](../data/experiments/research_cycle_20260913/daily_preview.txt)
shows the configuration and proposed destination; it is not collected evidence.

The earlier September 13–19, 09:00 proposal is preserved as a historical plan.
A resumed operating schedule must be dated prospectively from its actual first
observation. There is no measured actual daily start or active operator time for
this panel. Browser active operator time was also unmeasured.

## Read the notebooks

Follow **00 → 10 → 11 → 20 → 24 → 30** using the
[operating guide](status_experiment.md). Restart Kernel and Run All remains
offline and read-only. Notebook 11's live function is defined but not invoked.

Notebook 24's saved cutoff is an earlier September 12 replay. To include this
checkpoint, set its ordinary `AS_OF` to
`'2026-09-13T23:39:33.757066+00:00'`, keep the existing `STUDY`, and optionally
set `EXAMPLE_VIN = '5YJ3E1EA0LF611240'` before restarting and running all cells.
This reads the new retained check without changing the study's endpoint rules.

Notebook 20 defaults to the original Tesla history. Its commented broader-panel
configuration still has no operating baseline. Use the explicit September 11/12
source-only pair and cutoff in the ordinary settings for the existing worked
inventory comparison. Notebook 30's empty actual forecast inputs intentionally
leave forecast accuracy unavailable. Notebooks 21, 22 and 23 remain references;
Notebook 22 does not automatically include this newer browser batch.

## Preservation

All **6,309 preexisting vehicle data files** were hashed before the operation;
the [preservation check](../data/experiments/research_cycle_20260913/preservation_check.json)
found **zero changed and zero missing files**. New evidence is confined to
`data/experiments/research_cycle_20260913/` and
`data/experiments/carvana_detail_batches/20260913_primary_followup/`.
No vehicle code or notebook settings changed for this checkpoint. Raw data is
ignored by Git and requires the repository's separate data backup.
