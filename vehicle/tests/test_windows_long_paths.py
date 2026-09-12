"""The prepared Windows daily layout has short query paths but long hash files."""
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from test_inventory_vin_trial import data
from test_search_evidence import response
from vehicle_tracker.notebook_lab import load_capture
from vehicle_tracker.search import collect_search
from vehicle_tracker.search_plan import query_outcome
from vehicle_tracker.storage import retain_bytes


@pytest.mark.skipif(os.name != 'nt', reason='Windows native extended-path retention')
def test_long_hash_files_collect_reload_and_recover_without_prefixing_database(tmp_path):
    query = 'q004_chevrolet_2022_silverado_1500'
    padding = 201 - len(str(tmp_path/query)) - 1
    assert padding > 0
    destination = tmp_path/('p'*padding)/query
    assert len(str(destination)) == 201  # Longest prepared query directory depth.
    post = Mock(return_value=response(data()))
    report = collect_search(filters={}, zip_code='08542', destination=destination,
                            target_vins=1, post=post)
    assert post.call_count == report['requests'] == 1 and report['query_complete']
    page = report['pages'][0]
    for path, expected in [(page['retained_source'], page['source_sha256']),
                           (page['response_evidence']['source_path'], page['response_evidence']['source_sha256'])]:
        assert path.startswith('\\\\?\\')
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected
    assert not str(destination/'vehicle.sqlite').startswith('\\\\?\\')
    reloaded = load_capture(destination/'run_report.json')
    assert len(reloaded['rows']) == 24
    assert query_outcome(query, destination, recover=True)['query_complete']
    assert json.loads((destination/'run_report.json').read_text()) == report


@pytest.mark.skipif(os.name != 'nt', reason='Windows native extended-path retention')
def test_long_hash_retention_remains_immutable_and_idempotent(tmp_path):
    directory = tmp_path/('p'*(215-len(str(tmp_path))-1))
    content = b'bounded offline retention proof'
    path = retain_bytes(content, directory)
    assert path == retain_bytes(content, directory)
    assert path.read_bytes() == content and path.name == hashlib.sha256(content).hexdigest()+'.bin'
    path.write_bytes(b'changed test evidence')
    with pytest.raises(ValueError, match='content mismatch'):
        retain_bytes(content, directory)


def test_short_hash_paths_keep_existing_representation(tmp_path):
    path = retain_bytes(b'short offline source', tmp_path)
    assert path.parent == tmp_path and not str(path).startswith('\\\\?\\')
