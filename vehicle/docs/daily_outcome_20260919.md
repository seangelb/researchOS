# September 19 daily attempt: HTTP 403

The user instructed **"run todays run"** after the September 18 access failure
was reported. This authorized one fresh daily attempt that morning using the
unchanged 101-query configuration, ZIP 08542, location_filter=false, 600-request
ceiling, one-hour window and three-second minimum spacing. The
[dated authorization](../data/experiments/research_cycle_resume_20260913/daily_manual_20260919.json)
records the exception to the prior hold and planned 20:00 start. Earlier failed
attempts and stop records were preserved. No browser or facet run was authorized.

The first request, Hyundai/2023/Santa Fe page 1, started at
`2026-09-19T11:09:55.594515+00:00` (**7:09:55 a.m. New York**) and returned
HTTP 403 at `11:09:55.988963+00:00`. The original process stopped and exited 1.
There were **1 charged request, 0 completed queries, 1 blocked query and 100
unattempted queries**. No VIN observations were collected. The response outcome
is known; the cycle has stopped=true and pending_request=false.

The [query report](../data/experiments/mvp_completion_20260912/scale_acceptance_20260912/operating_validation/captures/2026-09-19/attempt_0001/q001_hyundai_2023_santa_fe/run_report.json)
retains HTTP status, request/response/evidence clocks and the 4,544-byte response
content hash `e87511420344fcfc7481c7aa6c5f0ad478d780aae1d66ff9772c9d03baf6015c`.
The access-failure body is unavailable; this does not establish why access was
denied. Its separate failure projection is retained and hash-verified.

The original cycle was registered and exported as partial. Source and SQLite
replay agree on zero collected VINs. **Zero observed VINs is not zero inventory**:
inventory totals, changes and sales estimates remain unavailable. The report
retains all seven September 13-19 dates, including missing September 15 and 18.
Only September 14 is a complete original cycle. September 19's morning timing
must remain explicit in the final comparison with the planned evening schedule.

The [new access-stop record](../data/experiments/research_cycle_resume_20260913/access_stop_20260919.json)
applies to all live Carvana work. The one-time daily authorization is consumed;
**the September 19 evening wake must only inspect this original attempt**.
There is no retry, replacement, browser fallback or automatic clearance. Offline
access review and the September 21 final audit remain outstanding; the broader
research goal is incomplete. The original 32-VIN study, Tesla source bytes,
gaming approvals and unknown operator labor are preserved.

Reconciliation, query coverage, preservation manifests and restore-verified
backups are retained at
`C:\Users\Sean\Documents\ChatGPT\ResearchOS\carvana_daily_20260919`.
