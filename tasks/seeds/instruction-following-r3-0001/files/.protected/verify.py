#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "travel_plan.md"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def require(pattern: str, value: str, message: str) -> None:
    if not re.search(pattern, value, flags=re.IGNORECASE | re.DOTALL):
        fail(message)


if not PLAN.is_file():
    fail("travel_plan.md is missing")

text = PLAN.read_text(encoding="utf-8")
if not text.endswith("\n"):
    fail("travel_plan.md must end with a newline")

expected_headings = [
    "# Santa Fe Weekend Plan",
    "## Trip Snapshot",
    "## Booking Checklist",
    "## Friday — Arrival and Railyard",
    "## Saturday — Museums and Canyon Road",
    "## Sunday — Plaza and Departure",
    "## Practical Notes",
]
headings = re.findall(r"(?m)^#{1,6} .+$", text)
if headings != expected_headings:
    fail(f"headings differ from the required ordered set: {headings!r}")

words = re.findall(r"\b[\w'’–.-]+\b", text, flags=re.UNICODE)
if not 350 <= len(words) <= 550:
    fail(f"plan must contain 350–550 words; found {len(words)}")


def section(name: str, next_name: str | None) -> str:
    start = text.index(name) + len(name)
    end = text.index(next_name, start) if next_name else len(text)
    return text[start:end]


snapshot = section("## Trip Snapshot", "## Booking Checklist")
checklist = section("## Booking Checklist", "## Friday — Arrival and Railyard")
friday = section("## Friday — Arrival and Railyard", "## Saturday — Museums and Canyon Road")
saturday = section("## Saturday — Museums and Canyon Road", "## Sunday — Plaza and Departure")
sunday = section("## Sunday — Plaza and Departure", "## Practical Notes")
practical = section("## Practical Notes", None)

require(r"two adults", text, "the plan must retain the two-adult party")
require(r"(?:Friday,? )?October 8(?:.{0,80}(?:Sunday,? )?October 10|\s*[–—-]\s*10),? 2027",
        text, "the plan must give the retained October 8–10, 2027 dates")
require(r"Casa Railyard", text, "the plan must retain Casa Railyard")
require(r"two(?: nights|[ -]night)", text, "the plan must retain the two-night stay")
require(r"Northern Star", text, "the plan must retain train travel")
require(r"(?:no|without).{0,20}(?:rental )?car", text,
        "the plan must state the retained no-car plan")

checklist_items = [line.strip() for line in checklist.splitlines()
                   if re.match(r"^\s*\d+[.)]\s+", line)]
if len(checklist_items) != 4:
    fail("Booking Checklist must contain exactly four ordered Markdown items")
ordered_checks = (
    r"(?=.*round[- ]trip)(?=.*(?:Northern Star|train))(?=.*seats?)",
    r"Casa Railyard",
    r"(?=.*Georgia O['’]Keeffe Museum)(?=.*timed entry)",
    r"(?=.*reserv)(?=.*Terra & Sage)(?=.*Juniper Kitchen)",
)
for index, (item, pattern) in enumerate(zip(checklist_items, ordered_checks), start=1):
    if not re.search(pattern, item, flags=re.IGNORECASE):
        fail(f"Booking Checklist item {index} is missing or out of order")


def table_rows(section_text: str, label: str) -> list[list[str]]:
    table_blocks: list[list[str]] = []
    current: list[str] = []
    for line in section_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            current.append(stripped)
        elif current:
            table_blocks.append(current)
            current = []
    if current:
        table_blocks.append(current)
    if len(table_blocks) != 1:
        fail(f"{label} must contain exactly one Markdown table")

    table_lines = table_blocks[0]
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in table_lines]
    if not rows or [cell.casefold() for cell in rows[0]] != ["time", "plan", "transit"]:
        fail(f"{label} must contain a Markdown table headed Time, Plan, Transit")
    if len(rows) < 4 or len(rows[1]) != 3 or not all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]):
        fail(f"{label} table is missing a valid alignment row or substantive entries")
    if any(len(row) != 3 for row in rows):
        fail(f"{label} table rows must each contain exactly three cells")
    if any(not cell for row in rows[2:] for cell in row):
        fail(f"{label} table entries must each give a time, plan, and transit method")
    return rows[2:]


friday_rows = table_rows(friday, "Friday")
saturday_rows = table_rows(saturday, "Saturday")
sunday_rows = table_rows(sunday, "Sunday")
if (len(friday_rows), len(saturday_rows), len(sunday_rows)) != (7, 6, 8):
    fail("daily tables must include all packet stops, meals, rest blocks, and transfers")

def normalized_time(value: str) -> str:
    compact = re.sub(
        r"\s+", "",
        value.casefold().replace("–", "-").replace("—", "-").replace(".", ""),
    )
    # Accept an explicitly repeated meridiem in ranges (for example,
    # "3:00 p.m.–5:30 p.m.") as equivalent to the packet's compact form.
    return re.sub(r"(?<=\d)(?:am|pm)-(?=\d)", "-", compact)


def check_schedule(
        rows: list[list[str]], label: str,
        expected: tuple[tuple[str, str, str], ...]) -> None:
    actual_times = [normalized_time(row[0]) for row in rows]
    expected_times = [normalized_time(item[0]) for item in expected]
    if actual_times != expected_times:
        fail(f"{label} entries must retain the packet times in chronological order")
    for row_number, (row, (_, plan_pattern, transit_pattern)) in enumerate(
            zip(rows, expected), start=1):
        if not re.search(plan_pattern, row[1], flags=re.IGNORECASE):
            fail(f"{label} row {row_number} is missing the packet activity or meal")
        if not re.search(transit_pattern, row[2], flags=re.IGNORECASE):
            fail(f"{label} row {row_number} is missing the packet transit method")


check_schedule(friday_rows, "Friday", (
    ("10:20 a.m.", r"arriv.{0,40}Santa Fe Depot|Santa Fe Depot.{0,40}arriv", r"(?:Northern Star|train|shuttle)"),
    ("10:30 a.m.", r"(?:Casa Railyard|hotel)", r"shuttle"),
    ("10:45 a.m.", r"(?:leave|drop).{0,30}bags?.{0,30}Casa Railyard|Casa Railyard.{0,30}(?:leave|drop).{0,30}bags?", r"shuttle"),
    ("11:15 a.m.", r"Green Chile Table.{0,80}roasted squash bowl|roasted squash bowl.{0,80}Green Chile Table", r"walk"),
    ("12:30 p.m.", r"Railyard Art Walk", r"walk"),
    ("3:00–5:30 p.m.", r"(?:check in|rest).{0,80}Casa Railyard|Casa Railyard.{0,80}(?:check in|rest)", r"walk"),
    ("6:30 p.m.", r"Terra & Sage.{0,80}blue[- ]corn enchiladas|blue[- ]corn enchiladas.{0,80}Terra & Sage", r"shuttle"),
))
check_schedule(saturday_rows, "Saturday", (
    ("8:30 a.m.", r"(?:breakfast|Casa Railyard).{0,80}oatmeal.{0,40}fruit|oatmeal.{0,40}fruit.{0,80}(?:breakfast|Casa Railyard)", r"(?:stay|remain|at (?:Casa Railyard|the hotel)|on[ -]?site)"),
    ("10:00 a.m.", r"Georgia O['’]Keeffe Museum", r"shuttle"),
    ("12:15 p.m.", r"Plaza Cafe.{0,80}chile relleno|chile relleno.{0,80}Plaza Cafe", r"walk"),
    ("2:00–3:30 p.m.", r"Canyon Road", r"shuttle"),
    ("4:30–6:00 p.m.", r"(?:seated )?rest.{0,80}Casa Railyard|Casa Railyard.{0,80}(?:seated )?rest", r"shuttle"),
    ("7:00 p.m.", r"Juniper Kitchen.{0,80}mushroom posole|mushroom posole.{0,80}Juniper Kitchen", r"shuttle"),
))
check_schedule(sunday_rows, "Sunday", (
    ("9:00 a.m.", r"check[ -]?out.{0,50}(?:store|bags)|(?:store|bags).{0,50}check[ -]?out", r"(?:stay|remain|at (?:Casa Railyard|the hotel)|on[ -]?site)"),
    ("9:30–10:45 a.m.", r"Plaza Architecture Walk", r"shuttle"),
    ("12:00 p.m.", r"Cactus Bloom.{0,80}bean[- ]and[- ]corn tostada|bean[- ]and[- ]corn tostada.{0,80}Cactus Bloom", r"walk"),
    ("1:30 p.m.", r"Railyard Park", r"shuttle"),
    ("3:00–4:30 p.m.", r"(?:seated )?rest.{0,80}Casa Railyard|Casa Railyard.{0,80}(?:seated )?rest", r"walk"),
    ("4:30 p.m.", r"collect.{0,30}(?:stored )?bags", r"(?:stay|remain|at Casa Railyard|at the hotel)"),
    ("5:00 p.m.", r"leave.{0,50}Santa Fe Depot|Santa Fe Depot.{0,50}(?:leave|depart)", r"shuttle"),
    ("6:05 p.m.", r"depart.{0,50}Northern Star train 206|Northern Star train 206.{0,50}depart", r"(?:board|train|depot)"),
))

require(r"vegetarian.{0,180}(?:Green Chile Table|Terra & Sage|Plaza Cafe|Juniper Kitchen|Cactus Bloom)|(?:Green Chile Table|Terra & Sage|Plaza Cafe|Juniper Kitchen|Cactus Bloom).{0,180}vegetarian",
        practical, "Practical Notes must connect named meals to the vegetarian need")
require(r"knee fatigue", practical, "Practical Notes must state the knee-fatigue need")
require(r"15(?: minutes|[ -]minute)", practical,
        "Practical Notes must retain the 15-minute walking limit")
require(r"shuttle", practical, "Practical Notes must retain shuttle use")
require(r"(?:afternoon )?rest", practical, "Practical Notes must retain afternoon rest blocks")

if re.search(r"Museum of International Folk Art", text, flags=re.IGNORECASE):
    fail("the superseded museum must be omitted")
if re.search(r"(?:\$|\bUSD\b|\bdollars?\b|\bprices?\b|\bcosts?\b)", text,
             flags=re.IGNORECASE):
    fail("prices must be omitted")
for address in ("101 Cerrillos Lane", "44 Market Street", "72 Agua Fria Street",
                "217 Johnson Street", "706 Camino Lejo", "305 Galisteo Street",
                "88 Palace Avenue"):
    if address.casefold() in text.casefold():
        fail(f"street address must be omitted: {address}")

allowed_top_level = {"trip_notes.md", ".protected", "travel_plan.md", ".git", ".sandbox-home"}
extras = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed_top_level)
if extras:
    fail(f"unexpected extra top-level artifacts: {extras}")
protected_extras = sorted(path.name for path in (ROOT / ".protected").iterdir()
                          if path.name != "verify.py")
if protected_extras:
    fail(f"unexpected artifacts under .protected/: {protected_extras}")

print(f"PASS: travel_plan.md satisfies the corrected multi-turn request ({len(words)} words)")
