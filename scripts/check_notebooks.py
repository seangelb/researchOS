"""Run Carvana notebook cells with network, exports and SQLite writes blocked.

This is an offline regression check for trusted repository notebooks, not a sandbox
for arbitrary code. It does not save notebook outputs or alter approval bindings.
"""
from __future__ import annotations

import argparse
import contextlib
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

APPROVAL_BLOCK = "FAIL CLOSED: analyst-approval binding mismatch:"


@contextlib.contextmanager
def offline_guards():
    """Allow SQLite only through an explicit read-only URI; block HTTP and exports."""
    import pandas as pd
    import requests
    import IPython.display

    original_connect = sqlite3.connect

    def deny(*args, **kwargs):
        raise RuntimeError("Offline notebook check blocks network and exports")

    def readonly(database, *args, **kwargs):
        uri = str(database)
        if not kwargs.get("uri") or not uri.startswith("file:") or parse_qs(urlsplit(uri).query).get("mode") != ["ro"]:
            raise RuntimeError("Offline notebook check requires SQLite URI mode=ro")
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
        ):
            stack.enter_context(patch(target, replacement))
        yield


def run_notebook(path: Path, root: Path) -> dict:
    import matplotlib.pyplot as plt

    notebook = json.loads(path.read_text(encoding="utf-8"))
    scope = {"__name__": "__main__"}
    output, completed = io.StringIO(), 0
    previous_cwd = Path.cwd()
    result = {"notebook": path.name, "status": "PASS"}
    try:
        os.chdir(root)
        with offline_guards(), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            for index, cell in enumerate(notebook["cells"]):
                if cell["cell_type"] == "code":
                    exec(compile("".join(cell["source"]), f"{path.name}:cell{index}", "exec"), scope)
                    completed += 1
                    plt.close("all")
    except (Exception, SystemExit) as exc:
        message = str(exc)
        result.update(status="BLOCKED" if isinstance(exc, SystemExit) and message.startswith(APPROVAL_BLOCK) else "FAIL",
                      cell=index, reason=message, output_tail=output.getvalue()[-1200:])
    finally:
        os.chdir(previous_cwd)
        plt.close("all")
    result["code_cells"] = completed
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    os.environ["MPLBACKEND"] = "Agg"
    with tempfile.TemporaryDirectory(prefix="researchos-notebook-mpl-") as cache:
        os.environ["MPLCONFIGDIR"] = cache
        results = [run_notebook(path, project)
                   for project in (args.root / "vehicle",)
                   for path in sorted((project / "notebooks").glob("*.ipynb"))]
    for result in results:
        print(json.dumps(result, ensure_ascii=True))
    # Approval blocks are understandable but never counted as successful execution.
    return 1 if any(r["status"] != "PASS" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
