"""Usage-limit detection for metered runtimes.

A usage limit is a live condition, not a fact. Providers move reset times,
credits get purchased mid-run, and plans roll over early, so the reset time a
provider quotes in an error string is stale the moment it is read. Moonshiner
therefore never persists a limit and never schedules against a quoted date:
the limit is detected in-process, the run stops, and the next start finds out
for itself whether the runtime answers.

``purge_legacy_markers`` removes the on-disk blocks written by releases before
0.5.65, so upgrading clears any project still sitting behind a quoted date.
"""
from __future__ import annotations

from pathlib import Path

from common import RUNS

LIMIT_PHRASES = (
    "you've hit your usage limit",
    # OpenRouter answers 402 when the account cannot afford the request it was
    # asked to make. That is the same live condition as any other quota block:
    # nothing to spend now, possibly funded a minute later. Treated as a
    # per-job failure instead, it burns one of a seed's limited attempts on
    # every seed in the queue while never reaching the model at all.
    "requires more credits",
    "insufficient credits",
)

# Exit status used when a queue stops because its runtime is out of quota. The
# systemd units list it in RestartPreventExitStatus so the supervisor does not
# restart a queue that has nothing to spend.
USAGE_LIMIT_EXIT = 75

# Exit status used when a queue stops because its infrastructure is broken —
# a missing prerequisite, an unusable environment. Listed alongside the usage
# limit in RestartPreventExitStatus so the supervisor does not restart a queue
# into the same wall. An infrastructure failure is never a seed's fault and is
# never worked around silently: the run stops and says so.
INFRASTRUCTURE_EXIT = 78


class ModelUnavailable(RuntimeError):
    """Raised when a metered runtime reports it is out of quota."""


def is_usage_limit(message: str | None) -> bool:
    lowered = (message or "").lower()
    return any(phrase in lowered for phrase in LIMIT_PHRASES)


def find_usage_limit(*messages: str | None) -> str | None:
    """Return the first message reporting a usage limit, if any."""
    for message in messages:
        if is_usage_limit(message):
            return message.strip()
    return None


def purge_legacy_markers() -> list[Path]:
    """Delete usage-limit markers persisted by earlier releases."""
    removed = []
    try:
        markers = sorted(RUNS.glob("model-unavailable-*.json"))
    except OSError:
        return removed
    for marker in markers:
        try:
            marker.unlink()
        except OSError:
            continue
        removed.append(marker)
    return removed
