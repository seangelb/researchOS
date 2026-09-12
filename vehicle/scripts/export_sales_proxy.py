"""Explicitly export retained research tables into a new directory; never collect."""
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


def fingerprint_key(path):
    """One hash key for Windows path aliases; retain readable paths for actual I/O."""
    value = str(path.resolve())
    if os.name == 'nt' and value.startswith('\\\\?\\UNC\\'):
        return '\\\\' + value[8:]
    if os.name == 'nt' and value.startswith('\\\\?\\'):
        return value[4:]
    return value


def retained_files(directory):
    """Enumerate retained inputs without Windows silently skipping long names."""
    value = str(directory.resolve())
    if os.name == 'nt' and not value.startswith('\\\\?\\'):
        value = '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value
    return (path for path in Path(value).rglob('*') if path.is_file())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', required=True, help='Timezone-aware evidence cutoff')
    parser.add_argument('--destination', required=True, type=Path, help='Must not exist')
    parser.add_argument('--notebook', choices=['20', '23', '24', '30'], default='23', help='Research notebook; default 23')
    parser.add_argument('--cycle-report', type=Path, action='append', help='Notebook 20: exact retained cycle; repeat for a comparable pair')
    parser.add_argument('--database', type=Path, help='Notebook 20: optional existing analysis database; omitted with --cycle-report replays sources')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    destination = args.destination.resolve()
    if destination.exists():
        raise ValueError('Use a new export directory; existing evidence is never replaced')
    if (args.cycle_report or args.database) and args.notebook != '20':
        parser.error('--cycle-report and --database are Notebook 20 options')
    if args.database and not args.cycle_report:
        parser.error('--database requires explicit --cycle-report selection')
    if args.database and not args.database.is_file():
        parser.error('--database must name an existing database file')
    name = {'20': '20_carvana_history_analysis.ipynb', '23': '23_carvana_daily_sales_research.ipynb',
            '24': '24_carvana_status_experiment.ipynb', '30': '30_carvana_sales_expectations.ipynb'}[args.notebook]
    notebook = root / 'vehicle/notebooks' / name
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
    initial = {fingerprint_key(p): sha(p) for folder in ['data', 'config']
               for p in retained_files(root / 'vehicle' / folder)}
    initial.update(source_hashes)
    # Selected cycles may live outside vehicle/data. Fingerprint their retained
    # companions now, including JSON/bin files only discovered after execution.
    for cycle in args.cycle_report or []:
        for path in retained_files(cycle.parent):
            if path.suffix in {'.json', '.bin'}:
                initial.setdefault(fingerprint_key(path), sha(path))
    # SQLite reads bypass Path.read_bytes/read_text, including databases selected
    # outside vehicle/data. Keep any earlier prescan hash if this path was covered.
    if args.database:
        initial.setdefault(fingerprint_key(args.database), sha(args.database))
    # Forecasts may explicitly reference files outside vehicle/data. Fingerprint
    # those reads as well, before execution consumes their content. Existing data
    # retains the earlier pre-run fingerprint so concurrent changes fail closed.
    original_read_bytes, original_read_text = Path.read_bytes, Path.read_text
    observed_paths = set()
    def observed_read_bytes(path):
        content = original_read_bytes(path)
        resolved = path.resolve()
        observed_paths.add(resolved)
        initial.setdefault(fingerprint_key(resolved), hashlib.sha256(content).hexdigest())
        return content
    def observed_read_text(path, *args, **kwargs):
        observed_read_bytes(path)
        return original_read_text(path, *args, **kwargs)
    scope = {'__name__': '__main__', 'AS_OF_OVERRIDE': args.as_of}
    if args.notebook == '20':
        scope['HEALTH_AS_OF_OVERRIDE'] = args.as_of
    if args.cycle_report:
        scope.update(CYCLE_REPORTS_OVERRIDE=[p.resolve() for p in args.cycle_report],
                     DAILY_DATABASE_OVERRIDE=args.database.resolve() if args.database else None)
    log = io.StringIO()
    figures = []
    previous = Path.cwd()
    try:
        os.chdir(root)
        with checker.offline_guards(), redirect_stdout(log), patch.object(plt, 'show', lambda: figures.append(plt.gcf())), \
                patch.object(Path, 'read_bytes', observed_read_bytes), patch.object(Path, 'read_text', observed_read_text):
            for cell in json.loads(notebook.read_text(encoding='utf-8'))['cells']:
                if cell['cell_type'] == 'code':
                    exec(compile(''.join(cell['source']), notebook.name + ':' + cell['id'], 'exec'), scope)
    finally:
        os.chdir(previous)
    # The notebook has now finished under network/export/SQLite-write guards.
    inputs = {p.resolve() for p in scope['input_paths'] if p.is_file()}
    if args.database:
        inputs.add(args.database.resolve())
    inputs.update(observed_paths)  # Includes unregistered operational-health evidence.
    # Include original response bytes and companion evidence beside selected projections/reports.
    for path in list(inputs):
        if path.name == 'run_report.json' or path.parent.name == 'raw':
            query_dir = path.parent if path.name == 'run_report.json' else path.parent.parent
            inputs.update(p.resolve() for p in retained_files(query_dir) if p.suffix in {'.json', '.bin'})
    before = {str(p): initial.get(fingerprint_key(p)) for p in sorted(inputs)}
    if before != {str(p): sha(p) for p in sorted(inputs)} or source_hashes != {str(p): sha(p) for p in sources}:
        raise ValueError('Input or code changed during execution; no export published')
    destination.mkdir(parents=True, exist_ok=False)
    table_availability = []
    for name, table in scope['research_tables'].items():
        table_availability.append(dict(table=name, rows=len(table), columns=len(table.columns),
            status='unavailable_no_schema' if len(table.columns) == 0 else
                   'schema_defined_no_rows' if table.empty else 'rows_available'))
        # A comparison withheld before its schema exists is not an observed zero.
        # Retain a readable explanation instead of an empty, unparseable CSV file.
        output = (scope['pd'].DataFrame({'unavailable_reason': [
            'No comparison/table is available at this cutoff; inspect coverage and input selection.']})
            if len(table.columns) == 0 else table)
        output.to_csv(destination / (name + '.csv'), index=False, lineterminator='\n')
    scope['pd'].DataFrame(table_availability).to_csv(destination / 'table_availability.csv', index=False, lineterminator='\n')
    for index, figure in enumerate(figures, start=1):
        figure.savefig(destination / f'figure_{index}.png', dpi=160, bbox_inches='tight')
    plt.close('all')
    (destination / 'execution.txt').write_text(log.getvalue(), encoding='utf-8')
    if before != {str(p): sha(p) for p in sorted(inputs)}:
        raise ValueError('Input changed during export; do not use this incomplete export')
    manifest = dict(as_of=args.as_of, generated_at=datetime.now(timezone.utc).isoformat(),
        method=f'Notebook {args.notebook}, offline read-only execution followed by explicit CSV export',
        git_head=subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
        python=sys.version, pandas=scope['pd'].__version__, source_hashes=source_hashes,
        input_hashes=before, tables=table_availability, output_hashes={p.name: sha(p) for p in sorted(destination.iterdir())},
        notes='Uncommitted code is bound by hashes. Not transaction labels or nationally scaled sales.')
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print('Exported', len(scope['research_tables']), 'tables:', destination)


if __name__ == '__main__':
    main()
