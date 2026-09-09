# Carvana page-status study: September 8, 2026

Five live Chrome page checks established that the public vehicle pages exposed
different purchase states. None displayed an explicit Sold status. This study
does not establish sales accuracy, delivery timing, or unattended access reliability.

## Inventory collection decision

The command was previewed at September 8, 22:36 New York time (September 9 UTC).
September 8 was already registered, so no new live inventory cycle was started.
The existing baseline remains 674 Tesla Model 3 VINs across seven complete year
queries, collected 21:44:11-21:45:40 New York time. There is no second daily
snapshot, no daily disappearance cohort, and no daily sales count.

## Real page observations

The sample was the first two pending VINs and first three non-pending VINs in VIN
order from the baseline. No actionable daily-change queue existed yet. Five distinct
pages were read in ordinary Chrome, without interacting with purchase, hold,
notification, or account controls. Browser shopper context was not independently
matched to the API ZIP; no price or delivery comparisons are made.

| Listing ID | Earlier API pending | Page observation | Saved category |
| --- | --- | --- | --- |
| 4640427 | True | Purchase in progress | pending |
| 4681095 | True | Get Started and a delivery estimate | available |
| 4671091 | False | Pre-order now; Inspection in progress | unknown |
| 4460674 | False | Pre-order now; Inspection in progress | unknown |
| 4715778 | False | On Hold; another customer started purchasing | pending |

Checks occurred at 22:38:24-22:38:52 America/New_York. Each loaded page exposed the
expected VIN and matched the original listing URL. Record availability is later
than these physical observations; historical cutoffs exclude the records until
their actual availability time.

`available` describes an offered purchase on that page; the Get Started button was
not clicked. `unknown` on the two pre-order pages means ready-to-purchase status
was not established within the existing six-category vocabulary. Their pre-order
wording is retained; no new schema was added. Both are still observed listings.

One formerly pending API row offered purchase at the later check, and one formerly
non-pending row showed a hold. Time differences, source behavior and context are
possible explanations. Neither comparison proves a cancellation or a new order.
The hold page also showed mileage 76,511 versus 76,505 in the earlier API row;
the original observation was preserved.

All five pages included generic equipment wording containing "as originally sold."
It was not a vehicle-status label and did not become sold evidence. Checks were
read from the loaded UI, not classified from a keyword search or an initial loading
screen. No search-engine snippets were recorded as current observations.

## Evidence and reproduction

[observations.json](../data/experiments/carvana_status_validation/2026-09-08/observations.json)
retains manually transcribed excerpts from the live browser accessibility state,
with URL, VIN, status location, check time, selection and limitations. These are
excerpts, not complete HTML or screenshots. Adjacent `check_<listing_id>.json`
files were recorded using the existing `record_evidence(..., kind='check')`
workflow. The canonical history is
`data/analysis/carvana_daily/listing_checks.csv`. No candidate reviews were added.

Notebook 20 reads the same existing report functions and saved checks. It exposes
the VIN comparison, earlier API observation beside each page check, native wording,
source and note. It shows later page/inventory evidence when available, including
new listing IDs for the same VIN. Run All is offline and read-only.
The old intraday walkthrough and audits are preserved in notebook 21.

## Decision and next step

Establish consecutive collection and status coverage before choosing a sales rule.
The next useful operation is one unchanged-pilot cycle on September 9 at about
21:45 New York time, followed by first-disappearance and native-change page checks.
If that date has passed, use the actual date and preserve gaps; do not backfill.

From the repository root, preview and then deliberately collect:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --live
```

The eventual sales-rule decision needs a cohort of disappearing VINs, readable
status evidence including actual Sold labels if they appear, and subsequent days
showing persistence or reappearance. Follow both missing vehicles and controls.
This targeted five-page sample has no accuracy denominator or verified sale dates.
The three-day candidate rule is unchanged and estimated sales remain unavailable.
