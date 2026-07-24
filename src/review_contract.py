"""The single trace-judge contract used by every queue stage."""
from __future__ import annotations

ACCEPTED = "accepted"
STATUS = "status"
JUDGE_ERROR = "judge_error"


def is_accepted(review: dict | None) -> bool:
    """Only an explicit judge acceptance moves a trace to publication."""
    return (bool(review) and review.get(ACCEPTED) is True
            and isinstance(review.get("judge"), dict))


def verdict_accepts(verdict: dict | None) -> bool:
    """Parse the schema-constrained verdict returned directly by the judge."""
    return bool(verdict) and verdict.get(ACCEPTED) is True


def is_judge_error(review: dict | None) -> bool:
    """Judge execution failures are infrastructure blocks, not rejections."""
    return bool(review) and (review.get(STATUS) == JUDGE_ERROR or ((review.get("deterministic") or {}).get("gates") or {}).get("setup_ok") is False)
