"""Ticket Triage Console (layout A) with the Jev logic included.

Run:  python -m pip install streamlit pandas python-dotenv typesafe-sdk sqlalchemy pymysql
      python -m streamlit run jev_streamlit.py
Needs TYPESAFE_API_KEY and MYSQL_URL in your .env file, for example
MYSQL_URL=mysql+pymysql://user:password@localhost:3306/triage (the database must already exist). Streamlit 1.39 or newer is needed
for the sidebar link styling.
"""
import html
import os
import re
import time

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from typesafe_sdk import TypeSafeClient, Noul, Choice, Score, NoulCriteria

st.set_page_config(page_title="Ticket Triage", layout="wide")

load_dotenv()

client = TypeSafeClient()

# ======================= Jev logic =======================

criteria_team = {
    "technical": "Bugs, crashes, errors, login or export problems",
    "billing": "Charges, invoices, refunds, payment problems",
    "feature_request": "Requests for new features or improvements",
    "retention": "Customer threatens to cancel or leave"
}

criteria_severity = [
    "sev3: Not very urgent. Can be resolved in 5 days",
    "sev2: It needs attention. Can be resolved in 3 days",
    "sev1: It needs immediate attention. Should be resolved in 24 hours"
]

# Urgency shown in the UI has only three values
urgency_labels = {"sev1": "High", "sev2": "Medium", "sev3": "Low"}

questions = {
    "is_it_urgent": Noul(instructions="Does this customer question need urgent attention",
                         criteria=NoulCriteria(true="if customer is showing urgency")),
    "ticket_team": Choice(instructions="Which team should handle this ticket",
                          criteria=criteria_team),
    "ticket_severity": Score(instructions="What is the severity level of the ticket?",
                             criteria=criteria_severity)
}


def classify_ticket(text, subject=""):
    """Classify one ticket and return a flat dict the UI can show directly."""

    state = {"ticket": {"subject": subject, "text": text}}

    response = client.system_one(state=state, questions=questions)
    answers = response.answers

    team_answer = answers["ticket_team"]
    severity_answer = answers["ticket_severity"]
    urgent_answer = answers["is_it_urgent"]

    # Most likely severity level, then its label from the legend ("sev1: ...")
    severity_index = max(severity_answer.probabilities, key=severity_answer.probabilities.get)
    severity = severity_answer.legend[severity_index].split(":")[0]

    return {
        "team": team_answer.choice,
        "team_confidence": team_answer.confidence,
        "severity": severity,
        "urgency": urgency_labels[severity],
        "severity_confidence": severity_answer.confidence,
        "urgent_probability": urgent_answer.noul,
        # One number for the threshold slider: the weakest of the two decisions
        "confidence": min(team_answer.confidence, severity_answer.confidence),
    }


def classify_tickets(texts):
    """Classify a list of ticket texts, one after another."""
    return [classify_ticket(text) for text in texts]


# ======================= Streamlit UI =======================

SAMPLE_TICKETS = [
    "App crashes on every PDF upload",
    "Charged twice for the March plan",
    "Can you add a dark mode?",
    "Need an invoice copy for our audit",
    "Login works sometimes, not always",
    "Cancel my account unless this is fixed today",
    "The export has been failing since Monday",
    "Would be nice to have support for SSO",
]

PAGES = ["Inbox", "Review queue"]

# Make the sidebar buttons look like plain navigation links
st.markdown(
    """
    <style>
    [class*="st-key-nav_"] button, [class*="st-key-navon_"] button {
        background: transparent; border: none; box-shadow: none;
        padding: 0.5rem 0.75rem;
        justify-content: flex-start !important; text-align: left !important;
    }
    [class*="st-key-nav_"] button > div, [class*="st-key-navon_"] button > div {
        justify-content: flex-start !important; width: 100%;
    }
    [class*="st-key-nav_"] button [data-testid="stMarkdownContainer"],
    [class*="st-key-navon_"] button [data-testid="stMarkdownContainer"] {
        width: 100%; text-align: left !important;
    }
    [class*="st-key-nav_"] button p, [class*="st-key-navon_"] button p {
        text-align: left !important; width: 100%;
    }
    [class*="st-key-nav_"] button:hover { background: #e6e9ef; }
    [class*="st-key-navon_"] button { background: #dfe3ea; font-weight: 600; }
    button[data-testid="stBaseButton-primary"] { background-color: #d9372f; border-color: #d9372f; }
    button[data-testid="stBaseButton-primary"]:hover { background-color: #c22f27; border-color: #c22f27; }
    [data-testid="stSlider"] { filter: brightness(0.84); }
    [data-testid="stFileUploaderDropzone"] { padding: 0; background: transparent; border: none; }
    [data-testid="stFileUploaderDropzoneInstructions"] { display: none; }
    [data-testid="stFileUploaderDropzone"] button { width: 100%; }
    </style>
    """,
    unsafe_allow_html=True,
)


def classify_all(texts):
    """Run Jev on every ticket and time each call."""
    rows = []
    progress = st.progress(0.0)
    for i, text in enumerate(texts, start=1):
        start = time.perf_counter()
        result = classify_ticket(text)
        latency_ms = (time.perf_counter() - start) * 1000
        rows.append({"id": f"T-{1000 + i}", "ticket": text, **result, "latency_ms": latency_ms})
        progress.progress(i / len(texts))
    progress.empty()
    return pd.DataFrame(rows)


# ---------- Review queue ----------
TEAM_OPTIONS = list(criteria_team)
URGENCY_OPTIONS = ["High", "Medium", "Low"]
engine = create_engine(
    os.getenv("MYSQL_URL", "mysql+pymysql://root:password@localhost:3306/triage")
)


def execute_query(query, params=None):
    """For SELECT queries. Returns a DataFrame."""
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn, params=params)


def execute_write(query, params=None):
    """For INSERT and CREATE queries."""
    with engine.begin() as conn:
        conn.execute(text(query), params or {})


def pretty(team):
    return team.replace("_", " ").capitalize()


def load_reviews():
    execute_write(
        "CREATE TABLE IF NOT EXISTS reviews ("
        "id INT AUTO_INCREMENT PRIMARY KEY, ticket TEXT, "
        "predicted_team VARCHAR(50), predicted_urgency VARCHAR(10), confidence DOUBLE, "
        "final_team VARCHAR(50), final_urgency VARCHAR(10), decision VARCHAR(10), "
        "reviewed_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    return execute_query("SELECT * FROM reviews ORDER BY id")


def save_review(row, final_team, final_urgency):
    """Store the reviewer's answer as labeled data. Returns 'approved' or 'corrected'."""
    unchanged = (final_team, final_urgency) == (row["team"], row["urgency"])
    decision = "approved" if unchanged else "corrected"
    execute_write(
        "INSERT INTO reviews (ticket, predicted_team, predicted_urgency, confidence, "
        "final_team, final_urgency, decision) "
        "VALUES (:ticket, :pt, :pu, :conf, :ft, :fu, :decision)",
        {"ticket": row["ticket"], "pt": row["team"], "pu": row["urgency"],
         "conf": float(row["confidence"]), "ft": final_team, "fu": final_urgency,
         "decision": decision},
    )
    return decision


def try_save(row, final_team, final_urgency):
    """save_review, but show an error instead of crashing if MySQL is unavailable."""
    try:
        return save_review(row, final_team, final_urgency)
    except SQLAlchemyError as e:
        st.error(f"Could not save to MySQL: {e}")
        return None


RED = "#d9372f"

REVIEW_CSS = """
<style>
[class*="st-key-qcard_"] button, [class*="st-key-qcardon_"] button {
    background: #fff; border: 1px solid #e3e6ec; border-radius: 12px;
    padding: 16px 20px; height: auto; min-height: 0;
    justify-content: flex-start !important; text-align: left !important;
}
[class*="st-key-qcard_"] button:hover { background: #fafbfc; border-color: #c4c8d2; }
[class*="st-key-qcardon_"] button { background: #fff6f5; border-color: __RED__; }
[class*="st-key-qcard_"] button > div, [class*="st-key-qcardon_"] button > div {
    justify-content: flex-start !important; width: 100%;
}
[data-testid="stButton"] button p { white-space: nowrap; }
[class*="st-key-qcard_"] button p, [class*="st-key-qcardon_"] button p {
    text-align: left !important; white-space: pre-line !important;
    font-size: 1.05rem; line-height: 1.5; width: 100%;
}
</style>
""".replace("__RED__", RED)


def confidence_badge(confidence, threshold):
    """Colored badge for a button label (same color rule as the Inbox table)."""
    color = "yellow" if confidence >= threshold - 0.10 else "red"
    return f":{color}-background[**{confidence:.2f}**]"


def md_escape(text):
    """Escape characters that Markdown would treat as formatting in a button label."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|~:<>$])", r"\\\1", text)


def bar_html(label, value, right=None):
    """A label on the left, the value on the right and a thin bar underneath."""
    pct = int(round(float(value) * 100))
    right_text = f"{float(value):.2f}" if right is None else right
    return (
        '<div style="margin-top:16px;display:flex;justify-content:space-between;align-items:baseline">'
        f'<span style="font-size:1.05rem">{label}</span>'
        f'<span style="font-size:0.85rem;color:#5d6270">{right_text}</span></div>'
        '<div style="height:10px;border-radius:5px;background:#e8eaf0;margin-top:4px">'
        f'<div style="height:10px;width:{pct}%;border-radius:5px;background:{RED}"></div></div>'
    )


def render_review_page(classified, threshold):
    st.markdown(REVIEW_CSS, unsafe_allow_html=True)
    st.title("Review queue")

    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"))

    if not classified:
        st.info("Run classification first. Tickets below the threshold will show up here for review.")
        return

    try:
        reviews = load_reviews()
    except SQLAlchemyError as e:
        st.error(f"Could not connect to MySQL: {e}")
        st.caption("Check MYSQL_URL in your .env file and that the database exists.")
        return
    skipped = st.session_state.setdefault("skipped", set())
    df = st.session_state["df"]

    waiting = df[(df["confidence"] < threshold) & ~df["ticket"].isin(set(reviews["ticket"]))]
    waiting = waiting.sort_values("confidence")
    # Skipped tickets move to the end of the queue
    queue = pd.concat([waiting[~waiting["ticket"].isin(skipped)],
                       waiting[waiting["ticket"].isin(skipped)]])

    head_left, head_right = st.columns([3, 1])
    head_left.caption(f"{len(queue)} tickets waiting  |  {len(reviews)} reviewed")
    head_right.download_button(
        "Download labeled data", reviews.to_csv(index=False), "labeled_tickets.csv",
        "text/csv", disabled=reviews.empty, use_container_width=True,
    )

    if queue.empty:
        st.success("Nothing is waiting for review at this threshold.")
        return

    selected = st.session_state.get("selected_ticket")
    if selected not in set(queue["ticket"]):
        selected = queue.iloc[0]["ticket"]
    row = queue[queue["ticket"] == selected].iloc[0]

    left, right = st.columns([2, 3], gap="large")

    # Waiting tickets as cards: each card is one button (ID, confidence badge, ticket text)
    with left:
        for _, q in queue.iterrows():
            is_selected = q["ticket"] == selected
            label = f"**{q['id']}**  {confidence_badge(q['confidence'], threshold)}\n{md_escape(q['ticket'])}"
            key = ("qcardon_" if is_selected else "qcard_") + q["id"]
            if st.button(label, key=key, use_container_width=True):
                st.session_state["selected_ticket"] = q["ticket"]
                st.rerun()

    with right.container(border=True):
        st.markdown(
            f'<div style="font-size:0.9rem;color:#5d6270">{row["id"]}</div>'
            f'<div style="font-size:1.6rem;line-height:1.4;margin-top:6px">{html.escape(row["ticket"])}</div>'
            '<div style="font-size:0.9rem;color:#5d6270;margin-top:24px">Model predictions</div>'
            + bar_html(f"Team: {pretty(row['team'])}", row["team_confidence"])
            + bar_html(f"Urgency: {row['urgency']}", row["severity_confidence"])
            + bar_html("Likely urgent", row["urgent_probability"])
            + '<div style="height:12px"></div>',
            unsafe_allow_html=True,
        )

        c1, c2 = st.columns(2)
        team = c1.selectbox("Correct team", TEAM_OPTIONS, format_func=pretty,
                            index=TEAM_OPTIONS.index(row["team"]), key=f"team_{row['id']}")
        urgency = c2.selectbox("Correct urgency", URGENCY_OPTIONS,
                               index=URGENCY_OPTIONS.index(row["urgency"]), key=f"urg_{row['id']}")

        b1, b2, b3, _ = st.columns([4, 4, 2, 3])
        if b1.button("Approve prediction", type="primary", key=f"ok_{row['id']}"):
            if try_save(row, row["team"], row["urgency"]):
                st.session_state["flash"] = f"{row['id']} approved."
                st.session_state["selected_ticket"] = None
                st.rerun()
        if b2.button("Save correction", key=f"fix_{row['id']}"):
            decision = try_save(row, team, urgency)
            if decision:
                st.session_state["flash"] = f"{row['id']} saved as {decision}."
                st.session_state["selected_ticket"] = None
                st.rerun()
        if b3.button("Skip", key=f"skip_{row['id']}"):
            skipped.add(row["ticket"])
            st.session_state["selected_ticket"] = None
            st.rerun()

        st.caption("Corrections are stored as new labeled data for the next evaluation run.")


# ---------- Sidebar ----------
if "page" not in st.session_state:
    st.session_state["page"] = "Inbox"

st.sidebar.title("Ticket Triage")

for name in PAGES:
    is_active = st.session_state["page"] == name
    key = ("navon_" if is_active else "nav_") + name.replace(" ", "_")
    if st.sidebar.button(name, key=key, use_container_width=True):
        st.session_state["page"] = name
        st.rerun()

page = st.session_state["page"]

threshold = st.sidebar.slider("Auto-route threshold", 0.50, 0.99, 0.90, 0.01)
upload = st.sidebar.file_uploader("Upload CSV (needs a 'text' column)", type="csv")
run = st.sidebar.button("Run classification", type="primary", use_container_width=True)

# ---------- Data ----------
if upload is not None:
    uploaded = pd.read_csv(upload)
    if "text" not in uploaded.columns:
        st.error("The CSV needs a column named 'text'.")
        st.stop()
    texts = list(uploaded["text"].astype(str))
else:
    texts = SAMPLE_TICKETS

if run:
    try:
        st.session_state["df"] = classify_all(texts)
        st.session_state["texts"] = texts
    except Exception as e:
        st.error(f"Jev call failed: {e}")
        st.stop()

# Results only count if they belong to the tickets currently loaded
classified = "df" in st.session_state and st.session_state.get("texts") == texts

if page == "Review queue":
    render_review_page(classified, threshold)
    st.stop()

st.title("Inbox")

if not classified:
    # Before classification: show the loaded tickets with empty result columns
    st.info(f"{len(texts)} tickets loaded. Click 'Run classification' in the sidebar to classify them with Jev.")
    for col, label in zip(st.columns(3), ["Auto-routed", "Needs review", "Median latency"]):
        with col.container(border=True):
            st.metric(label, "\u2014")
    preview = pd.DataFrame({
        "ID": [f"T-{1000 + i}" for i in range(1, len(texts) + 1)],
        "Ticket": texts,
        "Team": "\u2014",
        "Urgency": "\u2014",
        "Confidence": "\u2014",
        "Status": "Not classified",
    })
    st.dataframe(preview, hide_index=True, use_container_width=True)
    st.stop()

df = st.session_state["df"].copy()
df["status"] = df["confidence"].ge(threshold).map({True: "Auto-routed", False: "In review"})

# ---------- Inbox ----------
st.caption("Tickets at or above the threshold are routed automatically; the rest go to review.")

kpis = [
    ("Auto-routed", int((df["status"] == "Auto-routed").sum())),
    ("Needs review", int((df["status"] == "In review").sum())),
    ("Median latency", f"{df['latency_ms'].median():.0f} ms"),
]
for col, (label, value) in zip(st.columns(3), kpis):
    with col.container(border=True):
        st.metric(label, value)

# Display table with capitalized column names
display = pd.DataFrame({
    "ID": df["id"],
    "Ticket": df["ticket"],
    "Team": df["team"].str.replace("_", " ").str.capitalize(),
    "Urgency": df["urgency"],
    "Confidence": df["confidence"],
    "Status": df["status"],
})


def color_confidence(value):
    """Green at or above the threshold, yellow within 0.10 below it, red below that."""
    if value >= threshold:
        return "background-color: #dff3e4; color: #1b6b34; font-weight: 600"
    if value >= threshold - 0.10:
        return "background-color: #fdf1cf; color: #7a5a00; font-weight: 600"
    return "background-color: #fde0e0; color: #a12828; font-weight: 600"


styled = display.style.format({"Confidence": "{:.2f}"})
apply_colors = styled.map if hasattr(styled, "map") else styled.applymap
styled = apply_colors(color_confidence, subset=["Confidence"])

st.dataframe(styled, hide_index=True, use_container_width=True)
st.caption(
    "Confidence colors: green at or above the threshold, yellow within 0.10 below it, red below that."
)