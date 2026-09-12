"""Retain evidence when its final path fits the Windows legacy path limit."""
import hashlib
import json
from pathlib import Path

import pytest
import requests

from vehicle_tracker import storage
from vehicle_tracker.search_evidence import retain_response_evidence, verify_response_evidence


@pytest.mark.parametrize('selected', [False, True])
def test_response_retention_avoids_temporary_path_overflow(tmp_path, monkeypatch, selected):
    # The failed live trial had a 183-character source directory: final JSON
    # paths fit at 253 characters, but digest-prefixed temporary paths hit 261.
    padding = 183 - len(str(tmp_path.resolve())) - 1
    if padding < 1:
        pytest.skip('Test temporary root is too long for the 183-character fixture')
    directory = tmp_path.resolve() / ('x' * padding)
    real_temporary = storage.tempfile.NamedTemporaryFile
    temporary_paths = []

    def legacy_windows_temporary(*args, **kwargs):
        candidate = Path(kwargs['dir']) / (kwargs['prefix'] + '12345678' + kwargs['suffix'])
        temporary_paths.append(candidate)
        # Emulate MAX_PATH on every platform; on affected Windows hosts the
        # underlying call also exercises the actual filesystem limit.
        if len(str(candidate)) >= 260:
            raise FileNotFoundError(2, 'Windows temporary path overflow', str(candidate))
        return real_temporary(*args, **kwargs)

    monkeypatch.setattr(storage.tempfile, 'NamedTemporaryFile', legacy_windows_temporary)
    public = {'inventory': {'vehicles': []}}
    data = dict(public, ignored='omitted') if selected else public
    response = requests.Response()
    response.status_code = 200
    response.headers['content-type'] = 'application/json'
    response._content = json.dumps(data).encode()

    evidence = retain_response_evidence(response, directory)
    assert evidence['kind'] == ('selected_source' if selected else 'original_response_content')
    assert verify_response_evidence(evidence) == public
    expected = ((json.dumps(public, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
                if selected else response.content)
    path = Path(evidence['source_path'])
    assert path.read_bytes() == expected
    assert path.name == hashlib.sha256(expected).hexdigest() + '.json'
    assert len(str(path)) == 253
    assert len(temporary_paths) == 1
    assert temporary_paths[0].parent == directory
    assert len(str(temporary_paths[0])) <= len(str(path))
    assert list(directory.iterdir()) == [path]
    assert retain_response_evidence(response, directory) == evidence
    assert len(temporary_paths) == 1  # Existing immutable evidence is reused.
