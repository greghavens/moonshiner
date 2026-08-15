#!/usr/bin/env python3
"""Protected acceptance checks for the final event coordination deliverable."""

from pathlib import Path
import hashlib
import re
import sys


PLAN = Path("event_plan.md")
failures: list[str] = []
SOURCE_HASHES = {
    "venue_packet.md": "ef3b077bfe747cbdac61cd8e5db42201e08dd9f6cb94d0facc5e2ac8bfa5a323",
    "participant_notes.md": "378c083d37fec52e932d04645e5e2bfe9ed9dcbe1b46bd1a1d68062913443a83",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def section(text: str, title: str) -> str:
    match = re.search(
        rf"(?ims)^#\s+{re.escape(title)}\s*$\n(.*?)(?=^#\s+|\Z)",
        text,
    )
    return match.group(1).strip() if match else ""


def bullets(block: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    for line in block.splitlines():
        if re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", line):
            if current:
                items.append(" ".join(current))
            current = [line.strip()]
        elif current and line.strip():
            current.append(line.strip())
    if current:
        items.append(" ".join(current))
    return items


TIME_RE = re.compile(
    r"(?<!\d)(1[0-2]|[1-9]):([0-5]\d)\s*([ap]\.?(?:\s*)m\.?)?",
    re.IGNORECASE,
)


def clock_minutes(hour: str, minute: str, meridiem: str) -> int:
    value = int(hour) % 12 * 60 + int(minute)
    return value + (12 * 60 if meridiem.lower().startswith("p") else 0)


def clock_points(line: str) -> list[int]:
    matches = list(TIME_RE.finditer(line))
    points: list[int] = []
    for index, match in enumerate(matches):
        meridiem = match.group(3)
        if not meridiem:
            later = next((item.group(3) for item in matches[index + 1 :] if item.group(3)), None)
            earlier = next((item.group(3) for item in reversed(matches[:index]) if item.group(3)), None)
            meridiem = later or earlier
        if meridiem:
            points.append(clock_minutes(match.group(1), match.group(2), meridiem))
    return points


def agenda_entries(block: str) -> list[tuple[int, int, str]]:
    """Return physical agenda lines containing a parseable time range."""
    entries: list[tuple[int, int, str]] = []
    for line in block.splitlines():
        matches = list(TIME_RE.finditer(line))
        if len(matches) < 2:
            continue
        first, second = matches[0], matches[1]
        first_meridiem = first.group(3) or second.group(3)
        second_meridiem = second.group(3) or first.group(3)
        if not first_meridiem or not second_meridiem:
            continue
        start = clock_minutes(first.group(1), first.group(2), first_meridiem)
        end = clock_minutes(second.group(1), second.group(2), second_meridiem)
        if end < start:
            end += 12 * 60
        entries.append((start, end, line))
    return entries


if not PLAN.is_file():
    print("FAIL: event_plan.md is missing; deliver the requested final plan")
    raise SystemExit(1)

text = PLAN.read_text(encoding="utf-8")
lower = text.lower()

required_sections = [
    "Event snapshot",
    "Sourced facts",
    "Recommendations",
    "Agenda",
    "Roles and responsibilities",
    "Outreach checkpoints",
    "Uncertainties",
]
top_level_sections = re.findall(r"(?m)^#\s+(.+?)\s*$", text)
require(
    len(top_level_sections) == len(required_sections)
    and set(top_level_sections) == set(required_sections),
    "top-level sections must be exactly the requested seven headings",
)
blocks = {name: section(text, name) for name in required_sections}
for name, block in blocks.items():
    require(bool(block), f"missing required section: {name}")

# The newest correction replaces only these two earlier details.
require(
    bool(re.search(r"(?i)Tuesday,?\s+(?:October|Oct\.?)[ ]+20,?\s+2026", text)),
    "final corrected date is not stated",
)
require("priya nair" in lower, "final corrected featured speaker is not stated")
require(
    not re.search(r"(?i)(?:Thursday,?\s+)?(?:October|Oct\.?)\s+22(?:,?\s+2026)?", text),
    "superseded October 22 date remains in the final plan",
)
require("morgan chen" not in lower, "superseded speaker remains in the final plan")

# All unaffected constraints must survive the correction.
snapshot = blocks["Event snapshot"].lower()
for token, label in [
    ("lakeview neighborhood climate readiness night", "event name"),
    ("riverbend library", "venue"),
    ("cedar room", "room"),
    ("80", "planning attendance"),
    ("5:30", "doors time"),
    ("6:00", "program start"),
    ("8:00", "program end"),
    ("8:15", "clear-room deadline"),
]:
    require(token in snapshot, f"event snapshot lost the {label}")
require(
    bool(re.search(r"(?i)Tuesday,?\s+(?:October|Oct\.?)\s+20,?\s+2026", snapshot)),
    "event snapshot omits the corrected date",
)
require("priya nair" in snapshot, "event snapshot omits the corrected featured speaker")

# Facts, recommendations, and unknowns must be visibly distinct.
fact_lines = bullets(blocks["Sourced facts"])
require(len(fact_lines) >= 2, "sourced facts must contain multiple useful bullets")
for line in fact_lines:
    require(
        bool(re.search(r"(?i)\b(?:venue_packet\.md|participant_notes\.md)\b", line)),
        f"sourced-fact bullet lacks a source-file citation: {line}",
    )
for source in SOURCE_HASHES:
    require(
        any(source.lower() in line.lower() for line in fact_lines),
        f"sourced facts never cite {source}",
    )
require(
    any("96" in line and "venue_packet.md" in line.lower() for line in fact_lines),
    "sourced facts do not give the room capacity from venue_packet.md",
)
require(
    any("80" in line and "participant_notes.md" in line.lower() for line in fact_lines),
    "sourced facts do not identify the planning attendance from participant_notes.md",
)

recommendation_lines = bullets(blocks["Recommendations"])
require(len(recommendation_lines) >= 2, "recommendations must contain multiple actionable bullets")

uncertainty_lines = bullets(blocks["Uncertainties"])
require(bool(uncertainty_lines), "uncertainties must contain explicit items")
unknown_status = re.compile(
    r"(?i)\b(?:unconfirmed|not confirmed|tbd|to confirm|pending|undecided|unknown|"
    r"unnamed|unidentified|open|unresolved|awaiting|not finalized|"
    r"not (?:yet )?(?:been )?(?:selected|booked|decided|named|identified|assigned|confirmed)|"
    r"remain(?:s)? to be (?:selected|booked|decided|named|identified|assigned|confirmed|finalized)|"
    r"no\b.{0,80}\b(?:selected|booked|decided|named|identified|assigned|confirmed))\b"
)
for pattern, label in [
    (r"caption", "captioning"),
    (r"spanish|bilingual|language reviewer|translation reviewer|translator", "Spanish-language review"),
    (r"city|resilience liaison|resource table", "city liaison"),
    (r"food|cater|refreshment", "food plan"),
    (r"photo|image", "photography"),
    (r"hold|reservation|venue confirmation|library.{0,40}(?:date|room)", "venue confirmation"),
    (r"family|child(?:ren)?(?:'s)? activity|activity table|association volunteers", "family-table staffing or materials"),
]:
    matching_items = [item for item in uncertainty_lines if re.search(pattern, item, re.IGNORECASE)]
    require(
        bool(matching_items) and any(unknown_status.search(item) for item in matching_items),
        f"uncertainties do not identify the unsettled {label}",
    )

# The agenda must be run-ready and reconcile participant availability without
# prescribing one arbitrary set of transitions.
agenda = blocks["Agenda"]
for time in ["5:30", "6:00", "8:00", "8:15"]:
    require(time in agenda, f"agenda omits the {time} transition")
for pattern, label in [
    (r"\bdoors?\b", "doors period"),
    (r"\bwelcome\b", "welcome"),
    (r"\bbreakout\b", "breakout"),
    (r"\bclos", "closing"),
    (r"\b(?:cleanup|clean-up|teardown|clear(?:ance)?)\b", "room cleanup"),
]:
    require(bool(re.search(pattern, agenda, re.IGNORECASE)), f"agenda omits the {label}")
require("Priya Nair" in agenda, "agenda does not schedule the corrected speaker")
timed_lines = [line for line in agenda.splitlines() if TIME_RE.search(line)]
require(len(timed_lines) >= 7, "agenda needs at least seven timed run-of-show entries")

priya_speaking_lines = [
    line
    for line in timed_lines
    if "priya nair" in line.lower()
    and re.search(r"(?i)talk|present|question|q\s*&\s*a|remarks|address", line)
]
require(priya_speaking_lines, "agenda does not give Priya a timed speaker segment")
for line in priya_speaking_lines:
    points = clock_points(line)
    require(
        bool(points) and all(17 * 60 + 45 <= point <= 18 * 60 + 45 for point in points),
        "agenda schedules Priya outside her 5:45-6:45 p.m. availability",
    )

sam_assignment_lines = [
    line
    for line in timed_lines
    if re.search(
        r"(?i)(?:sam patel.{0,100}(?:breakout|facilitat|leads?)|"
        r"(?:breakout|facilitat|leads?).{0,100}sam patel)",
        line,
    )
    and not re.search(r"(?i)unavailable|does not assign|not assigned", line)
]
require(sam_assignment_lines, "agenda does not assign Sam Patel a timed breakout")
sam_conflicts = [
    line
    for start, end, line in agenda_entries("\n".join(sam_assignment_lines))
    if start < 19 * 60 + 35 and end > 19 * 60 + 15
]
for line in sam_assignment_lines:
    if len(clock_points(line)) == 1 and 19 * 60 + 15 <= clock_points(line)[0] < 19 * 60 + 35:
        sam_conflicts.append(line)
require(not sam_conflicts, "agenda assigns Sam Patel during the 7:15-7:35 p.m. conflict")

roles = blocks["Roles and responsibilities"]
for pattern, responsibility in [
    (r"jordan lee.{0,200}\bevent lead\b", "Jordan Lee as event lead"),
    (r"casey brooks.{0,200}(?:\bvenue\b.{0,80}\bav\b|\bav\b.{0,80}\bvenue\b)", "Casey Brooks as venue and AV lead"),
    (r"robin alvarez.{0,200}(?:\bwelcome\b.{0,120}\bclos|\bclos.{0,120}\bwelcome\b)", "Robin Alvarez as welcome and closing owner"),
    (r"sam patel.{0,200}(?:\bbreakout\b.{0,120}\bfacilitat|\bfacilitat.{0,120}\bbreakout\b)", "Sam Patel as breakout facilitator"),
]:
    require(bool(re.search(pattern, roles, re.IGNORECASE | re.DOTALL)), f"roles lost {responsibility}")

outreach = blocks["Outreach checkpoints"].lower()
require(
    bool(re.search(r"(?:two|2)\s+weeks", outreach)),
    "outreach omits the two-weeks-before checkpoint",
)
require("48 hours" in outreach or "48-hour" in outreach, "outreach omits the 48-hour checkpoint")
require("english" in outreach and "spanish" in outreach, "outreach omits bilingual copy")

# These are user-supplied inputs and must remain unchanged in the final workspace.
for source, expected_hash in SOURCE_HASHES.items():
    source_path = Path(source)
    require(source_path.is_file(), f"protected source document is missing: {source}")
    if source_path.is_file():
        actual_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        require(actual_hash == expected_hash, f"protected source document was edited: {source}")

if failures:
    for failure in failures:
        print(f"FAIL: {failure}")
    raise SystemExit(1)

print("PASS: final event plan applies the correction and retains all other constraints")
