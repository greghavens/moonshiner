#!/usr/bin/env python3
"""Deterministic acceptance checks for the final workshop plan.

This file is evaluator-owned and is not part of the requested deliverable.
"""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "final_plan.md"
PACKET_TITLES = (
    "block by block",
    "the lot that became a garden",
    "bus stop at dawn",
    "library steps",
    "when the creek rose",
    "safe crossings",
)


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def field(section: str, *labels: str) -> str:
    """Read a labeled Markdown field without prescribing bold/list formatting."""
    raw_lines = section.splitlines()
    for index, raw_line in enumerate(raw_lines):
        line = raw_line.strip()
        line = re.sub(r"^[-*+]\s+", "", line)
        line = line.replace("**", "").replace("__", "")
        for label in labels:
            match = re.match(
                rf"^{re.escape(label)}(?:\s*\([^)]*\))?\s*:\s*(.*)$",
                line,
                flags=re.IGNORECASE,
            )
            if match:
                value = match.group(1).strip()
                if value:
                    return value
                for continuation in raw_lines[index + 1:]:
                    value = re.sub(r"^\s*[-*+]\s+", "", continuation).strip()
                    value = value.replace("**", "").replace("__", "")
                    if value:
                        return value
    return ""


def session_headings(text: str) -> list[tuple[re.Match[str], int, int, str]]:
    """Find session headings while allowing normal Markdown punctuation/order choices."""
    found: list[tuple[re.Match[str], int, int, str]] = []
    heading_pattern = r"^(?:#{1,6}\s+|\*\*)?((?:Week|Session)\b.+?)(?:\*\*)?\s*$"
    for match in re.finditer(heading_pattern, text, flags=re.MULTILINE | re.IGNORECASE):
        heading = match.group(1)
        week = re.search(r"\bWeek\s+([1-4])\b", heading, flags=re.IGNORECASE)
        session = re.search(r"\bSession\s+(1[0-2]|[1-9])\b", heading, flags=re.IGNORECASE)
        day = re.search(r"\b(Tuesday|Wednesday|Thursday)\b", heading, flags=re.IGNORECASE)
        if week and session and day:
            found.append((match, int(week.group(1)), int(session.group(1)), day.group(1).title()))
    return found


def agenda_rows(section: str) -> list[tuple[int, str]]:
    """Accept either Markdown table rows or minute-labeled agenda list entries."""
    rows = [
        (int(minutes), activity.strip())
        for minutes, activity in re.findall(
            r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*$",
            section,
            flags=re.MULTILINE,
        )
    ]
    rows.extend(
        (int(minutes), activity.strip())
        for minutes, activity in re.findall(
            r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?(\d+)\s*(?:minutes?|mins?)\b\s*[:—–-]?\s*(.+)$",
            section,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    )
    return rows


def material_is_allowed(item: str) -> bool:
    item = item.strip().strip(".").lower()
    if not item:
        return False
    # Packet descriptions may name one or more of the six included texts.
    if "packet" in item:
        remainder = item
        for title in PACKET_TITLES:
            remainder = remainder.replace(title, " ")
        words = re.findall(r"[a-z]+", remainder)
        packet_words = {
            "a", "all", "and", "available", "copies", "copy", "essay",
            "excerpt", "excerpts", "feature", "from", "in", "included",
            "narrative", "of", "one", "packet", "passage", "passages",
            "photo", "poem", "printed", "provided", "section", "sections",
            "six", "student", "text", "texts", "the", "transcript",
            "workshop",
        }
        return bool(words) and set(words) <= packet_words
    compact = re.sub(r"\s+", " ", item)
    allowed = (
        r"(?:the\s+)?notebooks?",
        r"(?:the\s+)?pencils?",
        r"(?:the\s+)?sticky notes?",
        r"(?:the\s+)?highlighters?",
        r"(?:the\s+)?chart paper",
        r"(?:the\s+)?markers?",
        r"(?:the\s+)?index cards?",
        r"(?:the\s+)?projector",
        r"(?:the\s+)?document camera",
    )
    return any(re.fullmatch(pattern, compact) for pattern in allowed)


def validate_goal_entries(text: str, errors: list[str]) -> None:
    """Validate three numbered goals without requiring a specific table layout."""
    table_rows = re.findall(
        r"^\|\s*(?:G|Goal\s*)?([1-3])\s*\|(.+?)\|(.+?)\|\s*$",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    if table_rows:
        require([row[0] for row in table_rows] == ["1", "2", "3"], "need exactly three ordered program goals", errors)
        for number, goal, indicator in table_rows:
            require(len(goal.strip()) >= 25, f"Goal {number} is not substantive", errors)
            require(re.search(r"\d", indicator) is not None, f"Goal {number} lacks a numerical success indicator", errors)
        return

    goal_heads = list(re.finditer(r"^#{2,6}\s+(?:G|Goal\s*)?([1-3])\b.*$", text, flags=re.MULTILINE | re.IGNORECASE))
    if goal_heads:
        require([m.group(1) for m in goal_heads] == ["1", "2", "3"], "need exactly three ordered program goals", errors)
        for index, match in enumerate(goal_heads):
            end = goal_heads[index + 1].start() if index + 1 < len(goal_heads) else len(text)
            body = text[match.end():end]
            require(len(body.split()) >= 8, f"Goal {match.group(1)} is not substantive", errors)
            require(re.search(r"\d", body) is not None, f"Goal {match.group(1)} lacks a numerical success indicator", errors)
        return

    list_rows = re.findall(r"^\s*([1-3])[.)]\s+(.+)$", text, flags=re.MULTILINE)
    require([row[0] for row in list_rows] == ["1", "2", "3"], "need exactly three ordered program goals", errors)
    for number, body in list_rows:
        require(len(body) >= 40, f"Goal {number} is not substantive", errors)
        require(re.search(r"\d", body) is not None, f"Goal {number} lacks a numerical success indicator", errors)


def validate_checkpoints(text: str, errors: list[str]) -> None:
    """Validate weekly evidence and responses in table or labeled-block form."""
    rows = re.findall(
        r"^\|\s*Week\s+([1-4])\s*\|(.+?)\|(.+?)\|\s*$",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    if rows:
        require([row[0] for row in rows] == ["1", "2", "3", "4"], "need one ordered evidence checkpoint for each of Weeks 1–4", errors)
        for week, evidence, response in rows:
            require(len(evidence.strip()) >= 20, f"Week {week} evidence is too vague", errors)
            require(len(response.strip()) >= 20, f"Week {week} instructional response is too vague", errors)
        return

    blocks = list(re.finditer(r"^(?:#{1,6}\s+|\*\*)?Week\s+([1-4])\b[^\n]*checkpoint[^\n]*$", text, flags=re.MULTILINE | re.IGNORECASE))
    require([m.group(1) for m in blocks] == ["1", "2", "3", "4"], "need one ordered evidence checkpoint for each of Weeks 1–4", errors)
    for index, match in enumerate(blocks):
        end = blocks[index + 1].start() if index + 1 < len(blocks) else len(text)
        body = text[match.start():end]
        require("evidence" in body.lower(), f"Week {match.group(1)} checkpoint lacks evidence to review", errors)
        require(re.search(r"instructional response|respond|reteach|adjust", body, flags=re.IGNORECASE) is not None, f"Week {match.group(1)} checkpoint lacks an instructional response", errors)


def main() -> int:
    errors: list[str] = []
    require(PLAN.is_file(), "final_plan.md was not created", errors)
    if errors:
        print("FAIL\n- " + "\n- ".join(errors))
        return 1

    text = PLAN.read_text(encoding="utf-8")
    lower = text.lower()
    require(len(text.split()) >= 1200, "plan is not substantive enough to be classroom-ready", errors)
    require(
        re.search(r"^#.+Voices of the Neighborhood", text, flags=re.MULTILINE | re.IGNORECASE) is not None,
        "missing the workshop name and theme in the title",
        errors,
    )

    # Corrected participant scope and retained program constraints.
    require(re.search(r"\bGrade\s+7\s+only\b", text, flags=re.IGNORECASE) is not None, "corrected Grade 7-only scope is missing", errors)
    require(re.search(r"\b18 learners\b", lower) is not None, "correct enrollment is missing", errors)
    require(
        re.search(r"exactly three stable groups of six", lower) is not None,
        "correct grouping statement is missing",
        errors,
    )
    require(re.search(r"grades?\s*6\s*[–-]\s*8", lower) is None, "obsolete grade span remains", errors)
    require(re.search(r"\b24 learners\b", lower) is None, "obsolete enrollment remains", errors)
    require(re.search(r"four stable groups", lower) is None, "obsolete group count remains", errors)
    require("four weeks" in lower or "4 weeks" in lower, "four-week duration is missing", errors)
    for day in ("tuesday", "wednesday", "thursday"):
        require(day in lower, f"retained weekday {day.title()} is missing", errors)
    require(re.search(r"\b45 minutes\b", lower) is not None, "45-minute duration is missing", errors)
    require("no homework" in lower, "no-homework condition is missing", errors)
    for component in ("program snapshot", "grouping plan", "assessment plan"):
        require(component in lower, f"{component} is missing", errors)

    # Resource retention: every supplied text is used and no new resource is assumed.
    headings = session_headings(text)
    session_text = text[headings[0][0].start():].lower() if headings else ""
    for title in PACKET_TITLES:
        require(title in session_text, f"supplied text is not used in a session: {title}", errors)
    require(re.search(r"\bonly\b[^.\n]{0,100}\bmaterials?\b|\bmaterials?\b[^.\n]{0,100}\bonly\b", lower) is not None, "resource limit is not acknowledged", errors)
    forbidden_resources = re.findall(
        r"\b(?:laptops?|computers?|tablets?|smartphones?|internet|websites?|online|scissors|glue|crayons?|colored pencils?|whiteboards?|worksheets?|handouts?)\b",
        lower,
    )
    require(not forbidden_resources, "plan assumes unlisted materials: " + ", ".join(sorted(set(forbidden_resources))), errors)

    validate_goal_entries(text, errors)
    validate_checkpoints(text, errors)

    matches = headings
    require(len(matches) == 12, "need exactly 12 session headings identifying week, session number, and weekday", errors)
    expected_days = ["Tuesday", "Wednesday", "Thursday"] * 4
    if len(matches) == 12:
        for index, (_, week, session, day) in enumerate(matches, start=1):
            require(week == ((index - 1) // 3) + 1, f"Session {index} is in the wrong week", errors)
            require(session == index, f"session numbering is not sequential at {index}", errors)
            require(day == expected_days[index - 1], f"Session {index} has the wrong weekday", errors)

        for index, (match, _, _, _) in enumerate(matches, start=1):
            end = matches[index][0].start() if index < len(matches) else len(text)
            section = text[match.end():end]
            objective = field(section, "Measurable objective", "Objective")
            materials = field(section, "Materials")
            check = field(section, "Formative check", "Formative assessment")
            ml_support = field(section, "Multilingual learner support", "Multilingual support", "Support for multilingual learners")
            dyslexia_support = field(section, "Dyslexia support", "Support for learners with dyslexia")
            require(len(objective) >= 25, f"Session {index} lacks a substantive measurable objective", errors)
            require(re.search(r"\b(?:identify|cite|write|state|select|explain|compare|annotate|label|rank|justify|compose|distinguish|develop|organize|draft|revise|complete|demonstrate|produce|summarize|analyze|trace|support)\b", objective, flags=re.IGNORECASE) is not None, f"Session {index} objective is not measurably observable", errors)
            require(len(materials) >= 8, f"Session {index} lacks materials", errors)
            require(len(check) >= 25, f"Session {index} lacks a named formative check", errors)
            require(len(ml_support) >= 30, f"Session {index} lacks embedded multilingual support", errors)
            require(len(dyslexia_support) >= 30, f"Session {index} lacks embedded dyslexia support", errors)
            require("timed agenda" in section.lower(), f"Session {index} lacks its timed agenda", errors)
            rows = agenda_rows(section)
            require(len(rows) >= 2, f"Session {index} timed agenda needs multiple entries", errors)
            require(sum(minutes for minutes, _ in rows) == 45, f"Session {index} agenda does not total 45", errors)
            material_items: list[str] = []
            for comma_part in materials.split(","):
                if "packet" in comma_part.lower():
                    material_items.append(comma_part)
                else:
                    material_items.extend(re.split(r"\s+and\s+", comma_part, flags=re.IGNORECASE))
            unknown = [item.strip() for item in material_items if item.strip() and not material_is_allowed(item)]
            require(not unknown, f"Session {index} names unavailable materials: {', '.join(unknown)}", errors)

    if matches:
        first_end = matches[1][0].start() if len(matches) > 1 else len(text)
        first = text[matches[0][0].end():first_end].lower()
        last = text[matches[-1][0].end():].lower()
        require("pre-assessment" in first, "Session 1 lacks the pre-assessment", errors)
        require("post-assessment" in last, "Session 12 lacks the post-assessment", errors)
        require("parallel" in last, "Session 12 does not identify the post-assessment as parallel", errors)

    if errors:
        print("FAIL\n- " + "\n- ".join(errors))
        return 1
    print("PASS: final_plan.md satisfies the corrected scope and all retained constraints")
    return 0


if __name__ == "__main__":
    sys.exit(main())
