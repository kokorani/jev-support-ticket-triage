# Ticket Triage Console

A Streamlit app that uses **Jev** (TypeSafe AI) to triage support tickets. Jev picks a team, severity and urgency for each ticket. Tickets Jev is confident about are auto-routed, and the rest go to a review queue where a person approves or corrects them. Every decision is saved to MySQL as labeled data.

<img width="1460" height="867" alt="Image" src="https://github.com/user-attachments/assets/6f045e51-ba78-4db0-adce-95ddae2dc103" />

<img width="1462" height="914" alt="Image" src="https://github.com/user-attachments/assets/27cf5d87-91fb-41d1-a771-f8a599d72c13" />

## Files

| File | Purpose |
|---|---|
| `main_jev.py` | Jev logic only. Run `python main_jev.py` to test it on six tickets. |
| `jev_streamlit.py` | The full app (includes its own copy of the Jev logic). |
| `sample_tickets.csv` | 10 sample tickets to upload. |
| `req.txt` | Packages to install. |
| `.env` | You create this (see Setup). |

If you change the Jev questions, update both `main_jev.py` and `jev_streamlit.py`.

## Setup

1. Install packages: `python -m pip install -r req.txt`
2. Create the database: `CREATE DATABASE triage;` (the app creates the table itself)
3. Create `.env` next to the code:
   ```
   TYPESAFE_API_KEY=your_key
   MYSQL_URL=mysql+pymysql://user:password@localhost:3306/triage
   ```
   URL-encode special characters in the password (`@` becomes `%40`).
4. Run: `python -m streamlit run jev_streamlit.py`

Always start it with `streamlit run`. Running `python jev_streamlit.py` causes "missing ScriptRunContext" warnings and a `KeyError`.

## How it works

1. **Inbox:** upload a CSV with a `text` column (or use the built-in samples) and click **Run classification**. Each ticket makes one Jev call.
2. Jev answers three questions per ticket: **team** (technical, billing, feature_request or retention), **severity** (sev1, sev2 or sev3) and **is it urgent** (yes/no).
3. The **threshold slider** splits tickets into *Auto-routed* and *In review*. Moving it never calls Jev again.
4. **Review queue:** click a ticket card, then **Approve prediction**, **Save correction** or **Skip**. Decisions are saved to MySQL, and **Download labeled data** exports them as CSV.

### Data flow

```mermaid
flowchart TD
    A["CSV upload (text column)<br/>or 8 built-in sample tickets"] --> B["Run classification"]
    B --> C["Jev: one call per ticket<br/>team, severity, urgent"]

    subgraph session["Browser session (cleared on refresh)"]
        D["Result per ticket<br/>team, urgency, confidence,<br/>urgent probability, latency"]
        E{"confidence >= threshold?<br/>(sidebar slider)"}
        F["Auto-routed"]
        G["In review<br/>(Review queue)"]
        H["Reviewer: Approve,<br/>Correct or Skip"]
        D --> E
        E -->|Yes| F
        E -->|No| G
        G --> H
        H -->|Skip| G
    end

    C --> D
    H -->|"Approve or Correct"| I[("MySQL<br/>reviews table")]
    I -.->|"reviewed tickets leave the queue"| G
    I --> J["Download labeled data (CSV)"]
```

## Metrics

| Metric | Meaning |
|---|---|
| **Team** | The team Jev chose. |
| **Team confidence** | Jev's confidence (0 to 1) in the team. Often exactly 1.0 on clear tickets. |
| **Severity** | The most likely of sev1, sev2 or sev3, taken from Jev's probabilities. Jev's raw blended `score` is not used. |
| **Severity confidence** | Jev's own confidence (0 to 1) in the severity. It is not the same as the top probability, so check TypeSafe's docs for its exact definition. |
| **Urgency** | High, Medium or Low, mapped from severity (sev1 = High, sev2 = Medium, sev3 = Low). |
| **Urgent probability** | Chance (0 to 1) from the yes/no urgency question. Shown as "Likely urgent" in the Review queue. Not used for routing. |
| **Confidence** | `min(team confidence, severity confidence)`. The number behind the colors, status and review queue. The lower of the two means a ticket is only auto-routed when Jev is sure about both. |
| **Latency** | Time in milliseconds around one Jev call. The **Median latency** card is the median across tickets, so one slow call doesn't distort it. |
| **Threshold** | Slider, 0.50 to 0.99, default 0.90. |
| **Status** | *Auto-routed* if `confidence >= threshold`, otherwise *In review*. |
| **Auto-routed / Needs review** | Ticket counts per status. They add up to the total. |
| **Confidence color** | Green: at or above the threshold. Yellow: within 0.10 below it. Red: lower. At 0.90: green 0.90+, yellow 0.80 to 0.89, red below 0.80. |
| **Tickets waiting** | Tickets below the threshold that haven't been reviewed (matched by ticket text), lowest confidence first, skipped ones last. |
| **Reviewed** | Number of rows in the `reviews` table, across all runs. |
| **Prediction bars** | In the Review queue: team confidence, severity confidence and urgent probability. |
| **Decision** | *approved* if the final team and urgency match Jev's answer, otherwise *corrected*. Approve prediction is always approved. |

**Example:** "Charged twice for the March plan" gets team `billing` (confidence 1.0) and severity sev2 (confidence 0.44), so urgency is Medium and confidence is `min(1.0, 0.44)` = 0.44. At threshold 0.90 it goes to review and shows red.

**Saved review fields** (`reviews` table): `id`, `ticket`, `predicted_team`, `predicted_urgency`, `confidence`, `final_team`, `final_urgency`, `decision`, `reviewed_at`.

## Reset saved reviews

```sql
USE triage;
DELETE FROM reviews;
```

Then refresh the app.

## Troubleshooting

- **`ModuleNotFoundError: typesafe_sdk`:** Streamlit is using a different Python. Use `python -m pip install -r req.txt` and `python -m streamlit run jev_streamlit.py`.
- **"Could not connect to MySQL":** check `MYSQL_URL`, that MySQL is running, and that the database exists.
- **"Jev call failed":** check `TYPESAFE_API_KEY` and your network.
- **Styling looks off:** upgrade Streamlit (`python -m pip install -U streamlit`).

## Notes

- Classification results live in the browser session and are lost on refresh. Saved reviews stay in MySQL.
- Ticket IDs (T-1001 and so on) are assigned per run and are not saved.
- Reviewers only see low-confidence tickets, so any accuracy worked out from the saved reviews will understate Jev's accuracy on confident tickets.
