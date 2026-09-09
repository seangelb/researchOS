# Carvana sale candidates and reviewed outcomes

The daily workflow has one real September 8 baseline: 674 VINs and 215 native
pending flags across seven complete Tesla Model 3 year queries. Daily sales remain
unknown. The older one-morning comparisons are separate from this daily history.

Listing checks and candidate reviews can now be saved with explicit commands.
Start with [the recording guide](listing_checks.md) for the two JSON templates,
CSV histories, queue priorities and refresh command. Notebook 20 and the daily
command read the same inputs and select versions at the same cutoff.

## What exists and what was missing

| Stage | Existing capability | Current limitation |
| --- | --- | --- |
| Collection | Public search, explicit partitions, retained JSON, SQLite and checkpoints | Broad daily completeness/reliability unproven |
| Daily history | Fixed scope/window, fresh-cycle selection, read-only import analysis | Needs real consecutive daily collections |
| VIN events | Pending changes, relisting, absence thresholds and reappearance | Events do not establish sales |
| Candidate review (added) | One candidate per threshold-qualified absence episode, follow-up state and explicit analyst outcome | Independent outcome evidence and dated reviews must be supplied |
| Notebook 30 | Coverage calendar and explicit assumption scenarios | No calibrated conversion, national population or sales forecast |

The candidate calculation is `src/vehicle_tracker/sales.py`; saved input validation,
version selection and queue construction are in `checks.py`. Use them through notebook
20 or the explicit daily command. It is pure pandas analysis with no network or database writes.
Existing observation/event definitions and notebook 30 estimates are unchanged.

## How a candidate becomes a reviewed record

1. A VIN is actually observed in the chosen population.
2. It is absent for the chosen number of consecutive complete days (default three).
3. One candidate is created on the threshold detection date, with a stable ID,
   last observation/source references, native pending flag and timing uncertainty.
4. Later presence marks it reappeared; changed listing IDs still match by retailer/VIN.
   Coverage gaps leave follow-up uncertain. No repeat candidate is created until
   actual presence followed by another qualifying absence episode.
5. An analyst may explicitly select an evidence-backed outcome. Reappearance does
   not automatically erase an earlier label or prove a return/cancellation.

The rule is an uncalibrated review screen. It may miss sales, including vehicles
that appear and disappear between captures. Reviewed candidate outcomes cannot
alone measure total sales or the screen's recall. Compare sampled retained listings
against independent outcomes as well as investigating candidates. Historical daily
timing cannot be validated merely by matching a quarterly aggregate.

## Review format in notebook 20

The normal workflow records a JSON review with `--record-review` and loads its
saved history automatically. For a deliberate in-memory override, set
`SALES_REVIEWS_OVERRIDE` before running the notebook:

```python
SALES_REVIEWS_OVERRIDE = [{
    "candidate_id": "<copy the exact ID from the candidate table>",
    "outcome": "unresolved",  # confirmed_sale, not_sale, or unresolved
    "sale_date": None,        # YYYY-MM-DD only when supported by sale-date evidence
    "reviewer": "<analyst name>",
    "source": "<retained evidence path or source reference>",
    "available_at": "<timezone-aware timestamp when this review became available>",
    "note": "<what the evidence establishes and what remains unknown>"
}]
```

This is a format template, not a populated review. A pending flag, missing listing,
or passage of three days alone does not support `confirmed_sale`. The function
validates required fields, IDs, dates and availability, but does not independently
authenticate a review's source. The shared loader selects the latest version available at `AS_OF` per candidate;
the calculation still receives one selected version. Conflicting versions with the
same candidate/availability timestamp are rejected. The CSV history retains earlier
versions, and old exports remain unchanged. Nothing is written back to inventory SQLite.

`AS_OF` excludes future observations and future reviews. The candidate ID includes
scope, retailer/VIN, last observed cycle, threshold and rule version, so changing
the rule does not silently carry forward an approval. The full candidate table is
available as `sale_candidate_rows`, including source keys and review provenance.

## Read the daily table correctly

| Column | Interpretation |
| --- | --- |
| `new_candidates` | Candidates first detected on this date; unknown on missing/incomplete days |
| `candidate_reappearances` | Known candidate VINs observed again on this date; partial days may miss other returns |
| `reviewed_sales_with_known_date` | Count of explicitly selected confirmed reviews by their evidenced sale date, known as of the selected cutoff |
| `estimated_sales` | Missing; no automatic sales conversion |

Zero reviewed confirmations means no selected dated confirmations, not zero sales.
Confirmed reviews without sale dates stay in the ledger but are not assigned to a
day. Dates outside the displayed collection calendar also stay in the ledger.
A later reappearance/gap sets `review_needs_followup` for a confirmed label; it
does not silently modify the analyst outcome. The daily reviewed count includes
those labels until the analyst explicitly selects a revised review. Inspect this
flag before interpreting reviewed counts.

## Daily collection now has one command

The fixed seven-query pilot is connected to an explicit date register, dedicated
SQLite history, daily tables and notebook 20. See [the operating guide](daily_inventory.md)
for preview, collection, import-only recovery, definitions and next steps.

From the repository root, run once per day at approximately the same time:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --live
```

Omit `--live` to preview without requests or writes. Every chosen query must pass
coverage checks before that day's inventory can support absence comparisons.
The first real day is only a baseline. Build 7-10 consecutive days, investigate
missing/changed listings with retained evidence, then test broader coverage under
a separate population. The candidate screen remains uncalibrated.
