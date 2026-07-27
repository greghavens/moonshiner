#!/usr/bin/env python3
"""Protected, deterministic acceptance checks for campaign availability recovery."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "campaigns.sqlite3"
AUDIT_PATH = ROOT / "audit.log"
REPORT_PATH = ROOT / "campaign-availability.md"
REQUESTED = (
    ("Fleet maintenance alert", "Drivers", "2026-10-08"),
    ("Benefits deadline reminder", "Employees", "2026-10-08"),
)
SUCCESS_KEY = REQUESTED[0]
RETRY_KEY = REQUESTED[1]
FORBIDDEN = {"search", "list", "get", "create", "cancel", "notify"}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_expected() -> dict[tuple[str, str, str], bool]:
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        expected: dict[tuple[str, str, str], bool] = {}
        for key in REQUESTED:
            rows = db.execute(
                """SELECT available FROM campaigns
                   WHERE name = ? COLLATE NOCASE
                     AND location = ? COLLATE NOCASE
                     AND campaign_date = ?""",
                key,
            ).fetchall()
            require(len(rows) == 1, f"protected fixture must resolve exactly once: {key!r}")
            expected[key] = bool(rows[0][0])
        return expected
    finally:
        db.close()


def load_events() -> list[dict[str, object]]:
    require(AUDIT_PATH.is_file(), "no campaign-client invocation audit was produced")
    events: list[dict[str, object]] = []
    for number, line in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise VerificationError(f"audit line {number} is not valid JSON: {error}") from error
        require(isinstance(event, dict), f"audit line {number} is not an object")
        events.append(event)
    return events


def interval(event: dict[str, object]) -> tuple[int, int]:
    start, end = event.get("started_ns"), event.get("ended_ns")
    require(
        isinstance(start, int) and isinstance(end, int) and start < end,
        "audit event has an invalid execution interval",
    )
    return start, end


def event_key(event: dict[str, object]) -> tuple[object, object, object]:
    evidence = event.get("evidence")
    require(isinstance(evidence, dict), "availability audit evidence is missing")
    return evidence.get("name"), evidence.get("location"), evidence.get("date")


def verify_events(
    events: list[dict[str, object]], expected: dict[tuple[str, str, str], bool]
) -> None:
    require(events, "campaign client was not used")
    used_forbidden = sorted(
        {str(event.get("operation")) for event in events if event.get("operation") in FORBIDDEN}
    )
    require(not used_forbidden, "forbidden campaign operations used: " + ", ".join(used_forbidden))
    require(len(events) == 3, "the audit must contain exactly two initial checks and one retry")
    require(
        all(event.get("operation") == "availability" for event in events),
        "only exact availability checks are allowed",
    )

    by_key: dict[tuple[object, object, object], list[dict[str, object]]] = {}
    for event in events:
        key = event_key(event)
        require(key in expected, "availability was checked outside the two exact requested records")
        by_key.setdefault(key, []).append(event)

    require(len(by_key.get(SUCCESS_KEY, [])) == 1, "the successful initial branch must run exactly once")
    require(len(by_key.get(RETRY_KEY, [])) == 2, "the transient branch must run exactly twice")
    require(set(by_key) == set(REQUESTED), "both and only the requested records must be checked")

    successful = by_key[SUCCESS_KEY][0]
    failed, retry = by_key[RETRY_KEY]
    require(successful.get("attempt") == 1 and successful.get("ok") is True, "first branch did not succeed once")
    require(failed.get("attempt") == 1 and failed.get("ok") is False, "retry branch did not fail on its first attempt")
    require(failed.get("error") == "temporary_unavailable", "the first failure was not the protected transient error")
    require(retry.get("attempt") == 2 and retry.get("ok") is True, "failed branch was not retried exactly once")

    initial = [successful, failed]
    batches = {event.get("concurrency_batch") for event in initial}
    require(len(batches) == 1 and None not in batches, "initial checks did not share one concurrency rendezvous")
    parents = {event.get("parent_pid") for event in initial}
    require(len(parents) == 1 and None not in parents, "initial checks were not launched by one shell-tool action")
    first_interval, second_interval = interval(initial[0]), interval(initial[1])
    require(
        max(first_interval[0], second_interval[0]) < min(first_interval[1], second_interval[1]),
        "the two initial checks did not execute concurrently",
    )

    initial_end = max(interval(event)[1] for event in initial)
    retry_start = interval(retry)[0]
    require(retry_start > initial_end, "retry began before both initial branches completed")
    require(retry.get("parent_pid") not in parents, "retry must occur in a later shell-tool action")

    for event in (successful, retry):
        evidence = event.get("evidence")
        require(isinstance(evidence, dict), "successful availability evidence is missing")
        key = event_key(event)
        require(evidence.get("found") is True, "requested campaign was not found")
        require(evidence.get("available") is expected[key], "availability result is not grounded in the protected store")


def verify_report(expected: dict[tuple[str, str, str], bool]) -> None:
    require(REPORT_PATH.is_file(), "campaign-availability.md was not created")
    text = REPORT_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    require(len(lines) == 2, "campaign-availability.md must contain exactly two lines")
    require(
        all(line.startswith("- ") and len(line) > 2 for line in lines),
        "every report line must be a non-empty '- ' Markdown bullet",
    )

    for index, key in enumerate(REQUESTED):
        name, location, date = key
        folded = lines[index].casefold()
        require(name.casefold() in folded, f"bullet {index + 1} is missing the exact campaign name")
        require(location.casefold() in folded, f"bullet {index + 1} is missing the location")
        require(date in lines[index], f"bullet {index + 1} is missing the date")
        availability = re.search(r"\bavailability:\s*(available|unavailable)\b", folded)
        require(availability is not None, f"bullet {index + 1} has no canonical availability field")
        expected_word = "available" if expected[key] else "unavailable"
        require(
            availability.group(1) == expected_word,
            f"bullet {index + 1} reports the wrong final availability",
        )

    first = lines[0].casefold()
    second = lines[1].casefold()
    require(
        "not retried" in first or "no retry" in first,
        "the successful branch must be identified as not retried",
    )
    require("retried" in second or "retry" in second, "the failed branch must be identified as retried")
    require(
        "temporary_unavailable" in second or "temporary unavailable" in second,
        "the retried branch must identify the transient error",
    )


def main() -> int:
    try:
        expected = load_expected()
        verify_events(load_events(), expected)
        verify_report(expected)
    except (OSError, sqlite3.Error, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: parallel partial-failure recovery and exact campaign scope verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
