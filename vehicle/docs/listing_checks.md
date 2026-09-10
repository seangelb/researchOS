# Save what you checked, then record what you concluded

Inventory observations, website checks, and analyst conclusions are three different
tables. The daily command and notebook 20 combine them at a chosen evidence cutoff.
They do not turn a disappearing VIN or a website label into a confirmed sale.

## Notebook entry without a JSON file

Notebook 20 sections 5 and 6 now provide selection, an editable `CHECK_DRAFT`, and
manual prepare/preview/save commands. Select all three identity fields from retained
inventory, inspect the original URL, and enter the actual wording and check time.
Use a console attached to the notebook kernel for the Markdown commands; leave
them out of executable notebook cells. Ordinary Run All remains read-only.

`prepare_check(observations, retailer=..., vin=..., listing_id=..., draft=...,
available_at=...)` is pure: no requests, clock reads, or writes. It validates the
draft against retained identity, rejects placeholders and synthetic source URIs,
and returns a content-bound `prepared-...` ID. Availability is supplied once during
preparation; it is not the time the page was physically checked.

`record_evidence(settings, prepared_check, kind='check')` accepts that dictionary
through the same locked validation/write path as the JSON command below. A changed
prepared record must be prepared and previewed again. Replaying the unchanged
record is idempotent. Saving updates the checks CSV and uses the existing local
`cycle.lock`; it does not alter inventory, register a cycle, or export tables.
Evidence references remain analyst-supplied: validation does not authenticate them.

The VIN timeline retains separate physical visits and selects the latest correction
to each visit at the cutoff. It shows gaps and derived absences separately from
actual inventory/page observations. The full histories retain older versions.
Alternative-cycle and synthetic notebook examples do not enable manual recording.

## 1. Check a listing and save the evidence

Start with `detail_followups` in notebook 20. Open its original listing URL manually
and retain the actual evidence, such as a screenshot or saved page. Checks may start
at the first disappearance; there is no need to wait for a three-day candidate.
An unchanged listing from the inventory table can also be checked as a control.

Create a UTF-8 JSON file such as `check.json`. This is a template: replace the
placeholders with real observed evidence. The program does not create this record
or visit the website for you.

```json
{
  "check_id": "<new unique ID for this saved record>",
  "retailer": "carvana",
  "vin": "<VIN from the observation table>",
  "listing_id": "<listing ID from that same observation>",
  "checked_at": "<timezone-aware time the page was actually checked>",
  "available_at": "<timezone-aware time this recorded result became available>",
  "observed_status": "unavailable",
  "native_text": "<exact relevant wording for this listing>",
  "source": "<retained evidence path or source reference>",
  "reviewer": "<your name>",
  "note": "<what was observed and any uncertainty>"
}
```

Use ISO timestamps such as `2026-09-09T21:50:00-04:00` or its UTC equivalent
`2026-09-10T01:50:00Z`. These example times are formatting examples, not observations.
`checked_at` is the physical evidence time; `available_at` is when the result became
available. Availability cannot precede the check or be in the future when recorded.
Do not backdate availability to make a later conclusion appear known earlier.

| Status | Meaning of the recorded check |
| --- | --- |
| `available` | The listing-specific evidence showed availability |
| `pending` | The evidence showed a hold/pending state |
| `sold_label` | The site explicitly said this listing was sold; no transaction date is inferred |
| `unavailable` | No longer available/removed, without explicit sold evidence |
| `access_blocked` | Access failed, including a challenge; vehicle status was not established |
| `unknown` | Evidence did not establish another listed state |

`not_checked` is only a report display value for no applicable saved check. It is
not accepted as an observed status. A 404 or challenge is never automatically sold.
For an access failure, `native_text` may be empty; explain the actual failure in
`note` and retain its source. Other website-state labels require native wording.

The [September 8 page study](status_validation_20260908.md) found two pre-order
pages among native non-pending inventory. The existing vocabulary has no pre-order
category: those checks use `unknown`, preserving "Pre-order now" and "Inspection
in progress" in `native_text` and explaining the limitation in `note`. Do not treat
a false API pending flag as proof that the vehicle is ready for purchase. Generic
equipment wording such as "as originally sold" is not a listing's Sold status.

From the repository root:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --record-check check.json
```

This validates the record against an already observed retailer/VIN/listing ID and
saves it in `vehicle/data/analysis/carvana_daily/listing_checks.csv`. It does not
collect inventory or rewrite the inventory database. Replaying the identical ID
and contents changes nothing; reusing that ID with different contents is rejected.

To correct a saved check, retain the old row and submit a new `check_id` with later
availability. If correcting the description of the same page check, keep its actual
`checked_at`. If checking the page again, use the new physical check time. For a
listing, the report chooses the newest physical check known by the cutoff, then
the latest available correction to that check time. Late entry of an older page
does not replace a newer physical check. Ambiguous conflicting records at the same
listing/check/availability time are rejected.

## 2. Record an analyst review when a candidate has supporting evidence

After three consecutive complete absent days, a candidate has a stable
`candidate_id`. Copy that ID from the full `sale_candidates` table. An unqualified
or unknown candidate cannot receive a saved review through this command.

Create `review.json`, replacing the placeholders:

```json
{
  "candidate_id": "<exact candidate ID>",
  "outcome": "unresolved",
  "sale_date": null,
  "reviewer": "<your name>",
  "source": "<evidence supporting this conclusion>",
  "available_at": "<timezone-aware time this review became available>",
  "note": "<what the evidence establishes and what remains uncertain>"
}
```

Allowed outcomes are `unresolved`, `not_sale`, and `confirmed_sale`. Pending,
absence, or a saved `sold_label` does not automatically set an outcome. A review is
an explicit analyst conclusion. Use `sale_date: null` unless evidence supports an
actual transaction date; only a confirmed outcome may have that date.

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --record-review review.json
```

The command saves to `vehicle/data/analysis/carvana_daily/candidate_reviews.csv`.
A correction uses the same candidate ID and a later `available_at`; it appends a
version and preserves the original. Identical replay is idempotent. Different
contents at the same candidate/availability timestamp are rejected. At a cutoff,
the report selects the latest available version per candidate and passes that one
row to the existing `sale_candidates` calculation.

Validation checks structure, identities and timing. It does not authenticate an
external document or judge whether your cited evidence proves a sale. Later
inventory reappearance/gaps preserve the selected analyst outcome and flag
`review_needs_followup`. To change the outcome, explicitly record a revised review.

## 3. View or export the updated report

Restart/run notebook 20 to read the saved inputs without writing. To save a new
versioned export from existing evidence:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/run_carvana_daily.py --refresh
```

The normal next-day `--live` command also incorporates saved inputs. Recording and
refresh are separate actions. Record operations share the daily register's lock;
they write complete CSV histories atomically so an interrupted write does not
publish half a row. Missing input files are empty tables, and viewing a notebook
does not create them. Use these commands rather than editing historical CSV rows.

New exports include `listing_checks.csv` (the latest applicable checks used),
`selected_reviews.csv` (the review versions used), the candidate table, and the
existing daily tables. The two canonical input CSVs retain all saved versions.
The manifest records input hashes or an explicit missing-input value, plus output
hashes. Old exports and their manifests remain unchanged. Current code/config or
growing input histories need not match an earlier export's recorded hashes.

## How the queue decides what comes next

1. A listing with actionable inventory history and no applicable check needs a
   **first check**. Older waiting listings come first.
2. A listing whose inventory evidence changed since its physical check needs a
   **changed-since-check** review. The change time breaks ties.
3. Other unresolved checks become **recheck due** after 48 hours. Oldest check
   first. Here unresolved includes pending, sold-label, unavailable, access-blocked,
   and unknown checks; a page label has not established a final sales outcome.

The 48-hour interval is an operating choice, not a sales model. Available checks
need no routine retry unless inventory evidence changes. Within ties, retailer,
VIN and listing ID make the result deterministic. Up to 20 eligible rows receive
`selected_for_check=True`; overflow and `up_to_date` rows remain inspectable.
Nothing is fetched automatically. Repeated review is still needed when capacity
is insufficient; this queue is not a representative statistical sample.

Actionable changes remain in the queue after their first day, so an unchecked
pending change is not forgotten on the next unchanged capture. A new listing ID
requires its own check; the old listing's evidence stays attached to the old ID.
After a missing/partial capture, the queue can revisit previously known evidence
but does not invent a fresh absence. Read its source date and check time.

## Small synthetic result

| Day | Saved check | Queue state | Candidate state | Sales |
| --- | --- | --- | --- | --- |
| 2 | unavailable, checked day 2 | up_to_date | No candidate yet | Unknown |
| 4 | Same day-2 evidence | recheck_due | Still absent; unreviewed | Unknown |
| 5 | Same day-2 evidence | changed_since_check | Reappeared; unreviewed | Unknown |

This is an illustrative workflow, not saved pilot data. Notebook 20 retains one
separate synthetic VIN story for gaps and reappearance; its primary tables now
show real saved page checks. The next research step is consecutive daily inventory
and first-disappearance checks, before assessing any sales estimate.
