"""
Synthetic data generator — Section 6: "Since we can't use real patient or
donor data, a script generates synthetic patients, Blood Bridge circles,
donors and donation histories to test against."

Reproduces the scenario walked through in the design doc's mock UI
(Section 4): 10 patients due in the next 7 days, 7 already matched, 3
unmatched (Arjun T. — no eligible circle donor for him, wait: he has one;
Divya R. — has one; Kabir N. — none, falls back to the general pool).

Note: the mockup screenshots show illustrative numbers that aren't always
internally consistent with a fixed 90-day donation gap (e.g. a donor shown
as eligible after "21 days"). This seed data tells the same story but with
donation dates that are actually consistent with the eligibility rule
implemented in matching.py, so a live run of /assign reproduces the same
narrative (Arjun T. and Divya R. get circle donors, Kabir N. falls back to
the nearest general donor) without a numeric contradiction baked in.
"""
import random
from datetime import date, timedelta

from sqlalchemy.orm import Session

from .database import Base, engine, SessionLocal
from . import models

random.seed(42)

# Rough Hyderabad-area coordinates used only to make distances meaningful.
BASE_LAT, BASE_LNG = 17.4239, 78.4738

BLOOD_GROUPS = ["O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"]
FIRST_NAMES_PATIENTS = ["Priya", "Arjun", "Meena", "Divya", "Kabir", "Sana", "Rohit", "Anika", "Vikram", "Neha"]
LAST_INITIALS = ["M.", "T.", "S.", "R.", "N.", "P.", "K.", "D.", "V.", "J."]
DONOR_NAMES = [
    "Ravi K.", "Sana P.", "Kiran V.", "Farhan A.", "Meera J.", "Aditi R.", "Suresh N.",
    "Priyanka L.", "Zoya H.", "Vikas T.", "Lakshmi B.", "Imran S.", "Kavya G.", "Deepak M.",
    "Ayesha F.", "Rahul C.", "Nandini S.", "Yusuf K.", "Tanvi A.", "Manoj R.", "Sneha V.",
    "Arif M.", "Pooja D.", "Karthik N.", "Reema S.", "Faisal Q.", "Harsha P.", "Divya L.",
    "Naveen G.", "Shreya M.",
]


def jittered_latlng():
    return (
        BASE_LAT + random.uniform(-0.08, 0.08),
        BASE_LNG + random.uniform(-0.08, 0.08),
    )


def make_donor(db: Session, name: str, blood_group: str, last_donation_days_ago: int,
                reliability: float = 0.85, unavailable: bool = False, min_gap: int = 90) -> models.Donor:
    lat, lng = jittered_latlng()
    donor = models.Donor(
        name=name,
        blood_group=blood_group,
        phone=f"+91-9{random.randint(100000000, 999999999)}",
        area="Hyderabad",
        lat=lat,
        lng=lng,
        last_donation_date=date.today() - timedelta(days=last_donation_days_ago),
        min_donation_gap_days=min_gap,
        self_reported_unavailable=unavailable,
        reliability_score=reliability,
    )
    db.add(donor)
    db.commit()
    db.refresh(donor)
    return donor


def make_patient(db: Session, name: str, blood_group: str, due_in_days: int,
                  cycle_length: int = 20, hospital: str = "Nizam's Institute") -> models.Patient:
    lat, lng = jittered_latlng()
    last_transfusion = date.today() + timedelta(days=due_in_days) - timedelta(days=cycle_length)
    patient = models.Patient(
        name=name,
        blood_group=blood_group,
        cycle_length_days=cycle_length,
        last_transfusion_date=last_transfusion,
        hospital=hospital,
        area="Hyderabad",
        lat=lat,
        lng=lng,
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


def add_to_circle(db: Session, patient: models.Patient, donor: models.Donor):
    db.add(models.CircleMembership(patient_id=patient.id, donor_id=donor.id))
    db.commit()


def make_cycle(db: Session, patient: models.Patient, due_in_days: int,
               status: models.CycleStatus = models.CycleStatus.DUE,
               confirmed_donor: models.Donor | None = None) -> models.Cycle:
    cycle = models.Cycle(
        patient_id=patient.id,
        due_date=date.today() + timedelta(days=due_in_days),
        status=status,
        confirmed_donor_id=confirmed_donor.id if confirmed_donor else None,
    )
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    return cycle


def seed():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # --- Named scenario patients (mirrors the design doc's mock UI walkthrough) ---

    # Priya M. — B+, due today, ALREADY MATCHED to a circle donor (Ravi K.)
    priya = make_patient(db, "Priya M.", "B+", due_in_days=0)
    ravi = make_donor(db, "Ravi K.", "B+", last_donation_days_ago=120)
    add_to_circle(db, priya, ravi)
    for i in range(9):
        # +1 offset avoids re-picking "Ravi K." (index 0) as a second,
        # distinct donor in the same circle.
        d = make_donor(db, f"{DONOR_NAMES[(i + 1) % len(DONOR_NAMES)]}", random.choice(["O+", "O-", "B+"]),
                        last_donation_days_ago=random.randint(10, 200))
        add_to_circle(db, priya, d)
    make_cycle(db, priya, due_in_days=0, status=models.CycleStatus.CONFIRMED, confirmed_donor=ravi)

    # Arjun T. — O-, due today, UNMATCHED — has an eligible circle donor (Kiran V.)
    arjun = make_patient(db, "Arjun T.", "O-", due_in_days=0)
    kiran = make_donor(db, "Kiran V.", "O-", last_donation_days_ago=110)
    add_to_circle(db, arjun, kiran)
    for i in range(9):
        # some ineligible (too recent) so the circle isn't trivially all-eligible
        d = make_donor(db, f"{DONOR_NAMES[(i + 5) % len(DONOR_NAMES)]}", "O-",
                        last_donation_days_ago=random.choice([15, 30, 45, 60]))
        add_to_circle(db, arjun, d)
    make_cycle(db, arjun, due_in_days=0, status=models.CycleStatus.DUE)

    # Meena S. — A+, due in 1 day, ALREADY MATCHED (Sana P.)
    meena = make_patient(db, "Meena S.", "A+", due_in_days=1)
    sana = make_donor(db, "Sana P.", "A+", last_donation_days_ago=95)
    add_to_circle(db, meena, sana)
    for i in range(9):
        d = make_donor(db, f"{DONOR_NAMES[(i + 10) % len(DONOR_NAMES)]}", "A+",
                        last_donation_days_ago=random.randint(10, 200))
        add_to_circle(db, meena, d)
    make_cycle(db, meena, due_in_days=1, status=models.CycleStatus.CONFIRMED, confirmed_donor=sana)

    # Divya R. — B-, due in 2 days, UNMATCHED — has an eligible circle donor (Farhan A.)
    divya = make_patient(db, "Divya R.", "B-", due_in_days=2)
    farhan = make_donor(db, "Farhan A.", "B-", last_donation_days_ago=100)
    add_to_circle(db, divya, farhan)
    for i in range(9):
        d = make_donor(db, f"{DONOR_NAMES[(i + 15) % len(DONOR_NAMES)]}", "B-",
                        last_donation_days_ago=random.choice([20, 30, 40, 50]))
        add_to_circle(db, divya, d)
    make_cycle(db, divya, due_in_days=2, status=models.CycleStatus.DUE)

    # Kabir N. — O+, due in 2 days, UNMATCHED — NO eligible circle donor
    kabir = make_patient(db, "Kabir N.", "O+", due_in_days=2)
    for i in range(10):
        d = make_donor(db, f"{DONOR_NAMES[(i + 20) % len(DONOR_NAMES)]}", "O+",
                        last_donation_days_ago=random.choice([10, 20, 30, 40, 50]))  # all too recent
        add_to_circle(db, kabir, d)
    # A general-pool donor, not in anyone's circle, placed close to Kabir's
    # hospital so she's reliably the nearest eligible fallback (matching the
    # design doc's worked example: "no eligible circle donor, nearest
    # general donor Meera J.").
    meera = make_donor(db, "Meera J.", "O+", last_donation_days_ago=130)
    meera.lat = kabir.lat + 0.001
    meera.lng = kabir.lng + 0.001
    db.commit()
    make_cycle(db, kabir, due_in_days=2, status=models.CycleStatus.DUE)

    # --- 5 more patients due in 3-7 days, all already matched ---
    extra_due_days = [3, 4, 5, 6, 7]
    for i, due in enumerate(extra_due_days):
        name = f"{FIRST_NAMES_PATIENTS[(i + 5) % len(FIRST_NAMES_PATIENTS)]} {LAST_INITIALS[(i + 5) % len(LAST_INITIALS)]}"
        bg = random.choice(BLOOD_GROUPS)
        p = make_patient(db, name, bg, due_in_days=due)
        circle_donor = make_donor(db, f"{DONOR_NAMES[(i + 25) % len(DONOR_NAMES)]}", bg,
                                   last_donation_days_ago=random.randint(95, 200))
        add_to_circle(db, p, circle_donor)
        for j in range(9):
            d = make_donor(db, f"donor-{name}-{j}", bg, last_donation_days_ago=random.randint(10, 200))
            add_to_circle(db, p, d)
        make_cycle(db, p, due_in_days=due, status=models.CycleStatus.CONFIRMED, confirmed_donor=circle_donor)

    db.close()
    print("Seed complete: 10 patients, 7 already matched, 3 unmatched (Arjun T., Divya R., Kabir N.).")
    print("Kabir N. has no eligible circle donor — /assign will fall back to Meera J. (general pool).")


if __name__ == "__main__":
    seed()
