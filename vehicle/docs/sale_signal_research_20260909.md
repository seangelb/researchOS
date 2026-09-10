# A public Carvana Sold signal: September 9, 2026

**Finding:** Carvana's public vehicle pages embed a native
`forVehicleContext.vehicleDetails.saleStatus` field. Two actual pages with a
visible **Sold** badge contained `saleStatus: "Sold"`. Two unavailable Tesla
pages instead contained `saleStatus: "Available"` and
`purchaseType: "NotPurchasable"`. Disappearance and inability to purchase are
therefore insufficient substitutes for the explicit Sold field.

This is a verified website signal, not a verified delivery date or final retail
transaction. No sales rule, production database, daily cycle, listing check, or
analyst review was changed by this study.

## What we observed

The seven checks ran in ordinary Chrome from **11:18:44 to 11:22:37 UTC**
(**07:18:44 to 07:22:37 America/New_York**) on September 9. Each native record's
vehicle ID matched its original URL; the VIN was read from that same record.

| Listing | Vehicle | Native saleStatus | Native purchaseType | Visible page evidence |
| --- | --- | --- | --- | --- |
| [4465259](https://www.carvana.com/vehicle/4465259) | 2016 Jeep Wrangler | Sold | NotPurchasable | Sold; no longer available |
| [4436696](https://www.carvana.com/vehicle/4436696) | 2018 BMW X1 | Sold | NotPurchasable | Sold; no longer available |
| [4632932](https://www.carvana.com/vehicle/4632932) | 2023 Tesla Model 3 | Available | NotPurchasable | No longer available; no Sold badge |
| [4678187](https://www.carvana.com/vehicle/4678187) | 2021 Tesla Model 3 | Available | NotPurchasable | No longer available; no Sold badge |
| [4640427](https://www.carvana.com/vehicle/4640427) | 2020 Tesla Model 3 | Available | Purchasable | Purchase in progress |
| [4681095](https://www.carvana.com/vehicle/4681095) | 2020 Tesla Model 3 | Available | Purchasable | Get Started |
| [4671091](https://www.carvana.com/vehicle/4671091) | 2020 Tesla Model 3 | Available | Reservable | Pre-order now; inspection in progress |

These are deliberately selected examples, not a random sample or a sales count.
The Jeep and BMW are outside the registered Tesla pilot. The older Tesla 4678187
comes from earlier research captures, not a registered daily disappearance.

The new daily missing Tesla is VIN **5YJ3E1EAXPF590130**, listing **4632932**.
At its September 9 01:44:16 UTC inventory observation it was Purchasable and
not pending. At this later page check it was NotPurchasable, but its saleStatus
was still Available. Its current evidence supports **unavailable**, not
site-reported Sold. The reason for unavailability remains unknown.

Native Available is also not a promise that a customer can buy immediately:
pending and pre-order examples use that value. Keep sale status, purchase type,
and page wording as separate columns. No sale or delivery timestamp was present
among the vehicleDetails keys inspected. A price-update timestamp is not a sale
timestamp.

## How the extraction works

1. Open the original public URL, such as `/vehicle/4465259`, in Chrome.
2. Carvana renders the page using React Server Component data embedded in
   `<script>` elements containing `self.__next_f.push(...)`.
3. Parse those serialized data chunks with JSON parsing, without executing script
   contents. Join the chunks before decoding their records because a record can
   cross script boundaries.
4. Locate `forVehicleContext.vehicleDetails`. Read the target listing's
   `vehicleId`, `vin`, `saleStatus`, `purchaseType`, and `inventoryType`.
   Do not take the first arbitrary vehicle object: recommendations may describe
   other cars. Validate the URL/vehicle ID and retained VIN binding.
5. Compare the native field with the vehicle-specific badge. Preserve the
   observation timestamp and exact native wording. Do not search the whole page
   for the substring "sold": equipment boilerplate contains "as originally sold".
6. Save a small public-vehicle projection; exclude account, cookie, session,
   financing and customer data. The RSC record prefix and numeric array positions
   are implementation details, not stable identifiers to hard-code.

The selected native record from the Jeep was:

```json
{
  "vehicleId": 4465259,
  "vin": "1C4AJWAG5GL241979",
  "saleStatus": "Sold",
  "purchaseType": "NotPurchasable",
  "inventoryType": "Core"
}
```

The future research rule can identify **site_reported_sold** when the target's
native saleStatus is exactly Sold, supported here by the visible badge. Missing,
unrecognized, conflicting or unreadable fields remain unresolved. The two signals
are from the same website, not independent transaction confirmations.

## Public application-code corroboration

Twenty static JavaScript assets referenced by the actual page were fetched
sequentially, at least three seconds apart, and retained without execution.
All twenty returned HTTP 200. The observed page bundle
[page-41c8a711f8b94267.js](../data/experiments/carvana_sale_signals/2026-09-09/assets/page-41c8a711f8b94267.js)
compares native saleStatus directly with Sold in its availability and pricing
logic. At character offset 19039, the Sold branch bypasses the normal availability
lookup. The bundle also distinguishes Sold, recall restrictions, and geographic
restrictions in the description it passes to the chat page context.

That chat description is not the visible badge renderer. The complete imported
selector behind the badge was not located within the bounded asset sample.
Neither these branches nor the page observations establish when the backend
changes Sold relative to delivery, the return period, or financial recognition.

[Original public bundle](https://assets.fastly.carvana.io/merchui/_next/static/chunks/app/(carvana-layout)/vehicle/%5BvehicleId%5D/page-41c8a711f8b94267.js)

## Collection feasibility

- **Existing public search API:** one authorized POST using the unchanged
  Tesla Model 3 / 2023 / ZIP 08542 / page 1 / 24-row contract returned HTTP 200.
  None of the 24 vehicle objects had a top-level saleStatus, isSold, saleDate,
  or soldDate key. Newly noticed keys included stockRecallStatusType and
  vehicleReservableReasons. This is one active-inventory batch, not proof that
  every possible response lacks sale information.
- **Ordinary Chrome:** all seven known detail pages exposed the native vehicle
  fields. No purchase, hold, notification or account action was taken.
- **Plain Python detail GET:** one credential-free request to the Jeep URL
  returned HTTP 403 with a Cloudflare challenge. It was retained as an access
  failure, with no retry or bypass. Unattended detail collection is not proven.

A practical next collector would use search for discovery, then check known
listing pages for changed/pending/missing vehicles and a few controls. It should
keep following VINs across new listing IDs to detect reappearances. This reduces
detail-page work, but the missed-sales rate of any targeted queue must be measured.
Do not describe a seven-page interactive study as a proven national daily scraper.

## Next measurement

Follow a prospective cohort from listed/pending through its first observed Sold
status, then continue checking for reappearance. Retain revisions as new evidence.
A newly encountered, already-Sold historical page cannot be assigned a sale to the
day we discovered it. If we observe a transition, last-not-Sold and first-Sold
bound the **website-status transition**, not necessarily the transaction date.

Measure how often unavailable pages later become Sold, how long that takes,
and how often Sold vehicles reappear. A reappearance can mean a return, relisting,
or another event; do not automatically label every reappearance a customer return.
Registration reports are optional corroboration, not required to observe this
website signal.

## Retained evidence and limits

- [Seven Chrome observations](../data/experiments/carvana_sale_signals/2026-09-09/observations.json):
  manually retained projections of actual browser tool output, with native
  values, source path, VIN/ID, selection reason and observation/availability
  timestamps. These are not original HTML bytes or complete screenshots.
- [One search probe](../data/experiments/carvana_sale_signals/2026-09-09/search_probe/search_status_projection.json):
  projected public vehicle fields and original response hash; original response
  bytes were not retained.
- [One plain HTTP probe](../data/experiments/carvana_sale_signals/2026-09-09/http_probe/manifest.json):
  metadata and hash of the retained 403 response body.
- [Asset manifest](../data/experiments/carvana_sale_signals/2026-09-09/assets/manifest.json)
  and [extension manifest](../data/experiments/carvana_sale_signals/2026-09-09/assets/manifest_extension_1.json):
  actual public JavaScript bytes, source URLs, timestamps and hashes.

The experimental evidence is excluded from production sales totals. Chrome
shopper ZIP was not independently matched to the API ZIP, so the study makes no
price, delivery or geographic-coverage comparison. Raw experimental files are
ignored by Git and need a local backup.
