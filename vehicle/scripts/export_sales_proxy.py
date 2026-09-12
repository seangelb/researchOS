"""Explicitly export Notebook 23's offline tables into a new research directory."""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', required=True, help='Timezone-aware evidence cutoff')
    parser.add_argument('--destination', required=True, type=Path, help='Must not exist')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    destination = args.destination.resolve()
    if destination.exists():
        raise ValueError('Use a new export directory; existing evidence is never replaced')
    notebook = root / 'vehicle/notebooks/23_carvana_daily_sales_research.ipynb'
    spec = importlib.util.spec_from_file_location('notebook_guards', root / 'scripts/check_notebooks.py')
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    os.environ['MPLBACKEND'] = 'Agg'
    import matplotlib.pyplot as plt
    sources = sorted([notebook, Path(__file__), root / 'scripts/check_notebooks.py',
                      root / 'vehicle/scripts/audit_carvana_vendor_metrics.py',
                      *(root / 'vehicle/src/vehicle_tracker').glob('*.py')])
    source_hashes = {str(p): sha(p) for p in sources}
    # Discover dependencies through execution, but fingerprint data BEFORE reading
    # it. This also catches a concurrent collector changing a register mid-run.
    initial = {str(p.resolve()): sha(p) for folder in ['data', 'config']
               for p in (root / 'vehicle' / folder).rglob('*') if p.is_file()}
    scope = {'__name__': '__main__', 'AS_OF_OVERRIDE': args.as_of}
    log = io.StringIO()
    figures = []
    previous = Path.cwd()
    try:
        os.chdir(root)
        with checker.offline_guards(), redirect_stdout(log), patch.object(plt, 'show', lambda: figures.append(plt.gcf())):
            for cell in json.loads(notebook.read_text(encoding='utf-8'))['cells']:
                if cell['cell_type'] == 'code':
                    exec(compile(''.join(cell['source']), notebook.name + ':' + cell['id'], 'exec'), scope)
    finally:
        os.chdir(previous)
    # The notebook has now finished under network/export/SQLite-write guards.
    inputs = {p.resolve() for p in scope['input_paths'] if p.is_file()}
    # Include original response bytes and companion evidence beside selected projections/reports.
    for path in list(inputs):
        if path.name == 'run_report.json' or path.parent.name == 'raw':
            query_dir = path.parent if path.name == 'run_report.json' else path.parent.parent
            inputs.update(p.resolve() for p in query_dir.rglob('*') if p.is_file() and p.suffix in {'.json', '.bin'})
    before = {str(p): initial.get(str(p)) for p in sorted(inputs)}
    if before != {str(p): sha(p) for p in sorted(inputs)} or source_hashes != {str(p): sha(p) for p in sources}:
        raise ValueError('Input or code changed during execution; no export published')
    destination.mkdir(parents=True, exist_ok=False)
    for name, table in scope['research_tables'].items():
        table.to_csv(destination / (name + '.csv'), index=False, lineterminator='\n')
    for index, figure in enumerate(figures, start=1):
        figure.savefig(destination / f'figure_{index}.png', dpi=160, bbox_inches='tight')
    plt.close('all')
    (destination / 'execution.txt').write_text(log.getvalue(), encoding='utf-8')
    if before != {str(p): sha(p) for p in sorted(inputs)}:
        raise ValueError('Input changed during export; do not use this incomplete export')
    manifest = dict(as_of=args.as_of, generated_at=datetime.now(timezone.utc).isoformat(),
        method='Notebook 23, offline read-only execution followed by explicit CSV export',
        git_head=subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
        python=sys.version, pandas=scope['pd'].__version__, source_hashes=source_hashes,
        input_hashes=before, output_hashes={p.name: sha(p) for p in sorted(destination.iterdir())},
        notes='Uncommitted code is bound by hashes. Not transaction labels or nationally scaled sales.')
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print('Exported', len(scope['research_tables']), 'tables:', destination)


if __name__ == '__main__':
    main()
