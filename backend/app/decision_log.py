"""Small helper so every service writes to the decision log the same way."""
from sqlalchemy.orm import Session
from . import models


def log(
    db: Session,
    actor: str,
    event: str,
    patient_id: int | None = None,
    donor_id: int | None = None,
    detail: str | None = None,
):
    entry = models.DecisionLogEntry(
        actor=actor,
        event=event,
        patient_id=patient_id,
        donor_id=donor_id,
        detail=detail,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
