"""
POST /webhook/whatsapp — Section 6: "Receive coordinator commands
(/upcoming, /matches, /assign, CONFIRM ALL) and donor replies."

This single webhook is what a real WhatsApp Business API integration would
call. It tells coordinator messages apart from donor replies by the sender's
phone number (a donor's own phone vs. the coordinator's). The included
whatsapp_mock/ chat UI calls this same endpoint so the demo behaves exactly
like the eventual live integration would.
"""
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models, matching, outreach
from ..database import get_db
from ..decision_log import log
from . import cycles as cycles_router

router = APIRouter(tags=["whatsapp"])

COORDINATOR_PHONE = "coordinator"


class WhatsAppMessage(BaseModel):
    from_: str = Field(default="coordinator", alias="from")  # phone number, or "coordinator"
    message: str

    model_config = {"populate_by_name": True}


def _format_upcoming(data: dict) -> str:
    lines = ["📋 Patients due for a transfusion in the next 7 days:", ""]
    labels = [("today", "🔴 Today"), ("in_1_day", "🟠 In 1 day"),
              ("in_2_days", "🟡 In 2 days"), ("in_3_7_days", "⚪ In 3–7 days")]
    for key, label in labels:
        items = data["buckets"][key]
        lines.append(f"{label} ({len(items)})")
        for it in items:
            lines.append(f"• {it['name']} — {it['blood_group']}, cycle due {it['due_date']}")
        lines.append("")
    lines.append("Reply /matches to see which of these already have a confirmed donor for this cycle.")
    return "\n".join(lines)


def _format_matches(data: dict) -> str:
    lines = [f"Match status for the next {data['days']} days:", ""]
    lines.append(f"✅ Matched ({data['matched_count']})")
    for m in data["matched"][:5]:
        lines.append(f"• {m['name']} — donor confirmed: {m['donor']} (Blood Bridge circle)")
    if data["matched_count"] > 5:
        lines.append(f"• [{data['matched_count'] - 5} more matched patients]")
    lines.append("")
    lines.append(f"❌ Unmatched ({data['unmatched_count']})")
    for u in data["unmatched"]:
        lines.append(f"• {u['name']} — due in {u['days_until']} day(s) — no donor confirmed yet")
    lines.append("")
    lines.append("Reply /assign to have the algorithm find matches for the unmatched patients.")
    return "\n".join(lines)


def _format_assign(data: dict) -> str:
    lines = ["Checking each unmatched patient's Blood Bridge circle first, "
             "then wider donor pool if needed...", ""]
    for r in data["proposals"]:
        top = r["top_proposal"]
        if top:
            lines.append(f"{r['patient_name']} (due {r['due_date']})")
            lines.append(f"→ {top['donor_name']} — {top['reason']}")
        else:
            lines.append(f"{r['patient_name']} (due {r['due_date']})")
            lines.append("→ No eligible donor found anywhere.")
        lines.append("")
    lines.append("Reply CONFIRM ALL, or reply with a patient's name to confirm one at a time.")
    return "\n".join(lines)


def _format_approve(data: dict) -> str:
    lines = ["Contacting donors now:"]
    for a in data["approved"]:
        lines.append(f"→ {a['donor_contacted']} contacted for {a['patient_name']}")
    lines.append("I'll update you here as they respond.")
    return "\n".join(lines)


@router.post("/webhook/whatsapp")
def webhook(msg: WhatsAppMessage, db: Session = Depends(get_db)):
    sender = msg.from_
    text = msg.message.strip()
    lower = text.lower()

    # Is this a donor replying, rather than the coordinator?
    donor = db.query(models.Donor).filter(models.Donor.phone == sender).first()
    if donor:
        contact = (
            db.query(models.ContactEvent)
            .filter(models.ContactEvent.donor_id == donor.id,
                    models.ContactEvent.outcome == models.ContactOutcome.PENDING)
            .order_by(models.ContactEvent.sent_at.desc())
            .first()
        )
        if not contact:
            return {"reply": "Thanks for your reply — no active request found for you right now."}
        cycle = db.query(models.Cycle).get(contact.cycle_id)
        accepted = lower in ("yes", "y", "available", "yes i can", "confirmed")
        reply = outreach.handle_donor_reply(db, cycle, donor.id, accepted, via="whatsapp")
        return {"reply": reply}

    # Otherwise treat as a coordinator command.
    if lower == "/upcoming":
        data = cycles_router.upcoming(days=7, db=db)
        return {"reply": _format_upcoming(data)}

    if lower == "/matches":
        data = cycles_router.matches(days=7, db=db)
        return {"reply": _format_matches(data)}

    if lower == "/assign":
        from .. import schemas
        data = cycles_router.assign(schemas.AssignRequest(), db=db)
        return {"reply": _format_assign(data)}

    if lower == "confirm all":
        from .. import schemas
        data = cycles_router.approve(schemas.ApproveRequest(), db=db)
        return {"reply": _format_approve(data)}

    # Otherwise, try to match a patient name to approve one proposal at a time.
    patient = db.query(models.Patient).filter(models.Patient.name.ilike(f"%{text}%")).first()
    if patient:
        from .. import schemas
        try:
            data = cycles_router.approve(schemas.ApproveRequest(patient_ids=[patient.id]), db=db)
            return {"reply": _format_approve(data)}
        except Exception:
            return {"reply": f"No pending proposal found for {patient.name}. "
                              f"Try /assign first, or check /matches."}

    return {"reply": "Sorry, I didn't understand that. Try /upcoming, /matches, /assign, "
                      "or CONFIRM ALL."}
