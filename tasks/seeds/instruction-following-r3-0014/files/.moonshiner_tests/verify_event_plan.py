#!/usr/bin/env python3
"""Deterministic acceptance checks for the final event coordination artifact."""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path


PLAN = Path("event_plan.md")
EXPECTED_HEADINGS = [
    "Event Snapshot",
    "Run of Show",
    "Room & Materials",
    "Communications",
    "Owners & Deadlines",
    "Open Questions",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def section(text: str, heading: str) -> str:
    match = re.search(
        rf"(?ms)^## {re.escape(heading)}\s*$\n(.*?)(?=^## |\Z)", text
    )
    if not match:
        fail(f"missing section {heading!r}")
    return match.group(1).strip()


if not PLAN.is_file():
    fail("event_plan.md was not created")

text = PLAN.read_text(encoding="utf-8")

h1 = re.findall(r"(?m)^# (?!#)(.+?)\s*$", text)
if h1 != ["Northstar Pilot Forum Event Plan"]:
    fail("the plan must have exactly the corrected H1 title")

headings = re.findall(r"(?m)^## (.+?)\s*$", text)
if headings != EXPECTED_HEADINGS:
    fail(f"H2 headings must be exactly {EXPECTED_HEADINGS!r} in order")
if re.search(r"(?m)^#{3,}\s+", text):
    fail("the plan contains headings beyond the requested H1 and H2 headings")

if any(line.count("|") >= 2 for line in text.splitlines()):
    fail("tables are not allowed")
if re.search(
    r"(?i)\b(?:budgets?|costs?|dollars?|prices?|pricing|USD|EUR)\b|"
    r"\b(?:catering|lunch|meal)\s+menus?\b|\bmenus?\s*:|[$\u00a3\u20ac]",
    text,
):
    fail("budgets, prices, and catering menus must be omitted")

stale_patterns = [
    (r"(?i)\bPartner Forum\b", "Partner Forum"),
    (r"(?i)\bWillow Room\b", "Willow Room"),
    (r"\b72\b", "72"),
    (r"(?i)\b(?:all\s+)?six\s+partner\s+teams?\b", "six partner teams"),
]
for pattern, description in stale_patterns:
    if re.search(pattern, text):
        fail(f"superseded detail remains: {description!r}")

snapshot = section(text, "Event Snapshot")
required_snapshot_lines = [
    "- Event: Northstar Pilot Forum",
    "- Purpose: Align the two pilot teams on the Q1 service launch.",
    "- Date: Thursday, October 22, 2026",
    "- Time: 09:00–16:00 CT",
    "- Venue: Cedar Studio",
    "- Audience: Two pilot teams; 28 attendees",
]
snapshot_lines = [line.strip() for line in snapshot.splitlines() if line.strip()]
if snapshot_lines[: len(required_snapshot_lines)] != required_snapshot_lines:
    fail("Event Snapshot must begin with the six corrected labeled bullets")

run_of_show = section(text, "Run of Show")
schedule_pattern = re.compile(
    r"^- (\d{2}):(\d{2})–(\d{2}):(\d{2}) CT — "
    r"(.+?) \(owner: ([^)\n]+)\)$"
)
schedule_lines = [line.strip() for line in run_of_show.splitlines() if line.strip()]
if not schedule_lines:
    fail("Run of Show must contain timed entries")

entries: list[tuple[int, int, str, str]] = []
for line in schedule_lines:
    match = schedule_pattern.fullmatch(line)
    if not match:
        fail(f"Run of Show line has the wrong format: {line!r}")
    start_h, start_m, end_h, end_m = map(int, match.group(1, 2, 3, 4))
    if start_h > 23 or end_h > 23 or start_m > 59 or end_m > 59:
        fail(f"invalid time in Run of Show: {line!r}")
    start = start_h * 60 + start_m
    end = end_h * 60 + end_m
    if start >= end:
        fail(f"non-positive schedule interval: {line!r}")
    activity, owner = match.group(5), match.group(6)
    if not re.search(r"[A-Za-z]", owner):
        fail(f"Run of Show item lacks a named owner: {line!r}")
    entries.append((start, end, activity, owner))

if entries[0][0] != 9 * 60 or entries[-1][1] != 16 * 60:
    fail("Run of Show must cover the full 09:00–16:00 period")
if any(current[0] != previous[1] for previous, current in zip(entries, entries[1:])):
    fail("Run of Show must be chronological, contiguous, and non-overlapping")
activities = [item[2].casefold() for item in entries]
for required, pattern in (
    ("arrival/check-in", r"\bcheck[- ]in\b"),
    ("opening", r"\bopening\b"),
    ("lunch", r"\blunch\b"),
    ("closing", r"\bclosing\b"),
):
    if not any(re.search(pattern, activity) for activity in activities):
        fail(f"Run of Show is missing {required!r}")
if sum(bool(re.search(r"\bbreakout\b", activity)) for activity in activities) < 2:
    fail("Run of Show must contain two distinct breakout blocks")

room = section(text, "Room & Materials")
room_lines = [line.strip() for line in room.splitlines() if line.strip()]
labeled_room_lines: dict[str, str] = {}
for label in ("- Welcome/check-in zone:", "- Plenary zone:", "- Breakout zones:"):
    matches = [line for line in room_lines if line.startswith(label)]
    populated_matches = [line for line in matches if line[len(label) :].strip()]
    if not populated_matches:
        fail(f"Room & Materials is missing labeled bullet {label!r}")
    labeled_room_lines[label] = "\n".join(populated_matches)
for material in ("name badge", "sign-in"):
    if material not in room.casefold():
        fail(f"room setup is missing {material!r}")
working_area_setup = "\n".join(
    line for line in room_lines if not line.startswith("- Welcome/check-in zone:")
)
if not re.search(r"\b28\b", room):
    fail("room setup must be specific to 28 attendees")
for material in ("marker", "sticky note", "timer"):
    if material not in working_area_setup.casefold():
        fail(f"room setup is missing working material {material!r}")

communications = section(text, "Communications")
communication_lines = [
    line.strip() for line in communications.splitlines() if line.strip()
]
if not communication_lines or communication_lines[0] != (
    "Subject: Northstar Pilot Forum — October 22 details"
):
    fail("send-ready attendee email must begin with the exact corrected subject")
if len(communication_lines) < 2 or not re.search(
    r"(?i)^(?:hello|dear|hi|greetings|good (?:morning|afternoon))\b.*"
    r"\bNorthstar participants\b",
    communication_lines[1],
):
    fail("send-ready attendee email must greet Northstar participants")
email_requirements = [
    "Thursday, October 22, 2026",
    "09:00–16:00 CT",
    "Cedar Studio",
    "check-in",
    "breakout",
    "lunch",
    "accommodation",
    "dietary",
]
for value in email_requirements:
    if value.casefold() not in communications.casefold():
        fail(f"send-ready attendee email is missing {value!r}")
if re.search(r"(?i)\b(?:talking points|email outline|draft notes)\b", communications):
    fail("Communications must contain the email itself, not notes about it")
if "Luis Romero" not in communication_lines[-3:]:
    fail("send-ready attendee email must be signed by Luis Romero")

owners = section(text, "Owners & Deadlines")
owner_lines = [line.strip() for line in owners.splitlines() if line.strip()]
if len(owner_lines) != 4 or any(not line.startswith("- ") for line in owner_lines):
    fail("Owners & Deadlines must contain exactly four bullet assignments")
owner_requirements = [
    ("Maya Chen", "attendee roster", "2026-10-12"),
    ("Theo Brooks", "room layout and materials", "2026-10-15"),
    ("Priya Shah", "facilitator briefs", "both breakouts", "2026-10-16"),
    ("Luis Romero", "attendee details email", "2026-10-19"),
]
for required_values in owner_requirements:
    matching_lines = [
        line
        for line in owner_lines
        if all(value.casefold() in line.casefold() for value in required_values)
    ]
    if len(matching_lines) != 1:
        fail(f"missing or incomplete owner assignment for {required_values[0]!r}")
for line in owner_lines:
    found = re.search(r"\b(2026-\d{2}-\d{2})\b", line)
    if not found:
        fail(f"owner assignment lacks an ISO deadline: {line!r}")
    if date.fromisoformat(found.group(1)) >= date(2026, 10, 22):
        fail(f"owner deadline is not before the event: {line!r}")

questions = section(text, "Open Questions")
question_lines = [line.strip() for line in questions.splitlines() if line.strip()]
expected_questions = [
    "- What accessibility accommodations are required?",
    "- Which dietary restrictions must be accommodated at lunch?",
    "- How should the two breakout topics be assigned?",
]
if question_lines != expected_questions:
    fail("Open Questions must preserve exactly the three unresolved questions")

print("PASS: corrected event plan is complete and retains every unaffected constraint")
