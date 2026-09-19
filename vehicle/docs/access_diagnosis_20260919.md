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

## Repair and remaining access decision

The narrow repair adds selected HTTP diagnostics to future response-evidence
records. It does not change outgoing requests, cookies, retries, stop conditions,
inventory parsing or old records. Only allowlisted header values and fixed body
marker labels are retained; arbitrary HTML, credentials, cookies, IP addresses
and URLs remain omitted. Marker matches are observations, not proof of origin.

The [Carvana terms](https://www.carvana.com/terms-of-use), effective October 17,
2025 and checked September 19, 2026, require an agreement for automated
collection in section II.B. The next access decision depends on whether the
user already has that permission or a data/API agreement. The user subsequently
confirmed **"we are given permission for automated collection"**. This review
accepts that statement; no independent verification of a permission document is
claimed. One isolated diagnostic request using the unchanged known payload and
transport will follow code review. Daily/browser/facet collection remains stopped.

If a server policy continues to refuse the permitted request, use its diagnostic
identifier to ask the party that granted access which endpoint or configuration
is required. A draft inquiry is below; it has not been sent.

> We have permission for automated collection for our inventory research workflow.
> Previously successful requests to your search API now return HTTP 403. Could you
> confirm the approved endpoint, required authentication and any access-policy
> configuration needed for that permission? Our prior
> bounded trial used 101 fixed search queries, at most 600 sequential requests
> per day with at least three seconds between starts, and stopped on access
> refusals. We can provide request times and safe diagnostic identifiers if
> useful. We are not requesting purchase or account access.

The technical cause remains unresolved beyond an observed access refusal.
The evidence-retention repair is not a claim that access has been restored.
