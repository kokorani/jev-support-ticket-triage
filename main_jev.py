from typesafe_sdk import TypeSafeClient, Noul, Choice, Score, NoulCriteria
from dotenv import load_dotenv

load_dotenv()

client = TypeSafeClient()

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


if __name__ == "__main__":

    sample_tickets = [
        "App crashes on every PDF upload",
        "Charged twice for the March plan",
        "Can you add a dark mode?",
        "Need an invoice copy for our audit",
        "Login works sometimes, not always",
        "Cancel my account unless this is fixed today",
    ]

    for text, result in zip(sample_tickets, classify_tickets(sample_tickets)):
        print(text)
        print(result)
        print()