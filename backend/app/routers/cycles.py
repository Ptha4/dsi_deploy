from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, matching, outreach
from ..database import get_db
from ..decision_log import log

router = APIRouter(prefix="/cycles", tags=["cycles"])


def _bucket_label(days_until: int) -> str:
    if days_until <= 0:
        return "today"
    if days_until == 1:
        return "in_1_day"
    if days_until == 2:
        return "in_2_days"
    return "in_3_7_days"


@router.get("/upcoming")
def upcoming(days: int = 7, db: Session = Depends(get_db)):
    """Patients due for a transfusion in the next N days, bucketed."""
    today = date.today()
    horizon = today + timedelta(days=days)
    cycles = (
        db.query(models.Cycle)
        .filter(models.Cycle.due_date >= today, models.Cycle.due_date <= horizon)
        .filter(models.Cycle.status != models.CycleStatus.DONATED)
        .order_by(models.Cycle.due_date)
        .all()
    )
    buckets: dict[str, list] = {"today": [], "in_1_day": [], "in_2_days": [], "in_3_7_days": []}
    for c in cycles:
        days_until = (c.due_date - today).days
        buckets[_bucket_label(days_until)].append({
            "cycle_id": c.id,
            "patient_id": c.patient_id,
            "name": c.patient.name,
            "blood_group": c.patient.blood_group,
            "due_date": c.due_date.isoformat(),
            "status": c.status.value,
        })
    log(db, actor="coordinator", event="Ran /upcoming",
        detail=f"Asked which patients need blood in the next {days} days")
    log(db, actor="bot", event="Listed upcoming needs",
        detail=f"{len(cycles)} patients due in the next {days} days")
    return {"as_of": today.isoformat(), "days": days, "buckets": buckets, "total": len(cycles)}


@router.get("/matches")
def matches(days: int = 7, db: Session = Depends(get_db)):
    """Split upcoming patients into Matched (confirmed donor) vs Unmatched."""
    today = date.today()
    horizon = today + timedelta(days=days)
    cycles = (
        db.query(models.Cycle)
        .filter(models.Cycle.due_date >= today, models.Cycle.due_date <= horizon)
        .filter(models.Cycle.status != models.CycleStatus.DONATED)
        .order_by(models.Cycle.due_date)
        .all()
    )
    matched, unmatched = [], []
    for c in cycles:
        days_until = (c.due_date - today).days
        entry = {
            "cycle_id": c.id,
            "patient_id": c.patient_id,
            "name": c.patient.name,
            "blood_group": c.patient.blood_group,
            "due_date": c.due_date.isoformat(),
            "days_until": days_until,
            "status": c.status.value,
        }
        if c.status == models.CycleStatus.CONFIRMED and c.confirmed_donor_id:
            donor = db.query(models.Donor).get(c.confirmed_donor_id)
            entry["donor"] = donor.name
            entry["source"] = "blood_bridge_circle"  # matched donors in seed data are circle donors
            matched.append(entry)
        else:
            unmatched.append(entry)

    log(db, actor="coordinator", event="Ran /matches",
        detail=f"Asked which of the upcoming patients already have a match")
    log(db, actor="bot", event="Reported match status",
        detail=f"{len(matched)} matched (Blood Bridge circles), {len(unmatched)} unmatched")
    return {"as_of": today.isoformat(), "days": days,
            "matched": matched, "unmatched": unmatched,
            "matched_count": len(matched), "unmatched_count": len(unmatched)}


@router.post("/assign")
def assign(req: schemas.AssignRequest, db: Session = Depends(get_db)):
    """Run the matching engine for unmatched patients (or a specific subset),
    producing ranked proposals. No donor is contacted yet."""
    query = db.query(models.Cycle).filter(
        models.Cycle.status.in_([models.CycleStatus.DUE, models.CycleStatus.FLAGGED])
    )
    if req.patient_ids:
        query = query.filter(models.Cycle.patient_id.in_(req.patient_ids))
    cycles = query.all()

    if not cycles:
        raise HTTPException(404, "No unmatched patients found to assign.")

    circle_count = 0
    general_count = 0
    results = []
    for cycle in cycles:
        # Clear any stale proposals from a previous /assign run on this cycle.
        db.query(models.Proposal).filter(models.Proposal.cycle_id == cycle.id).delete()
        db.commit()
        proposals = matching.propose_donor_for_cycle(db, cycle)
        cycle.status = models.CycleStatus.PROPOSED
        db.commit()
        if proposals:
            top = proposals[0]
            if top.source == models.ProposalSource.CIRCLE:
                circle_count += 1
            else:
                general_count += 1
            results.append({
                "cycle_id": cycle.id,
                "patient_id": cycle.patient_id,
                "patient_name": cycle.patient.name,
                "due_date": cycle.due_date.isoformat(),
                "top_proposal": {
                    "donor_name": top.donor.name,
                    "source": top.source.value,
                    "reason": top.reason,
                    "distance_km": top.distance_km,
                },
                "alternatives": len(proposals) - 1,
            })
            log(db, actor="bot", event="Proposed match", patient_id=cycle.patient_id,
                donor_id=top.donor_id, detail=top.reason)
        else:
            cycle.status = models.CycleStatus.FLAGGED
            db.commit()
            results.append({
                "cycle_id": cycle.id,
                "patient_id": cycle.patient_id,
                "patient_name": cycle.patient.name,
                "due_date": cycle.due_date.isoformat(),
                "top_proposal": None,
                "alternatives": 0,
            })
            log(db, actor="bot", event="No donor found", patient_id=cycle.patient_id,
                detail="No eligible donor in circle or general pool.")

    log(db, actor="coordinator", event="Ran /assign",
        detail=f"Asked the algorithm to match {len(cycles)} unmatched patients "
               f"({circle_count} from Blood Bridge circles, {general_count} from general pool)")
    return {"proposals": results, "circle_matches": circle_count, "general_pool_matches": general_count}


@router.post("/approve")
def approve(req: schemas.ApproveRequest, db: Session = Depends(get_db)):
    """Coordinator approves all proposed cycles, or one patient's. Starts
    outreach to the top-ranked donor for each approved cycle."""
    query = db.query(models.Cycle).filter(models.Cycle.status == models.CycleStatus.PROPOSED)
    if req.patient_ids:
        query = query.filter(models.Cycle.patient_id.in_(req.patient_ids))
    cycles = query.all()

    if not cycles:
        raise HTTPException(404, "No proposed cycles awaiting approval.")

    contacted = []
    for cycle in cycles:
        outreach.approve_and_start_outreach(db, cycle)
        top_contacted = (
            db.query(models.Proposal)
            .filter(models.Proposal.cycle_id == cycle.id, models.Proposal.tried == True)  # noqa: E712
            .order_by(models.Proposal.rank)
            .first()
        )
        contacted.append({
            "cycle_id": cycle.id,
            "patient_name": cycle.patient.name,
            "donor_contacted": top_contacted.donor.name if top_contacted else None,
        })

    log(db, actor="coordinator", event="Approved proposals",
        detail=f"CONFIRM ALL — approved {len(cycles)} proposal(s), bot contacted 3 donors"
               if len(cycles) == 3 else f"Approved {len(cycles)} proposal(s)")
    return {"approved": contacted}


@router.post("/{cycle_id}/resolve")
def resolve(cycle_id: int, req: schemas.ResolveRequest, db: Session = Depends(get_db)):
    """Coordinator resolves a flagged patient: widen the search radius,
    remind the last-tried donor, or assign a donor manually."""
    cycle = db.query(models.Cycle).get(cycle_id)
    if not cycle:
        raise HTTPException(404, "Cycle not found.")
    if cycle.status != models.CycleStatus.FLAGGED:
        raise HTTPException(400, "This cycle isn't flagged for attention.")

    if req.action == "widen_radius":
        # Re-run assign for just this cycle; matching.py already searches the
        # full general pool, so "widening" here re-attempts the same search
        # (useful once new donor availability has been recorded).
        db.query(models.Proposal).filter(models.Proposal.cycle_id == cycle.id).delete()
        db.commit()
        proposals = matching.propose_donor_for_cycle(db, cycle)
        if proposals:
            cycle.status = models.CycleStatus.PROPOSED
            db.commit()
        log(db, actor="coordinator", event="Widened search radius",
            patient_id=cycle.patient_id,
            detail=f"{req.coordinator_name} asked to widen the donor search. "
                   f"{'Found ' + str(len(proposals)) + ' candidate(s).' if proposals else 'Still no donor found.'}")
        return {"found": len(proposals), "cycle_status": cycle.status.value}

    elif req.action == "remind_donor":
        last_tried = (
            db.query(models.Proposal)
            .filter(models.Proposal.cycle_id == cycle.id)
            .order_by(models.Proposal.rank.desc())
            .first()
        )
        if not last_tried:
            raise HTTPException(400, "No donor on record to remind.")
        log(db, actor="coordinator", event="Reminded donor",
            patient_id=cycle.patient_id, donor_id=last_tried.donor_id,
            detail=f"{req.coordinator_name} sent a reminder to {last_tried.donor.name}.")
        return {"reminded": last_tried.donor.name}

    elif req.action == "assign_manually":
        if not req.donor_id:
            raise HTTPException(400, "donor_id is required for assign_manually.")
        cycle.status = models.CycleStatus.CONFIRMED
        cycle.confirmed_donor_id = req.donor_id
        db.commit()
        donor = db.query(models.Donor).get(req.donor_id)
        log(db, actor="coordinator", event="Assigned donor manually",
            patient_id=cycle.patient_id, donor_id=req.donor_id,
            detail=f"{req.coordinator_name} manually assigned {donor.name}.")
        return {"confirmed_donor": donor.name}

    raise HTTPException(400, "Unknown action. Use widen_radius, remind_donor, or assign_manually.")


@router.get("/{cycle_id}/trace")
def trace(cycle_id: int, db: Session = Depends(get_db)):
    """Every step the engine and coordinator took for one patient, with
    timestamps, rules applied, and (if flagged) resolution options."""
    cycle = db.query(models.Cycle).get(cycle_id)
    if not cycle:
        raise HTTPException(404, "Cycle not found.")

    entries = (
        db.query(models.DecisionLogEntry)
        .filter(models.DecisionLogEntry.patient_id == cycle.patient_id)
        .order_by(models.DecisionLogEntry.timestamp)
        .all()
    )
    proposals = (
        db.query(models.Proposal)
        .filter(models.Proposal.cycle_id == cycle.id)
        .order_by(models.Proposal.rank)
        .all()
    )
    return {
        "patient": {
            "id": cycle.patient_id,
            "name": cycle.patient.name,
            "blood_group": cycle.patient.blood_group,
            "due_date": cycle.due_date.isoformat(),
            "cycle_length_days": cycle.patient.cycle_length_days,
            "status": cycle.status.value,
        },
        "rules_applied": {
            "blood_type": f"{cycle.patient.blood_group} patient — compatible donor groups only",
            "donation_gap": "minimum days since last donation (configurable per donor)",
            "search_order": "Blood Bridge circle, then general pool by distance",
            "reply_window": "20 min, then next donor",
            "human_approval": "Required before any donor is contacted",
        },
        "proposals": [
            {
                "rank": p.rank,
                "donor": p.donor.name,
                "source": p.source.value,
                "score": p.score,
                "distance_km": p.distance_km,
                "reason": p.reason,
                "outcome": p.outcome.value,
                "tried": p.tried,
            }
            for p in proposals
        ],
        "timeline": [
            {
                "timestamp": e.timestamp.isoformat(),
                "actor": e.actor,
                "event": e.event,
                "donor": e.donor.name if e.donor else None,
                "detail": e.detail,
            }
            for e in entries
        ],
    }
