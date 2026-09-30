"""
Blood Warriors — circle-first donor matching engine.
Section 6: "The backend is written in Python using FastAPI. The matching
algorithm, the reply-window timers and the WhatsApp webhook all run in this
one service."
"""
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

if __package__:
    from .database import Base, engine, SessionLocal
    from .routers import cycles, activity, emergency, whatsapp, demo, patients
    from . import outreach
else:
    # Allows `python3 main.py` when Render's root directory is backend/app.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.database import Base, engine, SessionLocal
    from app.routers import cycles, activity, emergency, whatsapp, demo, patients
    from app import outreach

scheduler = BackgroundScheduler(timezone="UTC")


def _run_timeout_sweep():
    db = SessionLocal()
    try:
        outreach.sweep_timeouts(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    # Sweep frequently in fast-demo mode, otherwise every couple of minutes.
    interval = 5 if os.environ.get("DEMO_REPLY_WINDOW_SECONDS") else 120
    scheduler.add_job(_run_timeout_sweep, "interval", seconds=interval, id="timeout_sweep")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Blood Warriors — Donor Matching Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # PoC only — the mock UI is a local static file
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cycles.router)
app.include_router(activity.router)
app.include_router(emergency.router)
app.include_router(whatsapp.router)
app.include_router(demo.router)
app.include_router(patients.router)


@app.get("/")
def root():
    return {
        "service": "Blood Warriors donor matching engine",
        "docs": "/docs",
        "note": "See whatsapp_mock/index.html for the coordinator chat UI, "
                "and dashboard/streamlit_app.py for the read-only dashboard.",
    }
