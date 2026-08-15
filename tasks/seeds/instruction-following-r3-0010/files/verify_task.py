#!/usr/bin/env python3
"""Protected deterministic verification for the final nonprofit board brief."""

from __future__ import annotations

from pathlib import Path
import re
import sys


BRIEF = Path("board_brief.md")


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


if not BRIEF.is_file():
    fail("board_brief.md is missing")

try:
    text = BRIEF.read_text(encoding="utf-8")
except (OSError, UnicodeError) as error:
    fail(f"board_brief.md cannot be read as UTF-8: {error}")

headings = re.findall(r"(?m)^# ([^#\n]+)\s*$", text)
expected_headings = [
    "Board Decision",
    "Event Snapshot",
    "Staffing Assignments",
    "Follow-Up",
]
if headings != expected_headings:
    fail("the four H1 sections are missing, extra, or out of order")

if text[:1].isspace() or not text.startswith("# Board Decision\n"):
    fail("content appears before the first requested section")

parts = re.split(r"(?m)^# ([^#\n]+)\s*\n", text)
if len(parts) != 9 or parts[0] != "":
    fail("the brief contains content outside the four requested sections")
sections = {
    parts[index]: parts[index + 1].strip()
    for index in range(1, len(parts), 2)
}

decision_lines = [
    line.strip()
    for line in sections["Board Decision"].splitlines()
    if line.strip()
]
expected_decision = (
    "Approve the Fall Volunteer Open House plan and designate Morgan Lee "
    "as board liaison."
)
if decision_lines != [expected_decision]:
    fail("Board Decision must contain only the clarified sentence")

snapshot_lines = [
    line.strip()
    for line in sections["Event Snapshot"].splitlines()
    if line.strip()
]
expected_snapshot = [
    ("Date", "Saturday, September 19, 2026"),
    ("Time", "9:30 a.m.–12:00 p.m."),
    ("Location", "Cedar Grove Community Center, Room B"),
]


def bullet_body(line: str, section: str) -> str:
    match = re.fullmatch(r"[-+*]\s+(.+)", line)
    if match is None:
        fail(f"{section} entries must be Markdown bullets")
    return match.group(1).strip()


def plain_inline_text(value: str) -> str:
    """Ignore optional strong emphasis while checking substantive text."""
    return value.replace("**", "").replace("__", "")


snapshot = []
for line in snapshot_lines:
    body = plain_inline_text(bullet_body(line, "Event Snapshot"))
    match = re.fullmatch(
        r"(Date|Time|Location)\s*(?::|—|–|-)\s*(.+)",
        body,
    )
    if match is None:
        fail("Event Snapshot bullets must use the requested labels")
    snapshot.append((match.group(1), match.group(2)))
if snapshot != expected_snapshot:
    fail("Event Snapshot facts, labels, or order are incorrect")

table_lines = [
    line.strip()
    for line in sections["Staffing Assignments"].splitlines()
    if line.strip()
]
if len(table_lines) != 5:
    fail("Staffing Assignments must contain one header and three data rows")


def cells(row: str) -> list[str]:
    # Leading and trailing pipes are optional in Markdown tables.
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    values = [value.strip() for value in row.split("|")]
    if len(values) != 2:
        fail("Staffing Assignments must use a two-column Markdown table")
    return values


if [plain_inline_text(value) for value in cells(table_lines[0])] != [
    "Volunteer",
    "Assignment",
]:
    fail("the staffing table headings are incorrect")
separator = cells(table_lines[1])
if len(separator) != 2 or any(
    re.fullmatch(r":?-{3,}:?", value) is None for value in separator
):
    fail("the staffing table separator is invalid")
expected_rows = [
    ["Amina Patel", "Check-in lead"],
    ["Jorge Silva", "Pantry tour lead"],
    ["Mei Chen", "Intake demonstration"],
]
actual_rows = [
    [plain_inline_text(value) for value in cells(row)]
    for row in table_lines[2:]
]
if actual_rows != expected_rows:
    fail("the confirmed staffing assignments or their order are incorrect")

follow_up_lines = [
    line.strip()
    for line in sections["Follow-Up"].splitlines()
    if line.strip()
]
expected_follow_up = [
    (
        "2026-09-11",
        "Morgan Lee",
        "Send the approved run-of-show to program leads.",
    ),
    (
        "2026-09-14",
        "Priya Nair",
        "Confirm room access with the venue coordinator.",
    ),
]


follow_up = []
for line in follow_up_lines:
    body = plain_inline_text(bullet_body(line, "Follow-Up"))
    match = re.fullmatch(
        r"(?:Due\s+)?(\d{4}-\d{2}-\d{2})\s*(?:—|–|-)\s*"
        r"([^:]+):\s*(.+)",
        body,
    )
    if match is None:
        fail("Follow-Up bullets must put each due date before the owner")
    follow_up.append(tuple(value.strip() for value in match.groups()))
if follow_up != expected_follow_up:
    fail("Follow-Up must contain the two dated actions in source-sheet order")

folded = text.casefold()
for prohibited in (
    "elm street pantry",
    "ellis wright",
    "lydia carver",
    "2,500",
    "example.org",
    "312-555",
    "tentative",
):
    if prohibited in folded:
        fail(f"prohibited or superseded detail present: {prohibited}")
if "$" in text or "@" in text:
    fail("a prohibited funding or contact detail is present")

print("PASS: correction, retained constraints, ordering, omissions, and brief verified")
