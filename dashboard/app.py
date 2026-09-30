"""
Blood Warriors — read-only coordinator dashboard.
Section 3 / 4.3-4.6 of the Sprint 1 Design Doc: Overview, Matchings,
Decision trace, Activity log. Reads from the same backend the WhatsApp
bot uses, so it always mirrors what the bot has done — no separate
write path.
"""
import os
from datetime import date, datetime

import requests
import streamlit as st

st.set_page_config(page_title="Blood Warriors — Coordinator Dashboard", layout="wide")

BACKEND = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")

STATUS_COLORS = {
    "due": "#9e9e9e", "proposed": "#f0ad4e", "approved": "#5bc0de",
    "contacted": "#5bc0de", "confirmed": "#28a745", "flagged": "#d9534f",
    "donated": "#6c757d",
}


def api_get(path, **params):
    try:
        r = requests.get(f"{BACKEND}{path}", params=params, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Couldn't reach backend at {BACKEND}{path} — {e}")
        return None


def api_post(path, json=None):
    try:
        r = requests.post(f"{BACKEND}{path}", json=json or {}, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Backend call failed: {e}")
        return None


# ---------------------------------------------------------------- Sidebar
st.sidebar.markdown("### 🩸 Blood Warriors")
st.sidebar.caption("Coordinator dashboard")
page = st.sidebar.radio("", ["Overview", "Matchings", "Decision trace", "Activity log"], label_visibility="collapsed")
st.sidebar.markdown("---")
st.sidebar.caption(f"Synced with WhatsApp bot\n{datetime.now().strftime('%I:%M %p')}")
with st.sidebar.expander("Settings"):
    st.text_input("Backend URL", value=BACKEND, key="backend_url_display", disabled=True)
    st.caption("Set BACKEND_URL env var to change.")


# ---------------------------------------------------------------- Overview
def page_overview():
    st.title("Cycle overview")
    st.caption(f"Routine 20-day cycles · next 7 days · as of {date.today().strftime('%a %d %b %Y')}")

    upcoming = api_get("/cycles/upcoming", days=7)
    matches = api_get("/cycles/matches", days=7)
    activity = api_get("/activity")
    if not (upcoming and matches and activity):
        return

    total_due = upcoming["total"]
    matched_count = matches["matched_count"]
    unmatched_count = matches["unmatched_count"]

    today_str = date.today().isoformat()
    matched_today = sum(
        1 for e in activity["entries"]
        if e["event"] == "Proposed match" and e["timestamp"].startswith(today_str)
    )
    contacted_today = sum(
        1 for e in activity["entries"]
        if e["event"] == "Contacted donor" and e["timestamp"].startswith(today_str)
    )
    flagged = [b for bucket in upcoming["buckets"].values() for b in bucket if b["status"] == "flagged"]

    cols = st.columns(5)
    cols[0].metric("Patients due", total_due, help="Next 7 days")
    cols[1].metric("Donor confirmed", f"{matched_count} of {total_due}")
    cols[2].metric("Matched by algorithm today", matched_today)
    cols[3].metric("Donor outreach today", contacted_today)
    cols[4].metric("Needs attention", len(flagged))

    st.markdown("---")
    left, right = st.columns([2, 1])

    with left:
        st.subheader("Transfusions due this week")
        day_cols = st.columns(7)
        buckets = upcoming["buckets"]
        ordered = buckets["today"] + buckets["in_1_day"] + buckets["in_2_days"] + buckets["in_3_7_days"]
        by_date = {}
        for item in ordered:
            by_date.setdefault(item["due_date"], []).append(item)
        sorted_dates = sorted(by_date.keys())[:7]
        for i, col in enumerate(day_cols):
            if i < len(sorted_dates):
                d = sorted_dates[i]
                with col:
                    st.caption(datetime.fromisoformat(d).strftime("%a %d"))
                    for item in by_date[d]:
                        color = STATUS_COLORS.get(item["status"], "#999")
                        st.markdown(
                            f"<div style='background:{color}22;border-left:3px solid {color};"
                            f"padding:4px 6px;margin-bottom:4px;border-radius:3px;font-size:12px'>"
                            f"<b>{item['name']}</b><br>{item['blood_group']}</div>",
                            unsafe_allow_html=True,
                        )

        if flagged:
            st.markdown("---")
            for f in flagged:
                st.error(f"**{f['name']}** · {f['blood_group']} · due {f['due_date']} — no donor found. "
                         f"See Decision trace for details.")

    with right:
        st.subheader("Latest decisions")
        for e in list(reversed(activity["entries"]))[:8]:
            t = datetime.fromisoformat(e["timestamp"]).strftime("%H:%M")
            who = e["patient"] or e["donor"] or ""
            st.caption(f"`{t}` **{e['event']}** — {who}")


# ---------------------------------------------------------------- Matchings
def page_matchings():
    st.title("Matchings")
    st.caption("Who is donating to whom this cycle, and each patient's Blood Bridge circle")

    matches = api_get("/cycles/matches", days=7)
    if not matches:
        return

    all_rows = []
    for m in matches["matched"]:
        all_rows.append({**m, "confirmed": True})
    for u in matches["unmatched"]:
        all_rows.append({**u, "confirmed": False})
    all_rows.sort(key=lambda r: r["due_date"])

    filter_choice = st.radio("Filter", ["All", "Donor confirmed", "Needs attention"], horizontal=True)

    for row in all_rows:
        status = row.get("status", "confirmed" if row["confirmed"] else "due")
        if filter_choice == "Donor confirmed" and status != "confirmed":
            continue
        if filter_choice == "Needs attention" and status != "flagged":
            continue

        circle = api_get(f"/patients/{row['patient_id']}/circle")
        dots = ""
        if circle:
            for d in circle["donors"]:
                color = {"donating": "#28a745", "eligible": "#5bc0de", "not_eligible": "#ccc"}[d["state"]]
                dots += f"<span title='{d[\"name\"]} ({d[\"state\"]})' style='color:{color}'>●</span> "

        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([2, 3, 2, 2])
            with c1:
                st.markdown(f"**{row['name']}**")
                st.caption(f"{row['blood_group']} · due {row['due_date']}")
            with c2:
                st.markdown("Blood Bridge circle:")
                st.markdown(dots or "—", unsafe_allow_html=True)
            with c3:
                if row["confirmed"]:
                    st.success(f"Confirmed\n{row.get('donor', '—')}")
                elif status == "flagged":
                    st.error("No reply · flagged")
                else:
                    st.warning(status.capitalize())
            with c4:
                st.caption("Source: Blood Bridge circle" if row["confirmed"] else "—")

    st.markdown("---")
    st.subheader("How a match is made")
    steps = st.columns(5)
    labels = [
        "Check the patient's Blood Bridge circle",
        "If none eligible, search the wider donor pool",
        "Coordinator approves on WhatsApp",
        "Bot contacts the donor; 20-min reply window",
        "No donor left → flagged to coordinator",
    ]
    for col, (i, label) in zip(steps, enumerate(labels, start=1)):
        with col:
            st.markdown(f"**{i}**")
            st.caption(label)


# ---------------------------------------------------------------- Decision trace
def page_trace():
    st.title("Decision trace")
    st.caption("Every step the algorithm and the coordinator took for one patient, and why")

    matches = api_get("/cycles/matches", days=7)
    if not matches:
        return
    all_patients = [{"name": m["name"], "cycle_id": m["cycle_id"], "flagged": False} for m in matches["matched"]]
    all_patients += [{"name": u["name"], "cycle_id": u["cycle_id"], "flagged": u.get("status") == "flagged"}
                      for u in matches["unmatched"]]

    names = [f"{p['name']}" + (" ⚠️" if p["flagged"] else "") for p in all_patients]
    choice = st.radio("Patient", names, horizontal=True) if names else None
    if not choice:
        st.info("No patients in the current 7-day window.")
        return
    selected = all_patients[names.index(choice)]

    trace = api_get(f"/cycles/{selected['cycle_id']}/trace")
    if not trace:
        return

    left, right = st.columns([2, 1])

    with left:
        p = trace["patient"]
        badge = "🔴 Needs attention" if p["status"] == "flagged" else f"Status: {p['status']}"
        st.subheader(f"{p['name']} ({p['blood_group']})")
        st.caption(f"Transfusion due {p['due_date']} · {p['cycle_length_days']}-day cycle")
        st.markdown(f"**{badge}**")

        st.markdown("#### What happened")
        for i, tl in enumerate(trace["timeline"], start=1):
            t = datetime.fromisoformat(tl["timestamp"]).strftime("%H:%M")
            who = f" — {tl['donor']}" if tl["donor"] else ""
            st.markdown(f"**{i}. {tl['event']}**{who} · `{t}` · _{tl['actor']}_")
            if tl["detail"]:
                st.caption(tl["detail"])

    with right:
        st.markdown("#### Rules applied")
        for k, v in trace["rules_applied"].items():
            st.caption(f"**{k.replace('_', ' ').title()}:** {v}")

        if p["status"] == "flagged":
            st.markdown("---")
            st.error(f"Resolve {p['name']}")
            st.caption("Due soon. Pick how to proceed — the choice is logged with your name.")
            coordinator_name = st.text_input("Your name", value="Coordinator", key="resolver_name")

            if st.button("Widen search radius", use_container_width=True):
                res = api_post(f"/cycles/{selected['cycle_id']}/resolve",
                                {"action": "widen_radius", "coordinator_name": coordinator_name})
                if res:
                    st.success(f"Found {res['found']} candidate(s)." if res["found"] else "Still no donor found.")
                    st.rerun()

            if st.button("Remind last-tried donor", use_container_width=True):
                res = api_post(f"/cycles/{selected['cycle_id']}/resolve",
                                {"action": "remind_donor", "coordinator_name": coordinator_name})
                if res:
                    st.success(f"Reminder sent to {res['reminded']}.")
                    st.rerun()

            donor_id_manual = st.text_input("Donor ID to assign manually", key="manual_donor_id")
            if st.button("Assign donor manually", use_container_width=True):
                if donor_id_manual.strip().isdigit():
                    res = api_post(f"/cycles/{selected['cycle_id']}/resolve",
                                    {"action": "assign_manually", "donor_id": int(donor_id_manual),
                                     "coordinator_name": coordinator_name})
                    if res:
                        st.success(f"Confirmed donor: {res['confirmed_donor']}.")
                        st.rerun()
                else:
                    st.warning("Enter a numeric donor ID (see the Matchings or Activity log view).")


# ---------------------------------------------------------------- Activity log
def page_activity():
    st.title("Activity log")
    st.caption("Every message, proposal, approval and reply the bot handled — read-only, timestamped")

    c1, c2, c3 = st.columns(3)
    actor = c1.selectbox("Actor", ["All", "bot", "coordinator", "donor"])
    event = c2.text_input("Event contains", "")
    on_date = c3.date_input("Date", value=None)

    params = {}
    if actor != "All":
        params["actor"] = actor
    if event:
        params["event"] = event
    if on_date:
        params["on_date"] = on_date.isoformat()

    data = api_get("/activity", **params)
    if not data:
        return
    st.caption(f"{data['count']} events")

    rows = data["entries"]
    st.dataframe(
        [
            {
                "Time": datetime.fromisoformat(e["timestamp"]).strftime("%H:%M:%S"),
                "Actor": e["actor"],
                "Event": e["event"],
                "Patient": e["patient"] or "—",
                "Donor": e["donor"] or "—",
                "Detail": e["detail"] or "",
            }
            for e in rows
        ],
        use_container_width=True,
        hide_index=True,
    )

    csv_rows = "Time,Actor,Event,Patient,Donor,Detail\n" + "\n".join(
        f'"{e["timestamp"]}","{e["actor"]}","{e["event"]}","{e["patient"] or ""}",'
        f'"{e["donor"] or ""}","{(e["detail"] or "").replace(chr(34), chr(39))}"'
        for e in rows
    )
    st.download_button("⬇ Export CSV", csv_rows, file_name="activity_log.csv", mime="text/csv")


PAGES = {
    "Overview": page_overview,
    "Matchings": page_matchings,
    "Decision trace": page_trace,
    "Activity log": page_activity,
}
PAGES[page]()
