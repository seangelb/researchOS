"""Publication aliases cannot create extra acquisitions or backdate old sources."""
import json
from unittest.mock import Mock

import pandas as pd
import pytest

from test_daily_cycles import clock, options, plan, reply
from test_retained_history import PUBLISHED, LATER, publication, query, witness, write
from test_search import response_data
from vehicle_tracker import catalog_history, cycles, retained_history
from vehicle_tracker.history import file_hash


def selection(report, database=None):
    return dict(report_path=str(report), report_sha256=file_hash(report), diagnostic=False,
                database_witness=witness(database, 'history') if database else None)


def old_cycle(tmp_path, response_data):
    opts = options(tmp_path)
    opts['timezone_name'] = 'America/New_York'
    cycles.collect_cycle(plan(), **opts, post=Mock(return_value=reply(response_data)))
    path = tmp_path/'cycle/cycle.json'
    report = tmp_path/'cycle/attempt_0001/all/run_report.json'
    value = json.loads(report.read_text())
    value['pages'][0]['evidence_available_at_utc'] = None
    write(report, value)
    return path, report


def test_old_cycle_cannot_bypass_unknown_original_availability(tmp_path, response_data, clock):
    cycle, report = old_cycle(tmp_path, response_data)
    selected = dict(cycle_paths=[cycle], database=None)
    manifest = publication(tmp_path/'publication', query_sources=[selection(report)])
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    assert all(table.empty for table in catalog_history.observed_catalog_history(
        legacy_sources=[selected], as_of=PUBLISHED))
    assert all(table.empty for table in catalog_history.observed_catalog_history(
        legacy_sources=[selected], retained_manifests=[manifest], as_of='2026-09-19T11:59:59Z'))
    history, _, members = catalog_history.observed_catalog_history(
        legacy_sources=[selected], retained_manifests=[manifest], as_of=PUBLISHED)
    assert len(members) == len(history) == 3
    assert history.observed_cycles.eq(1).all() and history.retained_captures.eq(1).all()
    assert members.available_at.eq(pd.Timestamp(PUBLISHED)).all()
    assert members.original_evidence_available_at.isna().all()
    aliases = [item for value in members.source_aliases_json for item in json.loads(value)]
    assert {'cycle', 'query_report'} <= {item.get('source_group_kind') for item in aliases}
    assert before == {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}


def test_cycle_report_and_database_witness_count_one_physical_acquisition(tmp_path, response_data, clock):
    cycle, report = old_cycle(tmp_path, response_data)
    database = tmp_path/'history.sqlite'
    cycles.import_cycle(cycle, database)
    manifest = publication(tmp_path/'publication', query_sources=[selection(report, database)])
    before = database.read_bytes()
    history, _, members = catalog_history.observed_catalog_history(
        legacy_sources=[dict(cycle_paths=[cycle], database=database)],
        retained_manifests=[manifest], as_of='2030-01-01T00:00:00Z')
    assert len(members) == 3 and history.observed_cycles.eq(1).all()
    assert history.retained_captures.eq(1).all() and members.original_evidence_available_at.isna().all()
    aliases = [json.loads(value) for value in members.source_aliases_json]
    assert all(any(item.get('witness_kind') == 'history' and
                   item.get('witness_path') == str(database.resolve()) for item in group)
               for group in aliases)
    assert database.read_bytes() == before


def test_later_duplicate_publication_does_not_delay_earlier_witness(tmp_path, response_data):
    report, _ = query(tmp_path/'source', response_data)
    first = publication(tmp_path/'first', query_sources=[selection(report)], published_at=PUBLISHED)
    later = publication(tmp_path/'later', query_sources=[selection(report)], published_at=LATER)
    early_history, _, early = catalog_history.observed_catalog_history(
        retained_manifests=[later, first], as_of=PUBLISHED)
    history, _, members = catalog_history.observed_catalog_history(
        retained_manifests=[later, first], as_of=LATER)
    assert len(early) == len(members) == len(history) == 3
    assert history.first_known_at.eq(pd.Timestamp(PUBLISHED)).all()
    assert early_history.first_known_at.eq(history.first_known_at).all()
    assert members.available_at.eq(pd.Timestamp(PUBLISHED)).all()
    assert members.cycle_id.isna().all() and history.observed_cycles.eq(0).all()
    assert members.source_aliases_json.str.contains(first['sha256']).all()
    assert members.source_aliases_json.str.contains(later['sha256']).all()


@pytest.mark.parametrize(('field', 'replacement'), [
    ('asking_price_usd', 12345), ('purchase_pending', True),
    ('context_json', json.dumps(dict(endpoint='different-context'))),
    ('on_demand', False),
])
def test_physical_alias_conflict_fails_before_deduplication(tmp_path, response_data, clock,
                                                         monkeypatch, field, replacement):
    cycle, report = old_cycle(tmp_path, response_data)
    manifest = publication(tmp_path/'publication', query_sources=[selection(report)])
    read = retained_history.read_retained_history

    def changed(*args, **kwargs):
        rows = read(*args, **kwargs)
        rows.loc[0, field] = replacement
        return rows

    monkeypatch.setattr(retained_history, 'read_retained_history', changed)
    with pytest.raises(ValueError, match='Physical capture aliases differ.*'+field):
        catalog_history.observed_catalog_history(legacy_sources=[dict(cycle_paths=[cycle])],
            retained_manifests=[manifest], as_of=PUBLISHED)
