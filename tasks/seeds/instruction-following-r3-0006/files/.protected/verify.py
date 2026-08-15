#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "launch_day_brief.md"

HEADINGS = [
    "# Launch-Day Operations Brief",
    "## Morning Run",
    "## Midday Run",
    "## Afternoon Run",
    "## Coordination Links",
    "## Dispatch Order",
]

RUNS = {
    "## Morning Run": [
        [
            "Harbor Point",
            "Stage chilled display units",
            "07:30–08:15",
            "Photo check to facilities desk",
            "`cold-chain`",
        ],
        [
            "Mesa Annex",
            "Inspect insulated cart seals",
            "09:05–09:35",
            "Seal checklist to route captain",
            "`cold-chain`",
        ],
        [
            "Cedar Square",
            "Complete opening stock scan",
            "08:20–09:00",
            "Count sheet to inventory lead",
            "`inventory`",
        ],
    ],
    "## Midday Run": [
        [
            "Juniper Mall",
            "Validate returns staging lane",
            "11:10–11:50",
            "Exception log to service desk",
            "`returns`",
        ],
    ],
    "## Afternoon Run": [
        [
            "Orchard Row",
            "Reconcile shelf-ready case counts",
            "14:00–14:45",
            "Variance sheet to inventory lead",
            "`inventory`",
        ],
        [
            "Union Depot",
            "Audit customer return exceptions",
            "15:10–15:55",
            "Resolved log to service desk",
            "`returns`",
        ],
    ],
}

COORDINATION_LINKS = [
    "- **Harbor Point ↔ Mesa Annex:** Share the insulated-cart checklist before either team unloads.",
    "- **Cedar Square ↔ Orchard Row:** Reuse the opening-count sheet after the first stock scan.",
    "- **Juniper Mall ↔ Union Depot:** Compare exception logs before the midday status rollup.",
]

DISPATCH_ORDER = [
    "Harbor Point",
    "Mesa Annex",
    "Cedar Square",
    "Juniper Mall",
    "Orchard Row",
    "Union Depot",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def nonblank(lines: list[str]) -> list[str]:
    return [line.rstrip() for line in lines if line.strip()]


def table_cells(line: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def check_run(heading: str, lines: list[str]) -> None:
    table = nonblank(lines)
    expected_rows = RUNS[heading]
    if len(table) != len(expected_rows) + 2:
        fail(f"{heading[3:]} must contain only its one requested table")

    if table_cells(table[0]) != ["Site", "Task", "Window", "Handoff", "Tag"]:
        fail(f"{heading[3:]} has incorrect table columns")

    delimiters = table_cells(table[1])
    if len(delimiters) != 5 or any(
        re.fullmatch(r":?-{3,}:?", cell) is None for cell in delimiters
    ):
        fail(f"{heading[3:]} has an invalid Markdown table delimiter")

    actual_rows = [table_cells(line) for line in table[2:]]
    if actual_rows != expected_rows:
        fail(f"{heading[3:]} has incorrect placement, order, or source fields")


def main() -> None:
    if not OUTPUT.is_file():
        fail("launch_day_brief.md was not created")

    try:
        lines = OUTPUT.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        fail("launch_day_brief.md is not valid UTF-8")

    found_headings = [
        (index, line.rstrip())
        for index, line in enumerate(lines)
        if re.match(r"^#{1,6}(?:\s|$)", line)
    ]
    if [heading for _, heading in found_headings] != HEADINGS:
        fail("headings are not exactly the requested headings in the requested order")

    if any(line.strip() for line in lines[: found_headings[0][0]]):
        fail("content appears before the title")

    sections: dict[str, list[str]] = {}
    for position, (line_index, heading) in enumerate(found_headings):
        next_index = (
            found_headings[position + 1][0]
            if position + 1 < len(found_headings)
            else len(lines)
        )
        sections[heading] = lines[line_index + 1 : next_index]

    if nonblank(sections[HEADINGS[0]]):
        fail("an introduction was added")

    for heading in HEADINGS[1:4]:
        check_run(heading, sections[heading])

    if nonblank(sections["## Coordination Links"]) != COORDINATION_LINKS:
        fail("Coordination Links must contain exactly the three requested bullets")

    dispatch_lines = nonblank(sections["## Dispatch Order"])
    if len(dispatch_lines) != len(DISPATCH_ORDER):
        fail("Dispatch Order must contain exactly six numbered items")

    actual_order: list[str] = []
    for line in dispatch_lines:
        match = re.fullmatch(r"\d+[.)]\s+(.+?)\s*", line)
        if match is None:
            fail("Dispatch Order must be one numbered list")
        actual_order.append(match.group(1))
    if actual_order != DISPATCH_ORDER:
        fail("Dispatch Order has incorrect items or order")

    print(
        "PASS: launch_day_brief.md preserves the corrected placement and all retained constraints"
    )


if __name__ == "__main__":
    main()
