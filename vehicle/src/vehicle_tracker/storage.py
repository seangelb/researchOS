"""Explicit snapshot writes; analysis opens SQLite read-only."""
import hashlib
import json
from pathlib import Path
import sqlite3
import os
import tempfile
import time

import pandas as pd

from vehicle_tracker.carvana import parse_capture


_CHECKPOINT_RETRY_DELAYS = (0.05, 0.10, 0.20, 0.40)


def _checkpoint_sharing_violation(error, source, destination):
    """Identify Windows sharing denial without retrying general access failures.

    MoveFileEx (used by os.replace) can report access denied, error 5, when an
    open reader omits delete sharing. A DELETE-access handle probe distinguishes
    that case: it must itself report sharing/lock violation 32/33. Opening and
    closing this handle never deletes or changes either file or its permissions.
    """
    winerror = getattr(error, 'winerror', None)
    if winerror in (32, 33):
        return winerror
    if os.name != 'nt' or winerror != 5:
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    for path in (source, destination):
        # DELETE access, share read/write/delete, OPEN_EXISTING, normal attributes.
        handle = kernel.CreateFileW(str(Path(path).resolve()), 0x10000, 0x7, None, 3, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            code = ctypes.get_last_error()
            if code in (32, 33):
                return code
        else:
            kernel.CloseHandle(handle)
    return None


def write_json_atomic(path, data):
    """Atomically publish a checkpoint; only confirmed Windows sharing gets retries.

    Retry the same fully written local temporary file at most four times, waiting
    0.75 seconds in total. No source/network operation or request budget is retried.
    A persistent error leaves the old checkpoint intact and is raised to the caller.
    """
    path = Path(path)
    temporary = None
    primary_error = None
    operation, attempts = 'create_checkpoint_temporary', 0
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name+'.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            operation = 'write_checkpoint_temporary'
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            operation = 'sync_checkpoint_temporary'
            os.fsync(stream.fileno())
        operation = 'replace_checkpoint'
        for attempts, delay in enumerate((*_CHECKPOINT_RETRY_DELAYS, None), start=1):
            try:
                os.replace(temporary, path)
                break
            except OSError as error:
                sharing = _checkpoint_sharing_violation(error, temporary, path)
                error.storage_sharing_violation = sharing
                if sharing is None or delay is None:
                    raise
                time.sleep(delay)
    except BaseException as error:
        primary_error = error
        if isinstance(error, OSError):
            error.storage_operation = operation
            error.storage_target = str(path.resolve())
            error.storage_publication_attempts = attempts
        raise
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_error:
                if primary_error is None:
                    cleanup_error.storage_operation = 'remove_checkpoint_temporary'
                    cleanup_error.storage_target = str(temporary)
                    cleanup_error.storage_publication_attempts = 1
                    raise
                # Preserve the publication error and its diagnostics if a locked
                # temporary file also cannot be removed. Never erase the old file.
                primary_error.add_note(f'Checkpoint temporary cleanup also failed: {cleanup_error}')


def retain_bytes(content: bytes, raw_directory: Path, *, suffix: str = '.bin') -> Path:
    """Publish complete, immutable bytes; a failed write cannot poison their hash path."""
    if not isinstance(content, bytes) or suffix not in {'.bin', '.json'}:
        raise ValueError('Require bytes and an explicit .bin or .json retention suffix')
    digest = hashlib.sha256(content).hexdigest()
    raw_directory = Path(raw_directory)
    raw_directory.mkdir(parents=True, exist_ok=True)
    path = raw_directory / f'{digest}{suffix}'
    # Fixed daily destinations can fit Windows' ordinary path limit while the
    # 64-character content hash does not. Prefix only that long final filename;
    # keep config, query, checkpoint and SQLite paths in their existing form.
    if os.name == 'nt' and len(str(path.resolve())) >= 260:
        absolute = str(path.resolve())
        if not absolute.startswith('\\\\?\\'):
            path = Path('\\\\?\\UNC\\' + absolute[2:] if absolute.startswith('\\\\') else '\\\\?\\' + absolute)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError('Retained capture content mismatch')
        return path
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=raw_directory, prefix='capture.',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # A hard link publishes the finished bytes without replacing old evidence.
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError('Retained capture content mismatch')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def retain_capture(capture: dict, raw_directory: Path) -> Path:
    """Retain the extracted browser document as immutable JSON, keyed by its bytes."""
    content = (json.dumps(capture, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    return retain_bytes(content, raw_directory, suffix='.json')


def store_capture(database: Path, *, run_id: str, page_number: int, raw_file: Path,
                  error: str | None = None) -> int:
    """Append one page attempt atomically; identical recovery imports are no-ops.

    The destination is explicitly chosen by the caller. Refuse unrelated databases.
    Partial/failed runs remain visible and never certify full inventory coverage.
    """
    raw_file = Path(raw_file).resolve()
    content = raw_file.read_bytes()
    capture = json.loads(content)
    frame = parse_capture(capture) if error is None else pd.DataFrame()
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables - {'vehicle_captures', 'vehicle_observations'}:
            raise ValueError('Refusing to write into an unrelated database')
        capture_row = (run_id, page_number, capture.get('captured_at_utc'), capture.get('page_url'),
            capture.get('zip_code'), capture.get('reported_total_text'),
            'failed' if error else 'parsed', len(frame), error, str(raw_file),
            hashlib.sha256(content).hexdigest(), 'unverified')
        values = frame.astype(object).where(frame.notna(), None)
        observation_rows = [(run_id, page_number, *row)
                            for row in values.itertuples(index=False, name=None)]
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('''CREATE TABLE IF NOT EXISTS vehicle_captures (
                run_id TEXT, page_number INTEGER, observed_at_utc TEXT, source_url TEXT,
                zip_code TEXT, reported_total_text TEXT, status TEXT, row_count INTEGER,
                error TEXT, raw_file TEXT, source_sha256 TEXT, coverage TEXT,
                PRIMARY KEY (run_id, page_number))''')
            prior = connection.execute('SELECT * FROM vehicle_captures WHERE run_id=? AND page_number=?',
                                       (run_id, page_number)).fetchone()
            if prior is not None:
                actual = (connection.execute('SELECT * FROM vehicle_observations WHERE run_id=? AND page_number=?',
                    (run_id, page_number)).fetchall() if 'vehicle_observations' in tables else [])
                if (prior != capture_row or sorted(actual, key=lambda row: row[2:4]) !=
                        sorted(observation_rows, key=lambda row: row[2:4])):
                    raise sqlite3.IntegrityError('Previously stored page conflicts with retained evidence')
                return len(frame)
            connection.execute('''INSERT INTO vehicle_captures VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', capture_row)
            if not frame.empty:
                # The schema is explicit and stays separate from gaming observations.
                connection.execute('''CREATE TABLE IF NOT EXISTS vehicle_observations (
                    run_id TEXT, page_number INTEGER, retailer TEXT, listing_id TEXT, vin TEXT,
                    observed_at_utc TEXT, year INTEGER, make TEXT, model TEXT,
                    mileage_miles INTEGER, asking_price_usd REAL, condition_native TEXT,
                    availability_native TEXT, card_text TEXT, listing_url TEXT, source_url TEXT,
                    PRIMARY KEY (run_id, retailer, listing_id))''')
                connection.executemany('INSERT INTO vehicle_observations VALUES (' + ','.join(['?'] * 16) + ')',
                    observation_rows)
        return len(frame)
    finally:
        connection.close()


def read_snapshots(database: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return capture attempts and listing observations through a read-only connection."""
    connection = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        captures = pd.read_sql_query('SELECT * FROM vehicle_captures ORDER BY observed_at_utc, page_number', connection)
        has_rows = connection.execute("SELECT 1 FROM sqlite_master WHERE name='vehicle_observations'").fetchone()
        observations = pd.read_sql_query('SELECT * FROM vehicle_observations', connection) if has_rows else pd.DataFrame()
        return captures, observations
    finally:
        connection.close()
