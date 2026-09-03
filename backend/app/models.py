"""SQLModel table definitions for ResearchOS."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Conviction(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Position(SQLModel, table=True):
    """A tracked investment idea / holding in the research workspace."""

    id: int | None = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    name: str
    sector: str = Field(default="Uncategorized")
    shares: float = Field(default=0.0)
    cost_basis: float = Field(default=0.0)
    current_price: float = Field(default=0.0)
    target_price: float = Field(default=0.0)
    conviction: Conviction = Field(default=Conviction.medium)
    thesis: str = Field(default="")
    created_at: datetime = Field(default_factory=_utcnow)


class ResearchNote(SQLModel, table=True):
    """A dated research note attached to a position."""

    id: int | None = Field(default=None, primary_key=True)
    position_id: int = Field(foreign_key="position.id", index=True)
    title: str
    content: str = Field(default="")
    created_at: datetime = Field(default_factory=_utcnow)
