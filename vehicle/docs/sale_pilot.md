# Follow the same VINs and retain Carvana's Sold status

Open [Notebook 20](../notebooks/20_carvana_history_analysis.ipynb) first for daily
inventory, matched-VIN asking prices, actual collection intervals, and canonical
vehicle review. [Notebook 22](../notebooks/22_carvana_sale_status_validation.ipynb)
replays the frozen Sold-status cohorts separately. Both Run All workflows are
offline and read-only; experimental evidence never becomes a canonical check.

The original cohort has seven VINs: four prospective inventory vehicles and three
historical controls. The extension has 26 distinct prospective VINs, selected from
the September 9 snapshot: 18 from 204 eligible native pending=true VINs and eight
from 474 eligible native pending=false VINs. False is not proof of availability;
pre-order/native purchase eligibility remains visible. The frozen extension's
selection time is September 10, 00:22:48 UTC (September 9 local time); its filename
must not replace that timestamp in historical analysis.

Notebook 22 validates disjoint identities known at the cutoff before combining
coverage. It shows unobserved, once-checked and repeatedly checked VINs, native
versus resolved evidence, failed/unresolved checks, and first observed Sold
transitions. If no extension runs are retained, all 26 extension VINs are
unobserved, rather than 26 measured zero outcomes. Outcome rates and actual sales
remain unavailable. The original pass described below is historical evidence;
read the notebook's cutoff-filtered tables for current coverage.

## Freshness and preceding inventory

Notebook 22 shows the last attempted physical page check separately from the last
usable native Available/Sold observation, with hours since each at the explicit
cutoff. A recent failed attempt does not refresh older status evidence. Native
Available/Reservable can establish non-Sold evidence while purchase availability
remains unresolved. `PILOT_RECHECK_HOURS` is visibly set to 24 hours; `overdue` is
true when usable native evidence is at least that old or has never been obtained.
This is a reminder, not a sales rule, persistent queue, or collection authorization.

The compact inventory comparison uses the latest preceding retailer/VIN row whose
physical observation **and cycle availability** precede the page check. Both must
also be within the overall cutoff. Inventory and page listing IDs stay separate;
ambiguous bindings block the comparison. The table preserves the inventory's
actual time, availability, native pending flag, asking price and source, together
with the page's native saleStatus/purchaseType and elapsed observation hours.

An older preceding row is not evidence of current availability. Read the latest
eligible cycle's coverage label: missing date, partial collection, complete-scope
non-observation, unestablished membership, and historical control never observed
in the retained scope are distinct. None establishes a sale. A pending inventory
row followed by an available page is an observation to investigate, not an
automatic cancellation or parser error.

For each qualifying first Sold transition, `transition_interval_hours` shows the
width between the last usable native non-Sold check and first Sold check. The
timestamps remain visible. Neither a midpoint, first Sold date nor disappearance
date is substituted for delivery; a later reappearance is not automatically a return.

## Recovered bounded pass: September 10, 2026 UTC

The [retained pass report](../data/experiments/carvana_sale_signals/pass_recovery_20260910T013836Z-437d819a/pass_report.json)
documents 24 already-produced public-page projections, physically checked at
00:24:24.120-00:27:15.890 UTC. They were recovered from the existing browser session
without repeating visits, previewed, and imported at 01:37:43-01:37:44 UTC in
separate batches of seven original, 12 extension and five extension captures.
There were **zero new vehicle-page visits during recovery**.

| Cohort | Selected | Attempted | Usable native | Resolved interpretation | Unresolved interpretation | Failed | Unvisited |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Original | 7 | 7 | 7 | 5 | 2 | 0 | 0 |
| Extension | 26 | 17 | 16 | 11 | 5 | 1 | 9 |

These counts describe this pass, not cumulative history. Native evidence and
resolved purchase/status interpretations are different denominators. The final
attempt encountered a Cloudflare challenge according to the contemporaneous
browser observation recorded in the recovery report. The original projection was
preserved unchanged: `access_outcome=unknown`, no vehicle contexts, and parser
`access_failed`. No HTTP status was measured. The pass stopped; no challenge was
retried and the remaining nine VINs were unvisited.

After these imports, history contains 21 original observations and 17 extension
observations. Extension baseline evidence now exists for 16 VINs; one attempted
VIN has no usable native observation and nine have no attempt. Extension listing
4674263 was already Sold on its first page observation, so it is not a qualifying
first non-Sold-to-Sold transition. Original listing 4681095 changed from an
available interpretation to unknown while retaining native Available/Purchasable;
this is not Sold evidence. No qualifying first Sold transition or transition
interval was observed, and sales/outcome rates remain unavailable. Notebook 22's
cutoff-filtered tables provide the current view.

## The result of the first follow-up pass

On September 9, 2026, seven public pages were visited sequentially in ordinary
Chrome. The saved projections were checked at 11:42:40–11:46:18 UTC and became
available to this workflow at 11:47:36 UTC. No additional inventory requests or
static-asset downloads were made in this pass.

| Listing | Cohort role | Previous → latest interpretation | Native saleStatus / purchaseType |
| --- | --- | --- | --- |
| 4632932 | Prospective inventory | unavailable → unavailable | Available / NotPurchasable |
| 4640427 | Prospective inventory | pending → pending | Available / Purchasable |
| 4681095 | Prospective inventory | available → available | Available / Purchasable |
| 4671091 | Prospective inventory | unknown/pre-order → unknown/pre-order | Available / Reservable |
| 4465259 | Historical control | sold_label → sold_label | Sold / NotPurchasable |
| 4436696 | Historical control | sold_label → sold_label | Sold / NotPurchasable |
| 4678187 | Historical control | unavailable → unavailable | Available / NotPurchasable |

There are **no newly observed Sold transitions** in this prospective cohort.
This does not establish zero actual sales. The two Sold controls were already
Sold when first encountered; neither is counted as a new transition. The missing
Tesla 4632932 remains unresolved. These are selected examples, not an accuracy
estimate or representative inventory sample.

The run is retained in
`vehicle/data/experiments/carvana_sale_signals/pilot_20260909T114736Z-546ff15a/`.
The earlier seven-page study remains unchanged. All 14 page observations are
available for comparison at a sufficiently late cutoff.

## What is automatic and what is manual

1. **Manual:** visit the selected original URL in Chrome and wait for the page.
2. **Repeatable extraction:** the small JavaScript helper reads embedded React
   records from the loaded DOM, joins their stream chunks, JSON-decodes them, and
   keeps only public vehicle identities/statuses and the specific hero/button.
3. **Manual:** copy the helper's JSON result into a new UTF-8 file.
4. **Repeatable import:** Python validates the selected cohort VIN, target listing,
   URLs and clocks; previews the interpretation; and saves only with `--save`.
5. **Automatic offline analysis:** Notebook 22 reads the saved captures, verifies
   hashes, filters by the evidence cutoff, and compares observations per VIN.

This is a **browser-assisted workflow**, not an unattended Python scraper.
The earlier plain Python GET returned a 403 challenge; this milestone did not
retry it. A dedicated browser framework and scheduled national scraping are
outside this small pilot.

## Run the next check

Launch Jupyter from the repository root:

```powershell
powershell -File scripts/start_jupyter.ps1
```

Open `vehicle/notebooks/22_carvana_sale_status_validation.ipynb`, Run All, and find
the coverage and source tables. Its queue shows fixed identities, original URLs
and selection reasons from `vehicle/config/carvana_sale_pilot.json` and
`vehicle/config/carvana_sale_pilot_extension_20260909.json`. Do not resample or
rewrite those files. Follow each cohort separately.

Open a selected URL in Chrome. Open DevTools Console (Ctrl+Shift+J), inspect and
paste the function in
[capture_carvana_page.js](../scripts/capture_carvana_page.js), then invoke it
with the queue's exact VIN and listing ID. Example for the missing Tesla:

```javascript
copy(JSON.stringify(captureCarvanaPage({
  vin: '5YJ3E1EAXPF590130', listing_id: '4632932'
}), null, 2))
```

`copy` is a Chrome DevTools command that copies the returned JSON to the clipboard.
Paste into a new `.json` file in a scratch folder, saved as UTF-8. The function
does not click anything, make additional requests, or write files. Do not change
the generated physical check time to the later import time.

Repeat for the same cohort, at most 12 page captures per import batch, with modest spacing.
Keep the frozen VINs. If a retained inventory observation or inspected public
page supplies a different listing ID for the same VIN, explicitly use that ID
in the capture call and verify the VIN. Both IDs remain in history; the cohort's
original URL is not silently replaced. An old Sold page alone does not prove the
VIN is still unavailable under every possible listing ID.

From the repository root, preview the files:

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/import_carvana_sale_pilot.py --cohort vehicle/config/carvana_sale_pilot.json --input "C:\path\capture_01.json" "C:\path\capture_02.json"
```

For the extension, use its exact cohort file instead (still at most 12 captures
per batch; do not combine original and extension captures):

```powershell
.\.venv\Scripts\python.exe -B vehicle/scripts/import_carvana_sale_pilot.py --cohort vehicle/config/carvana_sale_pilot_extension_20260909.json --input "C:\path\extension_01.json" "C:\path\extension_02.json"
```

The extension's 26 VINs therefore require multiple batches if all are checked.
Read the printed expected identities, native values, interpreted status and parse
outcome. Repeat that exact command with `--save` to retain a new experimental run.
The importer never writes the inventory database, registered cycles, canonical
checks, analyst reviews, or confirmed-sale totals. Imports are limited to cohort
VINs and one visit per VIN per pass. An explicit new listing ID is permitted for
the same cohort VIN; a mismatch with the native record remains unresolved.

Run Notebook 22 again. The default cutoff refreshes in its opening code cell.
If `AS_OF_OVERRIDE` is set, deliberately advance it to include the new evidence.
The two clocks are different: `checked_at` is the actual browser observation;
`available_at` is when the import became available. All timestamps are UTC.

## Access failures

If a page is blocked, do not retry challenges or fill in its previous vehicle
fields. Retain the actual failure as a separate JSON projection and stop that live
pass. Report remaining cohort VINs as unvisited; do not retry the blocked page or
continue the remaining visits in that pass. For example,
the following is a **template, not an observation**: replace the expected identity,
URLs, check time and note with what was actually observed.

```json
{
  "format": "carvana-public-detail-projection-v1",
  "expected": {"retailer": "carvana", "vin": "<selected VIN>", "listing_id": "<selected ID>"},
  "requested_url": "<original URL>",
  "final_url": "<actual final URL>",
  "checked_at": "<actual timezone-aware check time>",
  "access_outcome": "access_blocked",
  "contexts": [],
  "hero_text": null,
  "hero_badge": null,
  "purchase_button": null,
  "note": "<actual challenge/failure wording and retained screenshot reference, if any>"
}
```

Use `not_found` for an actually observed missing page or `unknown` when access
does not establish a vehicle status. A 404, unknown format, missing sale/identity field,
or identity contradiction never establishes Sold. The automated DOM helper does
not measure an HTTP status; do not claim it observed HTTP 403 solely from an empty
vehicle context.

## How interpretation works

`sale_pilot.py` contains plain functions:

- `parse_capture`: pure projection validation and interpretation, without I/O.
- `baseline_records`: adapts the earlier manually retained study; does not invent HTML.
- `load_pilot`: reads local evidence, checks hashes and both evidence clocks.
- `summarize_pilot`: pure comparison within each retailer/VIN, keeping listing IDs.

Exact native `Sold` maps to the existing `sold_label` vocabulary. Native
`Available + NotPurchasable` means unavailable, without a known cause.
`Available + Purchasable` needs specific page evidence to distinguish pending
from available. `Reservable` remains unknown/pre-order in the existing vocabulary.
Conflicting or unrecognized evidence stays unresolved. Relevant badge/button text
is retained separately; generic equipment text such as “as originally sold” cannot
create a Sold result.

The notebook retains every known source version while coverage uses the latest
available interpretation per physical visit. Its resolved-status count excludes
pre-order/unknown interpretations; a separate native-evidence count includes
matched Available/Reservable as a known non-Sold boundary. Access failures and
unresolved interpretations are displayed separately. A failed latest visit never
inherits the previous visit's native status.

The summary contains one first Sold transition per prospective VIN, through the
cutoff. `last_non_sold_at` and `first_sold_at` bound a website-status transition,
not a transaction date. Historical controls and initially already-Sold VINs are
excluded; repeated Sold checks add no transitions. A later Available status with
Purchasable or Reservable eligibility is retained as reappearance. It may reflect
a return, relisting, or another event; the pilot does not choose a cause.

The summary does not claim a daily sales rate, final net sales, or a missed-sales
rate. Follow the cohort again to observe actual transitions and later outcomes
before evaluating either the timing or accuracy of a sales estimate.

## Evidence and validation limits

The saved artifacts are selected public DOM/RSC projections transferred from
browser tool output, not raw HTML or original server-response bytes. The manifest
hashes describe those exact retained projection files. Browser extraction was
exercised on the seven real pages. The tracked projection fixture tests Python
interpretation; a clearly synthetic split-stream test exercises the browser helper
using Node when available. Node is optional test tooling, not a runtime requirement.

Experimental files are ignored by Git and require local backup. Preserve the
cohort configuration with those runs: the reader rejects a changed cohort rather
than silently changing who was eligible. Corrections to a physical check retain
its check time and appear as a later import; the summary uses the latest available
version at the cutoff. Reimporting the same visit does not create another transition.
