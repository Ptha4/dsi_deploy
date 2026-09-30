"""
Emergency matcher (secondary) — Section 3: "For urgent, unplanned requests
the coordinator can run the same engine with a same-day horizon. It skips
circles, searches the wider compatible pool nearest first, and still
respects eligibility rules."
"""
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models, matching
from ..database import get_db
from ..decision_log import log

router = APIRouter(tags=["emergency"])


class EmergencyRequest(BaseModel):
    blood_group: str
    hospital: str
    lat: float
    lng: float


@router.post("/emergency")
def emergency_match(req: EmergencyRequest, db: Session = Depends(get_db)):
    today = date.today()
    donors = db.query(models.Donor).all()
    candidates = []
    for donor in donors:
        if not matching.blood_compatible(req.blood_group, donor.blood_group):
            continue
        if not matching.is_eligible(db, donor, today):
            continue
        dist = matching.haversine_km(req.lat, req.lng, donor.lat, donor.lng)
        candidates.append((donor, dist))
    candidates.sort(key=lambda x: x[1])

    log(db, actor="coordinator", event="Ran emergency match",
        detail=f"Same-day emergency request for {req.blood_group} at {req.hospital}")

    return {
        "blood_group": req.blood_group,
        "hospital": req.hospital,
        "ranked_donors": [
            {"donor": d.name, "phone": d.phone, "distance_km": round(dist, 1)}
            for d, dist in candidates[:10]
        ],
    }
