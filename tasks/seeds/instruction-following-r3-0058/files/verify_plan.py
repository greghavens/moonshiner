from __future__ import annotations

import csv
import re
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PLAN = ROOT / "learning_plan.md"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def markdown_cells(line: str) -> list[str]:
    """Split a Markdown table row without treating an escaped pipe as a divider."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith(r"\|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", stripped)]


def load_resources() -> list[dict[str, str]]:
    with (ROOT / "resources.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def schedule_rows(markdown: str) -> list[list[str]]:
    expected_header = [
        "week",
        "tuesday session",
        "wednesday session",
        "saturday session",
        "core resource",
        "applied deliverable",
    ]
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        cells = markdown_cells(line)
        if [normalized(cell) for cell in cells] != expected_header:
            continue
        require(index + 1 < len(lines), "schedule table is missing its separator row")
        separator = markdown_cells(lines[index + 1])
        require(
            len(separator) == 6 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator),
            "schedule table separator is malformed",
        )
        rows: list[list[str]] = []
        for candidate in lines[index + 2 :]:
            if not candidate.strip().startswith("|"):
                break
            row = markdown_cells(candidate)
            if len(row) == 6:
                rows.append(row)
        return rows
    raise AssertionError("missing schedule table with the requested six columns")


def contains_date(cell: str, expected: date) -> bool:
    month = expected.strftime("%B")
    month_abbr = expected.strftime("%b")
    day = expected.day
    year = expected.year
    patterns = (
        rf"(?<!\d){year}-{expected.month:02d}-{day:02d}(?!\d)",
        rf"(?<!\d)0?{expected.month}/0?{day}/{year}(?!\d)",
        rf"\b(?:{month}|{month_abbr}\.?)\s+0?{day}(?:st|nd|rd|th)?[,]?\s+{year}\b",
        rf"\b0?{day}(?:st|nd|rd|th)?\s+(?:{month}|{month_abbr}\.?)[,]?\s+{year}\b",
    )
    return any(re.search(pattern, cell, re.I) for pattern in patterns)


def contains_time(cell: str, hour: int, meridiem: str) -> bool:
    twelve_hour = rf"(?<!\d){hour}(?::00)?\s*{meridiem[0]}\.?\s*m\.?(?!\w)"
    twenty_four_hour = 19 if hour == 7 and meridiem == "PM" else 10
    return re.search(twelve_hour, cell, re.I) is not None or re.search(
        rf"(?<!\d){twenty_four_hour:02d}:00(?!\d)(?!\s*[ap]\.?\s*m\.?)", cell, re.I
    ) is not None


def check_session(cell: str, expected_date: date, hour: int, meridiem: str, label: str) -> None:
    require(contains_date(cell, expected_date), f"{label} must use date {expected_date.isoformat()}")
    require(contains_time(cell, hour, meridiem), f"{label} must use time {hour}:00 {meridiem}")
    require(re.search(r"\b45(?:-|\s*)min(?:ute)?s?\b", cell, re.I) is not None, f"{label} must last 45 minutes")

    non_activity_words = {
        "activity", "am", "date", "duration", "learning", "pm", "min", "mins",
        "minute", "minutes", "scheduled", "session", "study", "task", "work",
        "january", "february", "march", "april", "may", "june", "july",
        "august", "september", "october", "november", "december",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
        "oct", "nov", "dec",
    }
    activity_words = [
        word.casefold()
        for word in re.findall(r"[A-Za-z]{2,}", cell)
        if word.casefold() not in non_activity_words
    ]
    require(len(activity_words) >= 2, f"{label} needs a specific activity, not just date and duration")


def headings(markdown: str) -> list[str]:
    return [
        normalized(match.group(1))
        for match in re.finditer(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", markdown, re.M)
    ]


def require_plan_sections(markdown: str) -> None:
    found = headings(markdown)
    section_concepts = (
        (("learner",), ("snapshot", "profile", "background"), "learner snapshot"),
        (("planning",), ("constraint", "requirement"), "planning constraints"),
        (("completion",), ("checklist",), "completion checklist"),
    )
    for required, alternatives, label in section_concepts:
        require(
            any(all(term in heading for term in required) and any(term in heading for term in alternatives) for heading in found),
            f"missing Markdown section for {label}",
        )


def main() -> None:
    require(PLAN.is_file(), "learning_plan.md was not created")
    require(not PLAN.is_symlink(), "learning_plan.md must be a self-contained file, not a symlink")
    markdown = PLAN.read_text(encoding="utf-8")
    folded = normalized(markdown)

    for placeholder in ("todo", "tbd", "placeholder", "fill this in"):
        require(placeholder not in folded, f"unfinished placeholder remains: {placeholder}")

    require_plan_sections(markdown)
    require("kai rivera" in folded, "plan must identify Kai Rivera")
    require("community college" in folded and "advisor" in folded, "plan must retain Kai's advisor role")
    require("student success" in folded and "survey" in folded, "plan must retain the student-success survey context")
    require(
        "spreadsheet" in folded and any(term in folded for term in ("comfortable", "proficient", "experienced", "familiar")),
        "plan must reflect Kai's spreadsheet experience",
    )
    require(
        "statistic" in folded and any(term in folded for term in ("new", "beginner", "novice", "learning", "introductory")),
        "plan must reflect that Kai is new to statistics",
    )
    require(contains_date(markdown, date(2026, 9, 14)), "plan must state that the eight-week period begins the week of 2026-09-14")
    require(
        "screen reader" in folded or "assistive technolog" in folded,
        "plan must keep materials screen-reader-friendly",
    )
    require(
        any(phrase in folded for phrase in ("text first", "text based", "primarily text", "written first", "textual material")),
        "plan must keep materials text-first",
    )
    require(
        any(phrase in folded for phrase in ("color alone", "rely on color", "depend on color", "without color", "beyond color")),
        "plan must state that meaning does not rely on color alone",
    )
    require("department meeting" in folded, "plan must retain the department-meeting goal")
    require("eight week" in folded or "8 week" in folded, "corrected eight-week duration is not stated")

    rows = schedule_rows(markdown)
    require(len(rows) == 8, f"schedule must have exactly eight weekly rows, found {len(rows)}")
    resources = load_resources()
    require(len(resources) == 8, "protected resource catalog must contain eight entries")

    start = date(2026, 9, 14)
    weekly_themes = (
        ("survey", "question", "measurement", "response", "field"),
        ("clean", "spreadsheet", "duplicate", "data dictionary", "transformation"),
        ("describ", "pattern", "count", "percent", "distribution", "summar"),
        ("sampl", "uncertain", "limitation", "nonresponse", "missing", "bias", "caveat"),
        ("compar", "overclaim", "causal", "denominator", "group", "difference"),
        ("chart", "visual", "label", "alt text", "accessib", "screen reader"),
        ("recommend", "evidence", "action", "next step"),
        ("memo", "briefing", "meeting", "present", "rehears"),
    )
    deliverable_markers = (
        "analysis", "annotation", "answer", "briefing", "chart", "checklist", "claim",
        "comparison", "data dictionary", "dataset", "draft", "file", "finding", "inventory",
        "interpretation", "log", "map", "memo", "note", "outline", "question", "recommendation",
        "reflection", "report", "sheet", "spreadsheet", "summary", "table", "takeaway",
        "worksheet", "workbook",
    )
    for offset, (row, resource) in enumerate(zip(rows, resources), start=0):
        require(normalized(row[0]) in {f"week {offset + 1}", str(offset + 1)}, f"schedule row {offset + 1} has the wrong week label")
        monday = start + timedelta(days=7 * offset)
        check_session(row[1], monday + timedelta(days=1), 7, "PM", f"week {offset + 1} Tuesday session")
        check_session(row[2], monday + timedelta(days=2), 7, "PM", f"week {offset + 1} Wednesday session")
        check_session(row[3], monday + timedelta(days=5), 10, "AM", f"week {offset + 1} Saturday session")

        resource_cell = normalized(row[4])
        require(normalized(resource["id"]) in resource_cell, f"week {offset + 1} must use resource {resource['id']}")
        require(normalized(resource["title"]) in resource_cell, f"week {offset + 1} must name resource title {resource['title']}")

        deliverable = normalized(row[5])
        require(len(deliverable.split()) >= 2, f"week {offset + 1} needs a concrete applied deliverable")
        require(
            deliverable not in {"weekly deliverable", "completed assignment", "finish activity", "complete work"},
            f"week {offset + 1} needs a concrete applied deliverable, not a generic label",
        )
        require(
            any(marker in deliverable for marker in deliverable_markers),
            f"week {offset + 1} deliverable must name a concrete output",
        )
        applied_work = normalized(" ".join(row[1:4] + [row[5]]))
        require(
            any(theme in applied_work for theme in weekly_themes[offset]),
            f"week {offset + 1} activities or deliverable must apply the core resource's topic",
        )

    require("clean" in folded and "spreadsheet" in folded, "learning arc must include cleaning the survey spreadsheet")
    require(
        "pattern" in folded and any(term in folded for term in ("overclaim", "cautious", "limitation", "causal overreach")),
        "learning arc must describe patterns without overclaiming",
    )
    require("chart" in folded and "accessib" in folded, "learning arc must include an accessible chart")
    require("one page" in folded and "recommendation memo" in folded, "learning arc must include a one-page recommendation memo")
    require(
        "five minute" in folded and "briefing" in folded and "note" in folded,
        "learning arc must include five-minute briefing notes",
    )

    checklist_items = re.findall(r"^\s*-\s*\[[ xX]\]\s+.+$", markdown, flags=re.M)
    require(checklist_items, "completion checklist needs at least one usable Markdown checkbox item")

    allowed_files = {"learning_plan.md", "resources.csv", "verify_plan.py"}
    unexpected_files = sorted(
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if (path.is_file() or path.is_symlink())
        and str(path.relative_to(ROOT)) not in allowed_files
        # `.git` is the project's own version control, present in any
        # checkout; it is not an artifact the assistant created.
        and path.relative_to(ROOT).parts[0] != ".git"
    )
    require(
        not unexpected_files,
        "final confirmation authorized only learning_plan.md; unexpected artifact(s): " + ", ".join(unexpected_files),
    )

    print("learning plan satisfies the corrected conversation state and final delivery contract")


if __name__ == "__main__":
    main()
