#!/usr/bin/env python3
"""Small stateful calendar service exposed over a local subprocess protocol."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sys
from typing import Iterator


RUNTIME = Path(__file__).resolve().parent
STATE_PATH = RUNTIME / "state.json"
LOCK_PATH = RUNTIME / "state.lock"


@contextmanager
def locked_state() -> Iterator[dict[str, object]]:
    LOCK_PATH.touch(exist_ok=True)
    with LOCK_PATH.open("r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        with STATE_PATH.open(encoding="utf-8") as state_file:
            state = json.load(state_file)
        try:
            yield state
        finally:
            temporary = STATE_PATH.with_suffix(".json.tmp")
            with temporary.open("w", encoding="utf-8") as state_file:
                json.dump(state, state_file, indent=2, sort_keys=True)
                state_file.write("\n")
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary, STATE_PATH)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def public_meeting(meeting: dict[str, object]) -> dict[str, object]:
    return {
        "id": meeting["id"],
        "title": meeting["title"],
        "status": meeting["status"],
        "cancellation_reason": meeting["cancellation_reason"],
    }


def handle_request(request: dict[str, object]) -> tuple[dict[str, object], bool]:
    operation = request.get("operation")
    meeting_id = request.get("meeting_id")
    if not isinstance(meeting_id, str):
        return {"ok": False, "error": "meeting_id must be a string"}, False

    lose_response = False
    with locked_state() as state:
        meetings = state["meetings"]
        assert isinstance(meetings, dict)
        meeting = meetings.get(meeting_id)
        if not isinstance(meeting, dict):
            return {"ok": False, "error": f"meeting not found: {meeting_id}"}, False

        audit = state["audit"]
        assert isinstance(audit, list)
        sequence = len(audit) + 1

        if operation == "get":
            audit.append(
                {
                    "sequence": sequence,
                    "operation": "get",
                    "meeting_id": meeting_id,
                    "observed_status": meeting["status"],
                }
            )
        elif operation == "cancel":
            reason = request.get("reason")
            if not isinstance(reason, str) or not reason:
                return {"ok": False, "error": "reason must be a non-empty string"}, False
            previous_status = meeting["status"]
            if previous_status == "active":
                meeting["status"] = "cancelled"
                meeting["cancellation_reason"] = reason
            audit.append(
                {
                    "sequence": sequence,
                    "operation": "cancel",
                    "meeting_id": meeting_id,
                    "reason": reason,
                    "status_before": previous_status,
                    "status_after": meeting["status"],
                }
            )
            faults = state["faults"]
            assert isinstance(faults, dict)
            if not faults["cancel_response_loss_consumed"]:
                faults["cancel_response_loss_consumed"] = True
                lose_response = True
        else:
            return {"ok": False, "error": f"unsupported operation: {operation}"}, False

        response = {"ok": True, "meeting": public_meeting(meeting)}
    return response, lose_response


def main() -> None:
    try:
        raw = sys.stdin.buffer.readline(64 * 1024)
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        response, lose_response = handle_request(request)
        if lose_response:
            return
    except (json.JSONDecodeError, ValueError) as exc:
        response = {"ok": False, "error": str(exc)}

    try:
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        pass


if __name__ == "__main__":
    main()
