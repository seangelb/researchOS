"""Exercise the export CLI with actual SQLite reads outside its data prescan."""
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def selected_database_export(tmp_path, monkeypatch):
    project = tmp_path / 'synthetic_project'
    script = project / 'vehicle/scripts/export_sales_proxy.py'
    checker = project / 'scripts/check_notebooks.py'
    for source, destination in [(ROOT / 'vehicle/scripts/export_sales_proxy.py', script),
                                (ROOT / 'scripts/check_notebooks.py', checker)]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    (script.parent / 'audit_carvana_vendor_metrics.py').write_text('# Synthetic source placeholder\n')
    report = project / 'vehicle/data/cycle.json'
    report.parent.mkdir(parents=True)
    report.write_text('{}', encoding='utf-8')
    database = tmp_path / 'selected.sqlite'
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('CREATE TABLE observations (observed_vins INTEGER)')
        connection.execute('INSERT INTO observations VALUES (3)')
        connection.commit()
    notebook = project / 'vehicle/notebooks/20_carvana_history_analysis.ipynb'
    notebook.parent.mkdir(parents=True)
    code = """import pandas as pd
import sqlite3
from contextlib import closing
with closing(sqlite3.connect(DAILY_DATABASE_OVERRIDE.as_uri() + '?mode=ro', uri=True)) as connection:
    rows = pd.read_sql_query('SELECT observed_vins FROM observations', connection)
research_tables = {'observations': rows}
input_paths = [DAILY_DATABASE_OVERRIDE, *CYCLE_REPORTS_OVERRIDE]
"""
    notebook.write_text(json.dumps(dict(cells=[dict(cell_type='code', id='synthetic-read', source=[code])])),
                        encoding='utf-8')
    spec = importlib.util.spec_from_file_location('selected_database_export', script)
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    # The fixture has no Git repository. Only the manifest's unrelated Git lookup is stubbed.
    monkeypatch.setattr(exporter.subprocess, 'check_output', lambda *args, **kwargs: 'synthetic-test-head\n')
    monkeypatch.setenv('MPLCONFIGDIR', str(tmp_path / 'matplotlib'))
    destination = tmp_path / 'export'
    monkeypatch.setattr(sys, 'argv', [str(script), '--notebook', '20',
        '--as-of', '2026-09-12T14:22:50.159714+00:00', '--cycle-report', str(report),
        '--database', str(database), '--destination', str(destination)])
    return exporter, database, notebook, destination


def test_cli_exports_unchanged_external_database_and_binds_its_hash(selected_database_export):
    exporter, database, _, destination = selected_database_export
    original = database.read_bytes()
    exporter.main()
    assert pd.read_csv(destination / 'observations.csv').observed_vins.tolist() == [3]
    manifest = json.loads((destination / 'manifest.json').read_text())
    assert manifest['input_hashes'][str(database.resolve())] == hashlib.sha256(original).hexdigest()
    assert database.read_bytes() == original


def test_cli_rejects_database_changed_after_fingerprint_even_when_read_again(
        selected_database_export, monkeypatch):
    exporter, database, notebook, destination = selected_database_export
    book = json.loads(notebook.read_text())
    book['cells'][0]['source'].append('DAILY_DATABASE_OVERRIDE.read_bytes()\n')
    notebook.write_text(json.dumps(book), encoding='utf-8')
    original_sha = exporter.sha
    changed = False

    def fingerprint_then_change(path):
        nonlocal changed
        digest = original_sha(path)
        if path.resolve() == database.resolve() and not changed:
            with closing(sqlite3.connect(database)) as connection:
                connection.execute('UPDATE observations SET observed_vins = 4')
                connection.commit()
            changed = True
        return digest

    monkeypatch.setattr(exporter, 'sha', fingerprint_then_change)
    with pytest.raises(ValueError, match='Input or code changed during execution'):
        exporter.main()
    assert changed and not destination.exists()


@pytest.mark.parametrize('path_kind', ['absent', 'directory'])
def test_cli_rejects_nonfile_database_before_execution(selected_database_export, capsys, path_kind):
    exporter, database, notebook, destination = selected_database_export
    database.unlink()
    if path_kind == 'directory':
        database.mkdir()
    # If validation reaches notebook execution, this error makes that visible.
    notebook.write_text(json.dumps(dict(cells=[dict(cell_type='code', id='must-not-run',
        source=["raise AssertionError('Notebook executed before database validation')"])])), encoding='utf-8')
    with pytest.raises(SystemExit) as error:
        exporter.main()
    assert error.value.code == 2
    assert '--database must name an existing database file' in capsys.readouterr().err
    assert not destination.exists()
