from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel


class DonorOut(BaseModel):
    id: int
    name: str
    blood_group: str
    phone: str
    area: str

    class Config:
        from_attributes = True


class ProposalOut(BaseModel):
    id: int
    donor: DonorOut
    rank: int
    source: str
    score: Optional[float]
    distance_km: Optional[float]
    reason: str
    outcome: str

    class Config:
        from_attributes = True


class PatientOut(BaseModel):
    id: int
    name: str
    blood_group: str
    hospital: str

    class Config:
        from_attributes = True


class CycleOut(BaseModel):
    id: int
    patient: PatientOut
    due_date: date
    status: str
    confirmed_donor_id: Optional[int]

    class Config:
        from_attributes = True


class AssignRequest(BaseModel):
    patient_ids: Optional[list[int]] = None  # None = all unmatched


class ApproveRequest(BaseModel):
    patient_ids: Optional[list[int]] = None  # None = approve all pending proposals


class DonorReplyRequest(BaseModel):
    cycle_id: int
    donor_id: int
    accepted: bool
    via: str = "whatsapp"  # "whatsapp" | "phone"


class ResolveRequest(BaseModel):
    action: str  # "widen_radius" | "remind_donor" | "assign_manually"
    donor_id: Optional[int] = None  # for assign_manually
    coordinator_name: str = "Coordinator"


class ActivityEntryOut(BaseModel):
    id: int
    timestamp: datetime
    actor: str
    event: str
    patient_id: Optional[int]
    donor_id: Optional[int]
    detail: Optional[str]

    class Config:
        from_attributes = True
