#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from decimal import Decimal
from hashlib import sha256
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "event_runbook.md"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


if not RUNBOOK.is_file():
    fail("event_runbook.md is missing")

text = RUNBOOK.read_text(encoding="utf-8")

headings = [heading.rstrip() for heading in re.findall(r"(?m)^#{1,6} .+$", text)]
expected_headings = [
    "# Cedar Grove Volunteer Celebration",
    "## Confirmed event",
    "## Run of show",
    "## Setup and accessibility",
    "## Catering",
    "## Attendee message",
    "## Coordinator checklist",
]
if headings != expected_headings:
    fail(f"headings differ from the required ordered set: {headings!r}")

words = re.findall(r"\b[\w$%–.-]+\b", text, flags=re.UNICODE)
if not 325 <= len(words) <= 600:
    fail(f"runbook must contain 325–600 words; found {len(words)}")

superseded_detail = re.compile(
    r"(?:"
    r"Saturday"
    r"|Oct(?:ober)?\.?\s+17(?:th)?(?:,?\s+2026)?"
    r"|10[/-]17[/-](?:2026|26)"
    r"|2026[/-]10[/-]17"
    r"|4(?::00)?\s*(?:p\.?m\.?)?\s*(?:[–—-]|to)\s*7(?::00)?\s*p\.?m\.?)",
    re.IGNORECASE,
)
if superseded_detail.search(text):
    fail("runbook mentions the superseded date or time")


def section(name: str, next_name: str | None) -> str:
    start = text.index(name) + len(name)
    end = text.index(next_name, start) if next_name else len(text)
    return text[start:end]


confirmed = section("## Confirmed event", "## Run of show")
run_of_show = section("## Run of show", "## Setup and accessibility")
setup = section("## Setup and accessibility", "## Catering")
catering = section("## Catering", "## Attendee message")
message = section("## Attendee message", "## Coordinator checklist")
checklist = section("## Coordinator checklist", None)

expected_bullets = [
    "- Host: Cedar Grove Arts Collective",
    "- Event: Cedar Grove Volunteer Celebration",
    "- Date: Sunday, October 18, 2026",
    "- Time: 2:00–5:00 p.m.",
    "- Venue: Maple Room, Lumen House",
    "- Address: 1842 West Armitage Avenue, Chicago, Illinois 60622",
    "- Attendance: 48 people",
    "- RSVP deadline: October 9, 2026",
]
confirmed_lines = [line.strip() for line in confirmed.splitlines() if line.strip()]
if len(confirmed_lines) != len(expected_bullets) or set(confirmed_lines) != set(expected_bullets):
    fail("Confirmed event must contain exactly the eight retained detail bullets")

def parse_markdown_table(value: str, headers: list[str], table_name: str) -> list[tuple[str, ...]]:
    lines = [line.strip() for line in value.splitlines() if "|" in line]
    if len(lines) < 2:
        fail(f"{table_name} must contain a Markdown table")

    def cells(line: str) -> tuple[str, ...]:
        if "|" not in line:
            fail(f"malformed {table_name} row: {line}")
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        return tuple(cell.strip() for cell in line.split("|"))

    parsed_headers = cells(lines[0])
    if parsed_headers != tuple(headers):
        fail(f"{table_name} has the wrong Markdown table header")
    separators = cells(lines[1])
    if len(separators) != len(headers) or any(
        not re.fullmatch(r":?-{3,}:?", separator) for separator in separators
    ):
        fail(f"{table_name} has a malformed Markdown table separator")

    rows = [cells(line) for line in lines[2:]]
    if any(len(row) != len(headers) for row in rows):
        fail(f"{table_name} has a row with the wrong number of cells")
    return rows


parsed_schedule = parse_markdown_table(
    run_of_show,
    ["Start", "End", "Activity", "Lead", "Operational note"],
    "Run of show",
)

expected_schedule = [
    ("2:00 p.m.", "2:20 p.m.", "Guest arrival and check-in", "Welcome team", "Place name badges at the west doors"),
    ("2:20 p.m.", "2:30 p.m.", "Welcome", "Program director", "Use the house microphone"),
    ("2:30 p.m.", "3:15 p.m.", "Buffet service", "Catering captain", "Release tables one at a time"),
    ("3:15 p.m.", "3:50 p.m.", "Volunteer stories", "Story hosts", "Introduce three confirmed speakers"),
    ("3:50 p.m.", "4:15 p.m.", "Recognition", "Board chair", "Present awards in surname order"),
    ("4:15 p.m.", "4:50 p.m.", "Community mingle", "All staff", "Keep the east alcove available for quiet seating"),
    ("4:50 p.m.", "5:00 p.m.", "Closing and departure", "Program director", "Direct departing guests to the west doors"),
]
if len(parsed_schedule) != len(expected_schedule):
    fail("Run of show must contain exactly the seven sourced program rows")

def time_in_minutes(value: str) -> int | None:
    normalized = re.sub(r"\s+", "", value.casefold().replace(".", ""))
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", normalized)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = match.group(3)
    if minute >= 60:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    elif hour >= 24:
        return None
    return hour * 60 + minute


schedule_matches = len(parsed_schedule) == len(expected_schedule)
for actual, expected in zip(parsed_schedule, expected_schedule):
    if time_in_minutes(actual[0]) != time_in_minutes(expected[0]):
        schedule_matches = False
    if time_in_minutes(actual[1]) != time_in_minutes(expected[1]):
        schedule_matches = False
    if tuple(re.sub(r"\s+", " ", cell).casefold() for cell in actual[2:]) != tuple(
        cell.casefold() for cell in expected[2:]
    ):
        schedule_matches = False
if not schedule_matches:
    fail("Run of show does not preserve the sourced program in the corrected time window")


def require(pattern: str, value: str, message_text: str) -> None:
    if not re.search(pattern, value, flags=re.IGNORECASE | re.DOTALL):
        fail(message_text)


setup_requirements = [
    (r"six\s+round\s+tables?", "setup must retain six round tables"),
    (r"eight\s+places\s+(?:at\s+)?each", "setup must retain eight places at each table"),
    (r"two\s+center[- ]aisle\s+tables?", "setup must identify the two center-aisle tables"),
    (r"chair[- ]free\s+wheelchair\s+positions?", "setup must retain the chair-free wheelchair positions"),
    (r"seven\s+chairs?", "setup must retain seven chairs at each wheelchair-position table"),
    (r"table\s+nearest\s+the\s+east\s+alcove", "setup must identify the table nearest the east alcove"),
    (r"quiet\s+seating", "setup must mark the east-alcove table as quiet seating"),
    (r"clear\s+center\s+aisle", "setup must retain the clear center aisle"),
    (r"elevator", "setup must connect the clear aisle to the elevator"),
    (r"front\s+of\s+the\s+room", "setup must connect the clear aisle to the front of the room"),
    (r"48\s+large[- ]print\s+programs?|large[- ]print\s+program\s+at\s+(?:each|every)\s+place", "setup must provide a large-print program at all 48 places"),
    (r"step[- ]free\s+west\s+doors?", "setup must identify the step-free west doors"),
    (r"staffed\s+elevator", "setup must include the staffed elevator"),
]
for pattern, message_text in setup_requirements:
    require(pattern, setup, message_text)

parsed_catering = parse_markdown_table(
    catering,
    ["Item", "Quantity", "Unit cost", "Line total"],
    "Catering",
)
if len(parsed_catering) != 3:
    fail("Catering must contain exactly two selected item rows and one total row")

def money(value: str) -> Decimal | None:
    if not re.fullmatch(r"\$(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}", value):
        return None
    return Decimal(value[1:].replace(",", ""))


catering_items = {
    row[0].casefold(): (row[1], money(row[2]), money(row[3]))
    for row in parsed_catering[:2]
}
catering_matches = (
    catering_items
    == {
        "garden table buffet": ("48", Decimal("26.00"), Decimal("1248.00")),
        "citrus sparkler station": ("48", Decimal("4.00"), Decimal("192.00")),
    }
    and parsed_catering[2][0].casefold() == "total"
    and money(parsed_catering[2][3]) == Decimal("1440.00")
)
if not catering_matches:
    fail("Catering quantities, selected items, or calculated totals are incorrect")
if Decimal("48") * (Decimal("26.00") + Decimal("4.00")) != Decimal("1440.00"):
    fail("internal catering oracle is inconsistent")

subject_lines = []
for line in message.splitlines():
    plain_line = re.sub(r"[*_`]", "", line).strip()
    match = re.fullmatch(r"Subject\s*:\s*(\S.*)", plain_line, flags=re.IGNORECASE)
    if match:
        subject_lines.append(match.group(1))
if len(subject_lines) != 1:
    fail("attendee message needs one nonempty subject line")
message_requirements = [
    (r"Sunday,\s+October\s+18,\s+2026", "attendee message must state the corrected date"),
    (r"2(?::00)?\s*(?:p\.?m\.?)?\s*(?:[–—-]|to)\s*5(?::00)?\s+p\.?m\.?", "attendee message must state the corrected time"),
    (r"Maple\s+Room", "attendee message must name the Maple Room"),
    (r"Lumen\s+House", "attendee message must name Lumen House"),
    (r"1842\s+West\s+Armitage\s+Avenue", "attendee message must give the sourced street address"),
    (r"Chicago,?\s+(?:Illinois|IL)\s+60622", "attendee message must give the sourced city, state, and ZIP code"),
    (r"west\s+doors?", "attendee message must give the west-door arrival instruction"),
    (r"Route\s+8", "attendee message must include the sourced Route 8 note"),
    (r"RSVP", "attendee message must identify the RSVP deadline"),
    (r"October\s+9,\s+2026", "attendee message must state the RSVP deadline"),
    (r"access\s+accommodations?", "attendee message must mention access accommodations"),
    (r"RSVP\s+form", "attendee message must direct accommodation requests through the RSVP form"),
]
for pattern, message_text in message_requirements:
    require(pattern, message, message_text)

checklist_lines = [line.strip() for line in checklist.splitlines() if line.strip()]
if len(checklist_lines) != 5 or any(not line.startswith("- ") for line in checklist_lines):
    fail("Coordinator checklist must contain exactly five bullets")
checklist_text = "\n".join(checklist_lines)
checklist_requirements = [
    (r"1:00\s+p\.?m\.?,?", "checklist must include the corrected setup-access time"),
    (r"setup", "checklist must cover setup access"),
    (r"west\s+doors?", "checklist must identify the west doors"),
    (r"check[- ]in", "checklist must cover west-door check-in"),
    (r"sign", "checklist must cover signs"),
    (r"house\s+microphone", "checklist must cover the house microphone"),
    (r"projection\s+screen", "checklist must cover the projection screen"),
    (r"48\s+large[- ]print\s+programs?", "checklist must cover the accessible materials"),
    (r"quiet\s+seating", "checklist must cover quiet seating"),
    (r"5:45\s+p\.?m\.?,?", "checklist must include the venue-clear deadline"),
    (r"clear", "checklist must say the venue is clear by its deadline"),
]
for pattern, message_text in checklist_requirements:
    require(pattern, checklist_text, message_text)

allowed_top_level = {"event_sources", ".protected", "event_runbook.md", ".git", ".sandbox-home"}
extras = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed_top_level)
if extras:
    fail(f"unexpected extra top-level artifacts: {extras}")

expected_sources = {"venue_brief.md", "catering_options.csv", "program_notes.md"}
source_dir = ROOT / "event_sources"
actual_sources = {path.name for path in source_dir.iterdir()} if source_dir.is_dir() else set()
if actual_sources != expected_sources:
    fail(f"event_sources/ contents changed: {sorted(actual_sources)!r}")
expected_source_hashes = {
    "venue_brief.md": "5d5a08aa1b3952702a13d3b1f454ac056e671c64fac2485f592101078b687246",
    "catering_options.csv": "128fc8807660baaad3641990c7415228470ddec439705492c60b4564fb728e70",
    "program_notes.md": "c97bf7611cf42336ee4690472d92ed872fb739c64b760c7d8b271fd87e1348dd",
}
for source_name, expected_hash in expected_source_hashes.items():
    source_path = source_dir / source_name
    if not source_path.is_file() or sha256(source_path.read_bytes()).hexdigest() != expected_hash:
        fail(f"source file was modified: event_sources/{source_name}")
protected_extras = sorted(path.name for path in (ROOT / ".protected").iterdir() if path.name != "verify.py")
if protected_extras:
    fail(f"unexpected artifacts under .protected/: {protected_extras}")

print(f"PASS: event_runbook.md satisfies the confirmed corrected event brief ({len(words)} words)")
