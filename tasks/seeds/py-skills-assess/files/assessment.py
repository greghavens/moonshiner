"""Scorer for the new-hire skills self-assessment.

During onboarding week each new hire works through a question bank.
Question kinds:

  * "scale"  — rate yourself 0..max; 0 is a real answer ("never used it").
               Worth rating * weight points. A scale question may declare
               an "assumed" rating that stands in when the question was
               skipped, so one skipped item doesn't crater the total.
  * "choice" — one correct option; full weight or nothing.
  * "text"   — free-form, ungraded (0 points) but counts for completion.

Responses arrive as {question_id: value}. The intake UI records exactly
what was submitted — a blank comment box comes through as "" and still
counts as answered. A question is unanswered only when its id is absent
from the responses dict or mapped to None.
"""


def points_for(question, value):
    """Points earned by *value* on one question."""
    kind = question["kind"]
    weight = question.get("weight", 1)
    if kind == "scale":
        if not 0 <= value <= question["max"]:
            raise ValueError(f"rating {value!r} out of range for {question['id']}")
        return value * weight
    if kind == "choice":
        return weight if value == question["answer"] else 0
    if kind == "text":
        return 0
    raise ValueError(f"unknown question kind: {kind!r}")


def total_score(questions, responses):
    """Sum of points across the bank, applying assumed ratings to skips."""
    total = 0
    for question in questions:
        value = responses.get(question["id"]) or question.get("assumed")
        if value is None:
            continue
        total += points_for(question, value)
    return total


def unanswered(questions, responses):
    """Ids of questions the new hire never submitted anything for."""
    missing = []
    for question in questions:
        if not responses.get(question["id"]):
            missing.append(question["id"])
    return missing


def completion_rate(questions, responses):
    """Fraction of the bank that was answered, 0.0..1.0."""
    if not questions:
        return 1.0
    answered = len(questions) - len(unanswered(questions, responses))
    return answered / len(questions)


def summary(questions, responses):
    """The row the manager dashboard renders for one new hire."""
    return {
        "score": total_score(questions, responses),
        "completion": round(completion_rate(questions, responses), 3),
        "needs_follow_up": unanswered(questions, responses),
    }
