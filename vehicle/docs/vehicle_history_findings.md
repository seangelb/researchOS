Continue the existing 33-VIN cohort. Vehicle-history reports can add useful corroboration, but this study does not validate a daily sales count. Do not expand the cohort or build a bulk report scraper yet. First collect longer comparable inventory history and prospective website-status transitions, then revisit reports for selected cases.

The attachment's checkout description was stale: this study found `main` at `78b67842db32bd10daf796ee0470e508b2e7dec2`, rather than `4074f969b2fd46f30a5584a18ab09acd5b0c0119`. The recovered pilot sources and frozen cohort already existed; implementation could extend them without switching checkouts or recreating the pilot. See [recorded preflight](../data/experiments/vehicle_history_study/20260910T034129Z/preflight.json).

On September 10, 2026 UTC, ordinary Chrome visits covered five frozen-cohort vehicles: available, pending, unavailable, and two initially Sold. The user-supplied Kia was a sixth, separate public demonstration. There were six detail visits and four linked-report views, with three accessible VIN-matched AutoCheck reports. A CARFAX device-verification challenge stopped all further source visits. Browser subrequests and HTTP status codes were not measured. The study used about four minutes of its 20-minute allowance; it did not exhaust the eight-detail/five-report limits. See [study accounting](../data/experiments/vehicle_history_study/20260910T034129Z/study.json) and [website observations](../data/experiments/vehicle_history_study/20260910T034129Z/detail_observations.json).

| Listing and VIN | Observed evidence | What it supports |
|---|---|---|
| Available Tesla `4722528`; `5YJ3E1EA0PF668477` | August 31, 2026 Helena, Montana title; September 10 page offered “Get Started.” | A Montana title can precede an available listing. It does not identify a retail sale. [Report projection](../data/experiments/vehicle_history_study/20260910T034129Z/report_4722528_projection.json) |
| Sold-labelled Tesla `4674263`; `5YJ3E1EA2PF544050` | Same August 31 Montana title date; September 1 registration; first retained detail check already Sold. | The registration predates September 9 inventory. It cannot date a subsequent sale. The shared Montana pattern does not distinguish available from Sold. [Report projection](../data/experiments/vehicle_history_study/20260910T034129Z/report_4674263_projection.json) |
| Demonstration Kia `3700211`; `5XYRKDLF4PG168771` | Sold page retained an accessible report. Owner 2 began September 2025, at month precision; September Ohio titles and a March 16, 2026 auction sale appear. | Ownership history merits investigation, but no selected event names Carvana as seller. The original listing interval is unverified. The later auction sale cannot be attributed to that original Carvana period. [Report projection](../data/experiments/vehicle_history_study/20260910T034129Z/report_3700211_projection.json) |

Provider documentation explains why these distinctions matter. [MarketCheck](https://docs.marketcheck.com/docs/guides/data/cars/inventory/key-concepts) infers sales from absence in the latest daily active dataset, a latest status date across listings more than seven days old, and attribution to the final searchable dealer listing. Our limited population cannot reproduce that cross-domain coverage. Compare seven days only as sensitivity; retain the existing threshold.

[CARFAX](https://www.carfax.com/buying/how-to-read-a-carfax-report) describes ownership and event timelines but acknowledges incomplete vehicle histories. [Experian](https://www.experian.com/automotive/vehicle-history-services) advertises auction announcements and real-time API access; neither establishes immediate reporting of retail sales. [NMVTIS](https://vehiclehistory.bja.ojp.gov/nmvtis_understandingvhr) describes title state/date, brands, odometer, total loss and salvage indicators. None of these pages supplies a numeric reporting-lag guarantee. A renewal, duplicate title, auction announcement or auction sale is not automatically a Carvana retail sale.

Keep four clocks separate: the reported event date, provider report-run time, our first observation, and local evidence availability. For example, the Kia report ran at `03:42:31Z`, was observed at `03:42:58.363Z`, and became locally available at `03:46:46.802568Z`. Its older events must not enter earlier cutoff results. One retrieval cannot measure publication lag. Retained files are selected public projections, not complete reports or original HTTP bytes; hashes cover those projection bytes.

Only two daily inventory cycles exist, and retained pilot evidence has no qualifying prospective Sold transition. Continuous seven-day absence is therefore **not yet evaluable**. Initially Sold vehicles and repeated Sold checks do not create new sales. Missing collection days or report events remain unresolved. Asking prices remain asking prices; analyst confirmation remains explicit. These new research observations stay in the experimental folder and do not enter the operating database or production pilot imports.

Open [Notebook 22](../notebooks/22_carvana_sale_status_validation.ipynb), section 5.
It shows the source events, both absence sensitivities and one VIN's inventory
capture, SQLite observation and website checks. Section 6 keeps the original URL
beside the latest verified follow-up URL. `AS_OF_OVERRIDE` selects a historical
cutoff; `INSPECT_HISTORY_VIN_OVERRIDE` selects a vehicle. Run All remains offline
and read-only. The optional study folder is explicit in `HISTORY_STUDY_DIR`.

Validation: **525 vehicle tests passed**, including manifest/projection tampering,
wrong VINs, late discovery, repeated Sold checks, changed listing identities and
incomplete coverage. Notebook 22 executed all 13 code cells against retained inputs.
All 1,336 pre-existing evidence/configuration files retained their hashes. The new
selected research evidence occupies 23,138 bytes; this is not full-report storage.

From the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider vehicle/tests
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py
powershell -File scripts/start_jupyter.ps1
```

No new collection command is added. Resume public report visits only after normal
access is available; the blocked case remains visible. No commit or push was made
in this stage.
