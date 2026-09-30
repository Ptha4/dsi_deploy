"""
Dashboard-support endpoint — not in the Section 6 API table, but needed to
render the Matchings view's per-patient Blood Bridge circle status (the
colored dots in Figure 4.4: donating this cycle / eligible / not eligible).
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, matching
from ..database import get_db

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get("/{patient_id}/circle")
def circle_status(patient_id: int, db: Session = Depends(get_db)):
    patient = db.query(models.Patient).get(patient_id)
    if not patient:
        raise HTTPException(404, "Patient not found.")

    today = date.today()
    current_cycle = (
        db.query(models.Cycle)
        .filter(models.Cycle.patient_id == patient_id)
        .order_by(models.Cycle.due_date.desc())
        .first()
    )
    confirmed_donor_id = current_cycle.confirmed_donor_id if current_cycle else None

    donors = []
    for m in patient.circle_memberships:
        d = m.donor
        if d.id == confirmed_donor_id:
            state = "donating"
        elif matching.is_eligible(db, d, today):
            state = "eligible"
        else:
            state = "not_eligible"
        donors.append({"id": d.id, "name": d.name, "state": state})

    return {"patient_id": patient_id, "circle_size": len(donors), "donors": donors}
