#!/usr/bin/env python3
from __future__ import annotations

import csv
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ITINERARY = ROOT / "itinerary.md"
OPTIONS = ROOT / "trip_options.csv"
COSTS = ROOT / "trip_costs.csv"


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def parse_money(value: str, context: str) -> Decimal:
    match = re.fullmatch(r"\$(\d+\.\d{2})", value.strip())
    if not match:
        fail(f"{context} must be dollars with two decimal places")
    return Decimal(match.group(1))


def parse_table_row(line: str, column_count: int, context: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    cells = [cell.strip() for cell in row.split("|")]
    if len(cells) != column_count or any(not cell for cell in cells):
        fail(f"malformed table row in {context}: {line}")
    return cells


def is_table_delimiter(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


if not ITINERARY.is_file():
    fail("itinerary.md is missing")

text = ITINERARY.read_text(encoding="utf-8")
if "bay textile museum" in text.casefold() or "AB-TEXTILE" in text:
    fail("the superseded museum is still present")

with OPTIONS.open(newline="", encoding="utf-8") as handle:
    option_rows = list(csv.DictReader(handle))
options = {row["code"]: row for row in option_rows}
if len(options) != len(option_rows):
    fail("trip_options.csv contains duplicate codes")

with COSTS.open(newline="", encoding="utf-8") as handle:
    costs = {row["key"]: row for row in csv.DictReader(handle)}

lines = text.splitlines()
headings = [line for line in lines if line.startswith("#")]
expected_headings = [
    "# Alder Bay Long Weekend",
    "## Confirmed brief",
    "## Day 1 — Friday, 2026-10-16",
    "## Day 2 — Saturday, 2026-10-17",
    "## Day 3 — Sunday, 2026-10-18",
    "## Cost summary",
    "## Practical notes",
]
if headings != expected_headings:
    fail("headings are missing, extra, or out of order")

required_decisions = {
    "- Travelers: 2 adults",
    "- Dates: 2026-10-16 through 2026-10-18",
    "- Lodging: Juniper House",
    "- Transport: Car-free",
    "- Earliest start: 09:30",
    "- Dietary preference: Vegetarian",
    "- Museum: Harbor Design Museum",
}
decision_start = lines.index("## Confirmed brief") + 1
decision_end = lines.index("## Day 1 — Friday, 2026-10-16")
decision_lines = [line for line in lines[decision_start:decision_end] if line.strip()]
if set(decision_lines) != required_decisions or len(decision_lines) != 7:
    fail("Confirmed brief must contain exactly the seven requested bullets")

day_specs = [
    ("## Day 1 — Friday, 2026-10-16", "2026-10-16"),
    ("## Day 2 — Saturday, 2026-10-17", "2026-10-17"),
    ("## Day 3 — Sunday, 2026-10-18", "2026-10-18"),
]
selected: list[dict[str, str]] = []

for index, (heading, date) in enumerate(day_specs):
    start = lines.index(heading) + 1
    next_heading = (
        day_specs[index + 1][0] if index + 1 < len(day_specs) else "## Cost summary"
    )
    end = lines.index(next_heading)
    block = [line for line in lines[start:end] if line.strip()]
    if len(block) != 5:
        fail(f"{heading} must contain only one three-row itinerary table")
    header = parse_table_row(block[0], 5, heading)
    delimiter = parse_table_row(block[1], 5, heading)
    if header != ["Slot", "Start", "Code", "Plan", "Transit"] or not is_table_delimiter(delimiter):
        fail(f"{heading} has the wrong table header")
    slots: list[str] = []
    for line in block[2:]:
        slot, start_time, code, plan, transit = parse_table_row(line, 5, heading)
        if slot not in {"Morning", "Afternoon", "Evening"} or not re.fullmatch(
            r"[0-2]\d:[0-5]\d", start_time
        ):
            fail(f"malformed itinerary row in {heading}: {line}")
        slots.append(slot)
        option = options.get(code)
        if option is None:
            fail(f"unknown option code {code}")
        if option["date"] != date or option["slot"] != slot:
            fail(f"{code} does not match its scheduled date and slot")
        if start_time != option["start_time"] or start_time < "09:30":
            fail(f"{code} has an altered or too-early start time")
        if plan != option["name"]:
            fail(f"{code} plan name differs from trip_options.csv")
        if transit != option["transit"]:
            fail(f"{code} transit text differs from trip_options.csv")
        selected.append(option)
    if slots != ["Morning", "Afternoon", "Evening"]:
        fail(f"{heading} slots are missing or out of order")

codes = [row["code"] for row in selected]
if len(codes) != 9 or len(set(codes)) != 9:
    fail("the plan must contain nine distinct scheduled options")
if "AB-DESIGN" not in codes:
    fail("the corrected Harbor Design Museum choice is missing")

tag_sets = [set(row["tags"].split(";")) for row in selected]
if sum("museum" in tags for tags in tag_sets) != 1:
    fail("the trip must contain exactly one museum")
if sum("architecture" in tags for tags in tag_sets) < 2:
    fail("the trip must contain at least two architecture options")
for row, tags in zip(selected, tag_sets):
    if row["slot"] == "Evening" and "low-key" not in tags:
        fail(f"evening option {row['code']} is not low-key")
    if "food" in tags and "vegetarian" not in tags:
        fail(f"food option {row['code']} is not vegetarian")

ticketed_by_date = Counter(
    row["date"] for row in selected if row["ticketed"].casefold() == "yes"
)
if any(count > 2 for count in ticketed_by_date.values()):
    fail("a day contains more than two ticketed options")

cost_start = lines.index("## Cost summary") + 1
cost_end = lines.index("## Practical notes")
cost_block = [line for line in lines[cost_start:cost_end] if line.strip()]
if len(cost_block) != 6:
    fail("Cost summary must be the requested four-row table")
cost_header = parse_table_row(cost_block[0], 2, "Cost summary")
cost_delimiter = parse_table_row(cost_block[1], 2, "Cost summary")
if cost_header != ["Item", "Amount"] or not is_table_delimiter(cost_delimiter):
    fail("Cost summary must be the requested four-row table")

amounts: dict[str, Decimal] = {}
items: list[str] = []
for line in cost_block[2:]:
    item, amount = parse_table_row(line, 2, "Cost summary")
    if not re.fullmatch(r"\$\d+\.\d{2}", amount):
        fail(f"malformed cost row: {line}")
    if item in amounts:
        fail(f"duplicate cost item {item}")
    items.append(item)
    amounts[item] = parse_money(amount, item)

activity_total = sum(Decimal(row["cost_per_person"]) for row in selected) * 2
lodging_total = Decimal(costs["lodging_juniper"]["unit_cost"]) * Decimal(
    costs["lodging_juniper"]["quantity"]
)
transit_total = Decimal(costs["local_transit"]["unit_cost"]) * Decimal(
    costs["local_transit"]["quantity"]
)
expected_amounts = {
    "Activities for two": activity_total,
    "Juniper House (2 nights)": lodging_total,
    "Local transit for two": transit_total,
    "Total for two": activity_total + lodging_total + transit_total,
}
if items != list(expected_amounts):
    fail("cost items are missing or out of order")
if amounts != expected_amounts:
    fail("cost summary is incomplete or mathematically incorrect")

note_lines = [line for line in lines[cost_end + 1 :] if line.strip()]
notes: list[str] = []
for line in note_lines:
    match = re.fullmatch(r"[-+*]\s+(.+)", line.strip())
    if not match:
        fail("Practical notes must contain exactly three bullets")
    notes.append(match.group(1))
if len(notes) != 3:
    fail("Practical notes must contain exactly three bullets")

folded_notes = [note.casefold() for note in notes]
car_free_notes = [
    index
    for index, note in enumerate(folded_notes)
    if (
        re.search(r"\b(?:walk(?:ing)?|bus(?:es)?|tram(?:s)?)\b", note)
        and any(
            word in note
            for word in (
                "transfer",
                "connect",
                "route",
                "travel",
                "transport",
                "getting around",
                "itinerary",
            )
        )
    )
    or (
        any(
            phrase in note
            for phrase in ("car-free", "car free", "without a car", "no car")
        )
        and any(word in note for word in ("transfer", "travel", "transport", "route"))
    )
]
low_key_notes = [
    index
    for index, note in enumerate(folded_notes)
    if any(period in note for period in ("evening", "night"))
    and any(
        description in note
        for description in (
            "low-key",
            "low key",
            "relaxed",
            "quiet",
            "unhurried",
            "gentle",
            "calm",
            "restful",
        )
    )
]
food_notes = [
    index
    for index, note in enumerate(folded_notes)
    if "vegetarian" in note
    and any(subject in note for subject in ("meal", "food", "dining"))
    and any(
        exclusion in note
        for exclusion in (
            "excluded",
            "not include",
            "not counted",
            "not covered",
            "omitted",
            "left out",
            "outside the total",
            "separate from the total",
            "doesn't include",
            "don't include",
            "isn't included",
            "aren't included",
        )
    )
]
if len(car_free_notes) != 1:
    fail("Practical notes need a car-free transfer explanation")
if len(low_key_notes) != 1:
    fail("Practical notes need a low-key evening explanation")
if len(food_notes) != 1:
    fail("Practical notes need the vegetarian meal-cost exclusion")
if len({car_free_notes[0], low_key_notes[0], food_notes[0]}) != 3:
    fail("Each Practical notes bullet must address one requested topic")

print("PASS: itinerary satisfies the clarified and corrected travel brief")
