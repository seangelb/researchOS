"""Run research notebook cells with network, exports and SQLite writes blocked.

This is an offline regression check for trusted repository notebooks, not a sandbox
for arbitrary code. It does not save notebook outputs or alter approval bindings.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import url2pathname

APPROVAL_BLOCK = "FAIL CLOSED: analyst-approval binding mismatch:"


@contextlib.contextmanager
def offline_guards():
    """Allow SQLite only through an explicit read-only URI; block HTTP and exports."""
    import pandas as pd
    import requests
    import IPython.display

    original_connect = sqlite3.connect
    original_open, original_io_open = builtins.open, io.open

    def deny(*args, **kwargs):
        raise RuntimeError("Offline notebook check blocks network and exports")

    def reader_only(original):
        def open_readonly(file, mode='r', *args, **kwargs):
            if any(flag in mode for flag in 'wax+'):
                raise RuntimeError('Offline notebook check blocks file writes')
            return original(file, mode, *args, **kwargs)
        return open_readonly

    def readonly(database, *args, **kwargs):
        uri = str(database)
        if not kwargs.get("uri") or not uri.startswith("file:") or parse_qs(urlsplit(uri).query).get("mode") != ["ro"]:
            raise RuntimeError("Offline notebook check requires SQLite URI mode=ro")
        parts = urlsplit(uri)
        local_path = Path(url2pathname(("//" + parts.netloc if parts.netloc else "") + parts.path))
        if not local_path.exists():
            raise FileNotFoundError(errno.ENOENT, "Missing retained SQLite database", str(local_path))
        return original_connect(database, *args, **kwargs)

    with contextlib.ExitStack() as stack:
        for target, replacement in (
            ("requests.sessions.Session.request", deny),
            ("socket.socket.connect", deny),
            ("socket.create_connection", deny),
            ("pandas.DataFrame.to_csv", deny),
            ("pandas.Series.to_csv", deny),
            ("sqlite3.connect", readonly),
            ("IPython.display.display", lambda *a, **k: None),
            ("builtins.open", reader_only(original_open)),
            ("io.open", reader_only(original_io_open)),
            ("pathlib.Path.mkdir", deny),
            ("pathlib.Path.unlink", deny),
            ("pathlib.Path.rename", deny),
            ("pathlib.Path.replace", deny),
            ("sys.dont_write_bytecode", True),
        ):
            stack.enter_context(patch(target, replacement))
        yield


def run_notebook(path: Path, root: Path) -> dict:
    import matplotlib.pyplot as plt

    notebook = json.loads(path.read_text(encoding="utf-8"))
    scope = {"__name__": "__main__"}
    output, completed, executable_cells = io.StringIO(), 0, 0
    previous_cwd = Path.cwd()
    result = {"notebook": path.name, "status": "PASS"}
    try:
        os.chdir(root)
        with offline_guards(), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            for index, cell in enumerate(notebook["cells"]):
                if cell["cell_type"] == "code":
                    filename = f"{path.name}:cell{index}"
                    syntax = ast.parse("".join(cell["source"]), filename=filename)
                    executable_cells += bool(syntax.body)
                    exec(compile(syntax, filename, "exec"), scope)
                    completed += 1
                    plt.close("all")
    except (Exception, SystemExit) as exc:
        message = str(exc)
        missing_data = (isinstance(exc, FileNotFoundError) and exc.filename is not None
                        and Path(exc.filename).resolve().is_relative_to((root / "data").resolve()))
        blocked = missing_data or (isinstance(exc, SystemExit) and message.startswith(APPROVAL_BLOCK))
        if missing_data:
            message = f"Missing retained data: {exc.filename}. Restore the matching archive before rerunning."
        result.update(status="BLOCKED" if blocked else "FAIL",
                      cell=index, reason=message, output_tail=output.getvalue()[-1200:])
    finally:
        os.chdir(previous_cwd)
        plt.close("all")
    result["code_cells"] = completed
    if result["status"] == "PASS" and not executable_cells:
        result.update(status="FAIL", reason="Notebook has no executable code cells")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--project", choices=("gaming", "vehicle", "all"), default="vehicle")
    args = parser.parse_args()
    projects = ["gaming", "vehicle"] if args.project == "all" else [args.project]
    jobs = []
    for name in projects:
        project = (args.root / name).resolve()
        directory = project / "notebooks"
        if not directory.is_dir():
            parser.error(f"Notebook directory does not exist: {directory}")
        if name == "gaming":
            active = ("00", "10", "11", "20", "30", "31", "90", "91", "92", "93")
            notebooks = [p for p in sorted(directory.glob("*.ipynb")) if p.name[:2] in active]
            missing = [prefix for prefix in active if not any(p.name.startswith(prefix + "_") for p in notebooks)]
            if missing:
                parser.error(f"Missing active gaming notebooks: {missing}")
        else:
            notebooks = sorted(directory.glob("*.ipynb"))
        if not notebooks:
            parser.error(f"No notebooks found in: {directory}")
        jobs.extend((path, project, name) for path in notebooks)
    os.environ["MPLBACKEND"] = "Agg"
    with tempfile.TemporaryDirectory(prefix="researchos-notebook-mpl-") as cache:
        os.environ["MPLCONFIGDIR"] = cache
        results = []
        for path, project, name in jobs:
            result = run_notebook(path, project)
            if args.project == "all":
                result["project"] = name
            results.append(result)
    for result in results:
        print(json.dumps(result, ensure_ascii=True))
    # Missing data and approval blocks never count as successful execution.
    return 1 if any(r["status"] != "PASS" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
