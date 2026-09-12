"""Offline checkpoint publication: only synthetic files, never retained evidence."""
import ctypes
import errno
import json
import os
from pathlib import Path

import pytest

from vehicle_tracker import storage


def denied(winerror=None):
    error = PermissionError(errno.EACCES, 'Synthetic publication denied', 'synthetic.tmp')
    error.filename2 = 'synthetic_checkpoint.json'
    if winerror is not None:
        error.winerror = winerror
    return error


def checkpoint(tmp_path):
    path = tmp_path/'synthetic_checkpoint.json'
    path.write_text('{"state":"old"}\n', encoding='utf-8')
    return path, path.read_bytes()


@pytest.mark.parametrize('existing', [False, True])
def test_failed_sync_never_publishes_partial_checkpoint(tmp_path, monkeypatch, existing):
    path = tmp_path/'synthetic_checkpoint.json'
    if existing:
        path.write_text('{"state":"old"}\n', encoding='utf-8')
    before = path.read_bytes() if existing else None
    def fail_sync(descriptor):
        raise OSError(errno.EIO, 'Synthetic interrupted durable write')
    monkeypatch.setattr(storage.os, 'fsync', fail_sync)
    with pytest.raises(OSError) as caught:
        storage.write_json_atomic(path, {'state': 'new', 'values': list(range(100))})
    assert caught.value.storage_operation == 'sync_checkpoint_temporary'
    assert caught.value.storage_publication_attempts == 0
    assert path.read_bytes() == before if existing else not path.exists()
    assert list(tmp_path.glob('*.tmp')) == []


@pytest.mark.parametrize('code', [32, 33])
def test_confirmed_sharing_retries_same_complete_file_only(tmp_path, monkeypatch, code):
    path, before = checkpoint(tmp_path)
    original_replace = storage.os.replace
    replacements, waits = [], []
    def replace(source, destination):
        replacements.append((Path(source), Path(source).read_bytes()))
        assert path.read_bytes() == before
        if len(replacements) <= 2:
            raise denied(code)
        original_replace(source, destination)
    monkeypatch.setattr(storage.os, 'replace', replace)
    monkeypatch.setattr(storage.time, 'sleep', waits.append)
    storage.write_json_atomic(path, {'state': 'new'})
    assert len(replacements) == 3 and len(set(replacements)) == 1
    assert waits == [0.05, 0.10]
    assert json.loads(path.read_text(encoding='utf-8')) == {'state': 'new'}
    assert list(tmp_path.glob('*.tmp')) == []


@pytest.mark.parametrize('code', [None, 5, 19, 112])
def test_unconfirmed_or_permanent_denial_stops_without_retry(tmp_path, monkeypatch, code):
    path, before = checkpoint(tmp_path)
    calls, waits = [], []
    error = denied(code)
    def replace(source, destination):
        calls.append((source, destination))
        raise error
    monkeypatch.setattr(storage.os, 'replace', replace)
    monkeypatch.setattr(storage.time, 'sleep', waits.append)
    with pytest.raises(PermissionError) as caught:
        storage.write_json_atomic(path, {'state': 'new'})
    assert caught.value is error and len(calls) == 1 and waits == []
    assert error.errno == errno.EACCES and error.filename2 == 'synthetic_checkpoint.json'
    assert error.storage_operation == 'replace_checkpoint'
    assert error.storage_target == str(path.resolve()) and error.storage_publication_attempts == 1
    assert error.storage_sharing_violation is None
    assert path.read_bytes() == before and list(tmp_path.glob('*.tmp')) == []


def test_persistent_confirmed_sharing_exhausts_bounded_local_retry(tmp_path, monkeypatch):
    path, before = checkpoint(tmp_path)
    errors, waits = [], []
    def replace(source, destination):
        error = denied(32)
        errors.append(error)
        assert path.read_bytes() == before
        raise error
    monkeypatch.setattr(storage.os, 'replace', replace)
    monkeypatch.setattr(storage.time, 'sleep', waits.append)
    with pytest.raises(PermissionError) as caught:
        storage.write_json_atomic(path, {'state': 'new'})
    assert len(errors) == caught.value.storage_publication_attempts == 5
    assert waits == [0.05, 0.10, 0.20, 0.40] and sum(waits) == 0.75
    assert caught.value.storage_sharing_violation == 32
    assert path.read_bytes() == before and list(tmp_path.glob('*.tmp')) == []


def test_cleanup_error_does_not_hide_publication_error(tmp_path, monkeypatch):
    path, before = checkpoint(tmp_path)
    original_unlink = Path.unlink
    primary = denied()
    def replace(source, destination):
        raise primary
    def unlink(temporary, *args, **kwargs):
        if temporary.suffix == '.tmp':
            raise denied(32)
        return original_unlink(temporary, *args, **kwargs)
    with monkeypatch.context() as patcher:
        patcher.setattr(storage.os, 'replace', replace)
        patcher.setattr(Path, 'unlink', unlink)
        with pytest.raises(PermissionError) as caught:
            storage.write_json_atomic(path, {'state': 'new'})
    assert caught.value is primary
    assert 'cleanup also failed' in primary.__notes__[0]
    assert path.read_bytes() == before
    leftovers = list(tmp_path.glob('*.tmp'))
    assert len(leftovers) == 1
    assert json.loads(leftovers[0].read_text(encoding='utf-8')) == {'state': 'new'}
    leftovers[0].unlink()


@pytest.fixture
def windows_api():
    if os.name != 'nt':
        pytest.skip('Actual Windows sharing semantics require Windows')
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel.SetFileAttributesW.restype = wintypes.BOOL
    return kernel


def hold_without_delete_sharing(kernel, path):
    from ctypes import wintypes
    handle = kernel.CreateFileW(str(path.resolve()), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def test_actual_windows_reader_releases_during_local_publication_retry(tmp_path, monkeypatch, windows_api):
    path, before = checkpoint(tmp_path)
    handle = hold_without_delete_sharing(windows_api, path)
    released, waits = [], []
    actual_probe, probes = storage._checkpoint_sharing_violation, []
    def probe(error, source, destination):
        result = actual_probe(error, source, destination)
        probes.append((error.winerror, result))
        return result
    def release(delay):
        waits.append(delay)
        assert path.read_bytes() == before
        assert windows_api.CloseHandle(handle)
        released.append(True)
    monkeypatch.setattr(storage, '_checkpoint_sharing_violation', probe)
    monkeypatch.setattr(storage.time, 'sleep', release)
    try:
        storage.write_json_atomic(path, {'state': 'new'})
    finally:
        if not released:
            windows_api.CloseHandle(handle)
    assert probes == [(5, 32)]  # MoveFileEx denied; the handle probe confirms sharing.
    assert waits == [0.05]
    assert json.loads(path.read_text(encoding='utf-8')) == {'state': 'new'}
    assert list(tmp_path.glob('*.tmp')) == []


def test_actual_windows_persistent_reader_keeps_old_checkpoint(tmp_path, monkeypatch, windows_api):
    path, before = checkpoint(tmp_path)
    handle = hold_without_delete_sharing(windows_api, path)
    waits = []
    monkeypatch.setattr(storage.time, 'sleep', waits.append)
    try:
        with pytest.raises(PermissionError) as caught:
            storage.write_json_atomic(path, {'state': 'new'})
        assert caught.value.winerror == 5 and caught.value.storage_sharing_violation == 32
        assert caught.value.storage_publication_attempts == 5 and len(waits) == 4
        assert path.read_bytes() == before
    finally:
        windows_api.CloseHandle(handle)
    assert list(tmp_path.glob('*.tmp')) == []


def test_actual_windows_readonly_target_is_not_treated_as_transient_sharing(tmp_path, monkeypatch, windows_api):
    path, before = checkpoint(tmp_path)
    assert windows_api.SetFileAttributesW(str(path.resolve()), 0x1)  # Read-only synthetic target.
    waits = []
    monkeypatch.setattr(storage.time, 'sleep', waits.append)
    try:
        with pytest.raises(PermissionError) as caught:
            storage.write_json_atomic(path, {'state': 'new'})
        assert caught.value.winerror == 5 and caught.value.storage_sharing_violation is None
        assert caught.value.storage_publication_attempts == 1 and waits == []
        assert path.read_bytes() == before
    finally:
        assert windows_api.SetFileAttributesW(str(path.resolve()), 0x80)
    assert list(tmp_path.glob('*.tmp')) == []
