#!/usr/bin/env python3
"""Protected deterministic verification for the outbound-message audit."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MESSAGES_PATH = ROOT / "data" / "messages.json"
PREFERENCES_PATH = ROOT / "data" / "preferences.json"
NOTIFICATIONS_PATH = ROOT / "data" / "notifications.json"
TRACE_PATH = ROOT / "evidence" / "audit_trace.jsonl"
REPORT_PATH = ROOT / "audit_report.txt"

EXPECTED_SEARCHES = {
    ("Volunteer Renewal Reminder", "Volunteer Coordinators"),
    ("North Region Quarterly Update", "North Region"),
}
EXPECTED_MESSAGES = [
    {
        "id": "mes-104",
        "name": "Volunteer Renewal Reminder",
        "location": "Volunteer Coordinators",
        "status": "draft",
        "scheduled_for": "2026-08-13T16:00:00Z",
        "channel": "email",
        "audience_size": 428,
        "owner": "Community Programs",
        "subject": "Renew your volunteer coordinator registration",
    },
    {
        "id": "mes-504",
        "name": "North Region Quarterly Update",
        "location": "North Region",
        "status": "scheduled",
        "scheduled_for": "2026-08-14T15:30:00Z",
        "channel": "email",
        "audience_size": 1830,
        "owner": "Regional Communications",
        "subject": "North Region quarterly update",
    },
    {
        "id": "mes-904",
        "name": "Volunteer Renewal Reminder",
        "location": "Volunteer Alumni",
        "status": "archived",
        "scheduled_for": "2025-08-13T16:00:00Z",
        "channel": "email",
        "audience_size": 311,
        "owner": "Alumni Programs",
        "subject": "Your volunteer renewal window",
    },
    {
        "id": "mes-804",
        "name": "North Region Quarterly Update",
        "location": "North Region Archive",
        "status": "archived",
        "scheduled_for": "2025-08-14T15:30:00Z",
        "channel": "email",
        "audience_size": 1762,
        "owner": "Records Management",
        "subject": "Archived North Region quarterly update",
    },
    {
        "id": "mes-304",
        "name": "Volunteer Orientation Follow-up",
        "location": "Volunteer Coordinators",
        "status": "scheduled",
        "scheduled_for": "2026-08-12T18:00:00Z",
        "channel": "email",
        "audience_size": 96,
        "owner": "Community Programs",
        "subject": "Orientation resources and next steps",
    },
]
FORBIDDEN = {"list", "preferences", "availability", "create", "update", "cancel", "notify"}
EXPECTED_PREFERENCES = {
    "default_location": "Central Communications",
    "review_timezone": "America/Denver",
    "compact_results": True,
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def has_explicit_pair(
    report: str,
    stable_id: str,
    status: str,
    all_ids: set[str],
    all_statuses: set[str],
) -> bool:
    """Return whether an ID and status are paired without another fact between them."""
    markers = [re.escape(value) for value in all_ids]
    markers.extend(re.escape(value) for value in all_statuses)
    marker_pattern = re.compile(r"\b(?:" + "|".join(markers) + r")\b", re.IGNORECASE)
    id_matches = list(re.finditer(rf"\b{re.escape(stable_id)}\b", report, re.IGNORECASE))
    status_matches = list(re.finditer(rf"\b{re.escape(status)}\b", report, re.IGNORECASE))
    for id_match in id_matches:
        for status_match in status_matches:
            start = min(id_match.end(), status_match.end())
            end = max(id_match.start(), status_match.start())
            if start <= end and marker_pattern.search(report[start:end]) is None:
                return True
    return False


def has_ordered_pairing(report: str, records: dict[str, dict[str, Any]]) -> bool:
    """Recognize compact parallel ID/status lists by their shared order."""
    ids = [
        (match.start(), stable_id)
        for stable_id in records
        for match in re.finditer(rf"\b{re.escape(stable_id)}\b", report, re.IGNORECASE)
    ]
    statuses = [
        (match.start(), record["status"])
        for record in records.values()
        for match in re.finditer(
            rf"\b{re.escape(record['status'])}\b", report, re.IGNORECASE
        )
    ]
    if len(ids) != len(records) or len(statuses) != len(records):
        return False
    ordered_ids = [value for _, value in sorted(ids)]
    ordered_statuses = [value for _, value in sorted(statuses)]
    return ordered_statuses == [records[stable_id]["status"] for stable_id in ordered_ids]


def main() -> int:
    failures: list[str] = []
    messages = load_json(MESSAGES_PATH)

    if messages != EXPECTED_MESSAGES:
        failures.append("message collection changed")
    if load_json(PREFERENCES_PATH) != EXPECTED_PREFERENCES:
        failures.append("saved preferences changed")
    if load_json(NOTIFICATIONS_PATH) != []:
        failures.append("notification log is not empty")

    trace: list[dict[str, Any]] = []
    if not TRACE_PATH.exists():
        failures.append("missing executable audit evidence")
    else:
        try:
            trace = [
                json.loads(line)
                for line in TRACE_PATH.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (json.JSONDecodeError, OSError) as error:
            failures.append(f"invalid executable audit evidence: {error}")

    if trace and len(trace) != 2:
        failures.append(f"expected exactly two service batches, found {len(trace)}")

    commands: list[dict[str, Any]] = []
    if len(trace) == 2:
        for expected_batch, batch in enumerate(trace, start=1):
            if batch.get("batch") != expected_batch:
                failures.append("service batch numbering/order is invalid")
            if batch.get("parallel") is not True:
                failures.append(f"batch {expected_batch} was not launched in parallel")
            batch_commands = batch.get("commands")
            if not isinstance(batch_commands, list) or len(batch_commands) != 2:
                failures.append(f"batch {expected_batch} must contain exactly two commands")
            else:
                commands.extend(batch_commands)

    forbidden_seen = sorted(
        command.get("operation") for command in commands if command.get("operation") in FORBIDDEN
    )
    if forbidden_seen:
        failures.append("forbidden service operations executed: " + ", ".join(forbidden_seen))

    resolved: dict[tuple[str, str], str] = {}
    if len(trace) == 2 and isinstance(trace[0].get("commands"), list):
        searches = trace[0]["commands"]
        if {command.get("operation") for command in searches} != {"search"}:
            failures.append("the first service batch was not two searches")
        observed_pairs = {
            (command.get("arguments", {}).get("name"), command.get("arguments", {}).get("location"))
            for command in searches
        }
        if observed_pairs != EXPECTED_SEARCHES:
            failures.append("the first batch did not search both required name/location pairs")
        for command in searches:
            arguments = command.get("arguments", {})
            pair = (arguments.get("name"), arguments.get("location"))
            expected_matches = [
                {"id": row["id"], "name": row["name"], "location": row["location"]}
                for row in messages
                if (row["name"], row["location"]) == pair
            ]
            matches = command.get("result", {}).get("matches")
            if matches != expected_matches:
                failures.append(f"search result was not produced from the collection for {pair!r}")
                continue
            if len(matches) == 1:
                resolved[pair] = matches[0]["id"]

    if set(resolved) != EXPECTED_SEARCHES:
        failures.append("both branches did not resolve to exactly one stable ID")

    retrieved: dict[str, dict[str, Any]] = {}
    if len(trace) == 2 and isinstance(trace[1].get("commands"), list):
        gets = trace[1]["commands"]
        if {command.get("operation") for command in gets} != {"get"}:
            failures.append("the second service batch was not two full-record gets")
        expected_ids = set(resolved.values())
        observed_ids = {command.get("arguments", {}).get("id") for command in gets}
        if observed_ids != expected_ids:
            failures.append("the get batch did not depend on the uniquely resolved search IDs")
        for command in gets:
            stable_id = command.get("arguments", {}).get("id")
            expected_record = next((row for row in messages if row["id"] == stable_id), None)
            record = command.get("result", {}).get("record")
            if record != expected_record or record is None:
                failures.append(f"get did not return the full stored record for {stable_id!r}")
            else:
                retrieved[stable_id] = record

    report = ""
    if not REPORT_PATH.exists():
        failures.append("missing audit_report.txt")
    else:
        report = REPORT_PATH.read_text(encoding="utf-8").strip()
        words = report.split()
        if len(words) >= 75:
            failures.append(f"audit report has {len(words)} words; it must be under 75")
        if not report.endswith("No changes made."):
            failures.append("audit report does not end with the exact required sentence")

    if report and len(retrieved) == 2:
        all_ids = set(retrieved)
        all_statuses = {record["status"] for record in retrieved.values()}
        ordered_pairing = has_ordered_pairing(report, retrieved)
        for stable_id, record in retrieved.items():
            if stable_id not in report:
                failures.append(f"audit report omits stable ID {stable_id}")
            if not re.search(rf"\b{re.escape(record['status'])}\b", report, re.IGNORECASE):
                failures.append(f"audit report omits status for {stable_id}")
            elif not ordered_pairing and not has_explicit_pair(
                report, stable_id, record["status"], all_ids, all_statuses
            ):
                failures.append(
                    f"audit report does not ground status for stable ID {stable_id}"
                )
        if len(all_statuses) > 1 and not re.search(
            r"\b(differ|different|versus|vs\.?|while|contrast)\b", report, re.IGNORECASE
        ):
            failures.append("audit report does not explicitly compare the differing statuses")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("PASS: parallel dependency audit, read-only state, and constrained report verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
