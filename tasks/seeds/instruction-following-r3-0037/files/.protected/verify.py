#!/usr/bin/env python3
"""Protected deterministic acceptance checks for the final health handout."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "home_bp_guide.md"
EXPECTED_HEADINGS = [
    "# Home Blood Pressure: A Three-Morning Guide",
    "## What the numbers mean",
    "## Before each reading",
    "## Three-morning record",
    "## When to get help",
    "## Sources",
]
TRACKING_HEADERS = ["day", "timing", "reading 1", "reading 2", "notes"]
SOURCE_BULLETS = [
    "- `sources/01_american_heart_association.md` — [AHA]",
    "- `sources/02_cdc_measurement.md` — [CDC]",
    "- `sources/03_nhlbi_basics.md` — [NHLBI]",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def clean_cell(value: str) -> str:
    value = re.sub(r"[`*_]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def split_row(line: str) -> list[str]:
    return [clean_cell(cell) for cell in line.strip().strip("|").split("|")]


def section(markdown: str, heading: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)", markdown
    )
    if not match:
        fail(f"missing section {heading!r}")
    return match.group(1).strip()


def markdown_tables(body: str) -> list[tuple[list[str], list[list[str]]]]:
    lines = body.splitlines()
    tables: list[tuple[list[str], list[list[str]]]] = []
    index = 0
    while index + 1 < len(lines):
        if not lines[index].lstrip().startswith("|"):
            index += 1
            continue
        headers = split_row(lines[index])
        separator = split_row(lines[index + 1])
        if len(separator) != len(headers) or not all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in separator
        ):
            index += 1
            continue
        rows: list[list[str]] = []
        index += 2
        while index < len(lines) and lines[index].lstrip().startswith("|"):
            row = split_row(lines[index])
            if len(row) != len(headers):
                fail("a Markdown table row has the wrong number of cells")
            rows.append(row)
            index += 1
        tables.append((headers, rows))
    return tables


def require(pattern: str, text: str, message: str) -> None:
    if not re.search(pattern, text, re.IGNORECASE | re.DOTALL):
        fail(message)


def main() -> None:
    if not REPORT.is_file():
        fail("home_bp_guide.md was not created")
    markdown = REPORT.read_text(encoding="utf-8")
    words = re.findall(r"\b[\w’'-]+\b", markdown)
    if not 650 <= len(words) <= 950:
        fail(f"handout must contain 650–950 words; found {len(words)}")

    headings = re.findall(r"(?m)^#{1,6}\s+.+$", markdown)
    if headings != EXPECTED_HEADINGS:
        fail("headings must match the exact requested text and order, with no extras")
    if markdown.splitlines()[0] != EXPECTED_HEADINGS[0]:
        fail("the handout must start with the requested title and no preface")

    superseded_patterns = {
        "seven-day schedule": r"\b(?:seven|7)[ -]?(?:day|morning)s?\b",
        "evening schedule": r"\bevenings?\b",
    }
    for label, pattern in superseded_patterns.items():
        if re.search(pattern, markdown, re.IGNORECASE):
            fail(f"the handout mentions the superseded {label}")

    numbers = section(markdown, "## What the numbers mean")
    number_tables = markdown_tables(numbers)
    if len(number_tables) != 1:
        fail("What the numbers mean must contain exactly one Markdown table")
    category_headers, category_rows = number_tables[0]
    if [cell.casefold() for cell in category_headers] != [
        "category", "systolic", "relationship", "diastolic"
    ]:
        fail("the category table must use the exact requested columns")
    if len(category_rows) != 4:
        fail("the category table must contain exactly four body rows")
    expected_categories = [
        ("normal", "less than 120", "and", "less than 80"),
        ("elevated", "120–129", "and", "less than 80"),
        ("stage 1 hypertension", "130–139", "or", "80–89"),
        ("stage 2 hypertension", "140 or higher", "or", "90 or higher"),
    ]
    normalized_rows = [tuple(cell.casefold() for cell in row) for row in category_rows]
    if normalized_rows != expected_categories:
        fail("the four category thresholds or their and/or relationships are incorrect")
    require(r"systolic.{0,180}(pump|contract)", numbers,
            "systolic pressure must be defined as the pumping phase")
    require(r"diastolic.{0,180}(between beats|fills?)", numbers,
            "diastolic pressure must be defined as the between-beats filling phase")
    require(r"mm\s*hg.{0,80}millimeters of mercury|millimeters of mercury.{0,80}mm\s*hg",
            numbers, "mm Hg must be defined")
    require(r"single (reading|measurement).{0,160}(not|cannot|doesn.t).{0,60}diagnos",
            numbers, "a single home reading must be distinguished from a diagnosis")
    if "[AHA]" not in numbers or "[NHLBI]" not in numbers:
        fail("the definitions and category discussion need AHA and NHLBI source labels")

    technique = section(markdown, "## Before each reading")
    checks = {
        "automatic upper-arm monitor": r"automatic.{0,50}upper[- ]arm|upper[- ]arm.{0,50}automatic",
        "30-minute preparation window": r"30\s+minutes?.{0,160}(smok|alcohol|caffeine|exercise)",
        "empty bladder": r"empty.{0,30}bladder",
        "five-minute seated rest": r"(sit|rest).{0,60}(five|5)\s+minutes?",
        "bare-skin cuff": r"(cuff.{0,40}bare skin|bare skin.{0,40}cuff)",
        "supported back": r"back.{0,30}support",
        "flat feet and uncrossed legs": r"feet.{0,30}flat.{0,100}(uncross|legs.{0,30}not crossed)",
        "supported arm at chest or heart height": r"arm.{0,60}(chest|heart) (height|level)",
        "no talking": r"(do not|don.t|avoid) talk|no talking",
        "two readings one minute apart": r"two readings.{0,50}one minute apart",
    }
    for label, pattern in checks.items():
        require(pattern, technique, f"measurement instructions omit {label}")
    if technique.count("[CDC]") < 2 or "[AHA]" not in technique:
        fail("measurement technique needs inline CDC and AHA source labels")

    record = section(markdown, "## Three-morning record")
    record_tables = markdown_tables(record)
    if len(record_tables) != 1:
        fail("Three-morning record must contain exactly one Markdown table")
    record_headers, record_rows = record_tables[0]
    if [cell.casefold() for cell in record_headers] != TRACKING_HEADERS:
        fail("the tracking table must use the exact requested columns")
    if len(record_rows) != 3:
        fail("the corrected tracking table must contain exactly three rows")
    for index, row in enumerate(record_rows, start=1):
        if row[0].casefold() != f"day {index}" or row[1].casefold() != "morning":
            fail("tracking rows must be Day 1 through Day 3 with Morning timing")
        placeholders = {"", "—", "-", "___", "____", "_____", "______"}
        if any(cell not in placeholders for cell in row[2:]):
            fail("reading and notes cells must remain blank placeholders")
    require(r"two readings.{0,50}one minute apart", record,
            "the corrected log must retain two readings one minute apart")

    help_text = section(markdown, "## When to get help")
    require(r"above\s+180\s*/\s*120.{0,120}(wait|pause).{0,50}(one|1)\s+minute.{0,100}(measure|check|test).{0,30}again",
            help_text, "the help section must instruct a one-minute wait and repeat measurement")
    require(r"(remain|still).{0,70}(above\s+180\s*/\s*120|that high).{0,180}(without|no).{0,80}symptom.{0,160}(contact|call).{0,60}(health care|healthcare|clinician|professional).{0,30}immediately|without.{0,80}symptom.{0,240}(contact|call).{0,60}(health care|healthcare|clinician|professional).{0,30}immediately",
            help_text, "repeated severe pressure without symptoms needs immediate professional contact")
    symptom_patterns = [
        r"chest pain", r"shortness of breath", r"back pain", r"numbness",
        r"weakness", r"(change in vision|vision change)", r"difficulty speaking",
    ]
    for pattern in symptom_patterns:
        require(pattern, help_text, f"emergency symptom missing: {pattern}")
    require(r"(call|dial)\s+911", help_text,
            "the emergency action must explicitly say to call 911")
    require(r"(do not|don.t|never).{0,80}(stop|change).{0,80}(medicine|medication).{0,140}(health care|healthcare|clinician|professional)",
            help_text, "the medication warning must be retained")
    if help_text.count("[AHA]") < 2:
        fail("the help guidance needs inline AHA source labels")

    sources = section(markdown, "## Sources")
    source_lines = [line.strip() for line in sources.splitlines() if line.strip()]
    if source_lines != SOURCE_BULLETS:
        fail("Sources must contain exactly the three protected source paths and labels")

    for label in ("[AHA]", "[CDC]", "[NHLBI]"):
        if markdown.count(label) < 2:
            fail(f"health claims are not adequately attributed with {label}")

    print("PASS: home_bp_guide.md satisfies the corrected multi-turn health brief")


if __name__ == "__main__":
    main()
