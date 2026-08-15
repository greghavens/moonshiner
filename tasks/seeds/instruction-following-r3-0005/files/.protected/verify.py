#!/usr/bin/env python3
"""Deterministic verifier for the personal knowledge map."""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "knowledge_map.md"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


if not OUTPUT.is_file():
    fail("knowledge_map.md was not created")

try:
    text = OUTPUT.read_text(encoding="utf-8")
except UnicodeDecodeError:
    fail("knowledge_map.md must be UTF-8 text")

lines = text.splitlines()
expected_headings = [
    "# Personal Knowledge Map",
    "## Active Threads",
    "## Incubator",
    "## Reference Shelf",
    "## Cross-links",
    "## Review Sequence",
]
headings = [line for line in lines if line.startswith("#")]
if headings != expected_headings:
    fail("headings are missing, extra, renamed, or out of order")
if not lines or lines[0] != expected_headings[0]:
    fail("the file must begin with the requested title")

prohibited_patterns = {
    "internal note ID": r"\bPK-\d+\b",
    "capture date": r"\b2026-\d{2}-\d{2}\b",
    "URL": r"https?://",
    "named attribution": r"\b(?:Maya Ortiz|Dev Shah|Lena Park|Omar Bell|Priya Nair|Elias Green)\b",
    "private context": r"\b(?:renovation|guest snacks|landlord|certification retake|family visit|former neighbor)\b",
    "superseded placement": (
        r"\b(?:superseded|previous(?:ly)?|original(?:ly)?|formerly|prior placement|"
        r"used to|move(?:d)? from Incubator)\b"
    ),
}
for label, pattern in prohibited_patterns.items():
    if re.search(pattern, text, flags=re.IGNORECASE):
        fail(f"output includes prohibited {label}")


def section(heading: str) -> list[str]:
    start = lines.index(heading) + 1
    end = len(lines)
    for candidate in expected_headings:
        if candidate == heading:
            continue
        try:
            position = lines.index(candidate, start)
        except ValueError:
            continue
        end = min(end, position)
    return [line for line in lines[start:end] if line.strip()]


if section("# Personal Knowledge Map"):
    fail("the title must be followed directly by Active Threads, with no introduction")


def table_cells(line: str) -> list[str] | None:
    """Return stripped cells for a pipe table row, or None for non-table text."""
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return None
    return [cell.strip() for cell in stripped[1:-1].split("|")]


table_header = ["Topic", "Key idea", "Next step", "Tag"]
expected_tables = {
    "## Active Threads": [
        ["Morning Light Experiment", "Twenty minutes of outdoor light within an hour of waking creates a consistent start-of-day cue.", "Track wake time, light start, and afternoon energy for 14 days.", "`habits`"],
        ["Rain Barrel Sizing", "Roof capture volume depends on rainfall and catchment area, while storage size must match watering demand.", "Measure the balcony catchment and one week of container watering.", "`garden`"],
        ["Pantry Rotation Board", "A visible first-in, first-out lane makes older staples easier to use before replacements.", "Label one shelf “use first” and review it before the weekly shop.", "`home-systems`"],
    ],
    "## Incubator": [
        ["Interleaving Practice", "Alternating related problem types improves discrimination better than long single-type blocks.", "Build two 30-minute mixed practice sets for next week.", "`learning`"],
    ],
    "## Reference Shelf": [
        ["Seasonal Produce Freezing", "Blanching time varies by vegetable and affects texture before freezing.", "Make a one-page blanching chart for peas, beans, and broccoli.", "`food`"],
        ["Neighborhood Tree ID", "Leaf arrangement plus bud shape narrows winter tree identification more reliably than bark color alone.", "Photograph buds on three block routes and annotate opposite versus alternate branching.", "`field-notes`"],
    ],
}
for heading, rows in expected_tables.items():
    content = section(heading)
    parsed = [table_cells(line) for line in content]
    valid_rule = (
        len(parsed) >= 2
        and parsed[1] is not None
        and len(parsed[1]) == 4
        and all(re.fullmatch(r":?-{3,}:?", cell) for cell in parsed[1])
    )
    if (
        len(content) != len(rows) + 2
        or parsed[0] != table_header
        or not valid_rule
        or parsed[2:] != rows
    ):
        fail(f"{heading} must contain only its correctly ordered source-derived table")


def bullet_items(content: list[str]) -> list[str] | None:
    """Parse bullet items while permitting any Markdown bullet marker and wrapping."""
    items: list[str] = []
    for line in content:
        match = re.match(r"^[*+-]\s+(.+)$", line)
        if match:
            items.append(match.group(1).strip())
        elif items and re.match(r"^\s{2,}\S", line):
            items[-1] += " " + line.strip()
        else:
            return None
    return items


cross_links = bullet_items(section("## Cross-links"))
if cross_links is None or len(cross_links) != 3:
    fail("Cross-links must contain exactly three bullets")
link_checks = [
    (
        ("Morning Light Experiment", "Interleaving Practice"),
        (r"\bmorning\b", r"\bcue\b", r"\bmixed[- ]practice\b"),
    ),
    (
        ("Pantry Rotation Board", "Seasonal Produce Freezing"),
        (r"\buse first\b", r"\bblanch\w*\b"),
    ),
    (
        ("Rain Barrel Sizing", "Neighborhood Tree ID"),
        (r"\bwater\w*\b", r"\btree\b", r"\bobserv\w*\b"),
    ),
]
all_titles = [title for pair, _ in link_checks for title in pair]
for item, (pair, required) in zip(cross_links, link_checks):
    lowered = item.lower().replace("“", "").replace("”", "")
    has_pair_only = (
        all(item.count(title) == 1 for title in pair)
        and all(title not in item for title in all_titles if title not in pair)
    )
    if not has_pair_only or not all(re.search(pattern, lowered) for pattern in required):
        fail("a cross-link is out of order or is not grounded in its paired cues")

expected_review = [
    "Morning Light Experiment",
    "Rain Barrel Sizing",
    "Pantry Rotation Board",
    "Interleaving Practice",
    "Seasonal Produce Freezing",
    "Neighborhood Tree ID",
]
review_items: list[str] = []
for line in section("## Review Sequence"):
    match = re.fullmatch(r"\d+[.)]\s+(.+)", line)
    if not match:
        fail("Review Sequence must be one numbered list")
    item = match.group(1).strip()
    for marker in ("**", "__", "*", "_"):
        if item.startswith(marker) and item.endswith(marker) and len(item) > 2 * len(marker):
            item = item[len(marker):-len(marker)].strip()
            break
    review_items.append(item)
if review_items != expected_review:
    fail("Review Sequence does not reflect the corrected placement order")

print("PASS: knowledge_map.md follows the accumulated instructions")
