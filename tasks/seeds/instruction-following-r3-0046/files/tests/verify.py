#!/usr/bin/env python3
"""Protected, deterministic verification for the dispatch handoff."""

from __future__ import annotations

import csv
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ORDERS_PATH = ROOT / "inputs" / "orders.csv"
STAFF_PATH = ROOT / "inputs" / "staff.csv"
SCHEDULE_PATH = ROOT / "deliverables" / "dispatch_schedule.csv"
BRIEF_PATH = ROOT / "deliverables" / "shift_brief.md"
RUN_DATE = "2026-09-17"
HEADERS = [
    "sequence",
    "order_id",
    "priority",
    "ship_by",
    "start",
    "end",
    "timezone",
    "station",
    "owner",
    "units",
    "check",
]
DURATIONS = {"cold": 45, "hazmat": 30, "standard": 60}
CHECKS = {"cold": "CHILL", "hazmat": "HAZMAT-SEAL", "standard": "COUNT"}
REQUIRED_HEADINGS = ["Shift Summary", "Staffing", "Exceptions", "Escalation"]


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    require(path.is_file(), f"missing required deliverable: {path.relative_to(ROOT)}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def at_time(value: str) -> datetime:
    try:
        return datetime.strptime(f"{RUN_DATE} {value}", "%Y-%m-%d %H:%M")
    except ValueError as error:
        raise VerificationError(f"invalid 24-hour time {value!r}") from error


def expected_assignments(
    orders: list[dict[str, str]], staff: list[dict[str, str]]
) -> list[dict[str, str]]:
    availability = {person["name"]: at_time(person["shift_start"]) for person in staff}
    expected: list[dict[str, str]] = []
    active = sorted(
        (order for order in orders if order["status"] == "ACTIVE"),
        key=lambda order: (int(order["priority"]), at_time(order["ship_by"]), order["order_id"]),
    )
    for sequence, order in enumerate(active, 1):
        qualified = [
            (index, person)
            for index, person in enumerate(staff)
            if order["required_cert"] in person["certifications"].split("|")
        ]
        require(qualified, f"fixture has no qualified staff for {order['order_id']}")
        _, owner = min(
            qualified,
            key=lambda item: (availability[item[1]["name"]], item[0]),
        )
        start = availability[owner["name"]]
        end = start + timedelta(minutes=DURATIONS[order["required_cert"]])
        require(end <= at_time(owner["shift_end"]), "fixture assignment exceeds a shift")
        require(end <= at_time(order["ship_by"]), "fixture assignment misses ship-by")
        availability[owner["name"]] = end
        expected.append(
            {
                "sequence": str(sequence),
                "order_id": order["order_id"],
                "priority": order["priority"],
                "ship_by": order["ship_by"],
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
                "timezone": "America/Chicago",
                "station": owner["station"],
                "owner": owner["name"],
                "units": order["units"],
                "check": CHECKS[order["required_cert"]],
            }
        )
    return expected


def verify_schedule(
    orders: list[dict[str, str]], staff: list[dict[str, str]]
) -> list[dict[str, str]]:
    headers, actual = read_csv(SCHEDULE_PATH)
    require(headers == HEADERS, f"schedule columns must be exactly {','.join(HEADERS)}")
    expected = expected_assignments(orders, staff)
    require(len(actual) == len(expected), "final correction requires one row per active order")
    require(actual == expected, "schedule values, ordering, or assignments do not follow the retained rules")
    require(len({row["order_id"] for row in actual}) == len(actual), "an active order was split or duplicated")
    return actual


def verify_no_private_data(orders: list[dict[str, str]], output_text: str) -> None:
    lowered = output_text.casefold()
    require("customer_email" not in lowered, "a private input column name leaked")
    require("internal_note" not in lowered, "a private input column name leaked")
    for order in orders:
        for field in ("customer_email", "internal_note"):
            value = order[field].strip()
            require(not value or value.casefold() not in lowered, f"private {field} value leaked")


def brief_sections(brief: str) -> dict[str, str]:
    """Return the four required sections and reject every other Markdown heading."""
    headings: list[tuple[str, int, int]] = []
    heading_pattern = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?)[ \t]*#*[ \t]*|[ \t]*)$", re.MULTILINE)
    for match in heading_pattern.finditer(brief):
        level, title = len(match.group(1)), (match.group(2) or "").strip()
        require(level == 1, "brief contains an additional non-level-one heading")
        headings.append((title, match.start(), match.end()))
    require(
        [title for title, _, _ in headings] == REQUIRED_HEADINGS,
        "brief headings are missing, reordered, or supplemented",
    )
    sections: dict[str, str] = {}
    for index, (title, _, body_start) in enumerate(headings):
        body_end = headings[index + 1][1] if index + 1 < len(headings) else len(brief)
        sections[title] = brief[body_start:body_end]
    return sections


def require_reported_number(text: str, number: int, subject_pattern: str, message: str) -> None:
    """Accept either subject-before-number or natural number-before-subject wording."""
    patterns = (
        rf"{subject_pattern}[^\n\d]{{0,30}}\b{number}\b",
        rf"\b{number}\b[^\n]{{0,30}}{subject_pattern}",
    )
    require(any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns), message)


def staffing_entries(
    staffing: str, staff: list[dict[str, str]]
) -> dict[str, str]:
    """Associate each person's staffing text with that name, independent of roster order."""
    located: list[tuple[int, str]] = []
    for person in staff:
        matches = list(re.finditer(re.escape(person["name"]), staffing, re.IGNORECASE))
        require(matches, f"brief lacks staffing details for {person['name']}")
        located.append((matches[0].start(), person["name"]))
    located.sort()
    entries: dict[str, str] = {}
    for index, (start, name) in enumerate(located):
        end = located[index + 1][0] if index + 1 < len(located) else len(staffing)
        entries[name] = staffing[start:end]
    return entries


def verify_brief(
    orders: list[dict[str, str]], staff: list[dict[str, str]], schedule: list[dict[str, str]]
) -> str:
    require(BRIEF_PATH.is_file(), "missing required deliverable: deliverables/shift_brief.md")
    brief = BRIEF_PATH.read_text(encoding="utf-8")
    sections = brief_sections(brief)
    active = [order for order in orders if order["status"] == "ACTIVE"]
    active_units = sum(int(order["units"]) for order in active)
    summary = sections["Shift Summary"]
    require_reported_number(
        summary,
        len(active),
        r"active[- ]orders?",
        "brief lacks the active-order count",
    )
    require_reported_number(
        summary,
        active_units,
        r"(?:active[- ]order\s+)?units?(?:\s+total)?",
        "brief lacks the active-unit total",
    )
    first_start = min(row["start"] for row in schedule)
    last_end = max(row["end"] for row in schedule)
    require(first_start in summary and last_end in summary and "America/Chicago" in summary,
            "brief lacks the overall schedule window and timezone")
    entries = staffing_entries(sections["Staffing"], staff)
    active_ids = {order["order_id"] for order in active}
    for person in staff:
        assigned = [row["order_id"] for row in schedule if row["owner"] == person["name"]]
        entry = entries[person["name"]]
        require(person["station"] in entry, f"brief lacks the station for {person['name']}")
        mentioned = {order_id for order_id in active_ids if order_id in entry}
        if assigned:
            require(mentioned == set(assigned), f"brief has incorrect assignments for {person['name']}")
        else:
            require(not mentioned and re.search(r"\bnone\b", entry, re.IGNORECASE) is not None,
                    f"brief must show none for {person['name']}")
    held = [order["order_id"] for order in orders if order["status"] == "HOLD"]
    exceptions = sections["Exceptions"]
    require(all(order_id in exceptions for order_id in held), "brief lacks the held-order exception")
    require(re.search(r"\b(?:hold|held)\b", exceptions, re.IGNORECASE) is not None
            and re.search(r"\bexcluded\b", exceptions, re.IGNORECASE) is not None,
            "held order must be identified as excluded")
    escalation = sections["Escalation"]
    owner_patterns = (
        r"(?i:\bowner\b)[^\n]{0,60}Jordan Lee",
        r"Jordan Lee[^\n]{0,60}(?i:\bowner\b)",
    )
    require(any(re.search(pattern, escalation) for pattern in owner_patterns),
            "brief lacks Jordan Lee as the clarified escalation owner")
    no_split_pattern = (
        r"(?:one(?:\s+schedule)?\s+row\s+per\s+active\s+order|not\s+split|no\s+lot|"
        r"unsplit|without\s+lotting|(?:kept|remain(?:ed)?)\s+(?:whole|intact))"
    )
    require(re.search(no_split_pattern, brief, re.IGNORECASE) is not None,
            "brief does not reflect the no-splitting correction")
    return brief


def main() -> int:
    try:
        _, orders = read_csv(ORDERS_PATH)
        _, staff = read_csv(STAFF_PATH)
        schedule = verify_schedule(orders, staff)
        brief = verify_brief(orders, staff, schedule)
        verify_no_private_data(
            orders,
            SCHEDULE_PATH.read_text(encoding="utf-8") + "\n" + brief,
        )
    except VerificationError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: dispatch schedule and shift brief satisfy the corrected request")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
