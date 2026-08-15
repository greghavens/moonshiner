#!/usr/bin/env python3
"""Deterministic verification for the final reading-support plan."""

from __future__ import annotations

import re
import sys
from pathlib import Path


PLAN = Path("reading-support-plan.md")
HEADINGS = [
    "## Program Overview",
    "## Learning Goals",
    "## Four-Week Schedule",
    "## Assessment",
    "## Materials",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def section(text: str, heading: str, next_heading: str | None) -> str:
    start = text.index(heading) + len(heading)
    end = text.index(next_heading, start) if next_heading else len(text)
    return text[start:end]


if not PLAN.exists():
    fail("reading-support-plan.md is missing")
if PLAN.is_symlink() or not PLAN.is_file():
    fail("reading-support-plan.md must be a regular file")

try:
    content = PLAN.read_text(encoding="utf-8-sig")
except UnicodeDecodeError:
    fail("reading-support-plan.md must be UTF-8 text")

lines = content.splitlines()
if not lines or lines[0] != HEADINGS[0]:
    fail("the first line must be ## Program Overview")

observed_headings = [
    line for line in lines if re.fullmatch(r"#{1,6}\s+\S.*", line)
]
if observed_headings != HEADINGS:
    fail("use only the five required level-2 headings in the required order")
if any(content.count(heading) != 1 for heading in HEADINGS):
    fail("each required heading must appear exactly once")

sections = {
    heading: section(
        content,
        heading,
        HEADINGS[index + 1] if index + 1 < len(HEADINGS) else None,
    )
    for index, heading in enumerate(HEADINGS)
}

overview = sections["## Program Overview"].casefold()
if not re.search(r"\w", overview):
    fail("Program Overview must not be empty")

lowered = content.casefold()
for description, patterns in {
    "Grade 6 audience": (r"grade\s*6", r"sixth[- ]grade"),
    "four-week duration": (r"four[- ]week", r"4[- ]week"),
    "small mixed-readiness group": (r"mixed[- ]readiness",),
    "science informational texts": (
        r"science[- ]themed informational text",
        r"science informational text",
        r"informational text.*science",
    ),
    "45-minute duration": (r"45\s*(?:-|–)?\s*minutes?",),
}.items():
    if not any(re.search(pattern, lowered) for pattern in patterns):
        fail(f"the plan must retain the {description}")

if re.search(r"\btuesday\b|\bthursday\b", lowered):
    fail("the superseded Tuesday/Thursday schedule must not remain")

goals = sections["## Learning Goals"].casefold()
goal_requirements = {
    "main ideas and supporting details": (r"\bmain ideas?\b", r"\bsupporting details?\b"),
    "context clues for academic vocabulary": (r"\bcontext clues?\b", r"\bacademic vocabulary\b"),
    "citing textual evidence": (
        r"\bcit(?:e|es|ed|ing|ation|ations)\b",
        r"\btextual evidence\b",
    ),
}
number = r"(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)"
measurement = re.compile(
    rf"(?:\b{number}(?:\s*%|\s+percent\b)|"
    rf"\b{number}\s+(?:of|out\s+of)\s+{number}\b|"
    rf"\b(?:at\s+least|no\s+fewer\s+than)\s+{number}\b)"
)
goal_statements = [
    statement.strip()
    for statement in re.split(r"(?:\n+|(?<=[.!?])\s+)", goals)
    if statement.strip()
]
for description, needles in goal_requirements.items():
    matching = [
        statement
        for statement in goal_statements
        if all(re.search(pattern, statement) for pattern in needles)
    ]
    if not matching:
        fail(f"Learning Goals must cover {description}")
    if not any(measurement.search(statement) for statement in matching):
        fail(f"Learning Goals must give a measurable criterion for {description}")

schedule = sections["## Four-Week Schedule"]
week_matches = list(
    re.finditer(
        r"(?im)^\s*(?:[-*+]\s*)?(?:\*\*)?Week\s+([1-4])\b[^\n]*",
        schedule,
    )
)
if [match.group(1) for match in week_matches] != ["1", "2", "3", "4"]:
    fail("Four-Week Schedule must contain Week 1 through Week 4 exactly once and in order")

for index, match in enumerate(week_matches):
    end = week_matches[index + 1].start() if index + 1 < len(week_matches) else len(schedule)
    week = schedule[match.end():end]
    week_number = index + 1
    sessions = list(
        re.finditer(
            r"(?im)^\s*(?:[-*+]\s*)?(?:\*\*)?(Monday|Wednesday)\b[^\n]*",
            week,
        )
    )
    if [session.group(1).casefold() for session in sessions] != ["monday", "wednesday"]:
        fail(f"Week {week_number} must have separate Monday and Wednesday sessions in order")
    for session_index, session in enumerate(sessions):
        session_end = sessions[session_index + 1].start() if session_index + 1 < len(sessions) else len(week)
        entry = week[session.end():session_end].casefold()
        day = session.group(1)
        for label in ("objective:", "activity:", "evidence:"):
            occurrences = entry.split(label)[1:]
            if not occurrences:
                fail(f"Week {week_number} {day} must include {label[:-1]}")
            values = [
                re.split(
                    r"\b(?:objective|activity|evidence):",
                    occurrence,
                    maxsplit=1,
                )[0]
                for occurrence in occurrences
            ]
            if not any(re.search(r"\b\w+\b", value) for value in values):
                fail(f"Week {week_number} {day} must state a value for {label[:-1]}")

for needle in ("main idea", "supporting detail", "context clue", "vocabulary", "textual evidence"):
    if needle not in schedule.casefold():
        fail(f"the session schedule must retain work on {needle}")

assessment = sections["## Assessment"].casefold()
if not all(needle in assessment for needle in ("initial", "diagnostic")):
    fail("Assessment must include the initial diagnostic")
if "exit ticket" not in assessment or not re.search(
    r"(?:every|each|all\s+(?:eight|8))\s+(?:session|meeting)",
    assessment,
):
    fail("Assessment must include an exit ticket after every session")
if "transfer task" not in assessment:
    fail("Assessment must include the end-of-program transfer task")
if not re.search(r"(?:4|four)[- ]point rubric", assessment):
    fail("Assessment must include the 4-point rubric")
for score in ("4", "3", "2", "1"):
    if not re.search(
        rf"(?:^|[\n;,.!?])\s*(?:[-*+]\s*)?(?:\|\s*)?(?:\*\*)?"
        rf"(?:(?:score|level)\s+)?{score}\b\s*(?:\*\*)?\s*"
        rf"(?:(?:[-–—:.)=|])\s*(?!point\b)|means\s+)(?:\*\*)?\w",
        assessment,
    ):
        fail("Assessment must describe all four rubric score levels")

materials = sections["## Materials"].casefold()
material_requirements = {
    "short science articles": ("short science", "article"),
    "print format": ("print",),
    "large-print or digital access": ("large-print", "digital"),
    "vocabulary organizers": ("vocabulary organizer",),
    "evidence trackers": ("evidence tracker",),
    "annotation supplies": ("annotation",),
}
for description, needles in material_requirements.items():
    if description == "large-print or digital access":
        if not any(needle in materials for needle in needles):
            fail(f"Materials must include {description}")
    elif not all(needle in materials for needle in needles):
        fail(f"Materials must include {description}")

print("PASS: reading-support-plan.md satisfies the final corrected brief")
