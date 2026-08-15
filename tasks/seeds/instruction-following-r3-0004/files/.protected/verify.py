#!/usr/bin/env python3
"""Deterministic acceptance checks for the corrected event brief."""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "event-brief.md"
SOURCE = ROOT / "event_options.csv"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def section(lines: list[str], heading: str, next_heading: str | None) -> list[str]:
    try:
        start = lines.index(heading) + 1
    except ValueError:
        fail(f"missing heading: {heading}")
    if next_heading is None:
        end = len(lines)
    else:
        try:
            end = lines.index(next_heading)
        except ValueError:
            fail(f"missing heading: {next_heading}")
    return lines[start:end]


if not REPORT.is_file():
    fail("event-brief.md is missing")
text = REPORT.read_text(encoding="utf-8")
if len(text.strip()) < 1200:
    fail("event-brief.md is not a substantive final event brief")

with SOURCE.open(newline="", encoding="utf-8") as handle:
    source_rows = list(csv.DictReader(handle))
rows = {row["id"]: row for row in source_rows}
if len(rows) != len(source_rows):
    fail("event_options.csv contains duplicate IDs")

headings = [line for line in text.splitlines() if line.startswith("#")]
expected_headings = [
    "# Harvest Commons Volunteer Thank-You Day",
    "## Event snapshot",
    "## Run of show",
    "## Attendee message",
    "## Coordinator checklist",
]
if headings != expected_headings:
    fail("headings are missing, extra, or out of the requested order")

folded = text.casefold()
for forbidden_id in ("WS-CLAY", "WS-GARDEN", "LUNCH-DELI", "LUNCH-BISTRO"):
    row = rows[forbidden_id]
    for value in (row["id"], row["name"]):
        if value.casefold() in folded:
            fail(f"unselected or superseded option is present: {value}")

# Reject distinctive facts from the unselected rows even if their names and IDs
# are omitted. Shared facts (for example, Dining Hall) are intentionally absent
# from this list because they also describe a selected row.
for fragment in (
    "studio a",
    "courtyard",
    "wear clothes that can handle a little clay",
    "set clay tools and water cups",
    "studio a is across from the garden room",
    "closed-toe shoes are required",
    "place soil tubs along the east wall",
    "outdoor route includes a two-inch threshold",
    "enter the courtyard through studio a",
    "boxed deli lunch with vegetarian sides",
    "stack boxes by surname at the south wall",
    "limited vegan selection",
    "no lowered service station",
    "vegetarian grain bowls with vegan choices",
    "arrange bowls on the annex counter",
    "bistro annex",
    "narrow counter lane",
):
    if fragment in folded:
        fail(f"a fact from an unselected or superseded option is present: {fragment}")

if re.search(
    r"(?i)(?:\$|\bUSD\b|\bdollars?\b|\bprice(?:s)?\b|\bbudget\b|\bcosts?\b|\bfees?\b)",
    text,
):
    fail("commercial details must be omitted")
for row in source_rows:
    price = row["price_usd"].strip()
    for amount in (price, price.removesuffix(".00")):
        if amount and re.search(rf"(?<![\d.]){re.escape(amount)}(?![\d.])", text):
            fail("a source price must be omitted")
if re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.IGNORECASE):
    fail("staff email address must be omitted")
for row in source_rows:
    phone_digits = re.sub(r"\D", "", row["staff_phone"])
    if phone_digits:
        flexible_phone = r"(?<!\d)" + r"\D{0,3}".join(phone_digits) + r"(?!\d)"
        if re.search(flexible_phone, text):
            fail("staff phone number must be omitted")
for row in source_rows:
    note = row["internal_vendor_note"].strip()
    if note and note.casefold() in folded:
        fail("an internal vendor note was copied into the brief")
for fragment in (
    "alarm code",
    "4418",
    "spare badge box",
    "green-room door",
    "loading dock",
    "service sink",
    "roller cleanup",
    "tool sets",
    "vendor invoice",
    "chafing-dish deposit",
    "substitution cutoff",
    "annex fire limit",
    "completed cards",
    "locked office",
):
    if fragment in folded:
        fail(f"an internal vendor-note detail is present: {fragment}")

lines = text.splitlines()
snapshot = [
    line
    for line in section(lines, "## Event snapshot", "## Run of show")
    if line.strip()
]
expected_snapshot = [
    "- Date: Saturday, 2026-10-17",
    "- Time: 09:00–14:30 America/Chicago",
    "- Venue: Juniper House",
    "- Attendance: 48 guests",
    "- Workshop: Neighborhood Printmaking Lab (WS-PRINT)",
    "- Lunch: Garden Table Buffet (LUNCH-GARDEN)",
    "- Meal accommodations: 6 vegan; 4 gluten-free",
]
if snapshot != expected_snapshot:
    fail("Event snapshot must contain exactly the seven requested bullets in order")

run_of_show = [
    line
    for line in section(lines, "## Run of show", "## Attendee message")
    if line.strip()
]
if run_of_show[:2] != [
    "| Time | ID | Segment | Location | Coordinator cue |",
    "|---|---|---|---|---|",
]:
    fail("Run of show has the wrong table header")
selected_ids = ["SEG-CHECK", "SEG-WELCOME", "WS-PRINT", "LUNCH-GARDEN", "SEG-CIRCLE"]
expected_rows = []
for selected_id in selected_ids:
    row = rows[selected_id]
    expected_rows.append(
        f'| {row["start"]}–{row["end"]} | {row["id"]} | {row["name"]} | '
        f'{row["location"]} | {row["coordinator_cue"]} |'
    )
if run_of_show[2:] != expected_rows:
    fail("Run of show must contain exactly the five corrected, sourced rows in order")

attendee_lines = section(lines, "## Attendee message", "## Coordinator checklist")
attendee = "\n".join(attendee_lines).strip()
blocks = [block.strip() for block in attendee.split("\n\n") if block.strip()]
if not blocks or blocks[0] != "Subject: Your Harvest Commons Volunteer Thank-You Day details":
    fail("Attendee message has the wrong or missing subject line")
paragraphs = blocks[1:]
if not 2 <= len(paragraphs) <= 3:
    fail("Attendee message must contain two or three paragraphs after the subject")
if any(paragraph.startswith(("-", "*", "|", "#")) for paragraph in paragraphs):
    fail("Attendee message must use send-ready prose paragraphs")
if any(not re.search(r"[.!?][\"')\]]*$", paragraph) for paragraph in paragraphs):
    fail("Attendee message paragraphs must be complete sentences")
attendee_folded = " ".join(paragraphs).casefold()
required_attendee_fragments = [
    "saturday, 2026-10-17",
    "09:00–14:30 america/chicago",
    "juniper house",
    "check-in opens at 09:00",
    rows["WS-PRINT"]["name"].casefold(),
    rows["WS-PRINT"]["public_note"].casefold(),
    rows["LUNCH-GARDEN"]["name"].casefold(),
    "vegetarian",
    "6 vegan",
    "4 gluten-free",
    "step-free main entrance",
    "all-gender accessible restroom",
    "quiet room beside the garden room",
    "route 6",
    "river street",
    "one block from the venue",
    "bike racks",
    "reusable water bottle",
]
missing = [item for item in required_attendee_fragments if item not in attendee_folded]
if missing:
    fail("Attendee message is missing retained facts: " + "; ".join(missing))

checklist = [
    line
    for line in section(lines, "## Coordinator checklist", None)
    if line.strip()
]
expected_checklist = [
    f'- [ ] {rows["SEG-CHECK"]["coordinator_cue"]}',
    f'- [ ] {rows["WS-PRINT"]["coordinator_cue"]}',
    f'- [ ] {rows["LUNCH-GARDEN"]["coordinator_cue"]}',
    f'- [ ] {rows["VEN-JUNIPER"]["coordinator_cue"]}',
    f'- [ ] {rows["SEG-CIRCLE"]["coordinator_cue"]}',
]
if checklist != expected_checklist:
    fail("Coordinator checklist must contain exactly the five sourced checkbox cues")

print("PASS: event-brief.md satisfies the retained and corrected event instructions")
