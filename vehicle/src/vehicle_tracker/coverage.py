"""Evidence for bounded query coverage; never a national-inventory certificate."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd

from vehicle_tracker.carvana import parse_capture


def query_context(capture):
    """Ignore only the page index, retaining every filter and sort parameter."""
    url = urlsplit(capture.get('page_url', ''))
    query = urlencode(sorted((k, v) for k, v in parse_qsl(url.query, keep_blank_values=True) if k != 'page'))
    return (urlunsplit((url.scheme, url.netloc, url.path, query, '')),
            capture.get('requested_zip'), capture.get('sort'))


def run_coverage(captures):
    """Return one readable query summary, retaining reasons for every failed check.

    A complete count means these pages enumerate this displayed query during an
    observation interval. It does not prove the site's universe or an instant census.
    Required VINs support the cross-ZIP identity audit; missing prices stay visible.
    """
    frames, reasons, totals, clocks = [], [], [], []
    context = query_context(captures[0]) if captures else None
    for number, capture in enumerate(captures, 1):
        if capture.get('page_number') != number:
            reasons.append('Pages must start at one and be consecutive')
        if query_context(capture) != context or not capture.get('sort'):
            reasons.append('Query filters or sort changed or are unknown')
        requested = capture.get('requested_query_url')
        if requested and query_context(capture)[0] != query_context(dict(capture, page_url=requested))[0]:
            reasons.append('Rendered query differs from requested filters')
        if not capture.get('requested_zip') or capture.get('zip_code') != capture['requested_zip']:
            reasons.append('Requested and displayed ZIP disagree or are unknown')
        if capture.get('sample_only') or not capture.get('results_container_present'):
            reasons.append('Sample or unverified results container')
        total = re.fullmatch(r'([\d,]+) cars', str(capture.get('reported_total_text') or ''))
        totals.append(int(total[1].replace(',', '')) if total else None)
        if number < len(captures) and capture.get('has_next_page') is not True:
            reasons.append('Pagination ended before the last retained page')
        try:
            frames.append(parse_capture(capture))
            clocks.append(pd.Timestamp(capture['captured_at_utc']))
        except (ValueError, KeyError, TypeError):
            reasons.append(f'Page {number} failed record/card validation')
    if any(later <= earlier for earlier, later in zip(clocks, clocks[1:])):
        reasons.append('Page observation times are not increasing')
    rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    unique = rows.listing_id.nunique() if len(rows) else 0
    duplicate_rows = len(rows) - unique
    if duplicate_rows:
        reasons.append('Repeated listing IDs across pages')
    if not totals or None in totals or len(set(totals)) != 1 or unique != totals[0]:
        reasons.append('Unique listing count does not reconcile to a stable displayed total')
    if len(rows) and (rows.vin.isna().any() or rows.vin.duplicated().any()):
        reasons.append('Missing or repeated VINs need identity review')
    if captures:
        last = captures[-1].get('has_next_page')
        if last is not False and not (last is None and len(captures) == 1 and totals[0] == unique):
            reasons.append('Pagination is truncated or its ending is unknown')
    if not len(rows):
        reasons.append('No validated observations; never zero inventory')
    return dict(query_complete=not reasons, complete_query_count=unique if not reasons else None,
                unique_listings=unique, parsed_rows=len(rows), duplicate_rows=duplicate_rows,
                valid_vins=rows.vin.nunique() if len(rows) else 0,
                missing_prices=int(rows.asking_price_usd.isna().sum()) if len(rows) else None,
                reported_total=totals[0] if totals else None,
                national_coverage_verified=False, reason='; '.join(dict.fromkeys(reasons)))


def partition_coverage(parent, partitions, *, expected_partitions):
    """Audit a declared set of child queries against a bounded parent capture."""
    parent_ids = set()
    child_ids = set()
    observed_rows = 0
    identities = {}
    valid = bool(expected_partitions) and set(partitions) == set(expected_partitions)
    for name, captures in [('parent', parent), *partitions.items()]:
        valid = bool(valid and run_coverage(captures)['query_complete'])
        ids = set()
        for capture in captures:
            try:
                frame = parse_capture(capture)
                ids.update(frame.listing_id)
                for row in frame.itertuples():
                    identities.setdefault(row.listing_id, set()).add(row.vin)
            except (ValueError, KeyError, TypeError):
                valid = False
        if name == 'parent':
            parent_ids = ids
        else:
            observed_rows += len(ids)
            child_ids.update(ids)
    conflicts = sorted(key for key, vins in identities.items() if len(vins) != 1)
    return dict(matches_parent=valid and parent_ids == child_ids and not conflicts,
                identity_conflicts=conflicts,
                union_listings=len(child_ids), overlapping_memberships=observed_rows - len(child_ids),
                missing_from_children=sorted(parent_ids - child_ids),
                absent_from_parent=sorted(child_ids - parent_ids), national_coverage_verified=False)


def compare_runs(before, after, *, seen_before=()):
    """Matched query observations, with gaps blocked and reappearances distinguished.

    seen_before contains listing IDs actually observed before the 'before' run.
    Price changes are USD asking-price changes. No transaction/event date is inferred.
    """
    if not all(run_coverage(c)['query_complete'] for c in (before, after)):
        raise ValueError('Incomplete comparison period; inspect run_coverage first')
    if query_context(before[0]) != query_context(after[0]):
        raise ValueError('Comparison query context differs')
    if pd.Timestamp(after[0]['captured_at_utc']) <= pd.Timestamp(before[-1]['captured_at_utc']):
        raise ValueError('Comparison intervals overlap or are out of order')
    left = pd.concat([parse_capture(c) for c in before], ignore_index=True)
    right = pd.concat([parse_capture(c) for c in after], ignore_index=True)
    result = left.merge(right, on=['retailer', 'listing_id'], how='outer',
                        suffixes=('_before', '_after'), indicator=True, validate='one_to_one')
    result['observation_change'] = result['_merge'].map({
        'both': 'observed_both', 'left_only': 'not_observed', 'right_only': 'first_observed'}).astype(object)
    result.loc[result['_merge'].eq('right_only') & result.listing_id.isin(seen_before), 'observation_change'] = 'reappearing'
    result['identity_conflict'] = result['_merge'].eq('both') & result.vin_before.ne(result.vin_after)
    result['asking_price_change_usd'] = (result.asking_price_usd_after - result.asking_price_usd_before).mask(result.identity_conflict)
    return result
