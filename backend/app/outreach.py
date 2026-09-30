"""
Outreach orchestrator — Section 5's "outreach orchestrator" service and the
"Donor flow" / "Fallback and flag" behaviour from Section 3 of the design doc.

Contacts the top-ranked untried proposal for an approved cycle, waits for a
reply within a window (default 20 min, overridable via
DEMO_REPLY_WINDOW_SECONDS for fast demoing), and on decline/timeout moves to
the next proposal. If no proposal is left, the cycle is flagged for the
coordinator with the last-tried donor's number to call.
"""
import os
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import models
from .decision_log import log

DEFAULT_REPLY_WINDOW_MINUTES = 20


def _reply_window() -> timedelta:
    fast_seconds = os.environ.get("DEMO_REPLY_WINDOW_SECONDS")
    if fast_seconds:
        return timedelta(seconds=int(fast_seconds))
    return timedelta(minutes=DEFAULT_REPLY_WINDOW_MINUTES)


def contact_next_donor(db: Session, cycle: models.Cycle) -> models.Proposal | None:
    """Find the next untried proposal in rank order and contact that donor.
    Returns the proposal contacted, or None if the list is exhausted (in
    which case the cycle is flagged)."""
    next_proposal = (
        db.query(models.Proposal)
        .filter(models.Proposal.cycle_id == cycle.id, models.Proposal.tried == False)  # noqa: E712
        .order_by(models.Proposal.rank)
        .first()
    )

    if next_proposal is None:
        cycle.status = models.CycleStatus.FLAGGED
        db.commit()
        last_tried = (
            db.query(models.Proposal)
            .filter(models.Proposal.cycle_id == cycle.id)
            .order_by(models.Proposal.rank.desc())
            .first()
        )
        detail = "No donor left in circle or nearby — flagged to coordinator."
        if last_tried:
            detail += f" Last tried: {last_tried.donor.name} ({last_tried.donor.phone})."
        log(db, actor="bot", event="Flagged to coordinator",
            patient_id=cycle.patient_id, detail=detail)
        return None

    next_proposal.tried = True
    contact = models.ContactEvent(
        cycle_id=cycle.id,
        donor_id=next_proposal.donor_id,
        sent_at=datetime.utcnow(),
        reply_window_minutes=int(_reply_window().total_seconds() // 60) or 1,
    )
    db.add(contact)
    cycle.status = models.CycleStatus.CONTACTED
    db.commit()

    donor = next_proposal.donor
    log(db, actor="bot", event="Contacted donor",
        patient_id=cycle.patient_id, donor_id=donor.id,
        detail=f"WhatsApp outreach sent to {donor.name} ({donor.phone}); "
               f"{contact.reply_window_minutes}-min reply window.")
    return next_proposal


def approve_and_start_outreach(db: Session, cycle: models.Cycle):
    cycle.status = models.CycleStatus.APPROVED
    db.commit()
    log(db, actor="coordinator", event="Approved proposal", patient_id=cycle.patient_id,
        detail="Coordinator approved proposal on WhatsApp.")
    contact_next_donor(db, cycle)


def handle_donor_reply(db: Session, cycle: models.Cycle, donor_id: int, accepted: bool,
                        via: str = "whatsapp") -> str:
    """Record a donor's YES/NO. On YES, confirm the cycle. On NO, cascade to
    the next donor. `via` is 'whatsapp' or 'phone' (coordinator relaying a
    call outcome, per the design doc's 'Reached by phone' flow)."""
    contact = (
        db.query(models.ContactEvent)
        .filter(models.ContactEvent.cycle_id == cycle.id, models.ContactEvent.donor_id == donor_id)
        .order_by(models.ContactEvent.sent_at.desc())
        .first()
    )
    proposal = (
        db.query(models.Proposal)
        .filter(models.Proposal.cycle_id == cycle.id, models.Proposal.donor_id == donor_id)
        .first()
    )
    donor = db.query(models.Donor).get(donor_id)

    if contact:
        contact.responded_at = datetime.utcnow()
        contact.outcome = models.ContactOutcome.CONFIRMED if accepted else models.ContactOutcome.DECLINED
    if proposal:
        proposal.outcome = models.ContactOutcome.CONFIRMED if accepted else models.ContactOutcome.DECLINED
    db.commit()

    if accepted:
        cycle.status = models.CycleStatus.CONFIRMED
        cycle.confirmed_donor_id = donor_id
        db.commit()
        donor.last_donation_date = cycle.due_date  # will be finalised on actual donation
        log(db, actor="donor", event="Confirmed", patient_id=cycle.patient_id, donor_id=donor_id,
            detail=f"Replied YES{' (relayed by phone)' if via == 'phone' else ''} — match confirmed.")
        return f"✅ {donor.name} confirmed for {cycle.patient.name}."
    else:
        log(db, actor="donor", event="Declined" if via == "whatsapp" else "Declined (phone)",
            patient_id=cycle.patient_id, donor_id=donor_id,
            detail=f"Replied NO{' (relayed by phone)' if via == 'phone' else ''} — moving to next donor.")
        next_proposal = contact_next_donor(db, cycle)
        if next_proposal:
            return (f"{donor.name} can't donate this time. "
                    f"Contacting next donor — {next_proposal.donor.name} now.")
        return f"⚠️ No donor left for {cycle.patient.name}. Flagged for your attention."


def handle_timeout(db: Session, cycle: models.Cycle, donor_id: int) -> str:
    contact = (
        db.query(models.ContactEvent)
        .filter(models.ContactEvent.cycle_id == cycle.id, models.ContactEvent.donor_id == donor_id,
                models.ContactEvent.outcome == models.ContactOutcome.PENDING)
        .order_by(models.ContactEvent.sent_at.desc())
        .first()
    )
    donor = db.query(models.Donor).get(donor_id)
    if contact:
        contact.outcome = models.ContactOutcome.TIMED_OUT
        db.commit()
    log(db, actor="bot", event="Timed out", patient_id=cycle.patient_id, donor_id=donor_id,
        detail=f"No reply from {donor.name} within the reply window.")

    next_proposal = contact_next_donor(db, cycle)
    if next_proposal:
        return (f"{donor.name} hasn't responded in time. "
                f"Moving to next donor — contacting {next_proposal.donor.name} now.")
    return f"⚠️ No donor left for {cycle.patient.name}. Flagged for your attention."


def sweep_timeouts(db: Session):
    """Called periodically (APScheduler) to find contact events whose reply
    window has elapsed with no response, and cascade them."""
    window = _reply_window()
    cutoff = datetime.utcnow() - window
    pending = (
        db.query(models.ContactEvent)
        .filter(models.ContactEvent.outcome == models.ContactOutcome.PENDING,
                models.ContactEvent.sent_at <= cutoff)
        .all()
    )
    messages = []
    for contact in pending:
        cycle = db.query(models.Cycle).get(contact.cycle_id)
        if cycle and cycle.status == models.CycleStatus.CONTACTED:
            messages.append(handle_timeout(db, cycle, contact.donor_id))
    return messages
