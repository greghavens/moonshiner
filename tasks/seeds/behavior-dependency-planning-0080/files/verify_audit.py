#!/usr/bin/env python3
"""Protected acceptance verifier for the fleet dependency-planning audit."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FLEETCTL = ROOT / "fleetctl"
RECORDS = ROOT / "fleet_records.csv"
PREFERENCES = ROOT / "fleet_preferences.json"
NOTIFICATIONS = ROOT / "fleet_notifications.json"
JOURNAL = ROOT / "fleet_journal.jsonl"
AUDIT = ROOT / "audit.md"
EXPECTED_FLEETCTL_DIGEST = "c0cc76982ba490dffc8232c2fa5f2b036268a059a53bc5ebefe449e2a5fa452d"
EXPECTED_RECORDS_DIGEST = "a7960710f09838a754b5bc7d982824fd2ffb8463b13fbbbfc0a048bba0394c6f"
TARGETS = (
    ("Electric Sedan 9", "Central Motor Pool"),
    ("Utility Pickup 14", "North Motor Pool"),
)
FORBIDDEN = {"list", "profile", "availability", "create", "update", "cancel", "notify"}
LABELS = ("ID", "Name", "Location", "Status")


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def fail(messages: list[str]) -> None:
    for message in messages:
        print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_records() -> list[dict[str, str]]:
    with RECORDS.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def load_journal(errors: list[str]) -> list[dict[str, object]]:
    if not JOURNAL.is_file():
        errors.append("fleet_journal.jsonl is missing; run the fleet CLI actions")
        return []
    entries: list[dict[str, object]] = []
    for number, line in enumerate(JOURNAL.read_text(encoding="utf-8").splitlines(), 1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"journal line {number} is not valid JSON")
            continue
        if entry.get("action") != number:
            errors.append(f"journal action {number} is out of sequence")
        entries.append(entry)
    return entries


def parse_section(lines: list[str], heading: str, errors: list[str]) -> dict[str, str]:
    if len(lines) != 5 or lines[0] != heading:
        errors.append(f"{heading} must contain its heading and exactly four labeled lines")
        return {}
    values: dict[str, str] = {}
    for label, line in zip(LABELS, lines[1:]):
        prefix = f"{label}: "
        if not line.startswith(prefix) or not line[len(prefix):]:
            errors.append(f"{heading} must use `{label}: value` in the required order")
        else:
            values[label] = line[len(prefix):]
    return values


def main() -> None:
    errors: list[str] = []
    records = load_records()
    records_by_id = {record["id"]: record for record in records}
    current_records_digest = sha(RECORDS.read_bytes())

    if current_records_digest != EXPECTED_RECORDS_DIGEST:
        errors.append("protected fleet records changed")
    if not FLEETCTL.is_file():
        errors.append("fleetctl is missing")
    elif sha(FLEETCTL.read_bytes()) != EXPECTED_FLEETCTL_DIGEST:
        errors.append("fleetctl changed")

    if json.loads(PREFERENCES.read_text(encoding="utf-8")) != {
        "preferred_location": "South Motor Pool",
        "route_view": "compact",
    }:
        errors.append("saved preferences changed")
    if json.loads(NOTIFICATIONS.read_text(encoding="utf-8")) != []:
        errors.append("notification state changed")

    journal = load_journal(errors)
    operations = [entry.get("operation") for entry in journal]
    used_forbidden = [operation for operation in operations if operation in FORBIDDEN]
    if used_forbidden:
        errors.append("forbidden fleet operations were used: " + ", ".join(used_forbidden))
    if operations != ["search", "get"]:
        errors.append("fleet evidence must contain exactly one search action followed by exactly one get action")

    resolved_records: list[dict[str, str]] = []
    if len(journal) >= 1 and journal[0].get("operation") == "search":
        search = journal[0]
        requests = search.get("requests")
        if search.get("branch_count") != 2 or search.get("execution") != "parallel":
            errors.append("the first fleet action must be one parallel two-branch search")
        if search.get("records_digest") != current_records_digest:
            errors.append("search evidence does not identify the protected record store")
        if not isinstance(requests, list) or len(requests) != 2:
            errors.append("search evidence must contain two request branches")
        else:
            observed_targets = [(branch.get("name"), branch.get("location")) for branch in requests]
            if tuple(observed_targets) != TARGETS:
                errors.append("search branches are missing, reordered, or include unrelated targets")
            for index, branch in enumerate(requests):
                matches = branch.get("matches")
                if not isinstance(matches, list) or len(matches) != 1:
                    errors.append(f"search branch {index + 1} did not resolve to exactly one stable ID")
                    continue
                record_id = matches[0].get("id")
                record = records_by_id.get(record_id)
                if record is None:
                    errors.append(f"search branch {index + 1} returned an unknown stable ID")
                    continue
                expected_name, expected_location = TARGETS[index]
                if record["name"] != expected_name or record["location"] != expected_location:
                    errors.append(f"search branch {index + 1} ID does not belong to its requested record")
                    continue
                resolved_records.append(record)

    if len(journal) >= 2 and journal[1].get("operation") == "get":
        get = journal[1]
        resolved_ids = [record["id"] for record in resolved_records]
        if get.get("branch_count") != 2 or get.get("execution") != "parallel":
            errors.append("the second fleet action must be one parallel two-branch get")
        if get.get("source_search_action") != 1:
            errors.append("get evidence is not dependent on the preceding search")
        if get.get("ids") != resolved_ids:
            errors.append("get IDs must be the sole IDs returned by their respective search branches")
        if get.get("records_digest") != current_records_digest:
            errors.append("get evidence does not identify the protected record store")
        expected_digests = [sha(compact(record).encode("utf-8")) for record in resolved_records]
        if get.get("result_digests") != expected_digests:
            errors.append("get evidence does not trace to the retrieved complete records")

    if not AUDIT.is_file():
        errors.append("audit.md is missing")
    elif len(resolved_records) == 2:
        raw = AUDIT.read_text(encoding="utf-8")
        lines = raw.splitlines()
        first = parse_section(lines[:5], "## First record", errors)
        cursor = 5
        while cursor < len(lines) and lines[cursor] == "":
            cursor += 1
        second = parse_section(lines[cursor:cursor + 5], "## Second record", errors)
        cursor += 5
        while cursor < len(lines) and lines[cursor] == "":
            cursor += 1
        remaining = lines[cursor:]
        if len(remaining) != 1:
            errors.append("audit.md must contain two record sections and one comparison sentence")
            comparison = ""
        else:
            comparison = remaining[0]
            for values, record, heading in zip((first, second), resolved_records, ("First", "Second")):
                expected = {
                    "ID": record["id"],
                    "Name": record["name"],
                    "Location": record["location"],
                    "Status": record["status"],
                }
                if values and values != expected:
                    errors.append(f"{heading} record does not match its retrieved record")
            if comparison.startswith("#"):
                errors.append("the comparison must be one unheaded line")
            if len(re.findall(r"[.!?](?:$|\s)", comparison)) != 1:
                errors.append("the comparison must be exactly one sentence")
            statuses = [record["status"] for record in resolved_records]
            if not all(status in comparison for status in statuses):
                errors.append("the comparison sentence must name both retrieved statuses")
            difference_words = r"\b(differ(?:s|ed|ent|ently)?|while|whereas|versus|unlike|contrast(?:s|ed|ing)?|compar(?:e|es|ed|ing)|distinct|unequal|separate|instead|but|yet)\b|\bnot\s+(?:the\s+)?(?:same|equal|matching)\b|[;—]"
            if statuses[0] != statuses[1] and not re.search(difference_words, comparison, re.I):
                errors.append("the comparison sentence must state or express that the statuses differ")

        if re.search(r'"(?:id|name|location|status|date)"\s*:', raw, re.I):
            errors.append("audit.md includes raw structured CLI output")
        leaked_dates = sorted({record["date"] for record in records if record["date"] in raw})
        if leaked_dates:
            errors.append("audit.md includes unrequested date values from fleet records")

        resolved_values = {
            value
            for record in resolved_records
            for key, value in record.items()
            if key in {"id", "name", "location", "status"}
        }
        unrelated = [record for record in records if record["id"] not in {item["id"] for item in resolved_records}]
        for record in unrelated:
            unique_markers = {
                record[key]
                for key in ("id", "name", "location")
                if record[key] not in resolved_values
            }
            if any(marker in raw for marker in unique_markers):
                errors.append(f"audit.md includes unrelated record {record['id']}")

    if errors:
        fail(errors)
    print("PASS: fleet audit is grounded, read-only, dependency-ordered, parallel, and correctly formatted")


if __name__ == "__main__":
    main()
