#!/usr/bin/env bash
# Idempotent dependency setup for the ResearchOS workspace.
# Safe to re-run: it refreshes the Python venv and Node modules in place.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> ResearchOS install starting in $ROOT"

# --- Backend (Python / FastAPI) ---
echo "==> Setting up backend virtualenv"
cd "$ROOT/backend"
if [ ! -d ".venv" ]; then
  if ! python3 -m venv .venv 2>/dev/null; then
    # The venv module (ensurepip) is missing on some base images; install it.
    echo "==> python3-venv missing, installing it"
    sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv
    python3 -m venv .venv
  fi
fi
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
pip install -r requirements.txt
deactivate

# --- Frontend (Node / Vite) ---
echo "==> Installing frontend dependencies"
cd "$ROOT/frontend"
if [ -f package-lock.json ]; then
  npm ci
else
  npm install
fi

echo "==> ResearchOS install complete"
