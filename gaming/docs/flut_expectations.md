# Quarterly expectations and eventual outcomes

Open [notebook 96](../notebooks/96_flut_expectations_review.ipynb) after the current-data and quarterly scorecard notebooks. Its default run is offline and read-only. State amounts remain USD, company revenue is USD millions, and incomplete state comparisons remain visible.

## What is supplied

`config/flut_company_reference.json` contains official US segment actuals for Q3 2025 and Q2 2025/2026, plus a **derived management reference** for Q3 2026. The $1,480m revenue midpoint is $7,400m FY US guidance multiplied by approximately 20% Q3 phasing. The $1,425m-$1,535m range applies that phasing to the FY endpoints; it is neither a separately disclosed Q3 range nor a statistical interval. US Q3 adjusted EBITDA guidance is approximately breakeven and is left qualitative.

Sources: [Q2 2026 release](https://flutter.com/media/g23an0ae/flutter-q2-2026-earnings-release.pdf), [Q3 2025 release](https://flutter.com/media/ze1o1pxl/q3-2025-earnings-release-final.pdf). Both PDFs are retained under `data/raw/FLUT/2026-09-12/` with content-hash filenames. Loading the reference verifies those bytes. Publication dates are known from release headers; exact historical public-release timestamps are not invented. September captures cannot support an August information cutoff.

The separate legacy approved baseline contains Group Q3 anchors; this module does not import or change them. It does not supply an approved FanDuel product forecast or a national state-data mapping. The earlier allocation of Q3 US revenue using Q2 product mix is not used as a house forecast here.

## Explicit inputs and calculations

`analyst_scenario` requires all four full-quarter, full-reported-US inputs and a rationale:

- Sportsbook handle, USD millions.
- Sportsbook net revenue margin as a fraction. `0.087` means 8.7%; `8.7` is rejected.
- iGaming revenue, USD millions.
- Other revenue, USD millions.

Total revenue is handle × net margin + iGaming revenue + other revenue. Missing assumptions are rejected; explicit zero remains zero. These inputs cannot be filled automatically from the native state panel. Geography, brands, quarter completion, seasonality, promotional deductions, and reporting timing require review. Gaming taxes discussed within issuer cost of sales are not subtracted again from company net revenue. A scenario remains exploratory and unapproved.

## Dated snapshots

`load_company_reference(path, project_root=...)` verifies retained source hashes and management-phasing arithmetic. `build_expectations_review(quarterly, reference, as_of=..., data_capture_at=..., database_path=...)` reloads the retained configuration and rejects a changed supplied document. It retains the quarterly scorecard alongside the separate reference and binds the database and source files. The supplied capture clock must equal the completed run's `run_manifest.finished_at`, and both `run_manifest.json` and `validation.json` must bind the current database hash. The existing snapshot validator checks the actual SQLite schema, source bytes, reporting dates and row retrieval clocks against that capture time. All timestamps require timezones; later captures and future reporting months fail. Nonempty SQLite WAL and journal files fail because their observations are not bound by the database-file hash.

`freeze_expectation(review, destination, scenario=None)` creates only a new directory, containing `expectation.json` and its SHA256 manifest. It checks for changed review contents, database/source bytes or capture receipts, revalidates the capture, uses the real current write time, and refuses overwrite. This is a local write-once workflow, not a cryptographically trusted timestamp or protection against deliberate simultaneous rewriting of the snapshot and manifest. Back up both files and raw evidence independently. No approval record is created.

The notebook has `export_snapshot=False` and `snapshot_destination=None` by default. Set an absolute new directory deliberately to save a vintage. Notebook execution, source verification, and passing tests do not create analyst approval.

## Actuals and evaluation

`evaluate_expectation(snapshot_path, actual=None, as_of=..., source_root=...)` is read-only. Missing actuals return `pending_actual`; a sourced actual with a missing value returns `unknown_actual_value`. Otherwise the actual must contain these keys:

```text
period: YYYYQn, exactly matching the frozen snapshot
scope: flutter_us_segment
metric: revenue
unit: USD_millions
value: a finite number or null
published_at: exact public-release timestamp, with timezone
captured_at: timestamp the retained actual was captured, with timezone
source_url: original HTTPS URL
source_file: retained path relative to source_root
source_sha256: hash of those retained bytes
```

The publication must follow the reported quarter, and publication/capture must precede the evaluation cutoff. The snapshot's real write time and information cutoff must precede the public results release. A snapshot made after the result cannot be evaluated as prospective, even if its requested information cutoff was earlier. Wrong period, scope, metric or units fail rather than producing a misleading comparison. The actual source bytes and snapshot checksum are verified.

Signed error = expectation minus actual; absolute error = absolute signed error. Percentage error is missing for an actual of zero. Management-reference and exploratory-scenario errors retain separate labels. State evidence has no mechanical company adjustment, so a management-reference comparison cannot be presented as evidence of proprietary state-data forecasting skill. No performance claim is enabled by a single case. Freeze expectations before results, append the eventual official result, and assess usefulness over multiple genuinely prospective, comparable quarters.
