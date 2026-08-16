"""The single trace-judge contract used by every queue stage."""
from __future__ import annotations

ACCEPTED = "accepted"
STATUS = "status"
JUDGE_ERROR = "judge_error"


def is_accepted(review: object | None) -> bool:
    """Only an explicit judge acceptance moves a trace to publication.

    Anything that is not a verdict mapping is simply not an acceptance. The
    reviews directory holds judge artifacts alongside the ``<seed-id>.json``
    verdicts, and ``accepted_ids`` reads every ``.json`` in it: the schema file
    has always been in there, and the OpenCode judge added a raw session
    transcript, which is a JSON *array*. Asking that for ``.get`` raised
    AttributeError out of seed selection -- a judge artifact could stop the
    trace queue from choosing any seed at all. Fail closed on shape here, at
    the one contract every stage shares, rather than in each caller.
    """
    return (isinstance(review, dict) and review.get(ACCEPTED) is True
            and isinstance(review.get("judge"), dict))


def verdict_accepts(verdict: dict | None) -> bool:
    """Parse the schema-constrained verdict returned directly by the judge."""
    return bool(verdict) and verdict.get(ACCEPTED) is True


def is_judge_error(review: object | None) -> bool:
    """Judge execution failures are infrastructure blocks, not rejections.

    Shape-guarded for the same reason as :func:`is_accepted`: these two read
    the same review objects, so a non-mapping reaching one reaches the other.
    """
    return isinstance(review, dict) and (review.get(STATUS) == JUDGE_ERROR or ((review.get("deterministic") or {}).get("gates") or {}).get("setup_ok") is False)
