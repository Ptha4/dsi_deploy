"""
ORM models for the Blood Warriors circle-first donor matching engine.

Schema mirrors the services in Section 5 of the Sprint 1 Design Doc:
cycle tracker, eligibility tracker, match status, matching engine,
outreach orchestrator, decision log.
"""
import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime, Boolean,
    ForeignKey, Enum, Text
)
from sqlalchemy.orm import relationship

from .database import Base


class CycleStatus(str, enum.Enum):
    DUE = "due"                # appears in /upcoming, not yet matched
    PROPOSED = "proposed"      # /assign picked a donor, awaiting approval
    APPROVED = "approved"      # coordinator confirmed, outreach about to start
    CONTACTED = "contacted"    # bot messaged the donor, reply window open
    CONFIRMED = "confirmed"    # donor said yes
    FLAGGED = "flagged"        # no donor left, needs coordinator attention
    DONATED = "donated"        # donation recorded, cycle closed


class ProposalSource(str, enum.Enum):
    CIRCLE = "blood_bridge_circle"
    GENERAL_POOL = "general_pool"


class ContactOutcome(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    TIMED_OUT = "timed_out"


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    blood_group = Column(String, nullable=False)  # e.g. "B+", "O-"
    cycle_length_days = Column(Integer, default=20, nullable=False)
    last_transfusion_date = Column(Date, nullable=False)
    hospital = Column(String, nullable=False)
    area = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)

    circle_memberships = relationship("CircleMembership", back_populates="patient")
    cycles = relationship("Cycle", back_populates="patient")


class Donor(Base):
    __tablename__ = "donors"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    blood_group = Column(String, nullable=False)
    phone = Column(String, nullable=False)  # synthetic
    area = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    last_donation_date = Column(Date, nullable=True)
    min_donation_gap_days = Column(Integer, default=90, nullable=False)
    self_reported_unavailable = Column(Boolean, default=False)
    reliability_score = Column(Float, default=0.85)  # past yes-rate, 0..1

    circle_memberships = relationship("CircleMembership", back_populates="donor")


class CircleMembership(Base):
    """A donor's membership in one patient's Blood Bridge circle (~10 donors/patient)."""
    __tablename__ = "circle_memberships"

    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    donor_id = Column(Integer, ForeignKey("donors.id"), nullable=False)

    patient = relationship("Patient", back_populates="circle_memberships")
    donor = relationship("Donor", back_populates="circle_memberships")


class Cycle(Base):
    """One patient's recurring transfusion cycle instance."""
    __tablename__ = "cycles"

    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    due_date = Column(Date, nullable=False)
    status = Column(Enum(CycleStatus), default=CycleStatus.DUE, nullable=False)
    confirmed_donor_id = Column(Integer, ForeignKey("donors.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="cycles")
    proposals = relationship("Proposal", back_populates="cycle", order_by="Proposal.rank")
    contact_events = relationship("ContactEvent", back_populates="cycle")


class Proposal(Base):
    """One candidate donor proposed for a cycle, in ranked order."""
    __tablename__ = "proposals"

    id = Column(Integer, primary_key=True)
    cycle_id = Column(Integer, ForeignKey("cycles.id"), nullable=False)
    donor_id = Column(Integer, ForeignKey("donors.id"), nullable=False)
    rank = Column(Integer, nullable=False)  # 1 = top choice
    source = Column(Enum(ProposalSource), nullable=False)
    score = Column(Float, nullable=True)  # null for general-pool (ranked by distance instead)
    distance_km = Column(Float, nullable=True)
    reason = Column(String, nullable=False)  # human-readable, feeds WhatsApp + dashboard
    tried = Column(Boolean, default=False)
    outcome = Column(Enum(ContactOutcome), default=ContactOutcome.PENDING)
    created_at = Column(DateTime, default=datetime.utcnow)

    cycle = relationship("Cycle", back_populates="proposals")
    donor = relationship("Donor")


class ContactEvent(Base):
    """One outreach attempt to a donor for a cycle."""
    __tablename__ = "contact_events"

    id = Column(Integer, primary_key=True)
    cycle_id = Column(Integer, ForeignKey("cycles.id"), nullable=False)
    donor_id = Column(Integer, ForeignKey("donors.id"), nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    reply_window_minutes = Column(Integer, default=20)
    responded_at = Column(DateTime, nullable=True)
    outcome = Column(Enum(ContactOutcome), default=ContactOutcome.PENDING)

    cycle = relationship("Cycle", back_populates="contact_events")
    donor = relationship("Donor")


class DecisionLogEntry(Base):
    """Append-only, timestamped record of every action — feeds the Activity Log
    and per-patient Decision Trace views on the dashboard."""
    __tablename__ = "decision_log"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    actor = Column(String, nullable=False)  # "bot" | "coordinator" | "donor"
    event = Column(String, nullable=False)  # short label, e.g. "Proposed match"
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    donor_id = Column(Integer, ForeignKey("donors.id"), nullable=True)
    detail = Column(Text, nullable=True)

    patient = relationship("Patient")
    donor = relationship("Donor")
