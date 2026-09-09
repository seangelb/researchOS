"""Read-only query readiness; completed queries never certify a national population."""
import json
import math
from pathlib import Path

import pandas as pd

from vehicle_tracker.history import OBSERVATION_COLUMNS, file_hash, read_query_evidence
from vehicle_tracker.search_plan import validate_plan


def query_readiness(manifest, report_paths):
    """Return attempt diagnostics and admitted query memberships, without deduping versions.

    Counts are vehicles/requests; durations are seconds. Missing estimates stay missing.
    A report must match filters, ZIP and location feature. Supply explicit report paths;
    multiple versions remain separate rows and require an analyst's scope selection.
    Conflict counts include both listing-to-VIN disagreements and VIN listing aliases.
    """
    validate_plan(manifest['queries'])
    reports = []
    for path in dict.fromkeys(map(str, report_paths)):
        reports.append((Path(path), json.loads(Path(path).read_text(encoding='utf-8'))))
    rows, memberships = [], []
    for query in manifest['queries']:
        matches = [(path, report) for path, report in reports if
                   all(report.get(key, False) == query.get(key, False)
                       for key in ('filters', 'zip_code', 'location_filter'))]
        for path, report in matches or [(None, {})]:
            row = dict(query_id=query['query_id'], population=manifest['population'],
                filters=json.dumps(query['filters'], sort_keys=True), zip_code=query['zip_code'],
                location_filter=query.get('location_filter', False), facet_source=manifest.get('facet_source'),
                report_path=str(path) if path else None, report_sha256=file_hash(path) if path else None,
                status='unattempted', query_complete=False, attempt_versions=len(matches),
                reason='No matching retained query', reported_total=None, admitted_rows=None,
                reconciliation_difference=None, duplicate_memberships=None, conflicting_vins=None,
                observation_start=None, observation_end=None, page_size=None, estimated_requests=None,
                actual_requests=report.get('requests'), elapsed_seconds=report.get('elapsed_seconds'),
                population_verified=False, category_gap='Unknown/out-of-range inventory and population scope unverified')
            if report:
                row.update(status=report['status'], reason=report.get('reason', ''))
                try:
                    run, _, observations = read_query_evidence(path)
                    pages = [json.loads(Path(page['retained_source']).read_text()) for page in report['pages']
                             if page['status'] == 'parsed']
                    sizes = {page['pagination']['pageSize'] for page in pages}
                    total = run['reported_total']
                    batch = next(iter(sizes)) if len(sizes) == 1 else None
                    row.update(query_complete=bool(run['query_complete']), reported_total=total,
                        admitted_rows=len(observations), observation_start=run['observation_start'],
                        observation_end=run['observation_end'], page_size=batch,
                        reconciliation_difference=len(observations)-total if total is not None else None,
                        estimated_requests=max(1, math.ceil(total/batch)) if total is not None and batch else None)
                    memberships.append(observations.assign(query_id=query['query_id'], report_path=str(path)))
                    if len(matches) > 1:
                        row['reason'] += '; Multiple attempts: select explicitly before comparing'
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    row.update(status='invalid_evidence', reason=f'{type(exc).__name__}: {exc}')
            rows.append(row)
    membership = pd.concat(memberships, ignore_index=True) if memberships else pd.DataFrame(
        columns=[*OBSERVATION_COLUMNS, 'query_id', 'report_path'])
    table = pd.DataFrame(rows)
    if not membership.empty:
        duplicate = (membership.duplicated(['retailer', 'listing_id'], keep=False)
                     | membership.duplicated(['retailer', 'vin'], keep=False))
        conflict = (membership.groupby(['retailer', 'listing_id']).vin.transform('nunique').gt(1)
                    | membership.groupby(['retailer', 'vin']).listing_id.transform('nunique').gt(1))
        counts = membership.assign(duplicate_memberships=duplicate, conflicting_vins=conflict).groupby(
            ['query_id', 'report_path'])[['duplicate_memberships', 'conflicting_vins']].sum()
        table = table.drop(columns=counts.columns).merge(counts, on=['query_id', 'report_path'], how='left', validate='one_to_one')
    table.loc[table.query_complete & table.admitted_rows.eq(0), ['duplicate_memberships', 'conflicting_vins']] = 0
    table.loc[table.conflicting_vins.gt(0), 'reason'] += '; Identity conflicts require review'
    return table, membership
