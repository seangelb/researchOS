"""ResearchOS FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .database import init_db
from .routers import notes, portfolio, positions
from .seed import seed_if_empty


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_if_empty()
    yield


app = FastAPI(
    title="ResearchOS API",
    description="Personal investment research workspace API.",
    version=__version__,
    lifespan=lifespan,
)

# The Vite dev server proxies /api, but allow direct cross-origin access too
# so the API is easy to exercise during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(positions.router)
app.include_router(notes.router)
app.include_router(portfolio.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "researchos-api", "version": __version__}
