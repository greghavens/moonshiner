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

LIMIT_PHRASE = "you've hit your usage limit"

# Exit status used when a queue stops because its runtime is out of quota. The
# systemd units list it in RestartPreventExitStatus so the supervisor does not
# restart a queue that has nothing to spend.
USAGE_LIMIT_EXIT = 75


class ModelUnavailable(RuntimeError):
    """Raised when a metered runtime reports it is out of quota."""


def is_usage_limit(message: str | None) -> bool:
    return bool(message) and LIMIT_PHRASE in message.lower()


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
