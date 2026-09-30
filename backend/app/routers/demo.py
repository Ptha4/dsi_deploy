"""
Demo-only helpers — not part of the Section 6 API surface. Since this PoC
doesn't have live Twilio/Meta WhatsApp credentials wired up, these let the
mock chat UI (or a presenter) simulate donor replies and force the 20-minute
timeout sweep instantly, instead of waiting for real messages or real time
to pass.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, outreach
from ..database import get_db

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/awaiting_reply")
def awaiting_reply(db: Session = Depends(get_db)):
    """Donors currently contacted and awaiting a reply — powers the
    'simulate donor reply' panel in the mock chat UI."""
    pending = (
        db.query(models.ContactEvent)
        .filter(models.ContactEvent.outcome == models.ContactOutcome.PENDING)
        .all()
    )
    return {
        "pending": [
            {
                "cycle_id": c.cycle_id,
                "donor_id": c.donor_id,
                "donor_name": c.donor.name,
                "donor_phone": c.donor.phone,
                "patient_name": c.cycle.patient.name,
                "sent_at": c.sent_at.isoformat(),
                "reply_window_minutes": c.reply_window_minutes,
            }
            for c in pending
        ]
    }


@router.post("/sweep_timeouts")
def sweep(db: Session = Depends(get_db)):
    """Force the timeout sweep immediately (normally runs on a background
    schedule) — for demoing without waiting out the real reply window."""
    messages = outreach.sweep_timeouts(db)
    return {"messages": messages}


@router.post("/reset")
def reset(db: Session = Depends(get_db)):
    """Wipe and reseed the database back to the walkthrough scenario."""
    from .. import seed_data
    seed_data.seed()
    return {"status": "reseeded"}
