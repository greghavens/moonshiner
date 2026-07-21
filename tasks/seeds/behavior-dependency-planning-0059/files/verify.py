#!/usr/bin/env python3
"""Protected deterministic verifier for the permit-queue audit task."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA_DB = ROOT / "data" / "permit_queue.sqlite3"
AUDIT_DB = ROOT / ".permit_runtime" / "execution.sqlite3"
REPORT = ROOT / "audit_report.txt"

TARGETS = (
    ("Youth Program Grant Application", "Arvada"),
    ("Tree Removal Permit Application", "Golden"),
)
ALLOWED_OPERATIONS = {"search", "get"}


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_expected_records() -> tuple[list[dict[str, str]], dict[tuple[str, str], str]]:
    connection = sqlite3.connect(f"file:{DATA_DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    records: list[dict[str, str]] = []
    ids_by_target: dict[tuple[str, str], str] = {}
    with connection:
        for name, location in TARGETS:
            rows = connection.execute(
                """
                SELECT stable_id, name, location, submitted_date, status
                  FROM applications
                 WHERE name = ? AND location = ? AND cancelled = 0
                 ORDER BY stable_id
                """,
                (name, location),
            ).fetchall()
            require(len(rows) == 1, "protected fixture target must resolve uniquely")
            record = dict(rows[0])
            records.append(record)
            ids_by_target[(name, location)] = record["stable_id"]
    records.sort(key=lambda item: (item["submitted_date"], item["stable_id"]))
    return records, ids_by_target


def load_invocations() -> list[dict[str, object]]:
    require(AUDIT_DB.is_file(), "no Permit Desk execution audit found")
    connection = sqlite3.connect(f"file:{AUDIT_DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT invocation_id, pid, operation, request_json,
                   started_ns, finished_ns, exit_code, result_json
              FROM invocations
             ORDER BY started_ns, invocation_id
            """
        ).fetchall()
    except sqlite3.Error as error:
        raise VerificationError(f"invalid execution audit: {error}") from error
    invocations: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        try:
            item["request"] = json.loads(item.pop("request_json"))
            item["result"] = json.loads(item.pop("result_json"))
        except (TypeError, json.JSONDecodeError) as error:
            raise VerificationError("execution audit contains an incomplete invocation") from error
        invocations.append(item)
    return invocations


def intervals_overlap(first: dict[str, object], second: dict[str, object]) -> bool:
    return max(int(first["started_ns"]), int(second["started_ns"])) < min(
        int(first["finished_ns"]), int(second["finished_ns"])
    )


def verify_execution(ids_by_target: dict[tuple[str, str], str]) -> None:
    invocations = load_invocations()
    require(len(invocations) == 4, "expected exactly four Permit Desk data operations")
    require(
        all(item["operation"] in ALLOWED_OPERATIONS for item in invocations),
        "a forbidden Permit Desk operation was executed",
    )
    require(
        [item["operation"] for item in invocations] == ["search", "search", "get", "get"],
        "operations must be two searches followed by two dependent gets",
    )
    require(
        all(item["finished_ns"] is not None and item["exit_code"] == 0 for item in invocations),
        "every required Permit Desk operation must finish successfully",
    )

    searches = invocations[:2]
    gets = invocations[2:]
    require(intervals_overlap(searches[0], searches[1]), "the two searches were not parallel")
    require(intervals_overlap(gets[0], gets[1]), "the two gets were not parallel")
    require(
        min(int(item["started_ns"]) for item in gets)
        > max(int(item["finished_ns"]) for item in searches),
        "a get began before both searches had returned",
    )

    observed_targets: dict[tuple[str, str], str] = {}
    for item in searches:
        request = item["request"]
        require(isinstance(request, dict), "invalid search request audit")
        target = (request.get("name"), request.get("location"))
        require(target in ids_by_target, "a search did not use one requested name/location pair")
        result = item["result"]
        require(isinstance(result, dict), "invalid search result audit")
        matches = result.get("matches")
        require(isinstance(matches, list) and len(matches) == 1, "a search was not uniquely resolved")
        stable_id = matches[0].get("stable_id") if isinstance(matches[0], dict) else None
        require(stable_id == ids_by_target[target], "search returned an unexpected stable ID")
        observed_targets[target] = stable_id
    require(set(observed_targets) == set(TARGETS), "both independent searches are required")

    expected_ids = set(observed_targets.values())
    observed_get_ids: set[str] = set()
    for item in gets:
        request = item["request"]
        require(isinstance(request, dict), "invalid get request audit")
        stable_id = request.get("stable_id")
        require(stable_id in expected_ids, "a get was not gated by its search result")
        require(stable_id not in observed_get_ids, "a uniquely resolved branch was retrieved more than once")
        result = item["result"]
        application = result.get("application") if isinstance(result, dict) else None
        require(
            isinstance(application, dict) and application.get("stable_id") == stable_id,
            "a get did not return its uniquely resolved application",
        )
        observed_get_ids.add(stable_id)
    require(observed_get_ids == expected_ids, "both uniquely resolved branches must be retrieved")


def expected_report(records: list[dict[str, str]]) -> str:
    lines = [
        " | ".join(
            (record["submitted_date"], record["name"], record["location"], record["status"])
        )
        for record in records
    ]
    first, second = records
    if first["status"] == second["status"]:
        comparison = f"Status comparison: Both records have status {first['status']}."
    else:
        comparison = (
            "Status comparison: The records have different statuses: "
            f"{first['name']} is {first['status']}, while "
            f"{second['name']} is {second['status']}."
        )
    return "\n".join((*lines, comparison)) + "\n"


def verify_report(records: list[dict[str, str]]) -> None:
    require(REPORT.is_file(), "audit_report.txt is missing")
    actual = REPORT.read_text(encoding="utf-8")
    require(actual.removesuffix("\n") == expected_report(records).removesuffix("\n"), "audit_report.txt is incorrect")


def main() -> int:
    try:
        records, ids_by_target = load_expected_records()
        verify_execution(ids_by_target)
        verify_report(records)
    except (OSError, sqlite3.Error, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: permit audit execution and report verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
