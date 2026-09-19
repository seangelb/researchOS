# Goal: diagnose and resolve Carvana HTTP 403

This is a focused subgoal of the unfinished Carvana coverage goal. The user
requested it on September 19: "okay review again. make a goal to identify why
we are getting error 403 and solve it". The app refused a second goal because
this thread already has an unfinished goal. Neither goal is marked complete.

## Acceptance criteria

1. Compare actual failed requests with retained successful requests and identify
   what the evidence supports, without inventing a provider, IP ban or cause.
2. Preserve the diagnostic information needed to distinguish a challenge,
   server/application refusal and rate limit without saving credentials or
   arbitrary response text. Retain all old evidence unchanged.
3. Fix a demonstrated request defect if one exists. Otherwise establish a
   permitted access route with the operator; no transport or fingerprint change
   intended to circumvent a refusal.
4. Verify that route with one separately scoped request, retaining response,
   source clocks and identity checks. A successful test must not resume an old
   failed cycle, change the original seven-date denominator or start another
   September 19 daily cycle. Offline tests alone do not establish restored access.

## Findings from retained evidence

| Evidence | Result | What it establishes |
| --- | --- | --- |
| September 17 first query, Hyundai/2023/Santa Fe, page 1 | HTTP 200 | The same request previously worked |
| Last retained API success, September 18 00:17:48 UTC | HTTP 200, Nissan/2024/Sentra page 3 | Access still worked at this time |
| Next request, 00:17:50 UTC | ConnectionError without a response | Transport outcome unknown; not evidence of HTTP 403 |
| September 18 14:32:00 UTC, broad facet opening | HTTP 403, 4,544 bytes | The server refused this request |
| September 19 11:09:55 UTC, Hyundai/2023/Santa Fe page 1 | HTTP 403, 4,544 bytes | Refusal persisted on a previously successful query |
| September 19 11:32:40 UTC, same request, after the user disconnected their VPN | HTTP 200, 24 valid VINs | Access worked for this isolated check on the changed network path |

The first September 17 and September 19 query payloads match exactly: filters,
ZIP 08542, page 1, page size 24, native MostPopular and no location prefilter.
The retained search/normalization code hashes match too. Review found no change
to the request or session implementation that explains the new response.
Historical package/TLS/network identity was not recorded, so unchanged repository
code cannot rule out every environment change.

The failure occurs before projection or VIN normalization. It is therefore not
caused by a notebook calculation or a pagination-repeat detector. A one-request
403 is not exhaustion of the local 600-request budget. We cannot infer a remote
rate limit from this status alone.

Both recent error bodies were discarded under the old public-inventory-only
retention contract. Their different SHA-256 hashes and equal byte lengths do
not identify their content. September 19's retained raw JSON is a failure
projection, not the response body. Missing headers/body cannot be reconstructed.

Older website/detail-page failures did retain Cloudflare challenge indicators,
but they concern different requests and dates. They do not prove that Cloudflare
generated these recent API refusals. The September 19 collector did not trigger
its exact `cf-mitigated: challenge` check; that still does not identify a different
provider or a particular security rule.

Cloudflare documents several possible causes of 403 and a specific challenge
header. These references guide diagnosis; they do not identify our response:
[403 documentation](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-403/),
[challenge response detection](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/).

## Diagnostic repair and verified access check

The narrow repair adds selected HTTP diagnostics to future response-evidence
records. It does not change outgoing requests, cookies, retries, stop conditions,
inventory parsing or old records. Only allowlisted header values and fixed body
marker labels are retained; arbitrary HTML, credentials, cookies, IP addresses
and URLs remain omitted. Marker matches are observations, not proof of origin.

The user confirmed **"we are given permission for automated collection"**. This
review accepts that statement. After code review, one separately recorded
diagnostic request used the unchanged known payload and transport. It returned
HTTP 200 with valid JSON and 24 VINs at 11:32:40 UTC. The response reported a
native query total of 235; the first page is a sample, not complete enumeration.

The user then confirmed that the network/VPN changed between the failed and
successful checks, specifically **"VPN was disconnected"**. The VPN network path
is therefore the leading explanation for the difference, rather than a request
or parser regression. This is a before/after observation with a user-reported
network change, not a controlled comparison: time also changed, and we did not
record historical network identity or obtain the server's access-policy logs.
The particular rule, IP reputation, origin policy or other mechanism remains
unknown. No reconnect/retry experiment is needed to preserve this finding.

The successful response identifies Cloudflare and retains request identifier
`a3d845634b614e6f-EWR`. It contains no recognized challenge header. This identifies
the successful response's server family, not the cause of the earlier refusals.
The logging repair runs after the response arrives; it did not cause HTTP 200.

The original diagnostic process stopped at its one-request budget before page 2.
Its nonzero exit is the expected budget stop, not another HTTP error. Exactly one
request was charged, no request is pending, and all 24 observations remain in the
isolated diagnostic destination. Nothing was imported into the daily database.

The working configuration verified here is the user's permitted collection
without their VPN. Future separately scoped collection should record that
network condition, preserve the same request pacing, and stop on any new access
refusal. One successful check does not establish sustained access or national
coverage. The September 19 daily attempt remains failed, consumes that date's
attempt and must not be replaced; previous stop records remain unchanged.

Retained diagnostic: `vehicle/data/experiments/access_diagnostic_20260919`.
Plan: `vehicle/data/experiments/access_review_20260919/diagnostic_plan.json`,
SHA-256 `d7c2cd414b7fec898d7951164ef0654253328f055f90f49f0ae6cda01a9a7651`.
The original goal record is preserved; a separate outcome records the subsequent
permission, live result and user-reported network change.

## Verification

The full offline suite passed: 1,781 tests. All nine vehicle notebooks and the
three active gaming notebooks passed the read-only runner. Historical gaming
notebooks 91-93 remain blocked by the missing original `gaming/data/gaming.sqlite`;
this access repair neither recreates that evidence nor changes their approvals.
All 12,866 pre-existing protected evidence/configuration files matched the
pre-review SHA-256 inventory. Concurrent saves to vehicle notebooks 10, 11 and 23
contain unchanged cell sources and were preserved separately from this code fix.

If a server policy continues to refuse the permitted request, use its diagnostic
identifier to ask the party that granted access which endpoint or configuration
is required. A draft inquiry is below; it has not been sent.

> We have permission for automated collection for our inventory research workflow.
> Our search API request returned HTTP 403 at 11:09:55 UTC on September 19, 2026,
> then HTTP 200 at 11:32:40 UTC after our VPN was disconnected, with the same
> payload and client transport. The successful response's Cloudflare request
> identifier is a3d845634b614e6f-EWR; the failed response's identifier was not
> retained. Could you
> confirm the approved endpoint, required authentication and any access-policy
> configuration needed for that permission? Our prior
> bounded trial used 101 fixed search queries, at most 600 sequential requests
> per day with at least three seconds between starts, and stopped on access
> refusals. We can provide request times and safe diagnostic identifiers if
> useful. We are not requesting purchase or account access.

Access was verified for one request after VPN disconnection, and future failures
now retain safer, more useful diagnostics. The exact server-side reason and
sustained access remain unverified; the broader coverage goal is unfinished.
