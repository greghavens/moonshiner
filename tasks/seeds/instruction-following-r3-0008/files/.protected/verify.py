#!/usr/bin/env python3
from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "learning_plan.md"
CATALOG = ROOT / "learning_modules.csv"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


if not PLAN.is_file():
    fail("learning_plan.md is missing")

text = PLAN.read_text(encoding="utf-8")
if not text.endswith("\n"):
    fail("learning_plan.md must end with a newline")

with CATALOG.open(newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))
by_title = {row["title"]: row for row in rows}
if len(by_title) != len(rows):
    fail("learning_modules.csv contains duplicate module titles")

if "poster" in text.casefold():
    fail("the superseded poster capstone is still mentioned")
for row in rows:
    if row["code"] in text:
        fail(f"catalog code {row['code']} must be omitted")
    if row["facilitator"].casefold() in text.casefold():
        fail(f"facilitator name {row['facilitator']} must be omitted")

lines = text.splitlines()
headings = [
    line
    for line in lines
    if re.match(r"^\s{0,3}#{1,6}(?:\s|$)", line)
]
setext_headings = [
    lines[index - 1]
    for index in range(1, len(lines))
    if lines[index - 1].strip()
    and re.fullmatch(r"\s{0,3}(?:=+|-+)\s*", lines[index])
]
expected_headings = [
    "# Four-Week Evidence Literacy Plan",
    "## Confirmed choices",
    "## Week 1 — Frame the Question",
    "## Week 2 — Evaluate Sources",
    "## Week 3 — Build the Claim",
    "## Week 4 — Publish and Reflect",
    "## Assessment checkpoints",
    "## Materials summary",
]
if headings != expected_headings or setext_headings:
    fail("headings are missing, extra, or out of order")

expected_choices = [
    "- Learner: Grade 9",
    "- Focus: Community water quality",
    "- Schedule: Tuesday, Thursday, Saturday",
    "- Access: Offline-capable",
    "- Feedback: Paired peer review",
    "- Capstone: Three-minute recorded audio briefing",
    "- Total scheduled time: 460 minutes",
]
choices_start = lines.index("## Confirmed choices") + 1
choices_end = lines.index("## Week 1 — Frame the Question")
choices = [line for line in lines[choices_start:choices_end] if line.strip()]
if choices != expected_choices:
    fail("Confirmed choices must contain exactly the seven requested bullets in order")

week_specs = [
    (1, "## Week 1 — Frame the Question"),
    (2, "## Week 2 — Evaluate Sources"),
    (3, "## Week 3 — Build the Claim"),
    (4, "## Week 4 — Publish and Reflect"),
]
expected_sequence = [
    ("Tuesday", "Launch"),
    ("Thursday", "Practice"),
    ("Saturday", "Check"),
]
row_pattern = re.compile(
    r"^\| (Tuesday|Thursday|Saturday) \| (Launch|Practice|Check) \| "
    r"([^|]+) \| (\d+) \| ([^|]+) \| ([^|]+) \|$"
)
selected: list[dict[str, str]] = []

for index, (week, heading) in enumerate(week_specs):
    start = lines.index(heading) + 1
    next_heading = (week_specs[index + 1][1]
                    if index + 1 < len(week_specs)
                    else "## Assessment checkpoints")
    end = lines.index(next_heading)
    block = [line for line in lines[start:end] if line.strip()]
    if len(block) != 5:
        fail(f"{heading} must contain only one three-row schedule table")
    if block[0] != "| Day | Phase | Module | Minutes | Materials | Evidence of learning |":
        fail(f"{heading} has the wrong table header")
    if block[1] != "|---|---|---|---:|---|---|":
        fail(f"{heading} has the wrong table separator")

    observed_sequence: list[tuple[str, str]] = []
    for line in block[2:]:
        match = row_pattern.fullmatch(line)
        if not match:
            fail(f"malformed schedule row in {heading}: {line}")
        day, phase, title, minutes_text, materials, evidence = (
            value.strip() for value in match.groups()
        )
        observed_sequence.append((day, phase))
        module = by_title.get(title)
        if module is None:
            fail(f"unknown module title {title!r}")
        if int(module["week"]) != week or module["phase"] != phase:
            fail(f"{title} does not match Week {week} and phase {phase}")
        if module["topic"] != "community-water":
            fail(f"{title} does not match the retained community-water focus")
        if module["offline_capable"] != "yes":
            fail(f"{title} is not offline-capable")
        if int(module["duration_minutes"]) > 50:
            fail(f"{title} exceeds the 50-minute session limit")
        if week == 4 and module["capstone_format"] != "audio":
            fail(f"{title} does not implement the corrected audio capstone")
        if week < 4 and module["capstone_format"] != "any":
            fail(f"{title} is not a general pre-capstone module")
        if minutes_text != module["duration_minutes"]:
            fail(f"{title} duration differs from learning_modules.csv")
        if materials != module["materials"]:
            fail(f"{title} materials differ from learning_modules.csv")
        if evidence != module["evidence"]:
            fail(f"{title} evidence text differs from learning_modules.csv")
        selected.append(module)
    if observed_sequence != expected_sequence:
        fail(f"{heading} rows are missing or out of order")

titles = [row["title"] for row in selected]
if len(titles) != 12 or len(set(titles)) != 12:
    fail("the plan must contain 12 distinct modules")
if sum(int(row["duration_minutes"]) for row in selected) != 460:
    fail("selected module durations do not total 460 minutes")

tags = [set(filter(None, row["tags"].split(";"))) for row in selected]
if sum("library" in item for item in tags) != 1:
    fail("the plan must contain exactly one library-tagged module")
if sum("peer-review" in item for item in tags) != 1:
    fail("the plan must contain exactly one peer-review-tagged module")
screen_by_week = Counter(
    int(row["week"]) for row in selected if row["screen_heavy"] == "yes"
)
if any(count > 2 for count in screen_by_week.values()):
    fail("a week contains more than two screen-heavy modules")

assessment_start = lines.index("## Assessment checkpoints") + 1
assessment_end = lines.index("## Materials summary")
assessments = [line for line in lines[assessment_start:assessment_end] if line.strip()]
labels = [
    "- Question:",
    "- Source judgment:",
    "- Evidence reasoning:",
    "- Communication and reflection:",
]
if len(assessments) != 4 or any(
    not line.startswith(label) for line, label in zip(assessments, labels)
):
    fail("Assessment checkpoints must contain exactly four rubric-ordered bullets")

question = assessments[0].casefold()
if not all(term in question for term in ("focused question", "community water quality", "evidence")):
    fail("Question checkpoint is not concrete for the selected focus")
if not re.search(r"(?:at least )?(?:three|3) observations", question):
    fail("Question checkpoint must require at least three observations")
source = assessments[1].casefold()
if not all(term in source for term in ("authority", "currency", "relevance", "corroboration")):
    fail("Source judgment checkpoint omits a rubric dimension")
if not re.search(r"(?:three|3) sources", source):
    fail("Source judgment checkpoint must cover at least three sources")
reasoning = assessments[2].casefold()
if "claim" not in reasoning or not re.search(
    r"(?:at least )?(?:two|2) data points", reasoning
):
    fail("Evidence reasoning checkpoint must cover at least two data points")
if not re.search(r"\b(?:address|answer|engage|rebut|reply|respond|response)\b", reasoning):
    fail("Evidence reasoning checkpoint must require a response to a counterclaim")
if not re.search(r"(?:at least )?(?:one|1) evidence-based counterclaim\b", reasoning):
    fail("Evidence reasoning checkpoint must respond to an evidence-based counterclaim")
communication = assessments[3].casefold()
if not all(
    term in communication
    for term in ("three-minute", "recorded audio briefing", "paired peer review")
):
    fail("Communication checkpoint does not retain the corrected capstone and feedback choice")
if not re.search(r"(?:at least )?(?:one|1) revision\b", communication):
    fail("Communication checkpoint must require one feedback-prompted revision")
if not re.search(
    r"(?:one|1) revision.{0,80}paired peer review|"
    r"paired peer review.{0,80}(?:one|1) revision",
    communication,
):
    fail("Communication checkpoint must link the revision to paired peer review")

materials_start = assessment_end + 1
materials_block = [line for line in lines[materials_start:] if line.strip()]
if len(materials_block) != 14:
    fail("Materials summary must contain its header and all 12 unique materials")
if materials_block[:2] != ["| Material | First used |", "|---|---|"]:
    fail("Materials summary has the wrong table header")

material_rows: list[tuple[str, str]] = []
for line in materials_block[2:]:
    match = re.fullmatch(r"\| ([^|]+) \| (Week [1-4]) \|", line)
    if not match:
        fail(f"malformed material row: {line}")
    material_rows.append(tuple(part.strip() for part in match.groups()))

expected_materials: list[tuple[str, str]] = []
seen_materials: set[str] = set()
for module in selected:
    material = module["materials"]
    if material not in seen_materials:
        seen_materials.add(material)
        expected_materials.append((material, f"Week {module['week']}"))
if material_rows != expected_materials:
    fail("materials are missing, duplicated, altered, or out of first-use order")

allowed_top_level = {
    ".git",
    ".protected",
    ".sandbox-home",
    "assessment_rubric.md",
    "learning_modules.csv",
    "learning_plan.md",
}
extras = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed_top_level)
if extras:
    fail(f"unexpected extra top-level artifacts: {extras}")
protected_extras = sorted(
    path.name for path in (ROOT / ".protected").iterdir() if path.name != "verify.py"
)
if protected_extras:
    fail(f"unexpected artifacts under .protected/: {protected_extras}")

print("PASS: learning_plan.md satisfies the corrected multi-turn education brief")
