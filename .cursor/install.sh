#!/usr/bin/env bash
# Minimal, idempotent environment setup for the `variant` research package.
#
# pyproject.toml pins `requires-python = ">=3.11,<3.12"`, so this script obtains a
# reproducible CPython 3.11 via `uv` (no system Python changes), creates a
# virtualenv, and installs the package with its dev extras exactly as documented:
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
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

# 5. Register a Jupyter kernel so the tracked notebooks can be executed with the
#    already-installed Jupyter dependencies (jupyterlab / ipykernel).
python -m ipykernel install --user --name python3 --display-name "Python 3.11 (variant)" >/dev/null 2>&1 || true

echo "==> Environment ready: $(python --version)"
