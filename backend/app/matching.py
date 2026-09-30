"""
Circle-first donor matching engine — Section 5 of the Sprint 1 Design Doc.

How the engine picks a donor
-----------------------------
1. Hard filters: blood-group compatible, past the minimum donation gap by
   the due date, not marked unavailable, not already booked this cycle.
2. Circle-first: candidates from the patient's Blood Bridge circle are
   considered first. Only if none pass the filters does the engine search
   the wider donor pool.
3. Within the circle, eligible donors are ranked by a weighted score so
   load is spread fairly and the circle stays able to cover future cycles:

       Score(d) = w1*rest(d) + w2*reliability(d) - w3*recentContacts(d) - w4*scarcity(d)

   - rest(d): days since last donation beyond the minimum gap
   - reliability(d): donor's past yes-rate
   - recentContacts(d): penalises donors asked often lately
   - scarcity(d): rises if using this donor now would leave the circle
     without an eligible donor for an upcoming cycle

4. In the wider pool, candidates are ranked by distance to the patient's
   hospital, nearest first (no weighted score — there's no shared circle
   history to protect).

Every proposal carries its source and a human-readable reason, which feeds
the WhatsApp message, the Matchings dashboard view, and the decision trace.
"""
import math
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from . import models

# --- Tunable weights (Section 5: "easy to tune ... without changing the
# structure of the algorithm") ---
W1_REST = 1.0
W2_RELIABILITY = 10.0
W3_RECENT_CONTACTS = 5.0
W4_SCARCITY = 8.0

RECENT_CONTACT_WINDOW_DAYS = 60


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def blood_compatible(patient_group: str, donor_group: str) -> bool:
    """Simplified ABO/Rh compatibility (recipient can receive from donor)."""
    compatibility = {
        "O-": {"O-"},
        "O+": {"O-", "O+"},
        "A-": {"O-", "A-"},
        "A+": {"O-", "O+", "A-", "A+"},
        "B-": {"O-", "B-"},
        "B+": {"O-", "O+", "B-", "B+"},
        "AB-": {"O-", "A-", "B-", "AB-"},
        "AB+": {"O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"},
    }
    return donor_group in compatibility.get(patient_group, set())


def is_eligible(db: Session, donor: models.Donor, as_of: date) -> bool:
    if donor.self_reported_unavailable:
        return False
    if donor.last_donation_date is None:
        return True
    days_since = (as_of - donor.last_donation_date).days
    return days_since >= donor.min_donation_gap_days


def already_booked_this_cycle(db: Session, donor_id: int, cycle_id: int) -> bool:
    """A donor already holding an active proposal/contact for another cycle
    close to this due date shouldn't be double-booked."""
    active = (
        db.query(models.Proposal)
        .join(models.Cycle)
        .filter(
            models.Proposal.donor_id == donor_id,
            models.Cycle.id != cycle_id,
            models.Cycle.status.in_(
                [models.CycleStatus.APPROVED, models.CycleStatus.CONTACTED, models.CycleStatus.CONFIRMED]
            ),
        )
        .first()
    )
    return active is not None


def recent_contact_count(db: Session, donor_id: int, as_of: date) -> int:
    since = as_of - timedelta(days=RECENT_CONTACT_WINDOW_DAYS)
    return (
        db.query(models.ContactEvent)
        .filter(
            models.ContactEvent.donor_id == donor_id,
            models.ContactEvent.sent_at >= since,
        )
        .count()
    )


def circle_scarcity(db: Session, patient_id: int, donor_id: int, as_of: date) -> float:
    """Rises toward 1.0 as fewer OTHER eligible donors remain in the circle."""
    memberships = (
        db.query(models.CircleMembership)
        .filter(models.CircleMembership.patient_id == patient_id)
        .all()
    )
    other_eligible = 0
    for m in memberships:
        if m.donor_id == donor_id:
            continue
        d = db.query(models.Donor).get(m.donor_id)
        if d and is_eligible(db, d, as_of) and not d.self_reported_unavailable:
            other_eligible += 1
    return 1.0 / (other_eligible + 1)


def score_circle_donor(db: Session, patient: models.Patient, donor: models.Donor, as_of: date) -> float:
    days_since = (as_of - donor.last_donation_date).days if donor.last_donation_date else 9999
    rest = max(0, days_since - donor.min_donation_gap_days)
    reliability = donor.reliability_score
    recent = recent_contact_count(db, donor.id, as_of)
    scarcity = circle_scarcity(db, patient.id, donor.id, as_of)
    return (
        W1_REST * rest
        + W2_RELIABILITY * reliability
        - W3_RECENT_CONTACTS * recent
        - W4_SCARCITY * scarcity
    )


def propose_donor_for_cycle(db: Session, cycle: models.Cycle, as_of: Optional[date] = None):
    """Circle-first, then general-pool fallback. Writes a ranked list of
    Proposal rows for this cycle (rank 1 = top choice) and returns them."""
    as_of = as_of or date.today()
    patient = cycle.patient

    # 1. Try the Blood Bridge circle first.
    circle_candidates = []
    for m in patient.circle_memberships:
        donor = m.donor
        if not blood_compatible(patient.blood_group, donor.blood_group):
            continue
        if not is_eligible(db, donor, as_of):
            continue
        if already_booked_this_cycle(db, donor.id, cycle.id):
            continue
        score = score_circle_donor(db, patient, donor, as_of)
        circle_candidates.append((donor, score))

    proposals = []
    if circle_candidates:
        circle_candidates.sort(key=lambda x: x[1], reverse=True)
        for rank, (donor, score) in enumerate(circle_candidates, start=1):
            days_since = (as_of - donor.last_donation_date).days if donor.last_donation_date else None
            reason = f"Blood Bridge circle · eligible (last donated {days_since} days ago)"
            proposals.append(
                models.Proposal(
                    cycle_id=cycle.id,
                    donor_id=donor.id,
                    rank=rank,
                    source=models.ProposalSource.CIRCLE,
                    score=score,
                    distance_km=haversine_km(patient.lat, patient.lng, donor.lat, donor.lng),
                    reason=reason,
                )
            )
    else:
        # 2. No eligible circle donor -> search the wider pool, nearest first.
        # The "general pool" means donors not already dedicated to ANY
        # patient's Blood Bridge circle — a circle donor belongs to their
        # own patient's rotation, not a floating resource other patients can
        # borrow. Only genuinely unaffiliated donors count here.
        general_candidates = []
        all_donors = db.query(models.Donor).all()
        for donor in all_donors:
            if donor.circle_memberships:
                continue  # already dedicated to some patient's circle
            if not blood_compatible(patient.blood_group, donor.blood_group):
                continue
            if not is_eligible(db, donor, as_of):
                continue
            if already_booked_this_cycle(db, donor.id, cycle.id):
                continue
            dist = haversine_km(patient.lat, patient.lng, donor.lat, donor.lng)
            general_candidates.append((donor, dist))

        general_candidates.sort(key=lambda x: x[1])
        for rank, (donor, dist) in enumerate(general_candidates, start=1):
            reason = f"No eligible circle donor · nearest general donor, {dist:.1f} km"
            proposals.append(
                models.Proposal(
                    cycle_id=cycle.id,
                    donor_id=donor.id,
                    rank=rank,
                    source=models.ProposalSource.GENERAL_POOL,
                    score=None,
                    distance_km=dist,
                    reason=reason,
                )
            )

    for p in proposals:
        db.add(p)
    db.commit()
    return proposals
