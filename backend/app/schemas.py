"""Request/response schemas for the ResearchOS API."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from .models import Conviction


class PositionCreate(BaseModel):
    symbol: str
    name: str
    sector: str = "Uncategorized"
    shares: float = 0.0
    cost_basis: float = 0.0
    current_price: float = 0.0
    target_price: float = 0.0
    conviction: Conviction = Conviction.medium
    thesis: str = ""


class PositionUpdate(BaseModel):
    symbol: str | None = None
    name: str | None = None
    sector: str | None = None
    shares: float | None = None
    cost_basis: float | None = None
    current_price: float | None = None
    target_price: float | None = None
    conviction: Conviction | None = None
    thesis: str | None = None


class PositionRead(BaseModel):
    id: int
    symbol: str
    name: str
    sector: str
    shares: float
    cost_basis: float
    current_price: float
    target_price: float
    conviction: Conviction
    thesis: str
    created_at: datetime
    market_value: float
    total_cost: float
    unrealized_gain: float
    unrealized_gain_pct: float
    upside_pct: float


class NoteCreate(BaseModel):
    title: str
    content: str = ""


class NoteRead(BaseModel):
    id: int
    position_id: int
    title: str
    content: str
    created_at: datetime


class PortfolioSummary(BaseModel):
    positions: int
    market_value: float
    total_cost: float
    unrealized_gain: float
    unrealized_gain_pct: float
    by_sector: dict[str, float]
