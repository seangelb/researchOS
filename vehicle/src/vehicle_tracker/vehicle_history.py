"""Read-only vehicle-history research and explicit, verified follow-up identities."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import pandas as pd

from vehicle_tracker.events import _aware
from vehicle_tracker.sale_pilot import _url_id

REPORT_COLUMNS = ['report_id', 'retailer', 'listing_id', 'expected_vin', 'report_vin',
    'identity_match', 'source_url', 'source_file', 'source_sha256', 'report_run_at',
    'first_observed_at', 'available_at', 'event_date', 'event_source', 'native_wording',
    'location', 'odometer_miles', 'event_kind', 'report_note', 'event_note',
    'report_outcome', 'sample_role', 'context']
FOLLOWUP_COLUMNS = ['retailer', 'vin', 'original_listing_id', 'original_url',
    'followup_listing_id', 'followup_url', 'followup_observed_at',
    'followup_available_at', 'followup_source', 'followup_reason']
EVENT_KINDS = {'title', 'registration', 'owner_change', 'auction_sale',
               'auction_listing', 'dealer_listing', 'other'}
REPORT_FIELDS = ['report_id', 'retailer', 'listing_id', 'expected_vin', 'report_vin',
    'source_url', 'report_run_at', 'first_observed_at', 'available_at', 'events',
    'context', 'report_outcome', 'sample_role', 'report_note']


def load_report_events(study_dir, *, as_of):
    """Read manually retained report events; matching a VIN is not sale attribution.

    Dates stay dates. Report-run, retrieval and local availability clocks remain
    separate. Wrong VINs remain visible for diagnosis but cannot corroborate the
    expected vehicle. A report with no usable dated rows gets a diagnostic row.
    Every report, including failed access, requires a hash-verified JSON selected
    projection. Manifest fields must agree with that projection before its clocks
    can be used for cutoff selection. This does not retain omitted HTTP content.
    No network, database writes or inferred sale labels are used.
    """
    cutoff = _aware(as_of)
    directory = Path(study_dir).resolve()
    manifest = directory / 'reports.json'
    if not manifest.is_file():
        return pd.DataFrame(columns=REPORT_COLUMNS)
    reports = json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(reports, list):
        raise ValueError('reports.json must contain a list of report records')
    output, seen = [], set()
    for report in reports:
        if not isinstance(report, dict) or not report.get('source_file') or not report.get('source_sha256'):
            raise ValueError('Every report requires a retained JSON source and its hash')
        relative = Path(report['source_file'])
        source = (directory / relative).resolve()
        if relative.is_absolute() or not source.is_relative_to(directory):
            raise ValueError('Report source must remain inside the study directory')
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != report['source_sha256']:
            raise ValueError('Retained report source hash differs')
        projection = json.loads(content)
        if not isinstance(projection, dict):
            raise ValueError('Retained report source must be a JSON selected projection')
        manifest_fields = {name: report.get(name) for name in REPORT_FIELDS}
        source_fields = {name: projection.get(name) for name in REPORT_FIELDS}
        if json.dumps(manifest_fields, sort_keys=True) != json.dumps(source_fields, sort_keys=True):
            raise ValueError('Report manifest differs from the retained source projection')
        if any(not isinstance(report.get(name), str) or not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', report[name])
               for name in ['expected_vin', *(['report_vin'] if report.get('report_vin') is not None else [])]):
            raise ValueError('Report VINs must retain valid 17-character vehicle identities')
        url = urlsplit(report['source_url'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('Report source requires a credential-free public HTTPS URL')
        observed, available = _aware(report['first_observed_at']), _aware(report['available_at'])
        if available < observed:
            raise ValueError('Report availability cannot precede retrieval')
        if observed > cutoff or available > cutoff:
            continue
        if report['report_id'] in seen:
            raise ValueError('Repeated report_id; retain separate report versions explicitly')
        seen.add(report['report_id'])
        run = _aware(report['report_run_at']) if report.get('report_run_at') else pd.NaT
        if pd.notna(run) and run > observed:
            raise ValueError('Report-run time cannot follow its retrieval')
        events = report['events']
        if not isinstance(events, list):
            raise ValueError('Report events must be a list')
        item = {name: projection.get(name) for name in REPORT_COLUMNS}
        item.update(source_file=report['source_file'], source_sha256=report['source_sha256'])
        item.update(report_run_at=run, first_observed_at=observed, available_at=available,
            identity_match=bool(report.get('report_vin') and report['expected_vin'] == report['report_vin']))
        boundary = (run if pd.notna(run) else observed).date()
        retained = []
        for event in events:
            date = event['event_date']
            if not isinstance(date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
                raise ValueError('Event dates must retain YYYY-MM-DD precision')
            stamp = pd.Timestamp(date)
            if stamp.date() > boundary:
                continue
            if event['event_kind'] not in EVENT_KINDS:
                raise ValueError('Unknown manually assigned event_kind')
            if any(not isinstance(event.get(name), str) or not event[name].strip()
                   for name in ['event_source', 'native_wording']):
                raise ValueError('Events require native wording and the reporting source')
            event_fields = {name: event.get(name) for name in
                ['event_date', 'event_source', 'native_wording', 'location', 'odometer_miles', 'event_kind']}
            retained.append(dict(item, **event_fields, event_note='Reported event; sale attribution remains unresolved.'))
        if retained:
            output.extend(retained)
        else:
            output.append(dict(item, event_note='No eligible dated events retained; this does not establish an unsold vehicle.'))
    return pd.DataFrame(output, columns=REPORT_COLUMNS)


def followup_identities(cohort_frame, pilot_records, inventory_source_rows, *, as_of):
    """Choose the latest verified physical identity, keeping the frozen reference.

    Failed attempts do not replace a successful identity. Both physical and
    availability clocks must meet the cutoff. The original cohort URL is an
    explicit fallback, not a newly verified vehicle observation.
    """
    cutoff = _aware(as_of)
    if 'selected_at' in cohort_frame.columns:
        cohort_frame = cohort_frame.loc[cohort_frame.selected_at.map(_aware).le(cutoff)]
    if cohort_frame.empty:
        return pd.DataFrame(columns=FOLLOWUP_COLUMNS)
    if cohort_frame.duplicated(['retailer', 'vin']).any():
        raise ValueError('Follow-up membership must contain distinct retailer/VINs')
    candidates, originals = [], []
    for row in cohort_frame.to_dict('records'):
        if row['retailer'] != 'carvana' or _url_id(row['url']) != row['listing_id']:
            raise ValueError('Original follow-up identity requires a numeric Carvana listing URL')
        originals.append(dict(retailer=row['retailer'], vin=row['vin'], listing_id=row['listing_id']))
    for kind, frame in [('page', pilot_records), ('inventory', inventory_source_rows)]:
        for row in frame.to_dict('records'):
            physical_field = 'checked_at' if kind == 'page' else 'inventory_observed_at'
            physical = row.get(physical_field, row.get('observed_at_utc'))
            local = row.get('inventory_available_at', row.get('available_at'))
            observed, available = _aware(physical), _aware(local)
            if observed > cutoff or available > cutoff:
                continue
            if available < observed:
                raise ValueError('Identity availability cannot precede its observation')
            if row.get('retailer') != 'carvana':
                continue
            listing = row.get('listing_id')
            vin = row.get('vin')
            if (not isinstance(listing, str) or not re.fullmatch(r'\d+', listing)
                    or not isinstance(vin, str) or not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', vin)):
                continue
            if kind == 'page':
                matched = (row.get('parse_outcome') == 'matched'
                    and row.get('saleStatus') in ['Available', 'Sold']
                    and row.get('observed_vin') == row.get('vin')
                    and str(row.get('observed_listing_id')) == listing
                    and _url_id(row.get('requested_url')) == listing
                    and _url_id(row.get('final_url')) == listing)
                if not matched:
                    continue
            elif _url_id(row.get('listing_url')) != listing:
                continue
            candidates.append(dict(retailer=row['retailer'], vin=row['vin'], listing_id=listing,
                observed_at=observed, available_at=available,
                source=row.get('source') if kind == 'page' else row.get('source_path', row.get('source_url')),
                kind=kind))
    bindings = pd.DataFrame([*originals, *candidates])
    if bindings.groupby(['retailer', 'listing_id']).vin.nunique(dropna=False).gt(1).any():
        raise ValueError('A verified listing ID is bound to conflicting VINs')
    output = []
    for original in cohort_frame.to_dict('records'):
        item = dict(retailer=original['retailer'], vin=original['vin'],
            original_listing_id=original['listing_id'], original_url=original['url'],
            followup_listing_id=original['listing_id'], followup_url=original['url'],
            followup_observed_at=pd.NaT, followup_available_at=pd.NaT, followup_source=None,
            followup_reason='Original cohort reference; no verified observation available at cutoff.')
        matching = [row for row in candidates if row['retailer'] == original['retailer'] and row['vin'] == original['vin']]
        if matching:
            latest = max(matching, key=lambda row: (row['observed_at'], row['available_at']))
            tied = [row['listing_id'] for row in matching if row['observed_at'] == latest['observed_at']]
            if len(set(tied)) > 1:
                raise ValueError('Simultaneous verified listings leave the latest follow-up identity ambiguous')
            item.update(followup_listing_id=latest['listing_id'],
                followup_url='https://www.carvana.com/vehicle/' + latest['listing_id'],
                followup_observed_at=latest['observed_at'], followup_available_at=latest['available_at'],
                followup_source=latest['source'], followup_reason='Latest verified ' + latest['kind'] + ' observation.')
        output.append(item)
    return pd.DataFrame(output, columns=FOLLOWUP_COLUMNS)
