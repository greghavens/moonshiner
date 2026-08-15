from __future__ import annotations

import csv
import hashlib
import re
import sys
from pathlib import Path


FIXTURE_ROOT = Path(__file__).resolve().parents[1]
# In an authored seed, fixtures live below files/ while the reference deliverable
# is applied at the seed root. In the trace harness, files/ contents are staged at
# the workspace root. Both layouts are resolved only from this artifact's path.
if FIXTURE_ROOT.name == "files" and (FIXTURE_ROOT.parent / "task.json").is_file():
    WORKSPACE_ROOT = FIXTURE_ROOT.parent
else:
    WORKSPACE_ROOT = FIXTURE_ROOT
DOC = WORKSPACE_ROOT / "housing_shortlist.md"
LISTINGS = FIXTURE_ROOT / "housing" / "listings.csv"
NOTES = FIXTURE_ROOT / "housing" / "neighborhood_notes.md"


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def bullet_items(lines: list[str], section_name: str) -> list[str]:
    """Parse a bullet-only section while allowing indented line wrapping."""
    items: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        if line.startswith("- "):
            item = line[2:].strip()
            if not item:
                fail(f"{section_name} contains an empty bullet")
            items.append(item)
        elif items and (line.startswith("  ") or line.startswith("\t")):
            items[-1] += " " + line.strip()
        else:
            fail(f"{section_name} must contain only Markdown bullets")
    return items


def contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(phrase.lower() in lowered for phrase in phrases)


def arithmetic_values(expression: str, term_count: int, label: str) -> list[int]:
    if expression.count("=") != 1:
        fail(f"{label} must show component arithmetic in the requested order")
    terms, total = expression.split("=", 1)
    components = terms.split("+")
    if len(components) != term_count:
        fail(f"{label} must show component arithmetic in the requested order")
    values: list[int] = []
    for part in [*components, total]:
        numbers = re.findall(r"(?<![\d.])\d[\d,]*(?![\d.])", part)
        if len(numbers) != 1 or re.search(r"[*/]|[-−]\s*\$?\s*\d", part):
            fail(f"{label} must show one amount for each component and total")
        values.append(int(numbers[0].replace(",", "")))
    return values


if not DOC.is_file():
    fail("housing_shortlist.md was not created")
if not LISTINGS.is_file() or not NOTES.is_file():
    fail("the research packet is missing")
expected_hashes = {
    LISTINGS: "1d4b6dfac33db95d549d1fa32a899afc5cbca3ff7e48304863ec276978568b91",
    NOTES: "5c6e06ac1f3500584945433705c8148e0f9f08552931b0f2843855b6aa586730",
}
for source, expected_hash in expected_hashes.items():
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected_hash:
        fail(f"research source was modified: {source.name}")

text = DOC.read_text(encoding="utf-8")
if not text.strip():
    fail("housing_shortlist.md is empty")
if re.search(r"https?://|www\.|\[[^\]]+\]\([^)]+\)|<[^>]+@[^>]+>", text, re.IGNORECASE):
    fail("links are not allowed")
if re.search(r"\bcivic center\b", text, re.IGNORECASE):
    fail("the superseded commute destination appears in the final document")

expected_headings = [
    "# Search Criteria",
    "# Shortlist",
    "# Trade-offs",
    "# Recommendation",
]
headings = [
    line
    for line in text.splitlines()
    if re.match(r" {0,3}#{1,6}(?:\s|$)", line)
]
if headings != expected_headings:
    fail(f"headings must be exactly {expected_headings}, found {headings}")
if any(re.fullmatch(r" {0,3}(?:=+|-+)\s*", line) for line in text.splitlines()):
    fail("setext headings and horizontal rules are not allowed")
if not text.startswith("# Search Criteria\n"):
    fail("content appears before the first required heading")

lines = text.splitlines()
positions = [lines.index(heading) for heading in expected_headings]
sections: dict[str, list[str]] = {}
for i, heading in enumerate(expected_headings):
    end = positions[i + 1] if i + 1 < len(positions) else len(lines)
    sections[heading] = lines[positions[i] + 1 : end]

criteria = bullet_items(sections["# Search Criteria"], "Search Criteria")
if len(criteria) != 7:
    fail("Search Criteria must contain exactly seven bullets")
criteria_text = "\n".join(criteria)
criteria_lower = criteria_text.lower()
scope_pattern = r"\b(?:(?:all(?:\s+10)?|every|each)\s+(?:(?:of\s+)?the\s+)?(?:packet\s+)?listings?|the\s+(?:entire|full)\s+(?:listing\s+)?packet)\b"
availability_pattern = r"(?:on or before|by|no later than)\s+(?:the\s+)?2026-10-15|2026-10-15\s+(?:or earlier|at latest)"
space_pattern = r"(?:at least|minimum(?: of)?)\s+(?:2|two)\s+bedrooms|(?:2|two)\s+or more bedrooms"
pet_pattern = r"dogs?\s+(?:are\s+)?allowed|allows?\s+dogs?|dog-friendly"
commute_limit_pattern = r"(?:no more than|at most|within)\s+35\s*(?:-| )?minutes?|35\s*(?:-| )?minutes?\s+(?:or less|maximum|max)|[≤≦]\s*35\s*(?:-| )?minutes?"
if not re.search(scope_pattern, criteria_lower):
    fail("Search Criteria does not say to screen every listing")
if not re.search(availability_pattern, criteria_lower):
    fail("Search Criteria does not retain the availability cutoff")
if not re.search(space_pattern, criteria_lower):
    fail("Search Criteria does not retain the minimum bedroom count")
if not re.search(pet_pattern, criteria_lower):
    fail("Search Criteria does not retain the dog requirement")
if not re.search(r"in[- ]unit laundry", criteria_lower):
    fail("Search Criteria does not retain the laundry requirement")
if "harbor tech campus" not in criteria_lower or not re.search(commute_limit_pattern, criteria_lower):
    fail("Search Criteria does not retain the corrected commute threshold")
formula_terms = (
    "monthly rent",
    "parking",
    "pet rent",
    "estimated utilities",
    "first month's rent",
    "security deposit",
    "one-time pet fee",
    "shorter grocery walk",
    "lower base rent",
)
formula_bullets = [
    bullet.lower()
    for bullet in criteria
    if all(phrase in bullet.lower() for phrase in formula_terms)
]
if len(formula_bullets) != 1:
    fail("one Search Criteria bullet must contain the ranking, tie-breakers, and both formulas")
formula_bullet = formula_bullets[0]
if "lowest recurring monthly total" not in formula_bullet:
    fail("the ranking/formulas bullet does not rank by lowest recurring monthly total")
if not re.search(
    r"monthly rent\s*\+\s*parking\s*\+\s*pet rent\s*\+\s*estimated utilities",
    formula_bullet,
):
    fail("the ranking/formulas bullet has an incorrect recurring-cost formula")
if not re.search(
    r"first month's rent\s*\+\s*security deposit\s*\+\s*one-time pet fee",
    formula_bullet,
):
    fail("the ranking/formulas bullet has an incorrect move-in formula")
if not re.search(
    r"comparison.{0,80}(?:not|no).{0,30}budget|(?:not|no).{0,30}budget.{0,80}comparison",
    formula_bullet,
):
    fail("the ranking/formulas bullet must say that the costs are comparisons, not budget limits")

criteria_items_lower = [bullet.lower() for bullet in criteria]
criteria_category_matches = [
    [
        index
        for index, bullet in enumerate(criteria_items_lower)
        if re.search(scope_pattern, bullet)
    ],
    [index for index, bullet in enumerate(criteria_items_lower) if re.search(availability_pattern, bullet)],
    [
        index
        for index, bullet in enumerate(criteria_items_lower)
        if re.search(space_pattern, bullet)
    ],
    [
        index
        for index, bullet in enumerate(criteria_items_lower)
        if re.search(pet_pattern, bullet)
    ],
    [index for index, bullet in enumerate(criteria_items_lower) if re.search(r"in[- ]unit laundry", bullet)],
    [
        index
        for index, bullet in enumerate(criteria_items_lower)
        if "harbor tech campus" in bullet
        and re.search(commute_limit_pattern, bullet)
    ],
    [index for index, bullet in enumerate(criteria_items_lower) if bullet == formula_bullet],
]


def categories_have_distinct_bullets(category_index: int, used: set[int]) -> bool:
    if category_index == len(criteria_category_matches):
        return True
    return any(
        index not in used and categories_have_distinct_bullets(category_index + 1, used | {index})
        for index in criteria_category_matches[category_index]
    )


if not categories_have_distinct_bullets(0, set()):
    fail("Search Criteria must use one distinct bullet for each of the seven requested categories")

shortlist_lines = [line for line in sections["# Shortlist"] if line.strip()]
table_lines = [line for line in shortlist_lines if line.startswith("|")]
if len(table_lines) != 5 or len(shortlist_lines) != 5:
    fail("Shortlist must contain only one header, one separator, and three data rows")

def cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


expected_columns = [
    "Rank",
    "Home",
    "Neighborhood",
    "Bed / bath",
    "Available",
    "Commute",
    "Recurring monthly total",
    "Move-in cash",
    "Outdoor space",
]
if cells(table_lines[0]) != expected_columns:
    fail("Shortlist table columns are incorrect")
separator_cells = cells(table_lines[1])
if len(separator_cells) != len(expected_columns) or any(
    not re.fullmatch(r":?-{3,}:?", cell) for cell in separator_cells
):
    fail("Shortlist table separator is malformed")

with LISTINGS.open(newline="", encoding="utf-8") as handle:
    records = list(csv.DictReader(handle))
if len(records) != 10:
    fail("the listing packet was modified")

qualifying = []
for row in records:
    if not (
        row["available_date"] <= "2026-10-15"
        and int(row["bedrooms"]) >= 2
        and row["dogs_allowed"] == "yes"
        and row["in_unit_laundry"] == "yes"
        and int(row["peak_transit_harbor_tech_min"]) <= 35
    ):
        continue
    recurring = sum(
        int(row[key])
        for key in (
            "monthly_rent",
            "parking_monthly",
            "pet_rent_monthly",
            "estimated_utilities_monthly",
        )
    )
    move_in = sum(
        int(row[key])
        for key in ("monthly_rent", "security_deposit", "one_time_pet_fee")
    )
    qualifying.append((recurring, int(row["grocery_walk_min"]), int(row["monthly_rent"]), row, move_in))

qualifying.sort(key=lambda item: item[:3])
expected_rows = qualifying[:3]
actual_rows = [cells(line) for line in table_lines[2:]]
if [item[3]["listing_id"] for item in expected_rows] != ["L-106", "L-109", "L-102"]:
    fail("internal fixture ranking is inconsistent")

for rank, (actual, expected) in enumerate(zip(actual_rows, expected_rows), start=1):
    recurring, _, _, row, move_in = expected
    if len(actual) != len(expected_columns):
        fail(f"Shortlist row {rank} has the wrong number of cells")
    exact_values = {
        0: str(rank),
        2: row["neighborhood"],
        4: row["available_date"],
        8: row["outdoor_space"],
    }
    for index, expected_value in exact_values.items():
        if actual[index] != expected_value:
            fail(f"Shortlist row {rank} has incorrect {expected_columns[index]}")
    home_pattern = re.escape(row["property"]) + rf"(?:\s*(?:\({row['listing_id']}\)|[-–—:]\s*{row['listing_id']}))?"
    if not re.fullmatch(home_pattern, actual[1]):
        fail(f"Shortlist row {rank} has incorrect Home")
    bed_bath_pattern = (
        rf"{re.escape(row['bedrooms'])}(?:\s*(?:beds?|bedrooms?|bd))?\s*/\s*"
        rf"{re.escape(row['bathrooms'])}(?:\s*(?:baths?|bathrooms?|ba))?"
    )
    if not re.fullmatch(bed_bath_pattern, actual[3], re.IGNORECASE):
        fail(f"Shortlist row {rank} has incorrect Bed / bath")
    commute_minutes = re.escape(row["peak_transit_harbor_tech_min"])
    if not re.search(rf"\b{commute_minutes}\s*(?:min(?:ute)?s?)?\b", actual[5], re.IGNORECASE):
        fail(f"Shortlist row {rank} has incorrect commute minutes")
    if "harbor tech campus" not in actual[5].lower():
        fail(f"Shortlist row {rank} has incorrect commute destination")
    recurring_components = [
        int(row["monthly_rent"]),
        int(row["parking_monthly"]),
        int(row["pet_rent_monthly"]),
        int(row["estimated_utilities_monthly"]),
        recurring,
    ]
    move_in_components = [
        int(row["monthly_rent"]),
        int(row["security_deposit"]),
        int(row["one_time_pet_fee"]),
        move_in,
    ]
    if arithmetic_values(actual[6], 4, f"Shortlist row {rank} recurring monthly total") != recurring_components:
        fail(f"Shortlist row {rank} has incorrect recurring-cost arithmetic")
    if arithmetic_values(actual[7], 3, f"Shortlist row {rank} move-in cash") != move_in_components:
        fail(f"Shortlist row {rank} has incorrect move-in arithmetic")

tradeoffs = bullet_items(sections["# Trade-offs"], "Trade-offs")
if len(tradeoffs) != 3:
    fail("Trade-offs must contain exactly three bullets")
tradeoff_requirements = {
    "L-106": {
        "property": "Foundry House",
        "note": "Foundry District note",
        "listing_facts": (
            "2 / 1", "2026-10-05", "35 min", "$2,665", "Juliet balcony",
            "east-edge", "main bedroom", "rail corridor", "six-minute grocery",
        ),
        "note_facts": (
            "produce market", "freight train", "eastern blocks", "evening visits",
            "frequency", "indoor noise", "not measured",
        ),
    },
    "L-109": {
        "property": "Juniper Place",
        "note": "Juniper Hills note",
        "listing_facts": (
            "2 / 2", "2026-09-25", "29 min", "$2,685", "private patio",
            "ground-floor", "uncovered", "12-minute grocery", "12 minute grocery",
        ),
        "note_facts": (
            "steep", "uphill", "final three blocks", "quieter side streets",
            "after 21:00", "sound measurements", "not measured",
        ),
    },
    "L-102": {
        "property": "Beacon Flats",
        "note": "Beacon North note",
        "listing_facts": (
            "2 / 1.5", "2026-10-10", "31 min", "$2,695", "shared courtyard",
            "courtyard-facing", "bicycle storage", "four-minute grocery", "4-minute grocery",
        ),
        "note_facts": (
            "level route", "restaurants", "Beacon Avenue", "after 22:00",
            "street noise", "avenue side",
        ),
    },
}
matched_bullets: set[int] = set()
for listing_id, requirements in tradeoff_requirements.items():
    matches = [
        index
        for index, bullet in enumerate(tradeoffs)
        if listing_id in bullet and requirements["property"].lower() in bullet.lower()
    ]
    if len(matches) != 1 or matches[0] in matched_bullets:
        fail(f"Trade-offs must contain one distinct bullet for {requirements['property']} ({listing_id})")
    matched_bullets.add(matches[0])
    bullet = tradeoffs[matches[0]]
    if requirements["note"].lower() not in bullet.lower():
        fail(f"{listing_id} trade-off does not cite the matching neighborhood note")
    if not contains_any(bullet, requirements["listing_facts"]):
        fail(f"{listing_id} trade-off does not discuss a listing fact")
    if not contains_any(bullet, requirements["note_facts"]):
        fail(f"{listing_id} trade-off does not discuss neighborhood-note evidence")

recommendations = bullet_items(sections["# Recommendation"], "Recommendation")
if len(recommendations) != 3:
    fail("Recommendation must contain exactly three bullets")
shortlisted_homes = {
    item[3]["listing_id"]: item[3]["property"]
    for item in expected_rows
}


def recommendation_choice(bullet: str, label: str) -> str:
    if label.lower() not in bullet.lower():
        fail(f"the {label.lower()} recommendation is missing its label")
    matches = [
        listing_id
        for listing_id, property_name in shortlisted_homes.items()
        if property_name.lower() in bullet.lower()
    ]
    if len(matches) != 1:
        fail(f"the {label.lower()} must identify exactly one shortlisted home")
    return matches[0]


primary_id = recommendation_choice(recommendations[0], "Primary")
runner_up_id = recommendation_choice(recommendations[1], "Runner-up")
if primary_id == runner_up_id:
    fail("the primary and runner-up recommendations must be different homes")
checks = recommendations[2].lower()
if "before signing" not in checks:
    fail("the final recommendation bullet must give checks to make before signing")
check_topics = (
    ("availability",),
    ("fee", "cost"),
    ("utilit",),
    ("pet", "dog"),
    ("transit", "commute", "timing"),
    ("noise", "sound", "freight", "rail"),
    ("balcony", "outdoor"),
)
if sum(contains_any(checks, topic) for topic in check_topics) < 2:
    fail("the before-signing bullet must include at least two packet-grounded checks")

print("PASS: housing_shortlist.md satisfies the corrected multi-turn requirements")
