"""Carvana public-page capture parsing. Pure functions: no network or writes."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import pandas as pd

# Read only the rendered document. No cookies, tokens, private endpoints or JS execution
# from the page's script contents. schema.org values remain distinct from card text.
CAPTURE_JS = r'''() => {
  const results = document.querySelector('#results-section');
  const links = Array.from(results?.querySelectorAll('a[data-testid="tile-link"]') || []);
  const allLinks = document.querySelectorAll('a[data-testid="tile-link"]');
  const next = document.querySelector('button[aria-label="Go to next page"]');
  const buttons = Array.from(document.querySelectorAll('button'));
  const total = document.body.innerText.match(/([\d,]+)\s+cars\b/i);
  return {
    page_url: location.href, captured_at_utc: new Date().toISOString(),
    capture_method: 'browser_dom', sample_only: false,
    visible_listing_count: links.length,
    results_container_present: Boolean(results),
    excluded_recommendation_count: allLinks.length - links.length,
    sort: buttons.find(b => /Sort by/.test(b.innerText))?.innerText.trim() || null,
    reported_total_text: total ? total[0] : null,
    zip_code: buttons.find(b => /^\d{5}$/.test(b.innerText.trim()))?.innerText.trim() || null,
    has_next_page: next ? !next.disabled : null,
    cards: links.map(a => ({url: a.href, text: a.parentElement.innerText})),
    records: Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
      .map(s => JSON.parse(s.textContent)).filter(x => x['@type'] === 'Vehicle')
  };
}'''


def listing_id(url: str) -> str:
    """Accept only a public Carvana vehicle URL, never a guessed identity."""
    parsed = urlsplit(url)
    match = re.fullmatch(r'/vehicle/(\d+)/?', parsed.path)
    if parsed.scheme != 'https' or parsed.hostname != 'www.carvana.com' or not match:
        raise ValueError('Invalid public Carvana listing URL')
    return match[1]


def parse_capture(capture: dict) -> pd.DataFrame:
    """One row per listing: USD asking price, odometer miles, UTC observation time.

    Missing optional fields stay missing. Native schema availability and card text
    are evidence, not confirmed sales. A sample is never a complete inventory.
    """
    source = urlsplit(capture['page_url'])
    if source.scheme != 'https' or source.hostname != 'www.carvana.com' or not source.path.startswith('/cars'):
        raise ValueError('Expected a public Carvana cars page')
    timestamp = pd.Timestamp(capture['captured_at_utc'])
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError('Capture requires a timezone-aware observation timestamp')
    if capture.get('capture_method') == 'carvana_search_projection':
        from vehicle_tracker.search import parse_search_capture
        return parse_search_capture(capture)
    if capture.get('capture_method') == 'browser_dom_projection':
        return parse_projection(capture)
    records = capture.get('records', [])
    if not records:
        raise ValueError('No Vehicle records; blocked, empty or changed page is not zero inventory')
    cards = {listing_id(c['url']): c['text'] for c in capture.get('cards', [])}
    if len(cards) != len(capture.get('cards', [])):
        raise ValueError('Duplicate visible listing cards')
    rows = []
    for record in records:
        if record.get('@type') != 'Vehicle':
            raise ValueError('Unexpected structured record type')
        offer = record.get('offers', {})
        identity = listing_id(offer.get('url', ''))
        if offer.get('priceCurrency') != 'USD':
            raise ValueError('Expected explicit USD asking price currency')
        vin = record.get('vehicleIdentificationNumber')
        if vin is not None and not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', str(vin)):
            raise ValueError('Malformed VIN; do not substitute a listing ID')
        rows.append(dict(retailer='carvana', listing_id=identity, vin=vin,
            observed_at_utc=timestamp.tz_convert('UTC').isoformat(),
            year=record.get('modelDate'), make=record.get('brand'), model=record.get('model'),
            mileage_miles=record.get('mileageFromOdometer'), asking_price_usd=offer.get('price'),
            condition_native=record.get('itemCondition'), availability_native=offer.get('availability'),
            card_text=cards.get(identity), listing_url=offer['url'], source_url=capture['page_url']))
    frame = pd.DataFrame(rows)
    for column in ('year', 'mileage_miles', 'asking_price_usd'):
        if frame[column].map(lambda x: isinstance(x, bool)).any():
            raise ValueError(f'Boolean is not a valid {column}')
        frame[column] = pd.to_numeric(frame[column], errors='raise')
        values = frame[column].dropna()
        if (values < 0).any() or values.isin([float('inf'), float('-inf')]).any():
            raise ValueError(f'Invalid {column}')
        if column != 'asking_price_usd' and (values % 1 != 0).any():
            raise ValueError(f'Expected whole-number {column}')
    if frame.duplicated(['retailer', 'listing_id']).any():
        raise ValueError('Duplicate listing IDs in one capture')
    if not capture.get('sample_only', False):
        if len(frame) != capture.get('visible_listing_count') or set(cards) != set(frame.listing_id):
            raise ValueError('Structured records and visible listing cards disagree')
    return frame


def parse_projection(capture):
    """Selected fields transcribed from Chrome output, not original schema/HTTP bytes.

    Native status/shipping/delivery lines are retained individually in the source.
    card_text here is only their selected-line projection, never the full card.
    """
    rows = capture.get('rows', [])
    if not rows or len(rows) != capture.get('visible_listing_count'):
        raise ValueError('Projected records and visible listing cards disagree')
    # Reuse the same field validation without representing this as a raw response.
    normalized = dict(capture, capture_method='validated_projection', sample_only=False)
    normalized['records'], normalized['cards'] = [], []
    for row in rows:
        identity = str(row['listing_id'])
        if not re.fullmatch(r'\d+', identity) or row.get('retailer') != 'carvana':
            raise ValueError('Invalid projected retailer/listing identity')
        url = 'https://www.carvana.com/vehicle/' + identity
        normalized['records'].append({'@type': 'Vehicle', 'modelDate': row.get('year'),
            'brand': row.get('make'), 'model': row.get('model'),
            'vehicleIdentificationNumber': row.get('vin'), 'itemCondition': row.get('condition_native'),
            'mileageFromOdometer': row.get('mileage_miles'),
            'offers': {'url': url, 'priceCurrency': 'USD', 'price': row.get('asking_price_usd'),
                       'availability': row.get('availability_native')}})
        lines = [row.get(key) for key in ('card_status_native', 'shipping_native', 'delivery_native')]
        normalized['cards'].append({'url': url, 'text': '\n'.join(x for x in lines if x) or None})
    return parse_capture(normalized)
