"""Bounded sequential Chrome collection. No automatic retries or scheduler."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
import re
import time

import pandas as pd

from vehicle_tracker.carvana import CAPTURE_JS, parse_capture
from vehicle_tracker.coverage import query_context, run_coverage
from vehicle_tracker.storage import retain_capture, store_capture


class CollectionStopped(ValueError):
    """Our own safe diagnostic; third-party exception messages are never logged."""


@dataclass
class NavigationBudget:
    """One shared budget across queries, including reloads and ZIP updates."""
    max_requests: int = 120
    max_seconds: float = 1200
    pause_seconds: float = 3
    requests: int = field(default=0, init=False)
    stopped: bool = field(default=False, init=False)
    started: float = field(default_factory=lambda: time.monotonic(), init=False)
    last_request: float | None = field(default=None, init=False)

    def __post_init__(self):
        if not 1 <= self.max_requests <= 600 or not 0 < self.max_seconds <= 3600 or self.pause_seconds < 3:
            raise ValueError('Use <=600 requests, <=3600 seconds and at least three seconds spacing')

    def stop(self):
        self.stopped = True

    def response_received(self):
        """In-memory budgets have no response checkpoint."""

    def request_started(self):
        """Anchor spacing to the actual send after any durable reservation writes."""
        self.last_request = time.monotonic()

    def timeout_ms(self, limit=45000):
        remaining = self.max_seconds - (time.monotonic() - self.started)
        if remaining <= 0 or self.stopped:
            raise CollectionStopped('Collection stopped: time/access budget exhausted')
        return max(1, min(limit, int(remaining * 1000)))

    def before_navigation(self):
        self.timeout_ms()
        if self.requests >= self.max_requests:
            raise CollectionStopped('Request budget exhausted')
        if self.last_request is not None:
            time.sleep(max(0, self.pause_seconds - (time.monotonic() - self.last_request)))
        self.timeout_ms()
        self.requests += 1  # Reserve before sending; uncertain failures still count.
        self.last_request = time.monotonic()


def check_response(response, capture, budget):
    """Record a small header allowlist; a generic 403 is not a CAPTCHA diagnosis."""
    if response is None:
        raise CollectionStopped('missing_response')
    headers = response.headers if isinstance(response.headers, dict) else {}
    status = response.status
    mitigated = headers.get('cf-mitigated') == 'challenge'
    retry = headers.get('retry-after', '')
    capture.update(http_status=status, cf_mitigated='challenge' if mitigated else None,
                   retry_after_seconds=int(retry) if str(retry).isdigit() else None)
    reason = ('rate_limited' if status == 429 else 'cloudflare_challenge' if mitigated else
              'access_denied' if status in (401, 403) else 'server_failure' if status >= 500 else
              'http_failure' if status >= 400 else None)
    if reason:
        budget.stopped = True
        raise CollectionStopped(reason)


def collect_pages(page, *, source_url: str, raw_directory: Path, database: Path,
                  zip_code: str = '08542', max_pages: int = 2, pause_seconds: float = 3,
                  budget: NavigationBudget | None = None) -> pd.DataFrame:
    """Collect one query; return page outcomes with a separately checked run summary.

    Per-page database coverage remains unverified. The returned query_complete field
    requires the entire run to reconcile; partial stored pages never become a census.
    """
    url = urlsplit(source_url)
    if url.scheme != 'https' or url.netloc != 'www.carvana.com' or not re.match(r'^/cars(?:/|$)', url.path):
        raise ValueError('Choose a public Carvana cars URL')
    if not 1 <= max_pages <= 20 or pause_seconds < 3:
        raise ValueError('Use 1–20 pages and at least three seconds between navigations')
    if not re.fullmatch(r'\d{5}', zip_code):
        raise ValueError('Choose an explicit five-digit ZIP query context')
    budget = budget or NavigationBudget(pause_seconds=pause_seconds)
    run_id = uuid4().hex
    attempts, captures, seen = [], [], set()
    for number in range(1, max_pages + 1):
        capture = dict(page_url=source_url, captured_at_utc=datetime.now(timezone.utc).isoformat(),
                       records=[], requested_zip=zip_code, requested_query_url=source_url, page_number=number)
        raw_file = None
        try:
            budget.before_navigation()
            if number == 1:
                response = page.goto(source_url, wait_until='domcontentloaded', timeout=budget.timeout_ms())
                check_response(response, capture, budget)
                page.get_by_role('button', name=re.compile(r'^\d{5}$')).click(timeout=budget.timeout_ms(20000))
                page.get_by_role('textbox', name='ZIP code', exact=True).fill(zip_code, timeout=budget.timeout_ms(20000))
                budget.before_navigation()
                page.get_by_role('button', name='Update', exact=True).click(timeout=budget.timeout_ms(20000))
                page.get_by_role('button', name=zip_code, exact=True).wait_for(timeout=budget.timeout_ms(20000))
            else:
                previous_url = page.url
                page.get_by_role('button', name='Go to next page', exact=True).click(timeout=budget.timeout_ms(15000))
                page.wait_for_function('old => location.href !== old', arg=previous_url, timeout=budget.timeout_ms(20000))
            # Live evidence: client navigation can leave stale JSON-LD until a reload.
            budget.before_navigation()
            response = page.reload(wait_until='domcontentloaded', timeout=budget.timeout_ms())
            check_response(response, capture, budget)
            page.locator('#results-section a[data-testid="tile-link"]').first.wait_for(timeout=budget.timeout_ms(20000))
            rendered = page.evaluate(CAPTURE_JS)
            capture.update(rendered, requested_zip=zip_code, page_number=number)
            raw_file = retain_capture(capture, raw_directory)
            if capture.get('zip_code') != zip_code:
                raise CollectionStopped('Displayed ZIP differs from requested query context')
            if query_context(capture)[0] != query_context(dict(capture, page_url=source_url))[0]:
                raise CollectionStopped('Rendered query differs from requested filters')
            frame = parse_capture(capture)
            current = set(frame.listing_id)
            if current & seen:
                raise CollectionStopped('Repeated listings across pages; coverage unstable')
            stored = store_capture(database, run_id=run_id, page_number=number, raw_file=raw_file)
            seen.update(current)
            captures.append(capture)
            attempts.append(dict(run_id=run_id, page=number, status='parsed', stored_rows=stored,
                                 coverage='unverified', reason=None, retained_source=str(raw_file)))
            if capture.get('has_next_page') is not True:
                break
        except Exception as exc:
            # External errors may contain request credentials. Keep only their type.
            reason = str(exc) if isinstance(exc, CollectionStopped) else f'{type(exc).__name__}: navigation or record validation failed'
            raw_file = raw_file or retain_capture(capture, raw_directory)
            store_capture(database, run_id=run_id, page_number=number, raw_file=raw_file, error=reason)
            attempts.append(dict(run_id=run_id, page=number, status='failed', stored_rows=0,
                                 coverage='unverified', reason=reason, retained_source=str(raw_file)))
            break
    summary = run_coverage(captures)
    if any(a['status'] == 'failed' for a in attempts):
        summary.update(query_complete=False, complete_query_count=None)
    result = pd.DataFrame(attempts)
    for key, value in summary.items():
        result['coverage_reason' if key == 'reason' else key] = value
    result.attrs['coverage'] = summary
    result.attrs['top_level_requests_used'] = budget.requests
    return result
