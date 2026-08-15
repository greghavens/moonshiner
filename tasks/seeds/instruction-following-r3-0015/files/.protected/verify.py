#!/usr/bin/env python3
"""Deterministic verifier for the corrected personal knowledge map."""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "knowledge_map.md"
INDEX = ROOT / "inbox" / "index.csv"
CARDS = ROOT / "inbox" / "cards"

EXPECTED_HEADINGS = [
    "# Practice Systems Knowledge Map",
    "## Scope",
    "## Synthesis",
    "## Note catalog",
    "## Connections",
    "## Review queue",
]
EXPECTED_SCOPE = [
    "- Included collection: Learning",
    "- Included status: active",
    "- Hub note: K-101 — Retrieval Before Review",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def section(text: str, heading: str) -> str:
    lines = text.splitlines()
    try:
        start = lines.index(heading) + 1
    except ValueError:
        fail(f"missing heading: {heading}")
    end = len(lines)
    for position in range(start, len(lines)):
        if lines[position].startswith("#"):
            end = position
            break
    return "\n".join(lines[start:end]).strip()


def card_section(card_id: str, heading: str) -> str:
    text = (CARDS / f"{card_id}.md").read_text(encoding="utf-8")
    return section(text, heading)


def parse_table(body: str) -> list[list[str]]:
    lines = [line for line in body.splitlines() if line.strip()]
    if len(lines) < 3:
        fail("Note catalog must be a Markdown table with data rows")

    def cells(line: str) -> list[str]:
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return [cell.strip() for cell in stripped.split("|")]

    expected_header = [
        "ID", "Captured", "Note", "Tags", "Source", "Key takeaway"
    ]
    if cells(lines[0]) != expected_header:
        fail("Note catalog header is incorrect")
    separators = cells(lines[1])
    if len(separators) != 6 or any(
        not re.fullmatch(r":?-{3,}:?", separator)
        for separator in separators
    ):
        fail("Note catalog separator is incorrect")
    rows: list[list[str]] = []
    for line in lines[2:]:
        row = cells(line)
        if len(row) != 6:
            fail("Note catalog row does not have six columns")
        rows.append(row)
    return rows


def check_workspace_changes() -> None:
    top_level = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    # Authors can exercise this fixture from the enclosing seed repository.
    # The trace harness materializes files/ as its own Git worktree; only that
    # worktree has a meaningful deliverable-only status to enforce.
    if Path(top_level.stdout.strip()).resolve() != ROOT:
        return
    completed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    unexpected: list[str] = []
    for line in completed.stdout.splitlines():
        path = line[3:]
        if path == "knowledge_map.md" or path.startswith(".sandbox-home/"):
            continue
        unexpected.append(line)
    if unexpected:
        fail("unexpected workspace changes: " + ", ".join(unexpected))


def main() -> int:
    if not OUTPUT.is_file():
        fail("knowledge_map.md is missing")
    if OUTPUT.is_symlink():
        fail("knowledge_map.md must be a self-contained regular file")
    try:
        text = OUTPUT.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        fail("knowledge_map.md is not UTF-8")

    headings = [
        line for line in text.splitlines()
        if re.fullmatch(r"#{1,6} .+", line)
    ]
    if headings != EXPECTED_HEADINGS:
        fail("headings or heading order are incorrect")
    if text.splitlines()[0] != EXPECTED_HEADINGS[0]:
        fail("content appears before the title")

    scope_lines = [
        line for line in section(text, "## Scope").splitlines() if line.strip()
    ]
    if scope_lines != EXPECTED_SCOPE:
        fail("Scope must contain exactly the corrected three bullets")

    with INDEX.open(encoding="utf-8", newline="") as handle:
        inventory = list(csv.DictReader(handle))
    retained = sorted(
        (
            row for row in inventory
            if row["collection"] == "Learning" and row["status"] == "active"
        ),
        key=lambda row: row["captured"],
    )
    if [row["id"] for row in retained] != [
        "K-101", "K-102", "K-103", "K-104", "K-105"
    ]:
        fail("protected inventory no longer has the expected retained scope")

    excluded = [row for row in inventory if row not in retained]
    lowered = text.casefold()
    for row in excluded:
        if row["id"].casefold() in lowered or row["title"].casefold() in lowered:
            fail(f"excluded card {row['id']} is mentioned")
    if "workflows" in lowered:
        fail("the superseded collection is mentioned")

    synthesis = section(text, "## Synthesis")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", synthesis) if part.strip()]
    if len(paragraphs) != 2:
        fail("Synthesis must contain exactly two prose paragraphs")
    words = re.findall(r"\b[\w’'-]+\b", synthesis, flags=re.UNICODE)
    if not 110 <= len(words) <= 170:
        fail(f"Synthesis must contain 110–170 words; found {len(words)}")
    for row in retained:
        if f"[{row['id']}]" not in synthesis:
            fail(f"Synthesis does not cite {row['id']}")
    normalized_synthesis = synthesis.replace("–", "-").casefold()
    if "1-3-7-14-day" not in normalized_synthesis:
        fail("Synthesis omits the K-102 cadence")
    if "personal starting heuristic" not in normalized_synthesis:
        fail("Synthesis does not label the cadence as a personal starting heuristic")
    if "not a universal rule" not in normalized_synthesis:
        fail("Synthesis does not distinguish the heuristic from a universal rule")

    actual_rows = parse_table(section(text, "## Note catalog"))
    expected_rows: list[list[str]] = []
    for row in retained:
        core = card_section(row["id"], "## Core idea")
        if "\n" in core:
            fail(f"protected Core idea for {row['id']} is not one sentence block")
        expected_rows.append([
            row["id"],
            row["captured"],
            row["title"],
            "; ".join(row["tags"].split(";")),
            row["source_label"],
            core,
        ])
    if actual_rows != expected_rows:
        fail("Note catalog does not exactly preserve the five retained cards")

    expected_connections: list[str] = []
    retained_ids = {row["id"] for row in retained}
    for row in retained:
        cues = card_section(row["id"], "## Connection cues").splitlines()
        for cue in cues:
            match = re.fullmatch(r"- (K-\d{3}) → (K-\d{3}) — .+", cue)
            if match and set(match.groups()) <= retained_ids:
                expected_connections.append(cue)
    actual_connections = [
        line for line in section(text, "## Connections").splitlines() if line.strip()
    ]
    if actual_connections != expected_connections or len(actual_connections) != 4:
        fail("Connections must copy the four retained connection cues in order")

    expected_queue: list[str] = []
    for row in sorted(retained, key=lambda item: item["review_on"]):
        action = card_section(row["id"], "## Review action")
        if "\n" in action:
            fail(f"protected Review action for {row['id']} is not one line")
        expected_queue.append(
            f"- [ ] {row['review_on']} — {row['id']} — {action}"
        )
    actual_queue = [
        line for line in section(text, "## Review queue").splitlines() if line.strip()
    ]
    if actual_queue != expected_queue:
        fail("Review queue does not preserve every retained review action in order")

    check_workspace_changes()
    print("PASS: corrected Learning-only knowledge map is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
