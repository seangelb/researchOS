# Prospective cohort follow-up: September 10, 2026 UTC

The checkout matched main at `78b67842db32bd10daf796ee0470e508b2e7dec2`. Existing uncommitted work was preserved. The only pre-existing file changed in this stage is Notebook 22. The capture helper, importer, vehicle_history.py, cohort definitions, databases, older captures and previous findings/tests are unchanged.

The single pass made **12 sequential Carvana detail visits**, with zero report views, navigation retries, observed challenges or identity conflicts. Recorded browser time was **04:05:48.361?04:10:41.307 UTC (292.946 seconds)**; the start was logged just after the first navigation completed. Minimum spacing between recorded navigation starts was **15.005 seconds**. Browser subrequests and HTTP statuses were not measured. Two string-form DOM evaluations returned no result during capture setup; these did not reload or revisit a page.

All 12 projected captures passed the existing importer preview with matching expected VIN/listing, observation times preceding import, and matched native records. They were saved at **04:12:05.852662 UTC** in one [new experimental pilot run](../data/experiments/carvana_sale_signals/pilot_20260910T041205Z-1d61613c/run.json). Results: **8 pending, 2 available, 2 native Available/Reservable with unresolved purchase readiness**. No Sold label was observed in this batch.

| Measure | Before | After |
| --- | ---: | ---: |
| Selected VINs | 33 | 33 |
| VINs visited | 24 | 33 |
| VINs with usable native evidence | 23 | 33 |
| No visit | 9 | 0 |
| Attempted, no usable native | 1 | 0 |
| Initially non-Sold prospective VINs with repeats | 4 | 6 |
| Initially Sold VINs | 3 | 3 |
| First qualifying Sold transitions | 0 | 0 |
| VINs with later reappearance | 0 | 0 |

Eight latest purchase interpretations remain unresolved; two additional vehicles are explicitly unavailable with unknown causes. All 33 now have a usable native baseline, but only six initially non-Sold prospective VINs have usable repeats. Repeated Sold labels and initially Sold controls do not add sales. With no qualifying transition, no new transition interval or delivery date can be inferred.

The original batch filled nine unvisited cases and one attempted/no-native case, then repeated one VIN from each frozen selection group. Non-pending and pending selections were interleaved early. The [saved plan](../data/experiments/prospective_followup/20260910T040311Z/plan.json) retains the 21 VINs not visited in this pass; the notebook also shows every current remainder row.

The next batch below contains six selections from each frozen pending group, all needing a first usable repeat. The selection group is historical metadata, not the current website status. Use the next planned daily window; no second live pass was performed. Continue the existing 33 VINs before considering expansion: this pass established baseline coverage and access for one short session, not daily unattended reliability or a sales classifier.

| Order | VIN | Frozen pending group | Intended listing | Last usable observation (UTC) |
| ---: | --- | --- | --- | --- |
| 1 | 5YJ3E1EA1MF071110 | false | [4659188](https://www.carvana.com/vehicle/4659188) | 2026-09-10T00:25:51.845Z |
| 2 | 5YJ3E1EA2PF428587 | true | [4665345](https://www.carvana.com/vehicle/4665345) | 2026-09-10T00:25:55.601Z |
| 3 | 5YJ3E1EA4MF072462 | false | [4728005](https://www.carvana.com/vehicle/4728005) | 2026-09-10T00:26:03.223Z |
| 4 | 5YJ3E1EA5PF591055 | true | [4524552](https://www.carvana.com/vehicle/4524552) | 2026-09-10T00:26:14.409Z |
| 5 | 5YJ3E1EA5NF322616 | false | [4713082](https://www.carvana.com/vehicle/4713082) | 2026-09-10T00:26:06.998Z |
| 6 | 5YJ3E1EA7NF288274 | true | [4567173](https://www.carvana.com/vehicle/4567173) | 2026-09-10T00:26:18.172Z |
| 7 | 5YJ3E1EA5NF344681 | false | [4610772](https://www.carvana.com/vehicle/4610772) | 2026-09-10T00:26:10.727Z |
| 8 | 5YJ3E1EAXMF875261 | true | [4623001](https://www.carvana.com/vehicle/4623001) | 2026-09-10T00:26:21.988Z |
| 9 | 5YJ3E1EB6NF207869 | false | [4686527](https://www.carvana.com/vehicle/4686527) | 2026-09-10T00:27:12.305Z |
| 10 | 5YJ3E1EAXMF941386 | true | [4621165](https://www.carvana.com/vehicle/4621165) | 2026-09-10T00:26:25.822Z |
| 11 | 5YJ3E1EBXMF984246 | false | [4695105](https://www.carvana.com/vehicle/4695105) | 2026-09-10T04:07:08.446Z |
| 12 | 5YJ3E1EAXNF203444 | true | [4682416](https://www.carvana.com/vehicle/4682416) | 2026-09-10T00:27:00.461Z |

**Notebook changes:** history-study status/listing/time now display beside native pilot status/time. The existing pilot summary and verified identity helper produce the next-batch view. A before/after table and exact pass-source rows show what was added. Study observations remain separate from native captures and analyst outcomes.

**Verification:** 529 vehicle tests passed, including four new focused notebook tests. All six notebooks execute offline, with 15 code cells in Notebook 22. Cutoff checks immediately before and after import availability preserve the historical view. Hash checks confirm 1,775 pre-existing files remained unchanged apart from the explicitly edited notebook. No canonical inventory, listing checks, analyst reviews or sales estimates were written; no commit or push was performed.

Open [Notebook 22](../notebooks/22_carvana_sale_status_validation.ipynb), section 6. Run All is offline and read-only. [Pass accounting](../data/experiments/prospective_followup/20260910T040311Z/pass.json) and [derived outcome](../data/experiments/prospective_followup/20260910T040311Z/outcome.json) remain in the dated experimental folder. These experimental files are ignored by Git and require local backup.

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider vehicle/tests
.\.venv\Scripts\python.exe -B scripts/check_notebooks.py
```
