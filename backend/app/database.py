"""Database engine and session management for ResearchOS."""
from __future__ import annotations

import os
from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

# Store the SQLite database alongside the backend package by default so the
# workspace stays self-contained and works without any external services.
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "researchos.db")
DATABASE_URL = os.environ.get("RESEARCHOS_DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")

# check_same_thread=False lets the SQLite connection be shared across the
# request threads that Uvicorn uses.
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)


def init_db() -> None:
    """Create all tables if they do not already exist."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    with Session(engine) as session:
        yield session
