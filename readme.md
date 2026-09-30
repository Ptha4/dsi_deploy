# Blood Warriors — Circle-First Donor Matching Engine (Sprint 1 PoC)

Working prototype of the system described in the Sprint 1 Design Doc:
a coordinator-facing WhatsApp bot backed by a circle-first donor matching
engine, plus a read-only dashboard for tracking and decision-tracing.

## What's here

```
backend/            FastAPI service — the engine, the API, the WhatsApp webhook
  app/
    models.py        SQLAlchemy schema (patients, donors, circles, cycles, proposals, ...)
    matching.py       Circle-first scoring engine (Score(d) = w1·rest + w2·reliability - w3·recentContacts - w4·scarcity)
    outreach.py       Contacts donors, handles the reply window, cascades on decline/timeout
    seed_data.py      Synthetic data generator (reproduces the design doc's walkthrough)
    decision_log.py   Append-only log helper — feeds the Activity Log & Decision Trace
    routers/          /cycles/*, /webhook/whatsapp, /activity, /emergency, /patients/*/circle, /demo/*
  requirements.txt

whatsapp_mock/       Coordinator chat UI (plain HTML/JS, styled like WhatsApp)
  index.html          Talks to the backend's /webhook/whatsapp — same endpoint a real
                       WhatsApp Business API integration would call

dashboard/           Read-only Streamlit dashboard (Overview, Matchings, Decision trace, Activity log)
  streamlit_app.py
  requirements.txt
```

## Why it's built this way (matches Section 6 of the design doc)

- **Backend:** Python + FastAPI, SQLite locally (drop-in swap to Postgres/Supabase
  for a hosted demo — same schema, just change `DATABASE_URL`).
- **Matching engine:** rule-based filtering + weighted scoring — no historical
  data needed, every decision traceable to a specific rule or score term.
- **WhatsApp:** the webhook (`POST /webhook/whatsapp`) is what a real Twilio
  Sandbox or Meta Cloud API integration would call. Since this PoC has no live
  WhatsApp credentials wired up yet, `whatsapp_mock/index.html` is a small
  static page that talks to the *same* webhook, so the demo behaves exactly
  like the eventual live integration would — swapping in real WhatsApp later
  means adding a webhook adapter in front of this endpoint, not rewriting the
  engine.
- **Dashboard:** read-only Streamlit app hitting the backend's REST API, so it
  always mirrors what the bot has actually done.

## Running it locally

### 1. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt

# Seed synthetic data (10 patients, 7 already matched, 3 unmatched —
# reproduces the design doc's Figures 4.1-4.6 walkthrough)
python3 -m app.seed_data

# Run the API (add DEMO_REPLY_WINDOW_SECONDS=20 to shrink the 20-minute
# donor reply window down to 20 seconds for fast demoing)
DEMO_REPLY_WINDOW_SECONDS=20 uvicorn app.main:app --reload --port 8000
```

API docs: http://127.0.0.1:8000/docs

### 2. WhatsApp mock chat UI

Just open `whatsapp_mock/index.html` in a browser (double-click, or
`open whatsapp_mock/index.html`). It defaults to `http://127.0.0.1:8000` —
change it via the ⋮ menu in the top-right if your backend runs elsewhere.

Try, in order: `/upcoming` → `/matches` → `/assign` → `CONFIRM ALL`.
As donors are contacted, the chat surfaces "(Demo) Simulate <donor>'s reply"
buttons so you can click YES/NO instead of waiting for a real WhatsApp
message — this is a stand-in for donor replies until real WhatsApp
credentials are wired in.

### 3. Dashboard

```bash
cd dashboard
pip install -r requirements.txt
BACKEND_URL=http://127.0.0.1:8000 streamlit run streamlit_app.py
```

Opens at http://localhost:8501 — Overview, Matchings, Decision trace, and
Activity log, matching Figures 4.3–4.6 of the design doc.

## The walkthrough scenario (seeded data)

Matches the design doc's mock UI exactly:

- **10 patients** due in the next 7 days.
- **7 already matched** to a confirmed donor from their Blood Bridge circle.
- **3 unmatched:** Arjun T. (due today), Divya R. and Kabir N. (due in 2 days).
- Running `/assign` finds circle donors for Arjun T. (Kiran V.) and Divya R.
  (Farhan A.); Kabir N. has no eligible donor in his circle, so the engine
  falls back to the nearest general-pool donor (Meera J.).
- After `CONFIRM ALL`, Kiran V. and Farhan A. can be confirmed via the
  simulate-reply buttons; if Meera J.'s simulated reply is left pending past
  the reply window, the timeout sweep automatically cascades and — since no
  one else is left to try in this scenario — flags Kabir N. for the
  coordinator, exactly like Figure 4.2's right-hand panel.

Reset back to this scenario at any time: `POST /demo/reset`, or the
"Reset demo data" button in the mock chat UI's settings panel.

## A deliberate departure from the mockup's exact numbers

The design doc's screenshots show illustrative numbers that aren't always
internally consistent with a single fixed donation-gap rule (e.g. a donor
shown as eligible after only "21 days" elsewhere, while the doc also states
a "90-day donor gap"). This build uses donation dates that are actually
consistent with the eligibility rule implemented in `matching.py`, so a live
run of `/assign` reproduces the same *narrative* (Arjun T. and Divya R. get
circle donors, Kabir N. falls back to the general pool) without baking in a
numeric contradiction.

## What's not wired up yet (matches "Out of scope for the PoC")

- No real WhatsApp Business API / Twilio credentials — `whatsapp_mock/` is
  the stand-in front end for the demo.
- No login, user management, or hospital system integration.
- No production frontend — the dashboard is the mock UI's tracking view,
  per the design doc.
- The emergency-matching endpoint (`POST /emergency`) exists and is tested,
  but per the design doc it's a secondary path and isn't exposed in the
  mock UI.

## Next (per the design doc's plan)

Tune the scoring weights (`W1_REST`, `W2_RELIABILITY`, `W3_RECENT_CONTACTS`,
`W4_SCARCITY` in `matching.py`) once there's real response data from Blood
Warriors to learn from — the structure of the algorithm doesn't need to
change to do this.
