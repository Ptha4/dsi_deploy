from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db

router = APIRouter(tags=["activity"])


@router.get("/activity")
def activity_log(
    actor: Optional[str] = None,       # "bot" | "coordinator" | "donor"
    event: Optional[str] = None,       # substring match
    patient_id: Optional[int] = None,
    donor_id: Optional[int] = None,
    on_date: Optional[date] = None,
    since_id: Optional[int] = None,    # for polling: only entries after this id
    db: Session = Depends(get_db),
):
    """Every message, proposal, approval and reply the bot handled — read-only,
    timestamped. Filterable by actor, event, patient, donor and date."""
    q = db.query(models.DecisionLogEntry)
    if actor:
        q = q.filter(models.DecisionLogEntry.actor == actor)
    if event:
        q = q.filter(models.DecisionLogEntry.event.ilike(f"%{event}%"))
    if patient_id:
        q = q.filter(models.DecisionLogEntry.patient_id == patient_id)
    if donor_id:
        q = q.filter(models.DecisionLogEntry.donor_id == donor_id)
    if on_date:
        start = datetime.combine(on_date, datetime.min.time())
        end = datetime.combine(on_date, datetime.max.time())
        q = q.filter(models.DecisionLogEntry.timestamp.between(start, end))
    if since_id:
        q = q.filter(models.DecisionLogEntry.id > since_id)

    entries = q.order_by(models.DecisionLogEntry.timestamp).all()
    return {
        "count": len(entries),
        "entries": [
            {
                "id": e.id,
                "timestamp": e.timestamp.isoformat(),
                "actor": e.actor,
                "event": e.event,
                "patient": e.patient.name if e.patient else None,
                "donor": e.donor.name if e.donor else None,
                "detail": e.detail,
            }
            for e in entries
        ],
    }
