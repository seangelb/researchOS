"""The notebook check must actually discover and execute notebook code."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


CHECKER = Path(__file__).resolve().parents[2] / 'scripts/check_notebooks.py'


def check(root):
    return subprocess.run([sys.executable, '-B', str(CHECKER), '--root', str(root)],
                          capture_output=True, text=True)


@pytest.mark.parametrize('exists', [False, True])
def test_missing_or_empty_notebook_directory_fails(tmp_path, exists):
    if exists:
        (tmp_path / 'vehicle/notebooks').mkdir(parents=True)
    result = check(tmp_path)
    assert result.returncode != 0
    assert ('No notebooks found' if exists else 'Notebook directory does not exist') in result.stderr
    assert not result.stdout


@pytest.mark.parametrize('source', [None, '', '  \n', '# Only a comment\n'])
def test_notebook_without_executable_code_fails(tmp_path, source):
    directory = tmp_path / 'vehicle/notebooks'
    directory.mkdir(parents=True)
    cells = [dict(cell_type='markdown', source=['Synthetic test notebook.'])]
    if source is not None:
        cells.append(dict(cell_type='code', source=[source]))
    (directory / 'empty.ipynb').write_text(json.dumps(dict(cells=cells)), encoding='utf-8')
    # A valid sibling must not hide an empty notebook's failure.
    (directory / 'valid.ipynb').write_text(json.dumps(dict(cells=[
        dict(cell_type='code', source=['assert 2 + 2 == 4'])])), encoding='utf-8')
    result = check(tmp_path)
    assert result.returncode == 1, result.stderr
    outcomes = {row['notebook']: row for row in map(json.loads, result.stdout.splitlines())}
    assert outcomes['empty.ipynb']['status'] == 'FAIL'
    assert outcomes['empty.ipynb']['reason'] == 'Notebook has no executable code cells'
    assert outcomes['valid.ipynb']['status'] == 'PASS'


def test_executable_notebook_passes_without_changing_source(tmp_path):
    directory = tmp_path / 'vehicle/notebooks'
    directory.mkdir(parents=True)
    notebook = directory / 'valid.ipynb'
    notebook.write_text(json.dumps(dict(cells=[
        dict(cell_type='code', source=['assert 2 + 2 == 4'])])), encoding='utf-8')
    before = notebook.read_bytes()
    result = check(tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == dict(notebook='valid.ipynb', status='PASS', code_cells=1)
    assert notebook.read_bytes() == before
