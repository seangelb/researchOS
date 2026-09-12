"""A DB-only hash must not accept live WAL contents or impossible capture clocks."""
import hashlib
import sqlite3

import pandas as pd
import pytest

from test_flut_expectations import setup
from variant_gaming.refresh import load_validated_snapshot


def load(setup, **changes):
    return load_validated_snapshot(root=setup["root"], database=setup["database"],
                                   **dict({"as_of": "2026-09-12T12:00:00Z"}, **changes))


def test_loader_returns_bound_rows_and_closes_its_connection(setup):
    result = load(setup)
    assert len(result["observations"]) == 6
    assert result["binding"]["database_sha256"] == hashlib.sha256(setup["database"].read_bytes()).hexdigest()
    # On Windows an open SQLite file handle prevents renaming: both reads closed.
    renamed = setup["database"].with_suffix(".renamed")
    setup["database"].rename(renamed)
    renamed.rename(setup["database"])


@pytest.mark.parametrize("cutoff", ["NaT", "2026-09-12", "2099-01-01T00:00:00Z", "2026-09-10T00:00:00Z"])
def test_invalid_cutoff_cannot_accept_a_snapshot(setup, cutoff):
    with pytest.raises(ValueError):
        load(setup, as_of=cutoff)


def test_explicit_selection_hash_and_raw_bytes_must_match(setup):
    with pytest.raises(ValueError, match="selected snapshot hash"):
        load(setup, expected_sha256="0"*64)
    setup["raw"].write_bytes(b"changed regulator source")
    with pytest.raises(ValueError, match="Source hash mismatch"):
        load(setup)


def test_sqlite_main_file_hash_cannot_hide_committed_wal_rows(setup):
    connection = sqlite3.connect(setup["database"])
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = hashlib.sha256(setup["database"].read_bytes()).hexdigest()
        setup["receipts"]()
        connection.execute("UPDATE gaming_results SET handle = 99999")
        connection.commit()
        assert hashlib.sha256(setup["database"].read_bytes()).hexdigest() == before
        with pytest.raises(ValueError, match="WAL"):
            load(setup)
    finally:
        connection.close()
