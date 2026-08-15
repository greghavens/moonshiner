#!/usr/bin/env python3
from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ITINERARY = ROOT / "itinerary.md"
OPTIONS_FILE = ROOT / "trip_options.csv"
COSTS_FILE = ROOT / "trip_costs.csv"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_money(value: str, label: str) -> Decimal:
    match = re.fullmatch(r"\$(\d+\.\d{2})", value.strip())
    if not match:
        fail(f"{label} must be dollars with two decimal places")
    return Decimal(match.group(1))


if not ITINERARY.is_file():
    fail("itinerary.md is missing")
text = ITINERARY.read_text(encoding="utf-8")
if not text.endswith("\n"):
    fail("itinerary.md must end with a newline")
for stale in ("Sunday", "2026-10-18", "Hillcrest Lookout Tram Ride"):
    if stale.casefold() in text.casefold():
        fail(f"the removed Sunday scope is still present: {stale}")

with OPTIONS_FILE.open(newline="", encoding="utf-8") as handle:
    option_rows = list(csv.DictReader(handle))
options = {row["code"]: row for row in option_rows}
if len(options) != len(option_rows):
    fail("trip_options.csv contains duplicate codes")
with COSTS_FILE.open(newline="", encoding="utf-8") as handle:
    costs = {row["key"]: Decimal(row["unit_cost"]) for row in csv.DictReader(handle)}

expected_headings = [
    "# Larkspur Family Trip",
    "## Confirmed brief",
    "## Day 1 — Friday, 2026-10-16",
    "## Day 2 — Saturday, 2026-10-17",
    "## Cost summary",
    "## Practical notes",
]
headings = [line for line in text.splitlines() if line.startswith("#")]
if headings != expected_headings:
    fail("headings are missing, extra, or out of order")

lines = text.splitlines()
expected_brief = [
    "- Travelers: 2 adults and 1 child (age 10)",
    "- Dates: 2026-10-16 through 2026-10-17",
    "- Lodging: Juniper Station Hotel (1 night)",
    "- Mobility: Car-free and step-free",
    "- Earliest start: 09:45",
    "- Dinners: Vegetarian and peanut-aware",
    "- Friday anchor: Larkspur Textile Museum",
    "- Saturday anchor: Glasshouse Garden Workshop",
]
brief_start = lines.index("## Confirmed brief") + 1
brief_end = lines.index("## Day 1 — Friday, 2026-10-16")
brief = [line for line in lines[brief_start:brief_end] if line.strip()]
if brief != expected_brief:
    fail("Confirmed brief must contain exactly the eight retained bullets in order")

day_specs = [
    ("## Day 1 — Friday, 2026-10-16", "2026-10-16"),
    ("## Day 2 — Saturday, 2026-10-17", "2026-10-17"),
]
row_pattern = re.compile(
    r"^\| (Morning|Afternoon|Evening) \| ([0-2]\d:[0-5]\d) \| "
    r"([A-Z]{2}-[A-Z]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$"
)
selected: list[dict[str, str]] = []
selected_by_date: dict[str, list[dict[str, str]]] = {}
for index, (heading, date) in enumerate(day_specs):
    start = lines.index(heading) + 1
    next_heading = day_specs[index + 1][0] if index + 1 < len(day_specs) else "## Cost summary"
    end = lines.index(next_heading)
    block = [line for line in lines[start:end] if line.strip()]
    if len(block) != 5:
        fail(f"{heading} must contain only one three-row itinerary table")
    if block[0] != "| Slot | Start | Code | Plan | District | Transit | Access |":
        fail(f"{heading} has the wrong table header")
    if block[1] != "|---|---|---|---|---|---|---|":
        fail(f"{heading} has the wrong table separator")
    day_options: list[dict[str, str]] = []
    seen_slots: list[str] = []
    for line in block[2:]:
        match = row_pattern.fullmatch(line)
        if not match:
            fail(f"malformed itinerary row in {heading}: {line}")
        slot, start_time, code, plan, district, transit, access = (
            part.strip() for part in match.groups()
        )
        seen_slots.append(slot)
        option = options.get(code)
        if option is None:
            fail(f"unknown option code {code}")
        if option["date"] != date or option["slot"] != slot:
            fail(f"{code} does not match its scheduled date and slot")
        exact_fields = {
            "start": start_time,
            "name": plan,
            "district": district,
            "transit": transit,
            "access_note": access,
        }
        for field, shown in exact_fields.items():
            if option[field] != shown:
                fail(f"{code} {field} differs from trip_options.csv")
        if start_time < "09:45":
            fail(f"{code} starts before the retained 09:45 limit")
        if option["step_free"].casefold() != "yes":
            fail(f"{code} is not step-free")
        if option["kid_friendly"].casefold() != "yes":
            fail(f"{code} is not kid-friendly")
        day_options.append(option)
        selected.append(option)
    if seen_slots != ["Morning", "Afternoon", "Evening"]:
        fail(f"{heading} slots are missing or out of order")
    selected_by_date[date] = day_options

codes = [row["code"] for row in selected]
if len(codes) != 6 or len(set(codes)) != 6:
    fail("the corrected plan must contain six distinct scheduled options")
if "LK-TEXTILE" not in codes or "LK-GLASS" not in codes:
    fail("one or both retained anchor choices are missing")
tags = [set(row["tags"].split(";")) for row in selected]
if sum("museum" in item for item in tags) != 1:
    fail("the corrected trip must contain exactly one museum")
for date, day_options in selected_by_date.items():
    day_tags = [set(row["tags"].split(";")) for row in day_options]
    if not any("indoor" in item for item in day_tags):
        fail(f"{date} does not contain an indoor option")
    if sum(row["ticketed"].casefold() == "yes" for row in day_options) > 2:
        fail(f"{date} contains more than two ticketed options")
    evening = day_options[2]
    evening_tags = set(evening["tags"].split(";"))
    if not {"food", "vegetarian", "peanut-aware"} <= evening_tags:
        fail(f"{date} evening choice is not a vegetarian, peanut-aware dinner")

cost_start = lines.index("## Cost summary") + 1
cost_end = lines.index("## Practical notes")
cost_block = [line for line in lines[cost_start:cost_end] if line.strip()]
if len(cost_block) != 6:
    fail("Cost summary must contain its header and four required rows")
if cost_block[:2] != ["| Item | Amount |", "|---|---:|"]:
    fail("Cost summary has the wrong table header")
amounts: dict[str, Decimal] = {}
item_order: list[str] = []
for line in cost_block[2:]:
    match = re.fullmatch(r"\| ([^|]+) \| (\$\d+\.\d{2}) \|", line)
    if not match:
        fail(f"malformed cost row: {line}")
    item, amount = (part.strip() for part in match.groups())
    if item in amounts:
        fail(f"duplicate cost item: {item}")
    item_order.append(item)
    amounts[item] = parse_money(amount, item)

activities = sum(
    Decimal(row["adult_price"]) * 2 + Decimal(row["child_price"])
    for row in selected
)
lodging = costs["lodging_juniper"]
local_transit = (costs["transit_adult_day"] * 2 + costs["transit_child_day"]) * 2
expected_amounts = {
    "Activities — 2 adults + 1 child": activities,
    "Lodging — Juniper Station Hotel (1 night)": lodging,
    "Local transit — 2 days": local_transit,
    "Total for 3 travelers": activities + lodging + local_transit,
}
if item_order != list(expected_amounts):
    fail("cost items are missing or not in the requested order")
if amounts != expected_amounts:
    fail("cost summary is incomplete or mathematically incorrect")

notes = [line for line in lines[cost_end + 1 :] if line.strip()]
if len(notes) != 3 or any(not line.startswith("- ") for line in notes):
    fail("Practical notes must contain exactly three bullets")
car_free = notes[0].casefold()
access = notes[1].casefold()
food = notes[2].casefold()
if "car-free" not in car_free or not any(term in car_free for term in ("tram", "bus", "walk")):
    fail("the first Practical note must explain the car-free transfers")
if "step-free" not in access or not any(term in access for term in ("level", "elevator", "ramp", "accessible")):
    fail("the second Practical note must preserve step-free access details")
if not all(term in food for term in ("vegetarian", "peanut-aware", "meal costs", "excluded")):
    fail("the third Practical note must retain the dinner and meal-cost constraints")

print("PASS: itinerary satisfies the corrected two-day brief")
