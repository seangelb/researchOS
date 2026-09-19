"""Bind three reviewed zero-admission attempts without clearing their stop records."""
import json
from pathlib import Path

from vehicle_tracker.catalog import _capture_roots, digest


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def same(left, right):
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def reviewed_empty_layout(config):
    review = config.get('reviewed_empty_layout')
    if review is None:
        return [], {}
    if config.get('reviewed_empty_page_failure') or digest(review['path']) != review['sha256']:
        raise ValueError('Changed or conflicting empty-layout review')
    diagnosis = load(review['path'])
    sources = diagnosis['bindings']
    for path, sha in sources.items():
        if digest(path) != sha:
            raise ValueError('Reviewed empty-layout evidence changed')
        if Path(path).suffix == '.sqlite' and any(Path(path+s).exists() for s in ('-wal','-shm','-journal')):
            raise ValueError('Reviewed empty-layout database has unsettled sidecars')

    def bound(path):
        path = str(Path(path).resolve())
        if path not in sources or digest(path) != sources[path]:
            raise ValueError('Missing reviewed failure source binding')
        return load(path)

    folders = [Path(p).resolve() for p in review['failures']]
    if (len(folders) != 3 or len(set(folders)) != 3
            or str(folders[-1]) != diagnosis['capture_directory']
            or diagnosis['one_request_reconciled'] is not True
            or diagnosis['exact_reproduced_missing_key'] != 'makes'
            or diagnosis['native_makes_present'] is not False
            or diagnosis['native_applied_year_matches_request'] is not True
            or diagnosis['native_applied_make_model_verified'] is not False):
        raise ValueError('Require the three exact reviewed attempts and unverified empty layout')
    q = load(config['plan_path'])['children'][0]
    reports, last_source = [], None
    for folder in folders:
        if folder.parent not in _capture_roots(config) or folder.parent == Path(config['capture_root']).resolve():
            raise ValueError('Every reviewed failure must remain a locked peer')
        report = bound(folder/'catalog_report.json')
        budget = bound(folder/'catalog_budget.json')['budget']
        stop = bound(folder.parent/'access_stop.json')
        if 'entries' in report:
            if len(report['entries']) != 500 or any(e['status'] != 'unattempted' for e in report['entries'][1:]):
                raise ValueError('Original 500-child failed denominator changed')
            child_path = report['entries'][0]['report']
        else:
            if report.get('diagnostic_only') is not True:
                raise ValueError('Third attempt must remain the one-request diagnostic')
            child_path = report['query_report']
        child = bound(child_path)
        if len(child['pages']) != 1:
            raise ValueError('Reviewed attempt contains additional pages')
        page = child['pages'][0]
        evidence = page['response_evidence']
        source = bound(evidence['source_path'])
        pagination = source['inventory']['pagination']
        expected = dict(currentPage=1,pageSize=24,totalMatchedInventory=0,totalMatchedPages=1)
        if (report['status'] != 'stopped' or report['requests'] != 1
                or budget['requests'] != 1 or budget['stopped'] is not True or budget['pending_request'] is not False
                or stop['run'] != str(folder/'catalog_report.json')
                or child['outcome_kind'] != 'schema_failure' or child['query_complete'] is not False
                or child['unique_listings'] != 0 or page['stored_rows'] != 0 or page['http_status'] != 200
                or not same(child['filters'],q['filters']) or child['zip_code'] != '08542'
                or child['location_filter'] is not False or source['userDeliveryInfo']['zip5'] != '08542'
                or not same(pagination,expected) or source['inventory']['vehicles'] != []
                or evidence['http_diagnostics']['media_type'] != 'application/json'
                or evidence['http_diagnostics'].get('cf_mitigated') is not None
                or any(evidence['http_diagnostics']['body_markers'].values())):
            raise ValueError('Reviewed attempt is not a settled HTTP200 empty schema failure')
        reports.append(report)
        last_source = source
    original = bound(folders[0]/'selected_config.json')
    if (config['max_requests'] > original['max_requests']-3
            or config.get('absolute_window_end') != reports[0]['window_end']
            or config['authorized_cycle_date'] != reports[0]['cycle_date']
            or config['plan_sha256'] != reports[0]['plan_sha256']
            or not same(last_source.get('facetData'), {'year':diagnosis['native_facet_year']})):
        raise ValueError('Original allowance, deadline, plan or native empty layout differs')
    return folders, {**sources,str(Path(review['path']).resolve()):review['sha256']}
