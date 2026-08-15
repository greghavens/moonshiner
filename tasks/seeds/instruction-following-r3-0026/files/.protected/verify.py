from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "operations_plan.md"


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def section(text: str, heading: str, next_heading: str | None) -> str:
    start_token = heading + "\n"
    start = text.find(start_token)
    if start < 0:
        fail(f"missing heading {heading!r}")
    start += len(start_token)
    if next_heading is None:
        return text[start:]
    end = text.find(next_heading + "\n", start)
    if end < 0:
        fail(f"missing heading {next_heading!r}")
    return text[start:end]


def require(pattern: str, text: str, message: str, flags: int = 0) -> None:
    if re.search(pattern, text, flags) is None:
        fail(message)


def table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [cell.strip() for cell in stripped[1:-1].split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return rows


def require_fact_row(
    rows: list[list[str]],
    values: tuple[str, ...],
    evidence_type: str,
    source: str,
    message: str,
) -> None:
    for row in rows[1:]:
        if (
            len(row) == 3
            and all(value in row[0] for value in values)
            and row[1].casefold() == evidence_type.casefold()
            and row[2] == source
        ):
            return
    fail(message)


if not OUTPUT.is_file():
    fail("operations_plan.md has not been created")

allowed = {
    Path("operations_plan.md"),
    Path("demand_forecast.csv"),
    Path("capacity_note.md"),
    Path("partner_sheet.md"),
    Path(".protected/verify.py"),
}
actual = {
    path.relative_to(ROOT)
    for path in ROOT.rglob("*")
    if path.is_file()
    and ".git" not in path.parts
    and ".sandbox-home" not in path.parts
    and "__pycache__" not in path.parts
}
extra = sorted(str(path) for path in actual - allowed)
if extra:
    fail("unexpected file(s) created: " + ", ".join(extra))

text = OUTPUT.read_text(encoding="utf-8")
words = re.findall(r"\b[\w’'-]+\b", text)
if not 550 <= len(words) <= 750:
    fail(f"expected 550-750 words, found {len(words)}")

expected_headings = [
    "# Q4 Overflow Fulfillment Plan",
    "## Decision summary",
    "## Sourced facts",
    "## Recommendation",
    "## Weekly allocation",
    "## Cost estimate",
    "## Uncertainty",
    "## Actions",
]
actual_headings = re.findall(r"^#{1,6} .+$", text, re.MULTILINE)
if actual_headings != expected_headings:
    fail(f"headings or heading order differ: {actual_headings!r}")

parts = {
    heading: section(text, heading, expected_headings[index + 1] if index + 1 < len(expected_headings) else None)
    for index, heading in enumerate(expected_headings)
}

decision_bullets = re.findall(r"^- .+$", parts["## Decision summary"], re.MULTILINE)
if len(decision_bullets) != 3:
    fail("Decision summary must contain exactly three bullets")
require(r"base forecast", decision_bullets[0], "first decision bullet must identify the base forecast", re.I)
require(r"Meridian Fulfillment", decision_bullets[1], "second decision bullet must identify Meridian Fulfillment")
require(r"75%.*Meridian.*25%.*overtime", decision_bullets[2], "third decision bullet must state the corrected allocation", re.I)

if re.search(r"\b65\s*%|\b35\s*%|65\s*/\s*35", text):
    fail("the superseded allocation appears in the final plan")

facts = parts["## Sourced facts"]
require(r"^\|\s*Fact\s*\|\s*Evidence type\s*\|\s*Source\s*\|$", facts, "Sourced facts table header is missing", re.M)
fact_rows = table_rows(facts)
if not fact_rows or fact_rows[0] != ["Fact", "Evidence type", "Source"]:
    fail("Sourced facts table must use exactly the requested columns")
require_fact_row(fact_rows, ("12,000",), "Observed", "[Capacity Note]", "capacity baseline must be identified as observed evidence from [Capacity Note]")
for date, units in (
    ("2026-11-02", "14,400"),
    ("2026-11-09", "16,800"),
    ("2026-11-16", "19,200"),
    ("2026-11-23", "15,600"),
):
    require_fact_row(fact_rows, (date, units), "Forecast", "[Demand Forecast]", f"base forecast for {date} must be identified as a forecast from [Demand Forecast]")
require_fact_row(fact_rows, ("2,000",), "Operating limit", "[Capacity Note]", "overtime capacity must be identified as an operating limit from [Capacity Note]")
require_fact_row(fact_rows, ("1,500", "96.2%"), "Observed", "[Capacity Note]", "pilot result must be identified as observed evidence from [Capacity Note]")
require_fact_row(fact_rows, ("6,000", "$2.40", "$1,500"), "Proposal term", "[Partner Sheet]", "Meridian capacity and fees must be identified as proposal terms from [Partner Sheet]")
require_fact_row(fact_rows, ("98.0%",), "Supplier projection", "[Partner Sheet]", "Meridian service level must be identified as a supplier projection from [Partner Sheet]")
if re.search(r"\b(?:recommend|should|propose)\b", facts, re.I):
    fail("recommendation language appears in Sourced facts")

recommendation = parts["## Recommendation"]
require(r"\brecommend\b", recommendation, "Recommendation section must make a recommendation", re.I)
require(r"600\s+units", recommendation, "Recommendation must explain Meridian headroom in the peak week", re.I)
require(r"200\s+units", recommendation, "Recommendation must explain overtime headroom in the peak week", re.I)

weekly = parts["## Weekly allocation"]
require(r"^\|\s*Week of\s*\|\s*Forecast units\s*\|\s*Baseline capacity\s*\|\s*Overflow\s*\|\s*Meridian \(75%\)\s*\|\s*Overtime \(25%\)\s*\|$", weekly, "Weekly allocation table header is missing", re.M)
expected_rows = [
    ("2026-11-02", "14,400", "12,000", "2,400", "1,800", "600"),
    ("2026-11-09", "16,800", "12,000", "4,800", "3,600", "1,200"),
    ("2026-11-16", "19,200", "12,000", "7,200", "5,400", "1,800"),
    ("2026-11-23", "15,600", "12,000", "3,600", "2,700", "900"),
    ("Total", "66,000", "48,000", "18,000", "13,500", "4,500"),
]
expected_weekly_table = [
    ["Week of", "Forecast units", "Baseline capacity", "Overflow", "Meridian (75%)", "Overtime (25%)"],
    *[list(row) for row in expected_rows],
]
if table_rows(weekly) != expected_weekly_table:
    fail("Weekly allocation must contain exactly the requested columns and allocation rows")

cost = parts["## Cost estimate"]
require(r"^\|\s*Item\s*\|\s*Amount\s*\|$", cost, "Cost table header is missing", re.M)
expected_costs = [
    ("Meridian handling", "$32,400.00"),
    ("Meridian setup", "$1,500.00"),
    ("Overtime handling", "$13,950.00"),
    ("Total incremental cost", "$47,850.00"),
]
expected_cost_table = [["Item", "Amount"], *[list(row) for row in expected_costs]]
if table_rows(cost) != expected_cost_table:
    fail("Cost estimate must contain exactly the four requested rows in order")
cost_lines = cost.splitlines()
total_index = next((index for index, line in enumerate(cost_lines) if "| Total incremental cost |" in line), None)
if total_index is None:
    fail("total incremental cost row is missing")
after_table = next((line for line in cost_lines[total_index + 1 :] if line.strip()), "")
for excluded in ("returns", "rework", "downstream freight"):
    require(excluded, after_table, f"the statement immediately below the cost table must mention {excluded}", re.I)
if "[Capacity Note]" not in after_table:
    fail("cost exclusion statement must cite [Capacity Note]")

uncertainty = parts["## Uncertainty"]
uncertainty_bullets = re.findall(r"^- .+$", uncertainty, re.MULTILINE)
if len(uncertainty_bullets) != 3:
    fail("Uncertainty must contain exactly three bullets")
for bullet in uncertainty_bullets:
    require(r"Uncertainty:", bullet, "each uncertainty bullet must identify the uncertainty")
    require(r"Impact:", bullet, "each uncertainty bullet must state its impact")
    require(r"Recommended response:", bullet, "each uncertainty bullet must recommend a response")
for bullet in uncertainty_bullets:
    if not any(citation in bullet for citation in ("[Demand Forecast]", "[Capacity Note]", "[Partner Sheet]")):
        fail("each uncertainty bullet must cite its sourced factual basis")

actions = parts["## Actions"]
action_lines = re.findall(r"^\d+\. .+$", actions, re.MULTILINE)
if len(action_lines) != 4 or [line.split(".", 1)[0] for line in action_lines] != ["1", "2", "3", "4"]:
    fail("Actions must contain exactly four numbered items")
for action in action_lines:
    require(r"Owner:\s*(?!Due\b)\S", action, "each action must state a nonempty owner")
    require(r"Due:\s*(?!Owner\b)\S", action, "each action must state a nonempty due date")

print("PASS: operations_plan.md satisfies the accumulated and corrected requirements")
