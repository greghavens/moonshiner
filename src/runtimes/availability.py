"""Fail-closed usage-limit backoff shared by metered runtimes.

Codex (ChatGPT-backed) and Claude Code emit a human "you've hit your usage
limit … try again at <when>" error. When observed, we persist a marker so no
further metered attempt is started until the reset time — a batch must defer,
not burn attempts against a wall. The marker lives under ``runs/`` keyed by
runtime so a Codex block does not stall a Pi run and vice versa.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from common import RUNS

LIMIT_PHRASE = "you've hit your usage limit"
RESET_RE = re.compile(
    r"try again at\s+([A-Z][a-z]{2})\s+(\d{1,2})(?:st|nd|rd|th),\s+"
    r"(\d{4})\s+(\d{1,2}:\d{2}\s+[AP]M)",
    re.IGNORECASE,
)


class ModelUnavailable(RuntimeError):
    """Raised before a metered lane can consume or damage an attempt."""


def _marker(runtime: str) -> Path:
    return RUNS / f"model-unavailable-{runtime}.json"


def _now() -> datetime:
    return datetime.now().astimezone()


def parse_retry_at(message: str, now: datetime | None = None) -> datetime | None:
    if LIMIT_PHRASE not in message.lower():
        return None
    match = RESET_RE.search(message)
    if not match:
        return None
    now = now or _now()
    value = datetime.strptime(
        f"{match.group(1)} {match.group(2)} {match.group(3)} {match.group(4).upper()}",
        "%b %d %Y %I:%M %p",
    )
    return value.replace(tzinfo=now.tzinfo)


def record_block(runtime: str, message: str, source: str,
                 now: datetime | None = None) -> dict | None:
    """Persist a backoff marker if ``message`` is a usage-limit notice."""
    now = now or _now()
    retry_at = parse_retry_at(message, now)
    if retry_at is None:
        return None
    value = {
        "kind": f"{runtime}_usage_limit",
        "runtime": runtime,
        "source": source,
        "observed_at": now.isoformat(),
        "retry_at": retry_at.isoformat(),
        "message": message,
    }
    marker = _marker(runtime)
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(marker)
    return value


def record_from_messages(runtime: str, messages: list[str], source: str,
                         now: datetime | None = None) -> dict | None:
    for message in messages:
        block = record_block(runtime, message, source, now)
        if block:
            return block
    return None


def active_block(runtime: str, now: datetime | None = None) -> dict | None:
    marker = _marker(runtime)
    try:
        value = json.loads(marker.read_text())
        retry_at = datetime.fromisoformat(value["retry_at"])
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return None
    now = now or _now()
    if retry_at.astimezone(timezone.utc) <= now.astimezone(timezone.utc):
        marker.unlink(missing_ok=True)
        return None
    return value


def require_available(runtime: str, now: datetime | None = None) -> None:
    block = active_block(runtime, now)
    if block:
        raise ModelUnavailable(
            f"{runtime} unavailable until {block['retry_at']} ({block['source']})")
