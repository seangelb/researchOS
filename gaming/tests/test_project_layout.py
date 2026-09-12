"""Focused path regressions for the gaming/ and vehicle/ project layout."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from variant_gaming.common import project_root

GAMING = Path(__file__).resolve().parents[1]
REPOSITORY = GAMING.parent


@pytest.mark.parametrize('start', [REPOSITORY, GAMING, GAMING / 'notebooks'])
def test_gaming_root_from_supported_launch_locations(monkeypatch, start):
    monkeypatch.chdir(start)
    assert project_root() == GAMING


def test_project_root_does_not_fall_back_to_unrelated_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match='gaming'):
        project_root()


@pytest.mark.parametrize('prefix', ['00', '10', '11', '20', '30', '31', '44', '45', '90', '91', '92', '93'])
@pytest.mark.parametrize('start', [REPOSITORY, GAMING, GAMING / 'notebooks'])
def test_actual_notebook_bootstrap_finds_gaming(monkeypatch, prefix, start):
    notebook = next((GAMING / 'notebooks').glob(f'{prefix}_*.ipynb'))
    cells = json.loads(notebook.read_text(encoding='utf-8'))['cells']
    cell = next(''.join(c['source']) for c in cells
                if c['cell_type'] == 'code' and 'ROOT = Path.cwd()' in ''.join(c['source']))
    bootstrap = 'ROOT = Path.cwd()' + cell.split('ROOT = Path.cwd()', 1)[1]
    bootstrap = bootstrap.split('sys.path.insert', 1)[0]
    monkeypatch.chdir(start)
    scope = {'Path': Path}
    exec(bootstrap, scope)
    assert scope['ROOT'] == GAMING


def test_moved_files_still_match_original_approved_git_blobs():
    # Historical paths intentionally stay repository-relative at the approved commit.
    # Working files are resolved from gaming/, without changing approval identities.
    notebook = json.loads((GAMING / 'notebooks/91_flut_ma_sportsbook_signal.ipynb').read_text(encoding='utf-8'))
    source = next(''.join(c['source']) for c in notebook['cells']
                  if c['cell_type'] == 'code' and 'PIPELINE_BIND_PATHS =' in ''.join(c['source']))
    scope = {}
    exec(source.split('hash_original_before = sha256(ORIGINAL_DB)', 1)[0], scope)
    for rel in scope['PIPELINE_BIND_PATHS']:
        assert scope['git_blob_hash'](scope['APPROVED_PIPELINE_COMMIT'], rel) == scope['working_tree_blob_hash'](GAMING / rel)


def test_existing_environment_launches_both_packages_without_install():
    result = subprocess.run(['powershell', '-NoProfile', '-File', str(REPOSITORY / 'scripts/start_jupyter.ps1'), '-Check', '-Python', sys.executable],
                            cwd=REPOSITORY.parent, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Both research packages import successfully' in result.stdout
