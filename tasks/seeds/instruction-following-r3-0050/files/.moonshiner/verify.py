#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path.cwd()
OUTPUT = ROOT / "deliverables" / "2026_recognition_packet.md"
OUTPUT_RELATIVE = "deliverables/2026_recognition_packet.md"

SOURCE_HASHES = {
    "records/awardees.csv": "aac9ee3bf03f2249a08a443bdfe60c74dfef3a8f5305afca1b852b27f8aeb9db",
    "records/event_notes.md": "29f46dfba0c35dace357c217e69cc3f0596099522c0e75ee9b9ee15999d5ec76",
    "templates/recognition_packet.md": "3901c25164d2fade66d0efdb345f56c013c36efbca199d91d668d3d88efee906",
}

HEADINGS = [
    "## Event details",
    "## Run of show",
    "## Recognition roll",
    "## Staff assignment checklist",
    "## Invitation email",
]

PROGRAM = [
    ("11:30", "11:45", 15, "Arrival, check-in, and lunch service", "Maya Ruiz"),
    ("11:45", "11:50", 5, "Welcome", "Dana Cho"),
    ("11:50", "12:00", 10, "Year-in-review", "Maya Ruiz"),
    ("12:00", "12:30", 30, "Recognition roll", "Maya Ruiz"),
    ("12:30", "12:40", 10, "Volunteer story", "Camila Reed"),
    ("12:40", "12:50", 10, "Next-season preview", "Maya Ruiz"),
    ("12:50", "12:55", 5, "Closing thanks", "Dana Cho"),
    ("12:55", "13:00", 5, "Group photo", "Jesse Park"),
]

ASSIGNMENTS = {
    "Dana Cho": ("master of ceremonies", "welcome", "closing thanks"),
    "Maya Ruiz": ("check-in", "year-in-review", "recognition roll", "next-season preview"),
    "Eli Patel": ("room setup", "lunch liaison"),
    "Noor Ahmed": ("A/V", "slides", "year-in-review", "recognition roll"),
    "Jesse Park": ("photograph", "group photo"),
    "Camila Reed": ("volunteer story",),
}

CHECKLIST_CONNECTORS = {
    "act",
    "advance",
    "also",
    "and",
    "as",
    "at",
    "be",
    "complete",
    "coordinate",
    "deliver",
    "during",
    "event",
    "for",
    "give",
    "handle",
    "is",
    "lead",
    "luncheon",
    "manage",
    "on",
    "organize",
    "oversee",
    "perform",
    "photograph",
    "photos",
    "present",
    "provide",
    "responsible",
    "run",
    "serve",
    "take",
    "the",
    "to",
    "will",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def section(text: str, heading: str, next_heading: str | None) -> str:
    start = text.index(heading) + len(heading)
    end = text.index(next_heading, start) if next_heading else len(text)
    return text[start:end]


def table_rows(block: str) -> list[list[str]]:
    rows = []
    for line in block.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return rows


for relative, expected in SOURCE_HASHES.items():
    path = ROOT / relative
    if not path.is_file():
        fail(f"source fixture missing: {relative}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        fail(f"source fixture was changed: {relative}")

deliverables = ROOT / "deliverables"
if not OUTPUT.is_file() or OUTPUT.is_symlink():
    fail("deliverables/2026_recognition_packet.md was not created")
if sorted(path.name for path in deliverables.iterdir()) != [OUTPUT.name]:
    fail("deliverables contains a scratch or alternative file")

allowed_files = set(SOURCE_HASHES) | {".moonshiner/verify.py", OUTPUT_RELATIVE}
workspace_files = {
    path.relative_to(ROOT).as_posix()
    for path in ROOT.rglob("*")
    if (path.is_file() or path.is_symlink()) and ".git" not in path.relative_to(ROOT).parts
}
unexpected_files = sorted(workspace_files - allowed_files)
if unexpected_files:
    fail(f"scratch or alternative file was created: {unexpected_files[0]}")

text = OUTPUT.read_text(encoding="utf-8")
if len(text.split()) < 250:
    fail("packet is not substantive")
markdown_headings = re.findall(r"^#{1,6} .+$", text, flags=re.MULTILINE)
if markdown_headings != ["# 2026 Volunteer Recognition Luncheon Packet", *HEADINGS]:
    fail("packet headings do not exactly match the template headings and order")
if re.search(r"\b(TBD|TK|placeholder)\b", text, flags=re.IGNORECASE):
    fail("packet contains a placeholder")
if re.search(r"Harbor Center|Community Room B", text, flags=re.IGNORECASE):
    fail("superseded venue remains in the packet")

event = section(text, HEADINGS[0], HEADINGS[1])
event_required = [
    "Tuesday, May 12, 2026",
    "11:30 a.m.",
    "1:00 p.m.",
    "Friday, May 1, 2026",
    "volunteers@northstarreading.org",
]
for value in event_required:
    if value.casefold() not in event.casefold():
        fail(f"event details omit: {value}")
if not re.search(r"East Library[^\n]{0,20}Nelson Hall", event, flags=re.IGNORECASE):
    fail("event details omit the corrected venue")
for role in ("invitation sender", "master of ceremonies"):
    if not re.search(rf"{role}[^\n]{{0,30}}Dana Cho", event, flags=re.IGNORECASE):
        fail(f"event details do not identify Dana Cho as {role}")

run_rows = table_rows(section(text, HEADINGS[1], HEADINGS[2]))
if not run_rows or [cell.casefold() for cell in run_rows[0]] != [
    "start", "end", "minutes", "component", "owner"
]:
    fail("run-of-show table header is incorrect")
body_rows = run_rows[1:]
if len(body_rows) != len(PROGRAM):
    fail("run of show must have exactly eight program rows")
total_minutes = 0
previous_end = None
for row, expected in zip(body_rows, PROGRAM):
    if len(row) != 5:
        fail("each run-of-show row must have five columns")
    start, end, minutes, component, owner = row
    exp_start, exp_end, exp_minutes, exp_component, exp_owner = expected
    if (start, end) != (exp_start, exp_end):
        fail(f"incorrect time range for {exp_component}")
    try:
        numeric_minutes = int(minutes)
    except ValueError:
        fail(f"minutes must be numeric for {exp_component}")
    calculated = int((datetime.strptime(end, "%H:%M") - datetime.strptime(start, "%H:%M")).total_seconds() / 60)
    if numeric_minutes != exp_minutes or calculated != exp_minutes:
        fail(f"incorrect duration for {exp_component}")
    if previous_end is not None and start != previous_end:
        fail("run of show has a gap or overlap")
    if component != exp_component or owner != exp_owner:
        fail(f"component or owner changed for {exp_component}")
    previous_end = end
    total_minutes += numeric_minutes
if total_minutes != 90 or body_rows[0][0] != "11:30" or body_rows[-1][1] != "13:00":
    fail("run of show does not cover the full 90-minute event")

with (ROOT / "records" / "awardees.csv").open(newline="", encoding="utf-8") as handle:
    awards = list(csv.DictReader(handle))
recognition_rows = table_rows(section(text, HEADINGS[2], HEADINGS[3]))
if not recognition_rows or [cell.casefold() for cell in recognition_rows[0]] != [
    "order", "name", "distinction", "citation"
]:
    fail("recognition table header is incorrect")
if len(recognition_rows[1:]) != len(awards):
    fail("recognition roll must contain every awardee exactly once")
for row, award in zip(recognition_rows[1:], awards):
    expected = [award["recognition_order"], award["name"], award["distinction"], award["citation"]]
    if row != expected:
        fail(f"recognition entry is missing, altered, or out of order: {award['name']}")

checklist = section(text, HEADINGS[3], HEADINGS[4])
items = [line for line in checklist.splitlines() if re.match(r"^\s*- \[ \] ", line)]
if len(items) != len(ASSIGNMENTS):
    fail("staff checklist must have exactly one unchecked item per staff member")
for name, duties in ASSIGNMENTS.items():
    matching = [line for line in items if name in line]
    if len(matching) != 1:
        fail(f"checklist must include {name} exactly once")
    folded = matching[0].casefold()
    for duty in duties:
        if duty.casefold() not in folded:
            fail(f"checklist omits a recorded duty for {name}: {duty}")
    remainder = folded.replace(name.casefold(), "")
    for duty in duties:
        remainder = remainder.replace(duty.casefold(), "")
    remainder_words = set(re.findall(r"[a-z]+", remainder))
    if remainder_words - CHECKLIST_CONNECTORS:
        fail(f"checklist adds unrecorded wording or a duty for {name}")
recorded_names = set(ASSIGNMENTS)
mentioned_names = set(re.findall(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", "\n".join(items)))
invented_names = sorted(mentioned_names - recorded_names)
if invented_names:
    fail(f"checklist invents a staff member: {invented_names[0]}")

email = section(text, HEADINGS[4], None).strip()
subject = "Subject: You’re invited: 2026 Northstar Volunteer Recognition Luncheon"
if email.count(subject) != 1:
    fail("invitation subject is missing or altered")
body = email.split(subject, 1)[1].strip()
word_count = len(re.findall(r"\b[\w’'-]+\b", body, flags=re.UNICODE))
if not 120 <= word_count <= 150:
    fail(f"invitation body is {word_count} words; expected 120–150")
email_required = [
    "Tuesday, May 12, 2026",
    "11:30 a.m.",
    "1:00 p.m.",
    "Friday, May 1, 2026",
    "volunteers@northstarreading.org",
]
for value in email_required:
    if value.casefold() not in body.casefold():
        fail(f"invitation body omits: {value}")
if not re.search(r"East Library[^\n]{0,20}Nelson Hall", body, flags=re.IGNORECASE):
    fail("invitation body omits the corrected venue")
nonempty_body_lines = [line.strip() for line in body.splitlines() if line.strip()]
if "Dana Cho" not in nonempty_body_lines[-3:]:
    fail("invitation body is not signed by Dana Cho")
email_addresses = {
    value.casefold()
    for value in re.findall(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", body, flags=re.IGNORECASE)
}
if email_addresses != {"volunteers@northstarreading.org"}:
    fail("invitation contains an invented or missing email contact")
if re.search(r"(?:https?://|www\.)", body, flags=re.IGNORECASE) or re.search(
    r"(?:\+?1[-. ]?)?(?:\(\d{3}\)|\d{3})[-. ]\d{3}[-. ]\d{4}", body
):
    fail("invitation contains an invented contact detail")

print("PASS: finalized recognition packet satisfies all retained and corrected constraints")
