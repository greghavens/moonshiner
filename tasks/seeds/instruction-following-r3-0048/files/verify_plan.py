#!/usr/bin/env python3
"""Deterministic verification for the completed study-plan artifact."""

from __future__ import annotations

import re
import sys
from pathlib import Path


PLAN = Path("study_plan.md")
EXPECTED_HEADINGS = [
    "# Six-Week Statistics Study Plan",
    "## Assumptions",
    "## Weekly plan",
    "## Progress checks",
]
EXPECTED_DATES = [
    r"\bjune\s+8\s*(?:[-–—]|to|through)\s*(?:june\s+)?14\b",
    r"\bjune\s+15\s*(?:[-–—]|to|through)\s*(?:june\s+)?21\b",
    r"\bjune\s+22\s*(?:[-–—]|to|through)\s*(?:june\s+)?28\b",
    r"\bjune\s+29\s*(?:[-–—]|to|through)\s*july\s+5\b",
    r"\bjuly\s+6\s*(?:[-–—]|to|through)\s*(?:july\s+)?12\b",
    r"\bjuly\s+13\s*(?:[-–—]|to|through)\s*(?:july\s+)?19\b",
]
EXPECTED_COLUMNS = [
    "Week & dates",
    "Focus and measurable outcome",
    "Session plan",
    "Tangible deliverable",
    "Checkpoint",
]
TOPIC_TERMS = [
    ("data", "descriptive"),
    ("probability", "conditional"),
    ("random variable", "sampling distribution"),
    ("confidence interval", "mean", "proportion"),
    ("hypothesis", "mean", "proportion"),
    ("correlation", "regression", "cumulative"),
]
RESOURCE_TERMS = (
    "openintro",
    "problem bank",
    "formula sheet",
    "error log",
    "practice set",
)
ACTION_TERMS = (
    "analyze", "answer", "annotate", "apply", "attempt", "audit", "build",
    "calculate", "check", "classify", "compare", "complete", "correct",
    "create", "critique", "defend", "derive", "diagram", "discuss", "draft",
    "evaluate", "explain", "graph", "identify", "interpret", "label", "log",
    "make", "outline", "practice", "present", "read", "redo", "retrieve",
    "review", "rewrite", "run", "score", "solve", "summarize", "take",
    "teach", "time", "use", "verify", "work", "write",
)


def fail(errors: list[str]) -> None:
    for error in errors:
        print(f"FAIL: {error}")
    raise SystemExit(1)


def table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def markdown_tables(lines: list[str]) -> list[list[str]]:
    """Return pipe-table blocks that have a valid Markdown delimiter row."""
    blocks: list[list[str]] = []
    block: list[str] = []
    for line in lines + [""]:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            block.append(line)
            continue
        if block:
            if len(block) >= 2:
                delimiters = table_cells(block[1])
                if delimiters and all(
                    re.fullmatch(r":?-{3,}:?", cell) for cell in delimiters
                ):
                    blocks.append(block)
            block = []
    return blocks


def session_segments(session_cell: str) -> dict[str, str] | None:
    """Split the four requested recurring sessions in their calendar order."""
    folded = session_cell.casefold()
    days = ("monday", "tuesday", "wednesday", "friday")
    starts: list[int] = []
    for day in days:
        match = re.search(rf"\b{day}\b", folded)
        if match is None:
            return None
        starts.append(match.start())
    if starts != sorted(starts):
        return None
    ends = starts[1:] + [len(folded)]
    return {
        day: folded[start:end]
        for day, start, end in zip(days, starts, ends)
    }


def day_mentions_are_study_free(text: str, day: str) -> bool:
    """Allow an otherwise unscheduled day only when every mention keeps it free."""
    token = rf"\b{day}s?\b"
    for line in text.casefold().splitlines():
        for match in re.finditer(token, line):
            window = line[max(0, match.start() - 60):match.end() + 60]
            after = rf"{token}[^.;|]{{0,45}}(?:off|free|study-free|unavailable|no study)"
            before = (
                rf"(?:off|free|study-free|unavailable|no (?:study|session|meeting)|"
                rf"without[^.;|]{{0,25}}|do not (?:study|meet)|never (?:study|meet))"
                rf"[^.;|]{{0,45}}{token}"
            )
            if not (re.search(after, window) or re.search(before, window)):
                return False
    return True


def main() -> None:
    errors: list[str] = []
    if not PLAN.is_file():
        fail(["study_plan.md was not created"])

    text = PLAN.read_text(encoding="utf-8")
    folded = text.casefold()
    if re.search(r"\b(?:todo|tbd|placeholder|fill in|to be decided)\b", folded):
        errors.append("the plan contains unfinished placeholder text")

    headings = [line.strip() for line in text.splitlines()
                if line.startswith("#")]
    if headings != EXPECTED_HEADINGS:
        errors.append("the headings are not exactly the four requested headings in order")

    required_facts = {
        "2026 calendar year": r"\b2026\b",
        "exam date": r"\bjuly\s+17(?:th)?\s*,?\s*2026\b",
        "exam time": r"\b9(?::00)?\s*(?:[-–—]|to)\s*11(?::00)?\s*a\.?\s*m\.?",
        "readiness target": r"(?:at least\s+)?80\s*%",
        "Sunday break": r"\bsunday\b",
        "OpenIntro resource": r"\bopenintro statistics\b",
        "problem-bank resource": r"\bcourse problem bank\b",
        "formula-sheet resource": r"\bclass formula sheet\b",
        "error-log resource": r"\berror log\b",
        "practice-set resource": r"\bpractice set\b",
    }
    for label, pattern in required_facts.items():
        if not re.search(pattern, folded):
            errors.append(f"missing or incorrect {label}")
    if not day_mentions_are_study_free(text, "sunday"):
        errors.append("every Sunday mention must explicitly keep Sunday study-free")
    if not day_mentions_are_study_free(text, "saturday"):
        errors.append("the superseded Saturday group schedule remains in the plan")
    if not day_mentions_are_study_free(text, "thursday"):
        errors.append("the plan adds a Thursday session outside the stated availability")

    tables = markdown_tables(text.splitlines())
    if len(tables) != 1:
        errors.append(f"expected exactly one Markdown table, found {len(tables)}")
        rows: list[str] = []
    else:
        table = tables[0]
        header = table_cells(table[0])
        if header != EXPECTED_COLUMNS:
            errors.append("the weekly table does not have the five exact requested columns")
        rows = table[2:]
        if len(rows) != 6:
            errors.append(f"expected exactly six weekly table rows, found {len(rows)}")

    if headings == EXPECTED_HEADINGS:
        weekly_section = text.split("## Weekly plan", 1)[1].split(
            "## Progress checks", 1
        )[0]
        if len(markdown_tables(weekly_section.splitlines())) != 1:
            errors.append("the Markdown table is not inside the Weekly plan section")

    if len(rows) == 6:
        for index, (line, date_pattern, terms) in enumerate(
                zip(rows, EXPECTED_DATES, TOPIC_TERMS), start=1):
            cells = table_cells(line)
            if len(cells) != 5:
                errors.append(f"Week {index} does not have exactly five table cells")
                continue
            focus = cells[1].casefold()
            if not cells[0].casefold().startswith(f"week {index}"):
                errors.append(f"weekly rows are not in Week 1 through Week 6 order")
            if not re.search(date_pattern, cells[0].casefold()):
                errors.append(f"Week {index} has the wrong date range")
            for term in terms:
                if term not in focus:
                    errors.append(
                        f"Week {index} focus is missing topic term {term!r}"
                    )

            segments = session_segments(cells[2])
            if segments is None:
                errors.append(
                    f"Week {index} must list Monday, Tuesday, Wednesday, and Friday sessions in order"
                )
            else:
                durations = {
                    "monday": 75,
                    "tuesday": 60,
                    "wednesday": 75,
                    "friday": 45,
                }
                for day, minutes in durations.items():
                    segment = segments[day]
                    if not re.search(rf"\b{minutes}\s*(?:min|minute)", segment):
                        errors.append(
                            f"Week {index} has the wrong or missing {day.title()} duration"
                        )
                    if not any(resource in segment for resource in RESOURCE_TERMS):
                        errors.append(
                            f"Week {index} {day.title()} does not name an available resource"
                        )
                    if not re.search(
                        rf"\b(?:{'|'.join(ACTION_TERMS)})(?:s|d|ed|ing)?\b",
                        segment,
                    ):
                        errors.append(
                            f"Week {index} {day.title()} does not give a concrete action"
                        )
                tuesday = segments["tuesday"]
                if "group" not in tuesday:
                    errors.append(f"Week {index} does not retain the Tuesday group format")
                if not re.search(
                    r"6:30\s*(?:-|–|—|to)\s*7:30\s*p\.?\s*m\.?", tuesday
                ):
                    errors.append(
                        f"Week {index} is missing the Tuesday 6:30-7:30 p.m. time"
                    )
                friday = segments["friday"]
                if not re.search(r"\b(?:quiz|retrieval check|practice set)\b", friday):
                    errors.append(f"Week {index} Friday lacks its low-stakes check")
                if not re.search(r"\b(?:error|miss|correct)", friday):
                    errors.append(f"Week {index} Friday lacks error review")
            if not re.search(r"\b(?:at least\s+)?\d+\b|\b\d+%", cells[1]):
                errors.append(f"Week {index} lacks a numeric measurable outcome")
            if len(cells[3]) < 25:
                errors.append(f"Week {index} lacks a substantive tangible deliverable")
            if len(cells[4]) < 25 or not re.search(r"\d", cells[4]):
                errors.append(f"Week {index} lacks an observable checkpoint threshold")

    if len(rows) == 6:
        probability = " ".join(table_cells(rows[1])).casefold()
        confidence = " ".join(table_cells(rows[3])).casefold()
        for label, weak_area in (
            ("probability", probability),
            ("confidence intervals", confidence),
        ):
            if not re.search(r"\b(?:extra|additional)\b", weak_area):
                errors.append(f"{label} does not receive explicit extra practice")
            if len(re.findall(r"\bretriev", weak_area)) < 2:
                errors.append(f"{label} does not receive repeated retrieval practice")

        for week_number in (5, 6):
            readiness_row = " ".join(table_cells(rows[week_number - 1])).casefold()
            if "practice set" not in readiness_row:
                errors.append(
                    f"Week {week_number} does not use a final cumulative practice set"
                )
            if not re.search(r"20[- ]question", readiness_row):
                errors.append(
                    f"Week {week_number} does not specify a 20-question readiness check"
                )
            if not re.search(r"(?:at least\s+)?80%|16/20", readiness_row):
                errors.append(
                    f"Week {week_number} does not retain the 80% readiness target"
                )

    if errors:
        fail(errors)
    print("PASS: study_plan.md satisfies the corrected, retained planning contract")


if __name__ == "__main__":
    main()
