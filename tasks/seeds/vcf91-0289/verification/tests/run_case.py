"""Run one rotation case in its own interpreter and report the result as JSON.

Usage: python3 tests/run_case.py <case.json>

The case file carries ``base_url``, optional ``timeout``, and the keyword
arguments handed to ``CertificateRotationClient.rotate``.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


class DeterministicClock:
    """Small virtual clock used only by the poll-timeout test case."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def read(self):
        # Advancing on reads ensures even an implementation that forgets to
        # sleep eventually reaches its deadline instead of hanging the verifier.
        self.now += 0.005
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += max(0.0, seconds)

    def snapshot(self):
        return {"now": self.now, "sleeps": self.sleeps}


def emit(payload, clock):
    if clock is not None:
        payload["clock"] = clock.snapshot()
    print(json.dumps(payload))


def main(argv):
    with open(argv[1], "r", encoding="utf-8") as handle:
        case = json.load(handle)

    clock = None
    if case.get("deterministic_clock"):
        clock = DeterministicClock()
        time.monotonic = clock.read
        time.perf_counter = clock.read
        time.time = clock.read
        time.sleep = clock.sleep

    try:
        from vcfonw_certrotate import (
            ApiError,
            CertificateRotationClient,
            PollTimeoutError,
            RotationOutcome,
        )
    except Exception:
        emit({
            "ok": False,
            "error_type": "ImportError",
            "message": traceback.format_exc(),
        }, clock)
        return 0

    client = CertificateRotationClient(case["base_url"], timeout=case.get("timeout", 10.0))

    try:
        outcome = client.rotate(**case["rotate_kwargs"])
    except ApiError as exc:
        emit({
            "ok": False,
            "error_type": "ApiError",
            "message": str(exc),
            "status": getattr(exc, "status", None),
            "body": getattr(exc, "body", None),
        }, clock)
        return 0
    except PollTimeoutError as exc:
        emit({
            "ok": False,
            "error_type": "PollTimeoutError",
            "message": str(exc),
            "update_id": getattr(exc, "update_id", None),
            "last_status": getattr(exc, "last_status", None),
            "poll_count": getattr(exc, "poll_count", 0),
        }, clock)
        return 0
    except Exception as exc:  # noqa: BLE001 - reported verbatim to the verifier
        emit({
            "ok": False,
            "error_type": type(exc).__name__,
            "message": "%s\n%s" % (exc, traceback.format_exc()),
        }, clock)
        return 0

    emit({
        "ok": True,
        "is_rotation_outcome": isinstance(outcome, RotationOutcome),
        "outcome": {
            "update_id": getattr(outcome, "update_id", None),
            "status": getattr(outcome, "status", None),
            "poll_count": getattr(outcome, "poll_count", None),
            "error_message": getattr(outcome, "error_message", None),
            "updated_nodes": getattr(outcome, "updated_nodes", None),
            "failed_nodes": getattr(outcome, "failed_nodes", None),
            "succeeded": getattr(outcome, "succeeded", None),
        },
    }, clock)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
