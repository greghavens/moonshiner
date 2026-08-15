#!/usr/bin/env python3
"""Deterministic acceptance checks for the final consumer comparison."""

from pathlib import Path
import re


OUTPUT = Path("comparison.md")
HEADINGS = [
    "## Quick comparison",
    "## Trade-offs",
    "## Recommendation",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


if not OUTPUT.is_file():
    fail("comparison.md is missing")

text = OUTPUT.read_text(encoding="utf-8")
if not text.startswith(HEADINGS[0] + "\n"):
    fail("the file must begin with the Quick comparison heading")

found_headings = re.findall(r"^#{1,6} .+$", text, flags=re.MULTILINE)
if found_headings != HEADINGS:
    fail(f"headings must be exactly {HEADINGS!r} in that order")

word_count = len(re.findall(r"\b[\w]+(?:[’'-][\w]+)*\b", text, flags=re.UNICODE))
if not 190 <= word_count <= 260:
    fail(f"comparison must contain 190–260 words; found {word_count}")

sections = {}
for index, heading in enumerate(HEADINGS):
    start = text.index(heading) + len(heading)
    end = text.index(HEADINGS[index + 1]) if index + 1 < len(HEADINGS) else len(text)
    sections[heading] = text[start:end].strip()

quick = sections[HEADINGS[0]]
table_lines = [line.strip() for line in quick.splitlines() if line.strip()]
if len(table_lines) != 8 or any(not line.startswith("|") for line in table_lines):
    fail("Quick comparison must contain only one six-row Markdown table")


def cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


if cells(table_lines[0]) != ["Attribute", "Alder AirLite", "Bracken PowerMax"]:
    fail("the comparison table has incorrect columns")
if len(cells(table_lines[1])) != 3 or any(
    re.fullmatch(r":?-{3,}:?", cell) is None for cell in cells(table_lines[1])
):
    fail("the comparison table separator is malformed")

rows = [cells(line) for line in table_lines[2:]]
if any(len(row) != 3 for row in rows):
    fail("each comparison row must contain values for both products")
expected_attributes = [
    "price",
    "weight",
    "maximum runtime",
    "dust-bin capacity",
    "filtration",
    "noise",
]
if [row[0].casefold() for row in rows] != expected_attributes:
    fail("the six requested attributes must appear in the requested order")


def require(row_number: int, alder_patterns: list[str], bracken_patterns: list[str]) -> None:
    alder = rows[row_number][1].casefold()
    bracken = rows[row_number][2].casefold()
    for pattern in alder_patterns:
        if re.search(pattern, alder) is None:
            fail(f"Alder value for {rows[row_number][0]} is incomplete or inaccurate")
    for pattern in bracken_patterns:
        if re.search(pattern, bracken) is None:
            fail(f"Bracken value for {rows[row_number][0]} is incomplete or inaccurate")


require(0, [r"\$219\b"], [r"\$249\b"])
require(1, [r"\b5\.7\s*(?:lb|pounds?)\b"], [r"\b7\.2\s*(?:lb|pounds?)\b"])
require(2, [r"\b42\s*(?:minutes?|min)\b"], [r"\b58\s*(?:minutes?|min)\b"])
require(3, [r"\b0\.45\s*l\b"], [r"\b0\.70\s*l\b"])
require(
    4,
    [r"\bsealed\b", r"\bhepa\b", r"99\.97%", r"0\.3\s*microns?"],
    [r"\bwashable\b", r"five-stage", r"not sealed"],
)
require(5, [r"\b74\s*db\b"], [r"\b78\s*db\b"])

tradeoffs = sections[HEADINGS[1]]
tradeoff_lines = [line.strip() for line in tradeoffs.splitlines() if line.strip()]
if len(tradeoff_lines) != 2 or any(not line.startswith("- ") for line in tradeoff_lines):
    fail("Trade-offs must contain exactly two bullets and no other prose")
if "alder airlite" not in tradeoff_lines[0].casefold():
    fail("the first trade-off bullet must cover Alder AirLite")
if "bracken powermax" not in tradeoff_lines[1].casefold():
    fail("the second trade-off bullet must cover Bracken PowerMax")

tailored = (tradeoffs + "\n" + sections[HEADINGS[2]]).casefold()
for pattern, label in [
    (r"\b950(?:-square-foot|\s+square\s+feet|\s+sq\.?\s*ft\.?)\b", "950-square-foot home"),
    (r"hard[ -]floors?", "mostly hard floors"),
    (r"two (?:area )?rugs", "two area rugs"),
    (r"(?:indoor )?cat", "indoor cat"),
]:
    if re.search(pattern, tailored) is None:
        fail(f"the comparison does not retain the {label} detail")

if re.search(
    r"\b(?:accessor(?:y|ies)\s+(?:count|number|total)|"
    r"(?:count|number|total)\s+of\s+accessories|"
    r"(?:\d+|one|two|three|four|five|more|fewer)\s+accessories)\b",
    text,
    re.IGNORECASE,
):
    fail("accessory count must not be discussed")

recommendation = sections[HEADINGS[2]]
if len([line for line in recommendation.splitlines() if line.strip()]) != 1:
    fail("Recommendation must be one paragraph with no extra commentary")
sentence_parts = [
    part.strip()
    for part in re.split(r"(?<!\d)[.!?]+(?=\s|$)", recommendation)
    if part.strip()
]
if len(sentence_parts) != 2:
    fail("Recommendation must contain exactly two sentences")
if "alder airlite" not in recommendation.casefold():
    fail("the recommendation must name Alder AirLite")
if re.match(r"(?:choose|recommend|pick)\b.{0,30}\balder airlite\b", recommendation, re.IGNORECASE) is None:
    fail("the recommendation must clearly select Alder AirLite")

lower_recommendation = recommendation.casefold()
filtration_position = min(
    (position for term in ("filtration", "sealed hepa")
     if (position := lower_recommendation.find(term)) >= 0),
    default=-1,
)
runtime_position = lower_recommendation.find("runtime")
if filtration_position < 0 or runtime_position < 0 or filtration_position > runtime_position:
    fail("the rationale must apply filtration first and runtime second")
if re.search(r"(?:weight|light(?:er|weight)?)\s+(?:is\s+)?(?:first|top|primary)|(?:first|top|primary)[ -](?:ranked\s+)?(?:priority\s+)?(?:is\s+)?(?:weight|light)", text, re.IGNORECASE):
    fail("the superseded weight-first priority was retained")

print(f"PASS: comparison.md satisfies the final retained constraints ({word_count} words)")
