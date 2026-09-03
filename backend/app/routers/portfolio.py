"""Portfolio-level summary endpoints."""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..database import get_session
from ..models import Position
from ..schemas import PortfolioSummary

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/summary", response_model=PortfolioSummary)
def portfolio_summary(session: Session = Depends(get_session)) -> PortfolioSummary:
    positions = session.exec(select(Position)).all()
    market_value = 0.0
    total_cost = 0.0
    by_sector: dict[str, float] = defaultdict(float)
    for p in positions:
        mv = p.shares * p.current_price
        market_value += mv
        total_cost += p.shares * p.cost_basis
        by_sector[p.sector] += mv
    unrealized_gain = market_value - total_cost
    unrealized_gain_pct = (unrealized_gain / total_cost * 100.0) if total_cost else 0.0
    return PortfolioSummary(
        positions=len(positions),
        market_value=round(market_value, 2),
        total_cost=round(total_cost, 2),
        unrealized_gain=round(unrealized_gain, 2),
        unrealized_gain_pct=round(unrealized_gain_pct, 2),
        by_sector={k: round(v, 2) for k, v in sorted(by_sector.items())},
    )
