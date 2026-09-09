# Carvana collection evaluation

September 7, 2026 Eastern; capture timestamps below are September 8 UTC.

## Decision

Use Chrome's structured DOM records as the demonstrated reference collector. Keep the
normal reload and require identity reconciliation within the actual results container.
Use explicit query partitions and per-run completeness checks. No second production
collector is justified by the evidence. The standalone runner is implemented and mock-tested
but not live-validated because Playwright is absent.

**National daily collection is not ready.** Complete eastern captures took 32.909 and
34.678 seconds between adjacent pages, averaging 33.7935 seconds. A scenario of 70,000
listings at 21 vehicles per full page needs 3,334 pages and about 31.3 hours at this pace,
before partition setup and failures. The 70,000 input is illustrative, not a measured
Carvana total. These interactive capture intervals include tool/inspection overhead;
optimized standalone throughput has not been measured. The current reload-based runner needs at least two top-level operations
per additional page. One successful session cannot establish daily reliability.

The next transport milestone is to inspect actual inventory response bodies in an authorized
standalone browser session or obtain documented site access/allowlisting. Verify the payload
against this DOM reference before optimizing navigation. No guessed endpoint, hidden bulk
feed, proxy rotation, fingerprint modification or national crawl was implemented.

## Observed inventory and coverage

The cohort was Tesla Model 3, model year >=2025, Recommended sort. All 36 unique VINs,
model years, makes/models, exact miles and asking prices were present. Native card status
is missing where no selected status line appeared; absence is not interpreted as available.

| Capture | ZIP | UTC observation interval | Rows | Unique IDs | Coverage result |
|---|---|---|---:|---:|---|
| Eastern parent | 08542 | 02:18:23.539–02:18:56.448 | 36 | 36 | Complete bounded query |
| Western parent | 90210 | 02:19:40.403–02:20:22.674 | 36 | 35 | Blocked: repeated 4678219; absent 4677703 versus eastern set |
| Western 2026+ before scoping | 90210 | 02:21:04.151 | 9 structured / 21 cards | — | Blocked: 12 recommendation cards outside results |
| Western 2026+ scoped | 90210 | 02:22:13.584 | 9 | 9 | Complete single-page bounded query |
| Western 2025 | 90210 | 02:22:39.859–02:22:59.095 | 27 | 27 | Complete bounded query |
| Eastern repeat | 08542 | 02:23:52.948–02:24:27.626 | 36 | 36 | Same listing IDs and prices as first eastern capture |

The western partition union equals the 36-listing eastern set, with no overlap between
year partitions. This checks this bounded population only. It does not establish national
coverage, all delivery eligibility or a census at one instant. ZIPs changed ordering and
shipping/delivery wording. Equal count alone would have hidden the western paging gap.

Listing 4609978 later displayed `On hold`; its earlier capture lacked that selected line.
Schema availability remained `InStock`. Preserve both facts without turning the change
into a completed sale or assigning an exact event time.

## Approach comparison

The notebook displays one comparison DataFrame from `method_comparison.json`, including
method, test status, source/query/ZIP/interval, requests, timing/bytes where measured,
identity and field completeness, reference agreement, pagination, challenges, cost,
maintenance and limitations. Unknown measurements remain missing.

| Approach | Demonstrated result | Limitation |
|---|---|---|
| Chrome DOM | Complete bounded captures and matching partition union | Slow reloads; pagination drift; recommendations require scoping |
| Standalone Playwright | Offline mocks pass | Missing `playwright>=1.50,<2`; isolated Chrome access untested |
| Browser batch responses | Tooling unavailable | Current supported Chrome tools do not expose response bodies; no verified endpoint |
| Direct HTTP | One request returned 403 and `cf-mitigated: challenge` | Stopped; no usable data, retry or credential export |
| Embedded application data | 52 stream blocks, 695,376 decoded characters; all 9 inspected schema VINs present | Matching fragments contained schema data; no independent coverage/field advantage proven |
| Documented access/vendor | Public Clarity product/data pages and supplied workbook inspected | No site feed, vendor API entitlement, collection method or sales rules established |
| 2Captcha | Mock trial covers polling, failures, reservation caps, redaction and token-with-failed-access | Paid path disabled; no supported task payload, enforceable price cap or concrete approval |

The HTTP response was 6,078 bytes and took 0.375 seconds. Its title was `Just a moment...`.
Only sanitized headers/status, hash and title were retained. Cloudflare documents
[`cf-mitigated: challenge`](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/)
as a challenge indicator. Other 403s remain generic access denials; 429 stops the shared
budget across queries. No method is switched to work around a rate-limit window.

[2Captcha's Turnstile documentation](https://2captcha.com/api-docs/cloudflare-turnstile)
distinguishes standalone widgets from Cloudflare challenge tasks. This task did not extract
a verified usable Carvana task payload. The documented
[createTask contract](https://2captcha.com/api-docs/create-task) does not establish a per-task
maximum-dollar parameter. Local mock reservations cannot cap an unknown provider charge,
so `live=True` fails closed. No key was read; the previously exposed key was not reused.
There were zero paid tasks and **$0 spent**. Solver completion rate, recovery success,
pages before rechallenge, and cost per usable recovered capture remain unmeasured.

## Retention and live scope

The experiment used 21 conservatively counted browser navigations/filter updates/reloads
and one direct HTTP request: **22 total**, zero detail pages, sequential, at least three
seconds between top-level operations. The browser experiment timer was 475 seconds;
initial tool/tab setup adds roughly one minute. Collection stayed below 20 minutes.
Subresource volume and original browser response bytes were not measurable with these tools.
The original ZIP 08542 was restored. No account, subscription or balance changes occurred.

All new raw evidence and the isolated SQLite database are under
`vehicle/data/experiments/20260907_approaches/`, ignored by Git. `evidence.json` contains
10 selected-field DOM projections, 153 observation rows and 36 unique listings. These are
transcriptions of public Chrome output, explicitly distinguished from original HTTP bytes.
The source packet retains requested/actual ZIP, sort, query, page, clock and native status,
shipping and delivery fields. Each page has an immutable retained-file reference and hash.

The existing storage schema was reused. It contains 129 normalized rows and 10 capture
attempts. The duplicate western page and unscoped recommendation capture remain failed
attempts with their public evidence retained; their rows were not silently ingested as
successful observations. Coverage analysis reads those retained captures to expose the
failure. Existing databases and raw files were not replayed or modified.

## Vendor workbook audit

Source: `CVNA_Q3_2026_Summary_Free.xlsx`, SHA-256
`e43372c2f4409b5ea3ff6e53d891931a2f1524d0358b0bae529e884f97d9abef`.
It remains unchanged. The audit reads formulas and cached values separately.

- Sales (C), orders (O), listings (S) and inventory (BL): 39 numeric dates, July 1–August 8.
- Prior-year sales (D): 92 numeric dates through September 30, including noninteger values.
- Trailing-week listings (T): 68 numeric dates through September 6; not equivalent to
  current daily metric coverage.
- 829 formulas have no cached results. Preserve the formulas rather than treating this
  as 829 missing underlying inputs. Text tokens: 116 `Null`, 15 `nan`, five `nan%`.
- O7=2,891 and Q7=3,264 imply −11.427696%, while R7 supplies the text `−99.72%`.
  P7=16,530 is preserved separately; it is not the stated comparator denominator.
- F2=`=C2+13225` includes a carry-in not independently supported within this quarter.
- A22 reports delayed collection and sales adjustments while orders/listings were not
  adjustable. It refers to January dates on a July row, an unresolved source ambiguity.
  A35 says the run started about 2.5 hours late and was slightly overstated.

The vendor is not ground truth. [Clarity's CVNA page](https://www.clarity-markets.com/data/CVNA)
and [data catalog](https://www.clarity-markets.com/data) do not establish the collection
method or compatible sales/population definitions used in this file. The supplied daily
metrics end before our September 7 observations; no same-date comparison is asserted.
The notebook keeps native values, formats/comments and independent calculations visible.

## Next manual reliability test

Do not schedule this automatically. After the optional dependency is deliberately installed,
run the small plan in a new folder twice on the first day to validate standalone access,
then manually at consistent times for seven days. Keep the current shared caps. Record
each run's start/end, expected and attempted queries, parsed/stored rows, unique VIN/IDs,
displayed totals, failure reasons, challenge frequency and hashes. Include every failed
day in the coverage report; do not compare it as if it were complete.

Track observed-both, first-observed, not-observed and reappearing listings. Review native
status changes and price changes separately. Capture times bound observations, not event
times. Inspect the raw source for unusual transitions. Seven days is a proposed transport
test, not enough to validate sales conversion or quarterly estimates.

Before broader inventory estimates, establish a reviewed universe/partition manifest,
exhaustive parent/child set reconciliation, boundary behavior, deduplicated ZIP coverage,
and complete repeat captures within the desired daily window. Measure end-to-end throughput
on complete batches before increasing volume. Smaller partitions improve enumeration
stability but add setup overhead; they do not by themselves solve access or runtime.

## Final offline verification

- Full suite after review fixes: **434 passed, 9 existing Matplotlib warnings, 116.96 seconds**.
  This includes 71 vehicle tests and the existing approval-binding and MA/MI regressions.
- Guarded active notebook execution: **12 notebooks / 78 code cells passed**, including
  the expanded vehicle notebook. Network, CSV exports and writable SQLite are blocked.
- The actual optional vehicle comparison cell was tested using temporary databases for
  valid, reversed, changed-sort and VIN-conflict cases. An invalid MA approval binding
  still produces its tested, understandable BLOCKED result.
- The initial vehicle baseline was **26 passed in 0.87 seconds**. New failing regressions
  reproduced duplicate visible cards, pacing/diagnostic/redaction gaps, dropped requested
  filters and cross-partition VIN conflicts before their repairs.
- The dry-run plan opened no browser/database and created no experiment directory.
- All **1,780 pre-existing gaming files** and **four prior vehicle data files** are
  byte-identical to preflight, including all existing databases/raw files and approval
  bindings. The supplied workbook hash is unchanged.
- Notebook 00 is unchanged. Notebook 10's original cells 0–5 and metadata are preserved;
  its comparison cell was deliberately tightened and the new walkthrough appended.
- HEAD and branch remain the supplied values. `git diff --check` and an additional
  whitespace check of the untracked vehicle source pass. No commit or push occurred.
- A separate read-only reviewer found three actionable issues. All were reproduced or
  covered in focused tests, fixed, and rechecked with no remaining actionable findings
  in those repaired areas. All changes remain local and uncommitted.
