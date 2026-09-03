"""Derived financial metrics for positions."""
from __future__ import annotations

from .models import Position
from .schemas import PositionRead


def enrich_position(position: Position) -> PositionRead:
    """Compute derived valuation metrics for a position."""
    market_value = position.shares * position.current_price
    total_cost = position.shares * position.cost_basis
    unrealized_gain = market_value - total_cost
    unrealized_gain_pct = (unrealized_gain / total_cost * 100.0) if total_cost else 0.0
    upside_pct = (
        (position.target_price - position.current_price) / position.current_price * 100.0
        if position.current_price
        else 0.0
    )
    return PositionRead(
        id=position.id,
        symbol=position.symbol,
        name=position.name,
        sector=position.sector,
        shares=position.shares,
        cost_basis=position.cost_basis,
        current_price=position.current_price,
        target_price=position.target_price,
        conviction=position.conviction,
        thesis=position.thesis,
        created_at=position.created_at,
        market_value=round(market_value, 2),
        total_cost=round(total_cost, 2),
        unrealized_gain=round(unrealized_gain, 2),
        unrealized_gain_pct=round(unrealized_gain_pct, 2),
        upside_pct=round(upside_pct, 2),
    )
