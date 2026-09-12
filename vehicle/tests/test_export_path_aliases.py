"""Export fingerprints must survive Windows path aliases and detect mutations."""
import importlib.util
import os
from pathlib import Path

import pytest

from vehicle_tracker.storage import retain_bytes


@pytest.fixture
def exporter():
    path = Path(__file__).resolve().parents[1]/'scripts/export_sales_proxy.py'
    spec = importlib.util.spec_from_file_location('export_path_alias_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def long_evidence(tmp_path):
    query_name = 'q004_chevrolet_2022_silverado_1500'
    padding = 201-len(str(tmp_path/query_name))-1
    assert padding > 0
    query = tmp_path/('p'*padding)/query_name
    capture = retain_bytes(b'original projection', query/'raw', suffix='.json')
    companion = retain_bytes(b'original response bytes', query/'response_sources')
    report = query/'run_report.json'
    report.write_text('{}', encoding='utf-8')
    try:
        yield query, capture, companion
    finally:
        # Delete the long files through their actual I/O aliases so pytest's
        # normal-path temporary-directory cleanup works on legacy-limit hosts.
        capture.unlink(missing_ok=True)
        companion.unlink(missing_ok=True)


@pytest.mark.skipif(os.name != 'nt', reason='Windows normal/extended path aliases')
def test_prescan_and_companion_discovery_keep_long_files_and_alias_lookup(exporter, long_evidence):
    query, capture, companion = long_evidence
    initial = {exporter.fingerprint_key(p): exporter.sha(p) for p in exporter.retained_files(query.parent)}
    assert len(initial) == 3  # Neither long hash filename may silently disappear.
    for path in [capture, companion]:
        assert str(path).startswith('\\\\?\\') and len(str(path)[4:]) >= 260
        normal = Path(str(path)[4:])
        assert exporter.fingerprint_key(normal) == exporter.fingerprint_key(path)
        assert initial[exporter.fingerprint_key(normal)] == exporter.sha(path)
    # Companion discovery may start from an already extended report directory.
    inputs = list(exporter.retained_files(Path('\\\\?\\'+str(query))))
    assert len(inputs) == 3
    before = {str(p): initial.get(exporter.fingerprint_key(p)) for p in inputs}
    assert before == {str(p): exporter.sha(p) for p in inputs}


@pytest.mark.skipif(os.name != 'nt', reason='Windows normal/extended path aliases')
def test_mutated_long_companion_cannot_replace_prescan_hash_at_first_read(exporter, long_evidence):
    query, _, companion = long_evidence
    initial = {exporter.fingerprint_key(p): exporter.sha(p) for p in exporter.retained_files(query.parent)}
    key = exporter.fingerprint_key(companion)
    original = initial[key]
    companion.write_bytes(b'changed after prescan, before notebook read')
    # This is the exporter's first-read rule: preserve the earlier fingerprint.
    initial.setdefault(key, exporter.sha(companion))
    assert initial[key] == original and initial[key] != exporter.sha(companion)
    inputs = list(exporter.retained_files(query))
    before = {str(p): initial.get(exporter.fingerprint_key(p)) for p in inputs}
    assert before != {str(p): exporter.sha(p) for p in inputs}


def test_normal_read_keeps_original_hash_when_prescan_used_io_alias(exporter, tmp_path):
    source = tmp_path/'short.json'
    source.write_bytes(b'original')
    initial = {exporter.fingerprint_key(p): exporter.sha(p) for p in exporter.retained_files(tmp_path)}
    original = initial[exporter.fingerprint_key(source)]
    source.write_bytes(b'changed')
    initial.setdefault(exporter.fingerprint_key(source), exporter.sha(source))
    assert initial[exporter.fingerprint_key(source)] == original
    assert original != exporter.sha(source)
