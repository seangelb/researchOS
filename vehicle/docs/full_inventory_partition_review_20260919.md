# Full-inventory pagination review: September 19, 2026

Terminal follow-up: [the completed September 19 outcome](full_inventory_outcome_20260919.md)
records all 25 gaps and the retained export. The checkpoint below is preserved
as written while the original sweep was still running.

The first all-year sweep started from the frozen `cb1924b` checkout. Its
opening response reported 82,583 vehicles across 40 makes. It already contains
an incomplete Audi A5 query, so it cannot qualify as the required complete
baseline. Monitor the original process and retain its terminal outcome; do not
restart it, change its plan, or fill its gaps from a replacement attempt.

## Verified first gap

The all-year Audi A5 query reported 127 vehicles and six pages throughout four
HTTP 200 responses. Page four's first vehicle exactly repeated page three's last:
listing `4757139`, VIN `WAUANCF51KA008404`, a 2019 Audi A5. The retained public
vehicle fields were identical. ZIP 08542, native MostPopular sorting and disabled
location filtering were unchanged. Actual request starts were more than three
seconds apart.

The collector admitted 72 identities from the first three pages. The failed
fourth page admitted zero rows; its additional 23 distinct VINs remain diagnostic
evidence. Deduplicating those pages would not establish that no vehicle was
missed. Counts did not change, and no access error occurred. The precise cause of
the repeated ranking boundary remains unknown.

The authoritative query is under
`C:\Users\Sean\VscProjects\researchOS-full-inventory\vehicle\data\experiments\carvana_full_inventory\2026-09-19\make_002_model_002`.
The external source-bound diagnosis is
`C:\Users\Sean\Documents\ChatGPT\ResearchOS\carvana_full_inventory_20260919\first_pagination_gap.json`,
SHA-256 `88f180f2b1b4340bc106554906e8197da5a76438f65476ccf3e44aa758f461f5`.
Original response serialization and omitted fields are unavailable; selected
public response fields, projections, journals and their hashes are retained.

## Overlapping Chevrolet model categories

At `2026-09-19T13:24:13.020271Z`, Chevrolet discovery reported 7,250 vehicles,
while its 28 model groups summed to 7,252. Model ID 497 belongs to both Silverado
3500 (count 40; IDs 242, 486, 497, 636, 665) and Silverado 3500 HD Chassis Cab
(count 3; IDs 490, 497). Both groups also have exclusive IDs, so discarding either
one would not preserve the complete declared category set.

The difference of two is consistent with overlapping membership, but does not
prove exactly two shared vehicles. The frozen collector consequently falls back
to the full Chevrolet make. A future strategy must retain overlapping coverage
explicitly or validate a grouped-model request; it cannot silently choose a
disjoint subset. Smaller years alone do not establish that this overlap vanishes.

The hash-bound `make_006` facet source is
`420203ff8693855c4d16300ec8a7190578a1204a1ce2fd628ac6194e58080dda`.
Its authoritative parent report and page journal are retained alongside the
original capture. This finding changes future partition design, not this attempt.

## Seven-failure checkpoint

The ledger through request 949, at `2026-09-19T13:54:55Z`, contained seven
terminal pagination failures. Independent review matched all seven report hashes
and 306 retained source/projection hashes. The original sweep was still running;
this checkpoint is not its final outcome.

| Query | Failed page | Retained source finding | Admitted rows before failure |
|---|---:|---|---:|
| Audi A5 | 4 | Exact boundary repeat; native total 127 unchanged | 72 |
| Chevrolet, whole make | 55 | Exact boundary repeat; total 7,250 unchanged | 1,296 |
| Chrysler Pacifica | 25 | Native total fell from 1,078 to 1,077 | 576 |
| Dodge Charger | 11 | Exact boundary repeat; total 388 unchanged | 240 |
| Ford F-150 | 27 | Exact boundary repeat; total 1,650 unchanged | 624 |
| Honda Accord | 4 | Native total fell from 874 to 873 | 72 |
| Honda CR-V | 27 | Exact boundary repeat; total 1,075 unchanged | 624 |

The failed page in each query retained 24 source vehicles and admitted zero rows.
The repeated identities had unchanged listing/VIN/year fields; all 153 reviewed
responses were HTTP 200 JSON. The retained evidence did not show an access,
identity or schema failure incorrectly treated as an isolated pagination gap.
The cause of ranking-boundary repeats remains unknown. A count changing by one
does not identify the affected vehicle or establish a sale.

Pacifica and Charger source pages include 2027 vehicles. The next strategy must
preserve these observations and both outside-year tails. Even small queries can
fail quickly: the A5 boundary repeat and Accord count change occurred within
roughly nine seconds of their respective first responses. Smaller groups reduce
exposure but do not guarantee a stable enumeration.

## Next design to validate

The user confirmed that access is limited to public website collection. A bulk
feed is not available. Smaller year groups should reduce pagination exposure;
they are a hypothesis to test, not a demonstrated fix.

Retained make/year responses show year-conditioned counts for every displayed
make, but model children only for the selected make. Consequently, one year
response cannot supply every model/year partition. The proposed sequence is:

1. Keep broad all-year discovery as the population anchor.
2. Derive exact-year contexts from the native minimum and maximum, plus an
   older-year tail and a newer-year tail. These contexts partition integer years;
   missing/noninteger years are not covered by this mathematical partition.
3. Validate each future year-only response against its exact requested bounds,
   ZIP and native application metadata. Its make counts must sum to its reported
   inventory total. Year-only and one-sided API request behavior has not yet been
   demonstrated by a real retained response. A mismatch must stop the experiment.
4. Use year-level make counts to plan small make/year cells directly. Probe larger
   make/year cells for model children, then enumerate their model/year leaves.
   A native zero is retained as category-count evidence, not fabricated as a
   successful inventory query or a vehicle observation.
5. Keep positive tails, missing-year uncertainty, temporal count residuals and
   every incomplete/unattempted category explicit. The observed 2010–2027 range
   alone is not an exhaustive population guarantee.

The separate offline planner and its synthetic tests implement the context and
validation rules only. They do not demonstrate endpoint support, execute a new
capture, alter the running collector, or establish completeness.
The selected facet file itself does not contain endpoint or page-journal
provenance. Before live integration, its parent query report and charged request
journal must bind that provenance and reconcile the retained source hash.

## Budget and remaining risk

The opening count implies a minimum of 3,441 inventory pages at 24 vehicles per
page, before partition rounding, discovery, geographic checks or failures. The
next plan must count all of these against the same 6,000-request/six-hour ceiling.
Native per-year counts must determine feasibility; a historical estimate does
not authorize an extension.

Single-year model queries can still be large and unstable. The retained history
contains such categories with up to 498 vehicles. Success still requires a
complete reconciled baseline, a second complete run and the separately declared
seven-date reliability trial. This diagnostic sweep does not replace those
requirements or the original seven-date panel experiment.
