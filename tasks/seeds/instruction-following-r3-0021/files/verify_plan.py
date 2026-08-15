#!/usr/bin/env python3
"""Protected deterministic acceptance checks for the final travel plan."""

from __future__ import annotations

import re
import sys
from pathlib import Path


PLAN = Path("travel_plan.md")
SOURCE = Path("venue_notes.md")


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


if not SOURCE.is_file():
    fail("protected venue_notes.md is missing")
if not PLAN.is_file():
    fail("travel_plan.md has not been created")

text = PLAN.read_text(encoding="utf-8")
source = SOURCE.read_text(encoding="utf-8")
lower = text.lower()

if len(text.split()) < 650:
    fail("the final plan is not substantive enough")
if any(token in lower for token in ("todo", "tbd", "placeholder", "i would plan")):
    fail("the final plan contains a placeholder instead of a completed result")
for heading in (
    "## Recommended itinerary",
    "## Sourced facts used",
    "## Recommendations and rationale",
    "## Uncertainty and checks",
):
    if heading not in text:
        fail(f"missing required section: {heading}")

positions = [text.index(h) for h in (
    "## Recommended itinerary",
    "## Sourced facts used",
    "## Recommendations and rationale",
    "## Uncertainty and checks",
)]
if positions != sorted(positions):
    fail("facts, recommendations, and uncertainty sections are not separated in the requested order")

constraint_match = re.search(r"^##\s+Constraint summary\s*$", text, re.IGNORECASE | re.MULTILINE)
if constraint_match is None:
    fail("missing required opening constraint summary")
if constraint_match.start() > text.index("## Recommended itinerary"):
    fail("the constraint summary must precede the recommended itinerary")
if re.search(r"^##\s+", text[:constraint_match.start()], re.MULTILINE):
    fail("the constraint summary must be the first substantive section")
constraint_summary = text[constraint_match.end():text.index("## Recommended itinerary")]
summary_checks = (
    (r"Maya\s+and\s+Leo", "traveler names"),
    (r"October\s+22(?:\s*[–—-]\s*|\s+through\s+(?:Sunday,?\s+)?October\s+)25,?\s+2026", "trip dates"),
    (r"King Street Station.{0,100}09:40|09:40.{0,100}King Street Station", "arrival details"),
    (r"18:40.{0,100}King Street Station|King Street Station.{0,100}18:40", "departure details"),
    (r"Pine Street Guesthouse.{0,100}(all three nights|three nights)", "lodging details"),
    (r"(bag|luggage).{0,80}(before check-in|arrival).{0,100}(after checkout|checkout)", "bag-hold details"),
    (r"(no car|without a car)", "no-car constraint"),
    (r"(4(?:\.0)? miles|four miles).{0,60}(per day|each day)|(per day|each day).{0,60}(4(?:\.0)? miles|four miles)", "walking cap"),
    (r"(two|2).{0,30}anchor.{0,30}(per day|each day)|(per day|each day).{0,30}(two|2).{0,30}anchor", "anchor cap"),
    (r"public art", "public-art interest"),
    (r"independent book", "independent-bookstore interest"),
    (r"garden", "garden interest"),
    (r"local history", "local-history interest"),
    (r"Saturday.{0,100}10:00.{0,20}12:00.{0,100}(remote call|call)|(remote call|call).{0,60}Saturday.{0,60}10:00.{0,20}12:00", "corrected Saturday call"),
    (r"Friday.{0,80}13:00.{0,20}15:00.{0,100}(available|Elliott Bay)|Elliott Bay.{0,100}Friday.{0,80}13:00.{0,20}15:00", "newly available Friday window"),
)
for pattern, label in summary_checks:
    if re.search(pattern, constraint_summary, re.IGNORECASE | re.DOTALL) is None:
        fail(f"constraint summary omits {label}")


day_specs = (
    ("Thursday, October 22", r"^###\s+[^\n]*(?:Thursday[^\n]*October\s+22|October\s+22[^\n]*Thursday)[^\n]*$"),
    ("Friday, October 23", r"^###\s+[^\n]*(?:Friday[^\n]*October\s+23|October\s+23[^\n]*Friday)[^\n]*$"),
    ("Saturday, October 24", r"^###\s+[^\n]*(?:Saturday[^\n]*October\s+24|October\s+24[^\n]*Saturday)[^\n]*$"),
    ("Sunday, October 25", r"^###\s+[^\n]*(?:Sunday[^\n]*October\s+25|October\s+25[^\n]*Sunday)[^\n]*$"),
)
days: dict[str, str] = {}
itinerary_start = text.index("## Recommended itinerary")
itinerary_end = text.index("## Sourced facts used")
itinerary_block = text[itinerary_start:itinerary_end]
day_matches = []
for heading, pattern in day_specs:
    match = re.search(pattern, itinerary_block, re.IGNORECASE | re.MULTILINE)
    if match is None:
        fail(f"missing separate ### heading for {heading}")
    day_matches.append((heading, match))
if [match.start() for _, match in day_matches] != sorted(match.start() for _, match in day_matches):
    fail("daily itinerary headings are not chronological")
for index, (heading, match) in enumerate(day_matches):
    start = match.end()
    end = day_matches[index + 1][1].start() if index + 1 < len(day_matches) else len(itinerary_block)
    days[heading] = itinerary_block[start:end]


def minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    if hour > 23 or minute > 59:
        fail(f"invalid itinerary time: {value}")
    return hour * 60 + minute


def parse_daily_table(section: str, heading: str) -> list[dict[str, object]]:
    header_re = re.compile(
        r"^\|\s*Time\s*\|\s*Type\s*\|\s*Plan\s*\|\s*Travel / mobility\s*\|\s*Why it fits\s*\|\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    header = header_re.search(section)
    if header is None:
        fail(f"{heading} is missing the required itinerary table columns")

    table_lines: list[str] = []
    for line in section[header.end():].splitlines():
        if not line.strip():
            if table_lines:
                break
            continue
        if not line.lstrip().startswith("|"):
            if table_lines:
                break
            fail(f"{heading} is missing the itinerary table separator")
        table_lines.append(line.strip())

    if not table_lines:
        fail(f"{heading} has an empty itinerary table")
    separator_cells = [cell.strip() for cell in table_lines[0].strip("|").split("|")]
    if len(separator_cells) != 5 or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator_cells):
        fail(f"{heading} is missing a valid itinerary table separator")

    parsed: list[dict[str, object]] = []
    time_re = re.compile(r"^(\d{1,2}:\d{2})(?:\s*[–—-]\s*(\d{1,2}:\d{2}))?$")
    for line in table_lines[1:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 5:
            fail(f"{heading} contains a malformed itinerary row")
        time_text, row_type, plan, mobility, fit = cells
        match = time_re.fullmatch(time_text)
        if match is None:
            fail(f"{heading} contains an invalid itinerary time: {time_text}")
        if row_type.lower() not in {"anchor", "fixed", "meal", "transit"}:
            fail(f"{heading} contains an unsupported Type value: {row_type}")
        if not plan or not mobility or not fit:
            fail(f"{heading} contains an incomplete itinerary row")
        start_min = minutes(match.group(1))
        end_min = minutes(match.group(2)) if match.group(2) else start_min
        if match.group(2) and end_min <= start_min:
            fail(f"{heading} contains a non-positive time range")
        parsed.append({
            "start": start_min,
            "end": end_min,
            "type": row_type.lower(),
            "plan": plan,
            "mobility": mobility,
            "fit": fit,
        })

    if not parsed:
        fail(f"{heading} has no itinerary rows")
    starts = [int(row["start"]) for row in parsed]
    if starts != sorted(starts):
        fail(f"{heading} itinerary rows are not chronological")
    for previous, current in zip(parsed, parsed[1:]):
        if int(current["start"]) < int(previous["end"]):
            fail(f"{heading} contains overlapping itinerary rows")
    return parsed


all_rows: list[dict[str, object]] = []
daily_rows: dict[str, list[dict[str, object]]] = {}
for heading, section in days.items():
    rows = parse_daily_table(section, heading)
    daily_rows[heading] = rows
    for row in rows:
        row["day"] = heading
        all_rows.append(row)
    anchors = [row for row in rows if row["type"] == "anchor"]
    if len(anchors) != 2:
        fail(f"{heading} must contain exactly two Anchor rows")
    if not any(row["type"] == "meal" for row in rows):
        fail(f"{heading} must distinguish at least one meal in its table")
    if not any(row["type"] == "transit" for row in rows):
        fail(f"{heading} must distinguish at least one transit segment in its table")
    if any(int(row["start"]) >= 20 * 60 or int(row["end"]) > 20 * 60 for row in rows):
        fail(f"{heading} schedules something after 20:00")
    if re.search(r"^Transit plan:\s*\S", section, re.IGNORECASE | re.MULTILINE) is None:
        fail(f"{heading} is missing a transit plan")
    if re.search(r"^Vegetarian meal idea:\s*\S", section, re.IGNORECASE | re.MULTILINE) is None:
        fail(f"{heading} is missing a vegetarian meal idea")
    if re.search(r"^Rain fallback:\s*\S", section, re.IGNORECASE | re.MULTILINE) is None:
        fail(f"{heading} is missing a named rain fallback")
    fallback = re.search(r"^Rain fallback:\s*(.+)$", section, re.IGNORECASE | re.MULTILINE)
    indoor_fallbacks = ("Seattle Central Library", "Elliott Bay Book Company", "Frye Art Museum", "MOHAI")
    if fallback is None or not any(name.lower() in fallback.group(1).lower() for name in indoor_fallbacks):
        fail(f"{heading} rain fallback must name an indoor venue from the notes")
    if re.search(r"^Evening plan:\s*.*(unscheduled|open|free).*(20:00|8:00)", section, re.IGNORECASE | re.MULTILINE) is None:
        fail(f"{heading} does not explicitly keep the evening after 20:00 unscheduled")
    walking = re.search(r"^Estimated walking:\s*([0-9]+(?:\.[0-9]+)?)\s*miles\b", section, re.IGNORECASE | re.MULTILINE)
    if walking is None:
        fail(f"{heading} is missing a numeric walking estimate")
    miles = float(walking.group(1))
    if not 0 < miles <= 4.0:
        fail(f"{heading} exceeds the four-mile walking cap")

if len([row for row in all_rows if row["type"] == "anchor"]) != 8:
    fail("the plan must provide eight substantive anchor activities")


# Correction semantics: Friday's old commitment is gone and the same window is used
# for the requested bookstore; Saturday owns the fixed call and nothing conflicts.
thursday_rows = daily_rows["Thursday, October 22"]
friday_rows = daily_rows["Friday, October 23"]
saturday_rows = daily_rows["Saturday, October 24"]
sunday_rows = daily_rows["Sunday, October 25"]

if not any(row["start"] == 13 * 60 and row["end"] == 15 * 60 and row["type"] == "anchor" and "elliott bay book company" in str(row["plan"]).lower() for row in friday_rows):
    fail("Friday 13:00–15:00 must be the Elliott Bay Book Company anchor")
if any(row["type"] == "fixed" and "call" in str(row["plan"]).lower() for row in friday_rows):
    fail("the superseded Friday call was incorrectly retained")
if not any(row["start"] == 10 * 60 and row["end"] == 12 * 60 and row["type"] == "fixed" and "call" in str(row["plan"]).lower() and any(place in str(row["plan"]).lower() for place in ("hotel", "guesthouse")) for row in saturday_rows):
    fail("Saturday 10:00–12:00 must contain the fixed call at the hotel")
if any(row["type"] != "fixed" and row["start"] < 12 * 60 and row["end"] > 10 * 60 for row in saturday_rows):
    fail("a Saturday recommendation conflicts with the corrected call")
if not any(row["type"] == "fixed" and (row["start"] == 9 * 60 + 40 or row["end"] == 9 * 60 + 40) and "king street station" in str(row["plan"]).lower() and "arriv" in str(row["plan"]).lower() for row in thursday_rows):
    fail("Thursday must show the fixed 09:40 arrival at King Street Station")
if not any(row["type"] == "fixed" and (row["start"] == 18 * 60 + 40 or row["end"] == 18 * 60 + 40) and "king street station" in str(row["plan"]).lower() and "depart" in str(row["plan"]).lower() for row in sunday_rows):
    fail("Sunday must show the fixed 18:40 departure from King Street Station")


required_anchors = (
    "Seattle Central Library",
    "Olympic Sculpture Park",
    "Pike Place Market",
    "Elliott Bay Book Company",
    "Seattle Japanese Garden",
    "Frye Art Museum",
    "Museum of History & Industry (MOHAI)",
    "Lake Union Park",
)
daily_anchor_pairs = {
    "Thursday, October 22": ("Seattle Central Library", "Olympic Sculpture Park"),
    "Friday, October 23": ("Pike Place Market", "Elliott Bay Book Company"),
    "Saturday, October 24": ("Seattle Japanese Garden", "Frye Art Museum"),
    "Sunday, October 25": ("MOHAI", "Lake Union Park"),
}
anchor_by_name: dict[str, dict[str, object]] = {}
for day, venues in daily_anchor_pairs.items():
    day_anchors = [row for row in daily_rows[day] if row["type"] == "anchor"]
    day_text = "\n".join(str(row["plan"]) for row in day_anchors).lower()
    for venue in venues:
        if venue.lower() not in day_text:
            fail(f"{day} is missing its required anchor: {venue}")
        matching = [row for row in day_anchors if venue.lower() in str(row["plan"]).lower()]
        if len(matching) != 1:
            fail(f"{day} must schedule {venue} in one Anchor row")
        anchor_by_name[venue] = matching[0]

friday_anchor_text = " ".join(str(row["plan"]) for row in friday_rows if row["type"] == "anchor").lower()
saturday_anchor_text = " ".join(str(row["plan"]) for row in saturday_rows if row["type"] == "anchor").lower()
if not ("pike place market" in friday_anchor_text and "elliott bay book company" in friday_anchor_text):
    fail("Friday must retain one mixed outdoor/covered and one indoor anchor")
if not ("seattle japanese garden" in saturday_anchor_text and "frye art museum" in saturday_anchor_text):
    fail("Saturday must retain one outdoor and one indoor anchor")
indoor_outdoor_rows = (
    (anchor_by_name["Pike Place Market"], "outdoor", "Friday's Pike Place Market anchor must be primarily outdoor"),
    (anchor_by_name["Elliott Bay Book Company"], "indoor", "Friday's Elliott Bay Book Company anchor must be primarily indoor"),
    (anchor_by_name["Seattle Japanese Garden"], "outdoor", "Saturday's Seattle Japanese Garden anchor must be primarily outdoor"),
    (anchor_by_name["Frye Art Museum"], "indoor", "Saturday's Frye Art Museum anchor must be primarily indoor"),
)
for row, classification, message in indoor_outdoor_rows:
    row_text = " ".join(str(row[key]) for key in ("plan", "mobility", "fit")).lower()
    if classification not in row_text:
        fail(message)

# Fixed-hours anchors must actually be placed inside the static source hours.
hours_limits = {
    "Seattle Central Library": (10 * 60, 18 * 60),
    "Elliott Bay Book Company": (10 * 60, 22 * 60),
    "Seattle Japanese Garden": (10 * 60, 17 * 60),
    "Frye Art Museum": (11 * 60, 17 * 60),
    "MOHAI": (10 * 60, 17 * 60),
    "Lake Union Park": (6 * 60, 22 * 60),
}
for venue, (opens, closes) in hours_limits.items():
    row = anchor_by_name[venue]
    if row["start"] < opens or row["end"] > closes:
        fail(f"{venue} is scheduled outside the hours in venue_notes.md")
if anchor_by_name["Seattle Japanese Garden"]["start"] > 16 * 60 + 45:
    fail("Seattle Japanese Garden starts after its listed last admission")

if any(row["type"] == "anchor" and row["end"] > 14 * 60 for row in sunday_rows):
    fail("Sunday sightseeing continues past 14:00")
for row in sunday_rows:
    if row["start"] >= 14 * 60 or row["end"] > 14 * 60:
        row_text = " ".join(str(row[key]) for key in ("plan", "mobility", "fit")).lower()
        if not any(place in row_text for place in ("guesthouse", "king street station", "station transfer")):
            fail("Sunday activity after 14:00 is outside the allowed guesthouse/station transfer")

itinerary_text = text[text.index("## Recommended itinerary"):text.index("## Sourced facts used")]
if re.search(r"\b(rental car|rideshare|taxi)\b", itinerary_text, re.IGNORECASE):
    fail("the plan introduced car travel despite the no-car constraint")
for heading, section in days.items():
    if "transit" not in section.lower() or "walk" not in section.lower():
        fail(f"{heading} does not explain public transit and walking")


# Facts must be explicitly sourced to headings that really exist in the protected notes.
facts_start = text.index("## Sourced facts used")
recommendations_start = text.index("## Recommendations and rationale")
facts = text[facts_start:recommendations_start]
recommendations = text[recommendations_start:text.index("## Uncertainty and checks")]
uncertainty = text[text.index("## Uncertainty and checks"):]

fact_rows: list[list[str]] = []
for line in facts.splitlines():
    if not line.lstrip().startswith("|"):
        continue
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if len(cells) >= 2 and not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
        fact_rows.append(cells)
fact_rows = [row for row in fact_rows if "fact | source" not in " | ".join(row).lower()]

fact_row_by_venue: dict[str, str] = {}
for venue in required_anchors:
    if f"## {venue}" not in source:
        fail(f"protected source lost venue heading: {venue}")
    matching = [row for row in fact_rows if venue.lower() in " | ".join(row).lower()]
    if not matching:
        fail(f"sourced-facts table omits: {venue}")
    cited = [
        row for row in matching
        if any("venue_notes.md" in cell.lower() and venue.lower() in cell.lower() for cell in row)
    ]
    if not cited:
        fail(f"fact for {venue} lacks a venue_notes.md citation to the exact heading")
    fact_row_by_venue[venue] = " | ".join(cited[0])

fact_checks = (
    ("Seattle Central Library", r"Thursday.{0,40}Saturday.{0,40}10:00.{0,30}18:00.{0,120}(admission is free|free admission|free)", "library hours and admission"),
    ("Olympic Sculpture Park", r"(admission is free|free admission|free).{0,180}(30 minutes before sunrise|daily opening)|(30 minutes before sunrise|daily opening).{0,180}(admission is free|free admission|free)", "sculpture park hours and admission"),
    ("Pike Place Market", r"vendor hours vary", "market variable-hours note"),
    ("Elliott Bay Book Company", r"Thursday.{0,40}Saturday.{0,40}10:00.{0,30}22:00", "bookstore hours"),
    ("Seattle Japanese Garden", r"10:00.{0,30}17:00.{0,80}16:45.{0,140}(admission is required|ticket availability)", "garden hours and admission caveat"),
    ("Frye Art Museum", r"(admission is free|free admission|free).{0,160}(Wednesday.{0,40}Sunday.{0,40}11:00.{0,30}17:00|11:00.{0,30}17:00)|(Wednesday.{0,40}Sunday.{0,40}11:00.{0,30}17:00|11:00.{0,30}17:00).{0,160}(admission is free|free admission|free)", "Frye hours and admission"),
    ("Museum of History & Industry (MOHAI)", r"(daily.{0,30}10:00.{0,30}17:00|10:00.{0,30}17:00.{0,40}daily).{0,140}(admission is required|ticket availability)", "MOHAI hours and admission caveat"),
    ("Lake Union Park", r"(06:00.{0,30}22:00|open daily)", "park hours"),
)
for venue, pattern, label in fact_checks:
    if re.search(pattern, fact_row_by_venue[venue], re.IGNORECASE | re.DOTALL) is None:
        fail(f"sourced-facts section does not accurately report {label}")

if len(recommendations.split()) < 60:
    fail("recommendations need a substantive rationale for the itinerary choices")
uncertainty_topics = {
    "hours or availability": ("hours", "availability", "closures"),
    "transit": ("transit", "service", "route"),
    "weather": ("weather", "forecast", "rain"),
    "vegetarian menus or dietary needs": ("vegetarian", "menu", "dietary"),
}
for label, terms in uncertainty_topics.items():
    if not any(term in uncertainty.lower() for term in terms):
        fail(f"uncertainty section does not address {label}")
if not any(term in uncertainty.lower() for term in ("recheck", "verify", "confirm")):
    fail("uncertainty section does not tell the travelers what to recheck")
if not any(term in uncertainty.lower() for term in ("not verified", "uncertain", "may change", "can change")):
    fail("uncertainty is not stated explicitly")
overclaim_re = re.compile(
    r"\b(?:definitely available|confirmed reservation|verified (?:current )?menu|"
    r"(?:availability|reservation|menu|weather|transit)\s+(?:is|are|has been|have been)\s+"
    r"(?:confirmed|verified|guaranteed))\b",
    re.IGNORECASE,
)
for line in text.splitlines():
    for match in overclaim_re.finditer(line):
        prefix = line[max(0, match.start() - 24):match.start()]
        if re.search(r"\b(?:no|not|never|isn't|aren't)\b[^.;:]{0,20}$", prefix, re.IGNORECASE):
            continue
        fail("the plan overclaims live availability or verification")

print("PASS: travel plan preserves state, applies the correction narrowly, and delivers a sourced itinerary")
sys.exit(0)
