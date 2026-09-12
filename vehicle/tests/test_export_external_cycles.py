"""The export entry point binds external cycle companions before execution."""
import builtins
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pandas as pd
import pytest

from test_daily_cycles import clock, options, plan, reply
from test_export_selected_database import selected_database_export
from test_search import response_data
from vehicle_tracker import cycles


@pytest.fixture
def external_cycle_export(selected_database_export, tmp_path, response_data, clock, monkeypatch):
    exporter, _, notebook, destination = selected_database_export
    cycle_root = tmp_path / 'external_retained'
    cycles.collect_cycle(plan(), **options(cycle_root), post=Mock(return_value=reply(response_data)))
    cycle = cycle_root / 'cycle/cycle.json'
    query = cycle.parent / 'attempt_0001/all'
    code = """from pathlib import Path
import pandas as pd
from vehicle_tracker.cycles import cycle_evidence, read_cycle_history
days, rows = read_cycle_history(CYCLE_REPORTS_OVERRIDE, as_of=AS_OF_OVERRIDE)
assert days.coverage_complete.all() and len(rows) == 3
research_tables = {'observations': rows}
input_paths = set(CYCLE_REPORTS_OVERRIDE) | set(map(Path, rows.source_path))
for cycle in CYCLE_REPORTS_OVERRIDE:
    input_paths.update(cycle_evidence(cycle, as_of=AS_OF_OVERRIDE)[2])
"""
    notebook.write_text(json.dumps(dict(cells=[dict(cell_type='code', id='retained-replay', source=[code])])),
                        encoding='utf-8')
    arguments = list(sys.argv)
    arguments[arguments.index('--cycle-report') + 1] = str(cycle)
    database_option = arguments.index('--database')
    del arguments[database_option:database_option + 2]
    monkeypatch.setattr(sys, 'argv', arguments)
    return exporter, cycle, query, notebook, destination


@pytest.mark.parametrize('suffix', ['.json', '.bin'])
def test_external_cycle_exports_with_unread_companion_and_preserves_source(external_cycle_export, suffix):
    exporter, cycle, query, _, destination = external_cycle_export
    attempt_checkpoint = query / 'attempts/0001.json'
    assert attempt_checkpoint.is_file()  # Actual checkpoint layout from the collector.
    companion = query / 'attempts' / ('unconsumed_companion' + suffix)
    companion.write_bytes(b'{"synthetic_companion": true}')
    before = {exporter.fingerprint_key(path): exporter.sha(path)
              for path in exporter.retained_files(cycle.parent)}
    exporter.main()
    rows = pd.read_csv(destination / 'observations.csv')
    assert len(rows) == 3 and rows.vin.nunique() == 3
    manifest = json.loads((destination / 'manifest.json').read_text(encoding='utf-8'))
    bound = {exporter.fingerprint_key(Path(path)): digest for path, digest in manifest['input_hashes'].items()}
    assert bound[exporter.fingerprint_key(attempt_checkpoint)] == before[exporter.fingerprint_key(attempt_checkpoint)]
    assert bound[exporter.fingerprint_key(companion)] == hashlib.sha256(companion.read_bytes()).hexdigest()
    assert {exporter.fingerprint_key(path): exporter.sha(path)
            for path in exporter.retained_files(cycle.parent)} == before


def test_external_companion_changed_during_execution_is_rejected_even_if_read_again(
        external_cycle_export, monkeypatch):
    exporter, _, query, notebook, destination = external_cycle_export
    companion = query / 'attempts/0001.json'
    assert companion.is_file()
    book = json.loads(notebook.read_text(encoding='utf-8'))
    book['cells'][0]['source'].append(f'Path({str(companion)!r}).read_bytes()\n')
    notebook.write_text(json.dumps(book), encoding='utf-8')
    original_read = cycles.read_cycle_history
    changed = False
    # An already-open fixture stream models an independent writer changing source
    # while the notebook itself remains under the normal read-only guards.
    with companion.open('r+b') as writer:
        def change_before_replay(*args, **kwargs):
            nonlocal changed
            writer.seek(0)
            writer.write(b'{"version": 2}')
            writer.truncate()
            writer.flush()
            changed = True
            return original_read(*args, **kwargs)

        monkeypatch.setattr(cycles, 'read_cycle_history', change_before_replay)
        with pytest.raises(ValueError, match='Input or code changed during execution'):
            exporter.main()
    assert changed and not destination.exists()


def test_external_companion_added_during_execution_has_no_accepted_late_hash(
        external_cycle_export, monkeypatch):
    exporter, _, query, _, destination = external_cycle_export
    companion = query / 'attempts/late_companion.bin'
    original_read, external_open = cycles.read_cycle_history, builtins.open

    def add_before_replay(*args, **kwargs):
        # Simulate another process creating an external fixture after the prescan.
        with external_open(companion, 'xb') as stream:
            stream.write(b'late synthetic response companion')
        return original_read(*args, **kwargs)

    monkeypatch.setattr(cycles, 'read_cycle_history', add_before_replay)
    with pytest.raises(ValueError, match='Input or code changed during execution'):
        exporter.main()
    assert companion.is_file() and not destination.exists()
