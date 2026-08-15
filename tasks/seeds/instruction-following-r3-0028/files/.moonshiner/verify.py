#!/usr/bin/env python3
"""Deterministic verifier for the completed education plan."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "learning_plan.md"
PACKET = ROOT / "planning_packet.md"
EXPECTED_PACKET_SHA256 = "70bf6bb2fe7dfbccffc19d2779b5af574a0b48e86ced0a5625732b73f4efedc0"


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def section(text: str, heading: str, next_headings: tuple[str, ...]) -> str:
    stop = "|".join(re.escape(item) for item in next_headings)
    match = re.search(
        rf"(?ms)^## {re.escape(heading)}\s*$\n(.*?)(?=^## (?:{stop})\s*$|\Z)",
        text,
    )
    if not match:
        fail(f"missing or malformed '## {heading}' section")
    return match.group(1).strip()


def labeled_value(block: str, label: str, context: str) -> str:
    """Return a Markdown labeled field without prescribing bold styling."""
    match = re.search(
        rf"(?mi)^\s*(?:\*\*)?{re.escape(label)}:\s*(?:\*\*)?\s*(\S.*)$",
        block,
    )
    if not match:
        fail(f"{context} lacks a '{label}' field")
    return match.group(1).strip()


def main() -> None:
    if not PLAN.is_file():
        fail("learning_plan.md was not created")

    packet_digest = hashlib.sha256(PACKET.read_bytes()).hexdigest()
    if packet_digest != EXPECTED_PACKET_SHA256:
        fail("protected planning_packet.md was modified")

    text = PLAN.read_text(encoding="utf-8")

    required_h2 = [
        "Sourced facts",
        "Recommendations",
        "Uncertainties",
        "Week 1",
        "Week 2",
        "Week 3",
        "Week 4",
        "Milestones and next step",
    ]
    positions = []
    for heading in required_h2:
        matches = list(re.finditer(rf"(?m)^## {re.escape(heading)}\s*$", text))
        if len(matches) != 1:
            fail(f"expected exactly one '## {heading}' heading")
        positions.append(matches[0].start())
    if positions != sorted(positions):
        fail("required sections are not in the requested order")

    weeks = re.findall(r"(?m)^## Week (\d+)\s*$", text)
    if weeks != ["1", "2", "3", "4"]:
        fail("the document must contain exactly Week 1 through Week 4")

    facts = section(text, "Sourced facts", tuple(required_h2[1:]))
    fact_lines = []
    for raw_line in facts.splitlines():
        line = raw_line.strip()
        if not line or re.match(r"^\|?\s*:?-{3,}", line):
            continue
        if re.search(r"(?i)\b(?:fact|source|citation)\b", line) and "|" in line:
            continue
        fact_lines.append(line)
    if not fact_lines:
        fail("Sourced facts must contain at least one factual item")
    for source_id in ("S1", "U1"):
        if not re.search(rf"\[{source_id}\]", facts):
            fail(f"Sourced facts does not cite [{source_id}]")
    if any(not re.search(r"\[(?:S[1-4]|U1)\]", line) for line in fact_lines):
        fail("every sourced-fact item must carry a packet or learner citation")
    invalid_citations = set(re.findall(r"\[((?:S|U)\d+)\]", facts)) - {
        "S1", "S2", "S3", "S4", "U1"
    }
    if invalid_citations:
        fail("Sourced facts uses a source ID not present in the packet or clarification")
    source_markers = {
        "diagnostic": "S1",
        "Algebra Cards A": "S2",
        "Data Lab B": "S3",
        "Mixed Check C": "S4",
    }
    for marker, source_id in source_markers.items():
        for line in fact_lines:
            if marker.lower() in line.lower() and f"[{source_id}]" not in line:
                fail(f"Sourced fact about {marker} is not cited [{source_id}]")
    facts_lower = facts.lower()
    for diagnostic_value in ("80%", "40%", "60%"):
        if diagnostic_value not in facts:
            fail(f"Sourced facts omits diagnostic result: {diagnostic_value}")
    for retained_detail in (
        "six weeks",
        "worked example",
        "calculator",
        "pencil",
        "paper",
        "printer",
        "independently",
        "tutor",
    ):
        if retained_detail not in facts_lower:
            fail(f"Sourced facts omits retained learner detail: {retained_detail}")
    if not re.search(r"\b(?:likes?|prefers?)\b.{0,40}\bworked example\b", facts_lower):
        fail("Sourced facts reverses or obscures the worked-example preference")
    if "can study independently" not in facts_lower:
        fail("Sourced facts does not say Jordan can study independently")
    if not re.search(
        r"(?:\b(?:lacks?|no|without)\b.{0,30}\bprinter\b|"
        r"\bprinter\b.{0,30}\b(?:not reliable|unreliable)\b)",
        facts_lower,
    ):
        fail("Sourced facts does not retain the lack of reliable printer access")
    if not re.search(
        r"(?:\b(?:no|without)\b.{0,30}\btutor\b|"
        r"\btutor\b.{0,30}\b(?:not available|unavailable)\b)",
        facts_lower,
    ):
        fail("Sourced facts does not retain that no tutor is available")

    recommendations = section(text, "Recommendations", tuple(required_h2[2:]))
    if not recommendations.strip():
        fail("Recommendations must contain at least one recommended action")

    uncertainties = section(text, "Uncertainties", tuple(required_h2[3:]))
    uncertainty_lower = uncertainties.lower()
    for concept in ("geometry", "readiness"):
        if concept not in uncertainty_lower:
            fail(f"Uncertainties must address {concept}")

    week_pattern = re.compile(
        r"(?ms)^## Week (\d+)\s*$\n(.*?)(?=^## (?:Week \d+|Milestones and next step)\s*$)"
    )
    week_blocks = week_pattern.findall(text)
    if len(week_blocks) != 4:
        fail("could not parse four complete weekly sections")

    required_rows = {
        "Monday": 60,
        "Thursday": 60,
        "Saturday": 90,
    }
    packet_resources = ("Algebra Cards A", "Data Lab B", "Mixed Check C")
    for week_number, block in week_blocks:
        context = f"Week {week_number}"
        objective = labeled_value(block, "Measurable objective", context)
        practice_set = labeled_value(block, "Practice set", context)
        progress_check = labeled_value(block, "Progress check", context)
        adjustment_rule = labeled_value(block, "Adjustment rule", context)
        if not re.search(r"\d", objective) or not re.search(
            r"(?i)\b(?:correct(?:ly)?|accuracy|score|solve|answers?)\b", objective
        ):
            fail(f"Week {week_number} objective is not measurable")
        if not re.search(r"\d", progress_check):
            fail(f"Week {week_number} progress check has no measurable result")
        if (not re.search(r"(?i)\b(?:if|when)\b", adjustment_rule)
                or not re.search(r"\d", adjustment_rule)
                or not re.search(
                    r"(?i)\b(?:begin|choose|devote|redo|repeat|review|schedule|use)\b",
                    adjustment_rule,
                )):
            fail(f"Week {week_number} adjustment rule is not conditional")

        timed_rows = re.findall(
            r"(?mi)^\|\s*([^|]+?)\s*\|\s*(\d+)\s+(?:min|minute|minutes)\s*\|\s*([^|]+?)\s*\|\s*$",
            block,
        )
        if len(timed_rows) != 3:
            fail(f"Week {week_number} must have exactly three session rows")
        rows = [(day.strip().title(), int(duration), activity.strip())
                for day, duration, activity in timed_rows]
        row_map = {day: duration for day, duration, _ in rows}
        if row_map != required_rows:
            fail(f"Week {week_number} has an incorrect day or duration")
        for day, duration, activity in rows:
            allocations = [
                int(value)
                for value in re.findall(
                    r"(?i)\b(\d+)\s*(?:min|minute|minutes)\b", activity
                )
            ]
            if len(allocations) < 2 or sum(allocations) != duration:
                fail(
                    f"Week {week_number} {day} activity allocations must add to "
                    f"{duration} minutes"
                )
        activities = " ".join(activity for _, _, activity in rows)
        if not any(resource in activities for resource in packet_resources):
            fail(f"Week {week_number} activities do not use a packet resource")
        if not re.search(r"(?i)\bworked(?:-style)?\b", activities) or not re.search(
            r"(?i)\bexample\b", activities
        ):
            fail(f"Week {week_number} does not include the worked-example preference")

    for resource in packet_resources:
        if not any(
            resource in " ".join(activity for _, _, activity in re.findall(
                r"(?mi)^\|\s*([^|]+?)\s*\|\s*(\d+)\s+(?:min|minute|minutes)\s*\|\s*([^|]+?)\s*\|\s*$",
                block,
            ))
            for _, block in week_blocks
        ):
            fail(f"packet resource '{resource}' is never used in an activity")

    milestones = section(text, "Milestones and next step", ())
    milestone_rows = re.findall(r"(?mi)^\|\s*Week\s+([1-4])\s*\|[^\n]+\|\s*$", milestones)
    if milestone_rows != ["1", "2", "3", "4"]:
        fail("the milestone table must contain one row for each week")
    next_step = labeled_value(milestones, "Next-step rule", "Milestones section")
    if (not re.search(r"(?i)\b(?:if|when)\b", next_step)
            or not re.search(r"\d", next_step)
            or not re.search(
                r"(?i)\b(?:final|form 2|week 4|mixed check c)\b", next_step
            )):
        fail("the final result needs a concrete conditional next-step rule")

    print("PASS: learning_plan.md satisfies the retained and corrected constraints")


if __name__ == "__main__":
    try:
        main()
    except UnicodeDecodeError:
        fail("learning_plan.md must be UTF-8 text")
