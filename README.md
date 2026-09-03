# ResearchOS

**ResearchOS** is a personal investment research workspace. Track a watchlist of
positions, monitor valuation metrics (market value, unrealized P/L, upside to
target), and keep dated research notes and a thesis for every idea — all in one
self-contained, offline-friendly app.

<img src="docs/screenshot.png" alt="ResearchOS dashboard" width="800" />

## Stack

| Layer    | Tech |
| -------- | ---- |
| Backend  | Python 3.12 · FastAPI · SQLModel · SQLite · Uvicorn |
| Frontend | React 19 · TypeScript · Vite 6 |
| Tests    | pytest (FastAPI `TestClient`) |

The backend stores data in a local SQLite file (`backend/researchos.db`) and
seeds a starter portfolio on first run, so there are **no external services,
API keys, or accounts required** to develop.

## Project layout

```
backend/            FastAPI application
  app/
    main.py         App entrypoint (creates tables + seeds on startup)
    models.py       SQLModel tables (Position, ResearchNote)
    schemas.py      Request/response models
    metrics.py      Derived valuation metrics
    routers/        positions, notes, portfolio endpoints
    seed.py         Idempotent starter data
  tests/            API tests
frontend/           React + Vite dashboard
scripts/install.sh  Idempotent dependency setup for both apps
.cursor/            Cloud Agent environment config
```

## Local development

Requirements: Python 3.12 (with `python3-venv`) and Node 22+.

```bash
# 1. Install dependencies for both apps (idempotent)
bash scripts/install.sh

# 2. Start the backend API (http://localhost:8000)
cd backend && . .venv/bin/activate && uvicorn app.main:app --reload

# 3. In another terminal, start the frontend (http://localhost:5173)
cd frontend && npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api/*` to the backend
on port 8000 (override with `VITE_API_PROXY_TARGET`).

## API

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET    | `/api/health` | Service health check |
| GET    | `/api/positions` | List positions with computed metrics |
| POST   | `/api/positions` | Create a position |
| GET    | `/api/positions/{id}` | Get one position |
| PATCH  | `/api/positions/{id}` | Update a position |
| DELETE | `/api/positions/{id}` | Delete a position (and its notes) |
| GET    | `/api/positions/{id}/notes` | List a position's research notes |
| POST   | `/api/positions/{id}/notes` | Add a research note |
| DELETE | `/api/notes/{id}` | Delete a note |
| GET    | `/api/portfolio/summary` | Portfolio totals + sector allocation |

Interactive API docs are available at http://localhost:8000/docs.

## Tests

```bash
cd backend && . .venv/bin/activate && pytest
```

## Cloud Agent environment

`.cursor/environment.json` configures Cursor Cloud Agents:

- **install**: `scripts/install.sh` sets up the Python venv and Node modules.
- **terminals**: `backend` (Uvicorn) and `frontend` (Vite) start automatically.
- **ports**: 8000 (API) and 5173 (Web) are exposed.
