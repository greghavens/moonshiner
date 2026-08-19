#!/usr/bin/env python3
"""Deterministic acceptance checks for the corrected summit plan."""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "event_plan.md"
PROGRAM = ROOT / "program_catalog.csv"
CATERING = ROOT / "catering_catalog.csv"
CUES = ROOT / "production_cues.csv"

EXPECTED_HEADINGS = [
    "# Brightwell Partner Summit",
    "## Confirmed brief",
    "## Run of show",
    "## Catering",
    "## Production cues",
    "## Coordinator handoff",
]
EXPECTED_CODES = [
    "ARR-01",
    "OPEN-01",
    "KEY-CC",
    "WORK-IR",
    "LUNCH-01",
    "PANEL-01",
    "CLOSE-RT",
]
EXPECTED_KINDS = ["arrival", "opening", "keynote", "workshop", "lunch", "panel", "closing"]
CONFIRMED_BULLETS = [
    "- Date: Wednesday, 2026-10-14",
    "- Attendance: 72",
    "- Venue: Harbor Foundry",
    "- Keynote track: Community Capacity",
    "- Workshop track: Incident Lab",
    "- Caterer: Greenline Kitchen — Orchard Lunch",
    "- Closing format: Facilitator-led roundtable",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def clean(value: str) -> str:
    value = re.sub(r"[`*_]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def section(markdown: str, heading: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)", markdown
    )
    if not match:
        fail(f"missing section {heading!r}")
    return match.group(1).strip()


def bullets(body: str) -> list[str]:
    return [line.strip() for line in body.splitlines() if line.lstrip().startswith("-")]


def table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if "|" not in stripped:
        return []
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [clean(cell) for cell in stripped.split("|")]


def markdown_table(body: str, expected_headers: list[str]) -> list[list[str]]:
    lines = body.splitlines()
    tables: list[tuple[list[str], list[list[str]]]] = []
    for index, line in enumerate(lines[:-1]):
        headers = table_cells(line)
        if not headers:
            continue
        separator = table_cells(lines[index + 1])
        valid_separator = len(separator) == len(headers) and all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in separator
        )
        if headers == expected_headers and not valid_separator:
            fail(f"table {expected_headers!r} needs a valid Markdown separator")
        if not valid_separator:
            continue
        rows: list[list[str]] = []
        for candidate in lines[index + 2 :]:
            row = table_cells(candidate)
            if not row:
                break
            if len(row) != len(headers):
                fail(f"a row in a Markdown table has the wrong cell count")
            rows.append(row)
        tables.append((headers, rows))
    if len(tables) != 1:
        fail(f"section must contain exactly one Markdown table, found {len(tables)}")
    headers, rows = tables[0]
    if headers != expected_headers:
        fail(f"table headers must be {expected_headers!r}")
    return rows


def minutes(value: str) -> int:
    match = re.fullmatch(r"(\d{2}):(\d{2})", value)
    if not match or int(match.group(2)) >= 60:
        fail(f"catalog time {value!r} is invalid")
    return int(match.group(1)) * 60 + int(match.group(2))


def main() -> None:
    if not REPORT.is_file():
        fail("event_plan.md was not created")
    allowed_files = {
        ".protected/verify.py",
        "catering_catalog.csv",
        "event_plan.md",
        "production_cues.csv",
        "program_catalog.csv",
    }
    actual_files = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if (path.is_file() or path.is_symlink())
        # `.git` is the project's own version control, present in any
        # checkout; it is not an artifact the assistant created.
        and path.relative_to(ROOT).parts[0] not in {".sandbox-home", ".git"}
    }
    unexpected_files = sorted(actual_files - allowed_files)
    if unexpected_files:
        fail(f"unexpected file(s) created: {', '.join(unexpected_files)}")
    markdown = REPORT.read_text(encoding="utf-8")
    if len(markdown.strip()) < 2600:
        fail("event_plan.md is not a substantive completed event plan")

    headings = re.findall(r"(?m)^#{1,6}\s+.+$", markdown)
    if headings != EXPECTED_HEADINGS:
        fail("headings must match the requested titles and order, with no extras")

    confirmed = section(markdown, "## Confirmed brief")
    if bullets(confirmed) != CONFIRMED_BULLETS:
        fail("Confirmed brief must contain exactly the seven retained final decisions")
    if any(line.strip() and not line.lstrip().startswith("-") for line in confirmed.splitlines()):
        fail("Confirmed brief may contain only the seven requested bullets")

    program_by_code = {row["code"]: row for row in read_rows(PROGRAM)}
    run_rows = markdown_table(
        section(markdown, "## Run of show"),
        ["Time", "Code", "Activity", "Room", "Setup", "Access and transition"],
    )
    if len(run_rows) != 7:
        fail(f"Run of show must contain exactly seven blocks, found {len(run_rows)}")
    selected_program = [program_by_code[code] for code in EXPECTED_CODES]
    if [source["kind"] for source in selected_program] != EXPECTED_KINDS:
        fail("selected program rows do not supply exactly one required block kind in order")
    for row, code, source in zip(run_rows, EXPECTED_CODES, selected_program):
        expected = [
            f'{source["start"]}–{source["end"]}',
            code,
            source["title"],
            source["room"],
            source["setup"],
            f'{source["access"]}; {source["transition"]}',
        ]
        if row != expected:
            fail(f"run-of-show row for {code} does not preserve the selected catalog facts")
        if int(source["capacity"]) < 72:
            fail(f"selected program block {code} cannot accommodate 72 attendees")
    starts = [minutes(source["start"]) for source in selected_program]
    ends = [minutes(source["end"]) for source in selected_program]
    if any(start < minutes("09:00") for start in starts):
        fail("a selected program block starts before 09:00")
    if any(end > minutes("16:30") for end in ends):
        fail("a selected program block ends after 16:30")
    if any(start >= end for start, end in zip(starts, ends)):
        fail("a selected program block has an invalid time range")
    if any(starts[index] - ends[index - 1] < 15 for index in range(2, len(starts))):
        fail("selected program blocks do not retain every required 15-minute transition")

    selected_catering = next(
        row
        for row in read_rows(CATERING)
        if row["vendor"] == "Greenline Kitchen" and row["package"] == "Orchard Lunch"
    )
    catering_expected = [
        f'- Provider and package: {selected_catering["vendor"]} — {selected_catering["package"]}',
        f'- Service window: {selected_catering["service_start"]}–{selected_catering["service_end"]}',
        f'- Guest range: {selected_catering["guest_min"]}–{selected_catering["guest_max"]}',
        f'- Menu: {selected_catering["menu"]}',
        f'- Dietary coverage: {selected_catering["dietary_coverage"]}',
        f'- Delivery: {selected_catering["delivery_point"]}',
    ]
    catering_body = section(markdown, "## Catering")
    if bullets(catering_body) != catering_expected:
        fail("Catering must contain the six exact selected package facts")
    if any(line.strip() and not line.lstrip().startswith("-") for line in catering_body.splitlines()):
        fail("Catering may contain only the six requested bullets")
    if not (
        int(selected_catering["guest_min"]) <= 72 <= int(selected_catering["guest_max"])
    ):
        fail("selected catering package does not support the retained headcount")
    coverage = selected_catering["dietary_coverage"].casefold()
    if not all(term in coverage for term in ("vegetarian", "vegan", "gluten-free")):
        fail("selected catering package does not cover all retained dietary needs")

    cue_by_code = {row["program_code"]: row for row in read_rows(CUES)}
    cue_rows = markdown_table(
        section(markdown, "## Production cues"),
        ["Cue time", "Code", "Owner", "Action"],
    )
    if len(cue_rows) != 7:
        fail(f"Production cues must contain exactly seven rows, found {len(cue_rows)}")
    for row, code in zip(cue_rows, EXPECTED_CODES):
        source = cue_by_code[code]
        expected = [source["cue_time"], code, source["owner"], source["action"]]
        if row != expected:
            fail(f"production cue for {code} does not match the protected cue catalog")

    handoff = bullets(section(markdown, "## Coordinator handoff"))
    if len(handoff) != 3:
        fail("Coordinator handoff must contain exactly three bullets")
    required_terms = [
        ("community capacity", "stage", "cue sheet"),
        ("eight cabaret pods", "hearing loop", "wide aisle"),
        ("atrium north doors", "12:15", "13:35"),
    ]
    synthesis_patterns = [
        r"\b(?:reconcil\w*|align\w*|match\w*|sync\w*|synchron\w*|consistent|ensure\w*|confirm\w*)\b",
        r"\b(?:and|while|with|alongside|keep(?:s|ing)?|maintain\w*|preserv\w*|pair\w*|support\w*|test\w*)\b",
        r"\b(?:lunch|service window)\b",
    ]
    for index, terms in enumerate(required_terms):
        lowered = handoff[index].casefold()
        if not all(term in lowered for term in terms) or not re.search(
            synthesis_patterns[index], lowered
        ):
            fail(f"Coordinator handoff bullet {index + 1} does not complete its required synthesis")
        if not re.search(r"[.!?]$", handoff[index]):
            fail(f"Coordinator handoff bullet {index + 1} must be a complete sentence")

    lowered_report = markdown.casefold()
    if "resilient systems" in lowered_report or "key-rs" in lowered_report:
        fail("event_plan.md mentions the superseded keynote")

    print("PASS: event_plan.md satisfies the corrected and retained event constraints")


if __name__ == "__main__":
    main()
