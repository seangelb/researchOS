"""Preview or run at most six anonymous public detail GETs in a new experiment.

No cookies/authentication, redirects, retries, browser fallback, or database import.
Reads a frozen plan; stops on the first HTTP, challenge, identity or parsing failure.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from vehicle_tracker.clarity_experiment import capture_public_html
from vehicle_tracker.events import _aware
from vehicle_tracker.sale_pilot import _url_id


def validate_plan(plan):
    prepared = _aware(plan['prepared_at'])
    if _aware(plan['as_of']) > prepared or _aware(plan['expires_at']) <= prepared:
        raise ValueError('Plan cutoff/preparation/expiry clocks are inconsistent')
    pages = plan['pages']
    if not 1 <= len(pages) <= 6:
        raise ValueError('Plan must contain one to six pages')
    if len({(p['retailer'], p['vin']) for p in pages}) != len(pages):
        raise ValueError('Duplicate planned VIN')
    if len({p['listing_id'] for p in pages}) != len(pages):
        raise ValueError('Duplicate planned listing')
    for p in pages:
        if (p['retailer'] != 'carvana' or not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', p['vin'])
                or _url_id(p['url']) != p['listing_id'] or not p['selection_reason'].strip()):
            raise ValueError('Invalid planned identity or source URL')
    return pages


def run_probe(plan, destination, *, get, now=None, sleep=time.sleep):
    """Explicit live path; injectable transport/clock make stop behavior testable."""
    now = now or (lambda: datetime.now(timezone.utc))
    pages = validate_plan(plan)
    if not _aware(plan['prepared_at']) <= _aware(now()) < _aware(plan['expires_at']):
        raise ValueError('Plan window has not started or expired; prepare a new plan')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    report = dict(started_at=_aware(now()).isoformat(), plan=plan, requests=0,
                  status='running', attempts=[], capture_method='anonymous_http_html_rsc_projection')
    path = destination / 'run.json'
    def save():
        path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    save()
    for index, page in enumerate(pages):
        if index:
            sleep(15)
        if _aware(now()) >= _aware(plan['expires_at']):
            report.update(status='stopped', stop_reason='Plan window expired')
            break
        attempt = dict(expected={k: page[k] for k in ['retailer', 'vin', 'listing_id']},
                       url=page['url'], selection_reason=page['selection_reason'],
                       started_at=_aware(now()).isoformat())
        report['attempts'].append(attempt)
        report['requests'] += 1
        save()  # An interrupted request remains an attempted, unresolved request.
        try:
            response = get(page['url'], timeout=25, allow_redirects=False)
            body = response.content
            attempt.update(checked_at=_aware(now()).isoformat(), http_status=response.status_code,
                           response_bytes=len(body), response_sha256=hashlib.sha256(body).hexdigest(),
                           response_retention='Only selected public vehicle projection is retained')
            if response.status_code != 200:
                raise ValueError('HTTP ' + str(response.status_code) + '; no retry or fallback')
            html = body.decode(response.encoding or 'utf-8', errors='replace')
            if len(body) > 8_000_000:
                raise ValueError('Response exceeds the bounded parser size')
            if re.search(r'<title[^>]*>[^<]*(access denied|just a moment|captcha|verify)', html, re.I):
                raise ValueError('Access challenge; no retry or fallback')
            capture = capture_public_html(html, attempt['expected'], checked_at=attempt['checked_at'], final_url=response.url)
            target = destination / f'capture_{index+1:02}.json'
            payload = (json.dumps(capture, indent=2) + '\n').encode()
            target.write_bytes(payload)
            attempt.update(capture_file=target.name, capture_sha256=hashlib.sha256(payload).hexdigest(),
                           available_at=_aware(now()).isoformat(), outcome='matched')
        except (requests.RequestException, ValueError, RecursionError) as error:
            attempt.update(outcome='unresolved', error=str(error))
            report.update(status='stopped', stop_reason=str(error))
            break
        finally:
            save()
    if report['status'] == 'running':
        report['status'] = 'complete'
    report['available_at'] = _aware(now()).isoformat()
    save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding='utf-8'))
    validate_plan(plan)
    print(json.dumps({'plan': plan, 'destination': str(args.destination.resolve()), 'live': args.live}, indent=2))
    if not args.live:
        return 0
    with requests.Session() as session:
        session.trust_env = False
        def get(url, **kwargs):
            session.cookies.clear()
            try:
                return session.get(url, **kwargs)
            finally:
                session.cookies.clear()
        report = run_probe(plan, args.destination, get=get)
    print(json.dumps({'status': report['status'], 'requests': report['requests'], 'stop_reason': report.get('stop_reason')}))
    return 0 if report['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
