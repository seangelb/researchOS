#!/usr/bin/env bash
# Minimal, idempotent, repeatable environment setup for the `variant` research
# package. This is repeatable setup, not an exactly reproducible dependency lock:
# Python patch versions and dependency versions are resolved at install time and
# are not fully pinned (there is no lockfile).
#
# pyproject.toml pins `requires-python = ">=3.11,<3.12"`, so this script provisions
# CPython 3.11 via `uv` (no system Python changes), creates a virtualenv, and
# installs the package with its dev extras exactly as documented:
#     python -m pip install -e ".[dev]"
#
# It intentionally sets up no database, server, or credentials — the research
# tasks run against tracked fixtures and public official reports.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PATH="$HOME/.local/bin:$PATH"

# 1. Ensure `uv` is available (used only to provision CPython 3.11).
if ! command -v uv >/dev/null 2>&1; then
  echo "==> Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 2. Provision CPython 3.11 (satisfies requires-python >=3.11,<3.12).
echo "==> Ensuring CPython 3.11 is installed"
uv python install 3.11
PY311="$(uv python find 3.11)"

# 3. Create the virtualenv if it does not already exist.
if [ ! -x ".venv/bin/python" ]; then
  echo "==> Creating .venv with $PY311"
  "$PY311" -m venv .venv
fi

# 4. Install the package and dependencies with dev extras.
# shellcheck disable=SC1091
. .venv/bin/activate

# Validate the active interpreter is Python 3.11. If a pre-existing .venv uses a
# different version, fail clearly instead of proceeding. We never auto-delete or
# rebuild the environment; the operator decides how to recreate it.
python - <<'PY'
import sys
version = "%d.%d" % sys.version_info[:2]
if sys.version_info[:2] != (3, 11):
    sys.exit(
        "ERROR: .venv uses Python %s, but this project requires Python 3.11.\n"
        "Remove .venv (or recreate it with Python 3.11) and re-run setup." % version
    )
print("Active interpreter: Python %s" % sys.version.split()[0])
PY

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

# 5. Register a Jupyter kernel so the tracked notebooks can be executed with the
#    already-installed Jupyter dependencies (jupyterlab / ipykernel). Registration
#    failures are surfaced (not suppressed) so setup fails clearly.
python -m ipykernel install --user --name python3 --display-name "Python 3.11 (variant)"

echo "==> Environment ready: $(python --version)"
