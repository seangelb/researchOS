"""Idempotent seed data so the workspace is useful on first boot."""
from __future__ import annotations

from sqlmodel import Session, select

from .database import engine
from .models import Conviction, Position, ResearchNote

_SEED_POSITIONS = [
    {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "sector": "Technology",
        "shares": 40,
        "cost_basis": 165.0,
        "current_price": 212.0,
        "target_price": 245.0,
        "conviction": Conviction.high,
        "thesis": "Services margin expansion and installed-base upgrade cycle.",
        "notes": [
            ("Services momentum", "App Store + subscriptions now ~25% of revenue and growing double digits."),
            ("Valuation check", "Forward P/E elevated vs history; sizing accordingly."),
        ],
    },
    {
        "symbol": "MSFT",
        "name": "Microsoft Corporation",
        "sector": "Technology",
        "shares": 18,
        "cost_basis": 305.0,
        "current_price": 418.0,
        "target_price": 470.0,
        "conviction": Conviction.high,
        "thesis": "Azure AI attach + Copilot monetization across M365.",
        "notes": [
            ("Azure growth", "Cloud reacceleration led by AI inference workloads."),
        ],
    },
    {
        "symbol": "NVDA",
        "name": "NVIDIA Corporation",
        "sector": "Semiconductors",
        "shares": 25,
        "cost_basis": 88.0,
        "current_price": 121.0,
        "target_price": 150.0,
        "conviction": Conviction.medium,
        "thesis": "Data-center GPU demand; monitoring supply normalization risk.",
        "notes": [
            ("Supply watch", "Track hyperscaler capex guidance for demand durability."),
        ],
    },
    {
        "symbol": "COST",
        "name": "Costco Wholesale",
        "sector": "Consumer Staples",
        "shares": 10,
        "cost_basis": 540.0,
        "current_price": 905.0,
        "target_price": 960.0,
        "conviction": Conviction.medium,
        "thesis": "Membership renewal rates and fee-hike optionality.",
        "notes": [],
    },
    {
        "symbol": "ASML",
        "name": "ASML Holding",
        "sector": "Semiconductors",
        "shares": 6,
        "cost_basis": 720.0,
        "current_price": 690.0,
        "target_price": 900.0,
        "conviction": Conviction.high,
        "thesis": "EUV monopoly; leading-edge lithography demand secular.",
        "notes": [
            ("China exposure", "Export-control headlines create entry points."),
        ],
    },
]


def seed_if_empty() -> int:
    """Insert seed data only when there are no positions yet. Returns count added."""
    with Session(engine) as session:
        existing = session.exec(select(Position)).first()
        if existing is not None:
            return 0
        added = 0
        for row in _SEED_POSITIONS:
            notes = row.pop("notes", [])
            position = Position(**row)
            session.add(position)
            session.commit()
            session.refresh(position)
            for title, content in notes:
                session.add(
                    ResearchNote(position_id=position.id, title=title, content=content)
                )
            session.commit()
            added += 1
        return added
