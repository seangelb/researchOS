"""Position (watchlist / holdings) endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..database import get_session
from ..metrics import enrich_position
from ..models import Position, ResearchNote
from ..schemas import NoteCreate, NoteRead, PositionCreate, PositionRead, PositionUpdate

router = APIRouter(prefix="/api/positions", tags=["positions"])


@router.get("", response_model=list[PositionRead])
def list_positions(session: Session = Depends(get_session)) -> list[PositionRead]:
    positions = session.exec(select(Position).order_by(Position.symbol)).all()
    return [enrich_position(p) for p in positions]


@router.post("", response_model=PositionRead, status_code=201)
def create_position(payload: PositionCreate, session: Session = Depends(get_session)) -> PositionRead:
    position = Position(**payload.model_dump())
    position.symbol = position.symbol.upper().strip()
    session.add(position)
    session.commit()
    session.refresh(position)
    return enrich_position(position)


def _get_or_404(position_id: int, session: Session) -> Position:
    position = session.get(Position, position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return position


@router.get("/{position_id}", response_model=PositionRead)
def get_position(position_id: int, session: Session = Depends(get_session)) -> PositionRead:
    return enrich_position(_get_or_404(position_id, session))


@router.patch("/{position_id}", response_model=PositionRead)
def update_position(
    position_id: int, payload: PositionUpdate, session: Session = Depends(get_session)
) -> PositionRead:
    position = _get_or_404(position_id, session)
    updates = payload.model_dump(exclude_unset=True)
    if "symbol" in updates and updates["symbol"]:
        updates["symbol"] = updates["symbol"].upper().strip()
    for key, value in updates.items():
        setattr(position, key, value)
    session.add(position)
    session.commit()
    session.refresh(position)
    return enrich_position(position)


@router.delete("/{position_id}", status_code=204)
def delete_position(position_id: int, session: Session = Depends(get_session)) -> None:
    position = _get_or_404(position_id, session)
    notes = session.exec(select(ResearchNote).where(ResearchNote.position_id == position_id)).all()
    for note in notes:
        session.delete(note)
    session.delete(position)
    session.commit()


@router.get("/{position_id}/notes", response_model=list[NoteRead])
def list_notes(position_id: int, session: Session = Depends(get_session)) -> list[NoteRead]:
    _get_or_404(position_id, session)
    notes = session.exec(
        select(ResearchNote)
        .where(ResearchNote.position_id == position_id)
        .order_by(ResearchNote.created_at.desc())
    ).all()
    return notes


@router.post("/{position_id}/notes", response_model=NoteRead, status_code=201)
def create_note(
    position_id: int, payload: NoteCreate, session: Session = Depends(get_session)
) -> NoteRead:
    _get_or_404(position_id, session)
    note = ResearchNote(position_id=position_id, **payload.model_dump())
    session.add(note)
    session.commit()
    session.refresh(note)
    return note
