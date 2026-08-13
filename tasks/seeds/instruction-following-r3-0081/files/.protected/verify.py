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
OPTIONS = ROOT / "travel_options.csv"
COSTS = ROOT / "trip_costs.csv"


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def money(value: str, context: str) -> Decimal:
    match = re.fullmatch(r"\$(\d+\.\d{2})", value.strip())
    if not match:
        fail(f"{context} must be dollars with two decimal places")
    return Decimal(match.group(1))


if not ITINERARY.is_file():
    fail("itinerary.md is missing")

text = ITINERARY.read_text(encoding="utf-8")
if not text.endswith("\n"):
    fail("itinerary.md must end with a newline")
if "northshore" in text.casefold():
    fail("the superseded Northshore excursion is still present")

with OPTIONS.open(newline="", encoding="utf-8") as handle:
    option_rows = list(csv.DictReader(handle))
options = {row["code"]: row for row in option_rows}
if len(options) != len(option_rows):
    fail("travel_options.csv contains duplicate codes")

with COSTS.open(newline="", encoding="utf-8") as handle:
    costs = {row["key"]: row for row in csv.DictReader(handle)}

headings = [line for line in text.splitlines() if line.startswith("#")]
expected_headings = [
    "# Harborport Trip Plan",
    "## Confirmed decisions",
    "## Day 1 — 2026-09-18 — Harborport",
    "## Day 2 — 2026-09-19 — Harborport",
    "## Day 3 — 2026-09-20 — Cedar Island",
    "## Day 4 — 2026-09-21 — Harborport",
    "## Cost summary",
    "## Practical notes",
]
if headings != expected_headings:
    fail("headings are missing, extra, or out of order")

required_decisions = {
    "- Travelers: 2 adults",
    "- Lodging: Tideglass Inn",
    "- Transport: Car-free",
    "- Earliest daily start: 09:30",
    "- Dietary preference: Pescatarian",
    "- Day 2 anchors: Beacon Point Lighthouse Walk; Harborport Art Museum",
    "- Day 3 excursion: Cedar Island",
}
lines = text.splitlines()
missing_decisions = sorted(required_decisions - set(lines))
if missing_decisions:
    fail("confirmed decisions are incomplete: " + "; ".join(missing_decisions))
decision_start = lines.index("## Confirmed decisions") + 1
decision_end = lines.index("## Day 1 — 2026-09-18 — Harborport")
decision_lines = [line for line in lines[decision_start:decision_end] if line.strip()]
if set(decision_lines) != required_decisions or len(decision_lines) != 7:
    fail("Confirmed decisions must contain exactly the seven requested bullets")

day_specs = [
    ("## Day 1 — 2026-09-18 — Harborport", "2026-09-18", "Harborport"),
    ("## Day 2 — 2026-09-19 — Harborport", "2026-09-19", "Harborport"),
    ("## Day 3 — 2026-09-20 — Cedar Island", "2026-09-20", "Cedar Island"),
    ("## Day 4 — 2026-09-21 — Harborport", "2026-09-21", "Harborport"),
]
row_pattern = re.compile(
    r"^\| (Morning|Afternoon|Evening) \| ([0-2]\d:[0-5]\d) \| "
    r"([A-Z]{2}-[A-Z]+) \| ([^|]+) \| ([^|]+) \|$"
)
selected: list[dict[str, str]] = []

for index, (heading, date, area) in enumerate(day_specs):
    start = lines.index(heading) + 1
    next_heading = day_specs[index + 1][0] if index + 1 < len(day_specs) else "## Cost summary"
    end = lines.index(next_heading)
    block = [line for line in lines[start:end] if line.strip()]
    if len(block) != 5:
        fail(f"{heading} must contain only one three-row itinerary table")
    if block[0] != "| Slot | Start | Code | Plan | Transit |":
        fail(f"{heading} has the wrong table header")
    if block[1] != "|---|---|---|---|---|":
        fail(f"{heading} has the wrong table separator")
    seen_slots = []
    for line in block[2:]:
        match = row_pattern.fullmatch(line)
        if not match:
            fail(f"malformed itinerary row in {heading}: {line}")
        slot, start_time, code, plan, transit = (part.strip() for part in match.groups())
        seen_slots.append(slot)
        if start_time < "09:30":
            fail(f"{code} starts before 09:30")
        option = options.get(code)
        if option is None:
            fail(f"unknown option code {code}")
        if option["date"] != date or option["area"] != area or option["slot"] != slot:
            fail(f"{code} does not match its scheduled date, area, and slot")
        if plan != option["name"]:
            fail(f"{code} plan name differs from travel_options.csv")
        if transit != option["transit"]:
            fail(f"{code} transit text differs from travel_options.csv")
        selected.append(option)
    if seen_slots != ["Morning", "Afternoon", "Evening"]:
        fail(f"{heading} slots are missing or out of order")

codes = [row["code"] for row in selected]
if len(codes) != 12 or len(set(codes)) != 12:
    fail("the plan must contain 12 distinct scheduled options")
if "HP-LIGHT" not in codes or "HP-ART" not in codes:
    fail("the retained day-2 lighthouse and art-museum choices are missing")
if "CI-ARRIVAL" not in codes:
    fail("the corrected Cedar Island ferry arrival is missing")

tags = [set(row["tags"].split(";")) for row in selected]
if sum("lighthouse" in item for item in tags) != 1:
    fail("the trip must contain exactly one lighthouse option")
if sum("museum" in item for item in tags) != 1:
    fail("the trip must contain exactly one museum option")
for row, item_tags in zip(selected, tags):
    if "food" in item_tags and "pescatarian" not in item_tags:
        fail(f"food option {row['code']} is not pescatarian")

ticketed_by_date = Counter(
    row["date"] for row in selected if row["ticketed"].casefold() == "yes"
)
if any(count > 2 for count in ticketed_by_date.values()):
    fail("a day contains more than two ticketed options")

cost_start = lines.index("## Cost summary") + 1
cost_end = lines.index("## Practical notes")
cost_block = [line for line in lines[cost_start:cost_end] if line.strip()]
if len(cost_block) != 7:
    fail("Cost summary must contain its header and five required rows")
if cost_block[:2] != ["| Item | Amount |", "|---|---:|"]:
    fail("Cost summary has the wrong table header")

amounts: dict[str, Decimal] = {}
cost_items: list[str] = []
for line in cost_block[2:]:
    match = re.fullmatch(r"\| ([^|]+) \| (\$\d+\.\d{2}) \|", line)
    if not match:
        fail(f"malformed cost row: {line}")
    item, amount = (part.strip() for part in match.groups())
    if item in amounts:
        fail(f"duplicate cost item {item}")
    cost_items.append(item)
    amounts[item] = money(amount, item)

activity_total = sum(Decimal(row["cost_per_person"]) for row in selected) * 2
lodging_total = Decimal(costs["lodging_tideglass"]["unit_cost"]) * Decimal(costs["lodging_tideglass"]["quantity"])
transit_total = Decimal(costs["local_transit"]["unit_cost"]) * Decimal(costs["local_transit"]["quantity"])
excursion_total = Decimal(costs["excursion_cedar"]["unit_cost"]) * Decimal(costs["excursion_cedar"]["quantity"])
expected_amounts = {
    "Activities for two": activity_total,
    "Lodging — Tideglass Inn (3 nights)": lodging_total,
    "Local transit for two": transit_total,
    "Cedar Island ferry for two": excursion_total,
    "Total for two": activity_total + lodging_total + transit_total + excursion_total,
}
if cost_items != list(expected_amounts):
    fail("cost items are missing or out of the requested order")
if amounts != expected_amounts:
    fail("cost summary is incomplete or mathematically incorrect")

notes = [line for line in lines[cost_end + 1:] if line.strip()]
if len(notes) != 2 or any(not line.startswith("- ") for line in notes):
    fail("Practical notes must contain exactly two bullets")
notes_text = " ".join(notes).casefold()
if "car-free" not in notes_text or not any(word in notes_text for word in ("walk", "bus", "tram", "ferry")):
    fail("Practical notes need a sourced car-free transfer note")
if not all(phrase in notes_text for phrase in ("pescatarian", "meal costs", "excluded")):
    fail("Practical notes need the pescatarian meal-cost exclusion")

print("PASS: itinerary satisfies the corrected trip brief")
