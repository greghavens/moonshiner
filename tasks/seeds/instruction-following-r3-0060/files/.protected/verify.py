#!/usr/bin/env python3
"""Protected deterministic verification for the confirmed service-day delivery."""

from __future__ import annotations

import csv
from pathlib import Path
import re
import sys


PACKET = Path("service_day_packet.md")
ROSTER = Path("official_roster.csv")


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_utf8(path: Path) -> str:
    if not path.is_file():
        fail(f"{path} is missing")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        fail(f"{path} cannot be read as UTF-8: {error}")


def plain_inline_text(value: str) -> str:
    return value.replace("**", "").replace("__", "").strip()


def bullet_body(line: str, section: str) -> str:
    match = re.fullmatch(r"[-+*]\s+(.+)", line.strip())
    if match is None:
        fail(f"{section} entries must be Markdown bullets")
    return plain_inline_text(match.group(1))


def table_cells(row: str) -> list[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    cells = [plain_inline_text(value) for value in row.split("|")]
    if len(cells) != 2:
        fail("Volunteer Assignments must use a two-column Markdown table")
    return cells


text = read_utf8(PACKET)
headings = re.findall(r"(?m)^# ([^#\n]+)\s*$", text)
expected_headings = [
    "Authorization",
    "Event Details",
    "Volunteer Assignments",
    "Follow-Up",
]
if headings != expected_headings:
    fail("the four H1 sections are missing, extra, or out of order")
if not text.startswith("# Authorization\n"):
    fail("content appears before the first requested section")

parts = re.split(r"(?m)^# ([^#\n]+)\s*\n", text)
if len(parts) != 9 or parts[0] != "":
    fail("the packet contains content outside the four requested sections")
sections = {
    parts[index]: parts[index + 1].strip()
    for index in range(1, len(parts), 2)
}

authorization_lines = [
    plain_inline_text(line)
    for line in sections["Authorization"].splitlines()
    if line.strip()
]
expected_authorization = (
    "Approve the Community Resource Fair volunteer plan and authorize Dana "
    "Brooks to coordinate day-of operations."
)
if authorization_lines != [expected_authorization]:
    fail("Authorization must contain only the clarified sentence")

detail_lines = [
    line.strip()
    for line in sections["Event Details"].splitlines()
    if line.strip()
]
expected_details = [
    ("Date", "Saturday, 2026-09-26"),
    ("Time", "8:30 a.m.–1:00 p.m."),
    ("Location", "Cedar Room at Lakeside Library"),
]
details: list[tuple[str, str]] = []
for line in detail_lines:
    body = bullet_body(line, "Event Details")
    match = re.fullmatch(
        r"(Date|Time|Location)(?:\s*(?::|—|–|-)\s*|\s+)(.+)",
        body,
    )
    if match is None:
        fail("Event Details bullets must use the requested labels")
    details.append((match.group(1), match.group(2).strip()))
if details != expected_details:
    fail("Event Details facts, labels, or order are incorrect")

table_lines = [
    line.strip()
    for line in sections["Volunteer Assignments"].splitlines()
    if line.strip()
]
if len(table_lines) != 5:
    fail("Volunteer Assignments must contain one header and three data rows")
if table_cells(table_lines[0]) != ["Volunteer", "Assignment"]:
    fail("the assignment table headings are incorrect")
separator = table_cells(table_lines[1])
if any(re.fullmatch(r":?-{3,}:?", value) is None for value in separator):
    fail("the assignment table separator is invalid")
expected_assignments = [
    ["Maya Ortiz", "Welcome desk"],
    ["Theo Grant", "Supply distribution"],
    ["Linh Nguyen", "Family activity table"],
]
actual_assignments = [table_cells(row) for row in table_lines[2:]]
if actual_assignments != expected_assignments:
    fail("the approved assignments or their source order are incorrect")

follow_up_lines = [
    line.strip()
    for line in sections["Follow-Up"].splitlines()
    if line.strip()
]
expected_follow_up = [
    ("2026-09-18", "Dana Brooks", "Send the final setup map to team leads."),
    ("2026-09-21", "Omar Hassan", "Confirm table delivery with facilities."),
]
if len(follow_up_lines) != len(expected_follow_up):
    fail("Follow-Up must contain exactly the two requested actions")
follow_up: list[tuple[str, str, str]] = []
for line, (_, expected_owner, _) in zip(
    follow_up_lines,
    expected_follow_up,
):
    body = bullet_body(line, "Follow-Up")
    date_match = re.match(r"(?:Due\s+)?(\d{4}-\d{2}-\d{2})\b", body)
    if date_match is None:
        fail("Follow-Up bullets must put each due date before the owner")
    remainder = body[date_match.end() :].lstrip(" \t:—–-,;")
    if not remainder.startswith(expected_owner):
        fail("Follow-Up bullets must put each due date before the owner")
    remainder = remainder[len(expected_owner) :]
    if not remainder or remainder[0] not in " \t:—–-,;":
        fail("Follow-Up actions must follow their owners")
    remainder = remainder.lstrip(" \t:—–-,;")
    follow_up.append((date_match.group(1), expected_owner, remainder))
if follow_up != expected_follow_up:
    fail("Follow-Up must contain the two dated actions in source order")

folded_packet = text.casefold()
for prohibited in (
    "east hall at juniper center",
    "granite bank",
    "4,500",
    "example.org",
    "312-555",
    "prefers not to be photographed",
):
    if prohibited in folded_packet:
        fail(f"prohibited or superseded detail present in packet: {prohibited}")
if "$" in text or "@" in text:
    fail("a prohibited funding or contact detail is present in the packet")

roster_text = read_utf8(ROSTER)
try:
    roster_rows = list(csv.reader(roster_text.splitlines()))
except csv.Error as error:
    fail(f"official_roster.csv is not valid CSV: {error}")
expected_roster = [
    ["event_date", "event_name", "location", "volunteer", "assignment", "status"],
    [
        "2026-08-15",
        "Summer Meal Packing",
        "South Warehouse",
        "Elena Park",
        "Check-in",
        "Completed",
    ],
    [
        "2026-09-26",
        "Community Resource Fair",
        "Cedar Room at Lakeside Library",
        "Maya Ortiz",
        "Welcome desk",
        "Confirmed",
    ],
    [
        "2026-09-26",
        "Community Resource Fair",
        "Cedar Room at Lakeside Library",
        "Theo Grant",
        "Supply distribution",
        "Confirmed",
    ],
    [
        "2026-09-26",
        "Community Resource Fair",
        "Cedar Room at Lakeside Library",
        "Linh Nguyen",
        "Family activity table",
        "Confirmed",
    ],
]
if roster_rows != expected_roster:
    fail("the official roster was not preserved and extended exactly as confirmed")

folded_roster = roster_text.casefold()
for prohibited in (
    "east hall at juniper center",
    "granite bank",
    "example.org",
    "312-555",
):
    if prohibited in folded_roster:
        fail(f"prohibited or superseded detail present in roster: {prohibited}")

print("PASS: confirmed action, newest correction, retained constraints, and final delivery verified")
