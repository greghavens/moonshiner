#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "volunteer_onboarding_plan.md"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


if not PLAN.is_file():
    fail("volunteer_onboarding_plan.md is missing")

text = PLAN.read_text(encoding="utf-8")
if not text.endswith("\n"):
    fail("volunteer_onboarding_plan.md must end with a newline")

headings = re.findall(r"(?m)^#{1,6} .+$", text)
expected_headings = [
    "# Harborlight Volunteer Onboarding Pilot",
    "## Administrative Decision",
    "## Record Synthesis",
    "## Operating Plan",
    "## Tracking",
    "## Risks and Responses",
    "## Sources",
]
if headings != expected_headings:
    fail(f"headings differ from the required ordered set: {headings!r}")

words = re.findall(r"\b[\w%–.-]+\b", text, flags=re.UNICODE)
if not 550 <= len(words) <= 750:
    fail(f"plan must contain 550–750 words; found {len(words)}")


def section(name: str, next_name: str | None) -> str:
    start = text.index(name) + len(name)
    end = text.index(next_name, start) if next_name else len(text)
    return text[start:end]


decision = section("## Administrative Decision", "## Record Synthesis")
synthesis = section("## Record Synthesis", "## Operating Plan")
operating = section("## Operating Plan", "## Tracking")
tracking = section("## Tracking", "## Risks and Responses")
risks = section("## Risks and Responses", "## Sources")
sources = section("## Sources", None)


def require(pattern: str, value: str, message: str) -> None:
    if not re.search(pattern, value, flags=re.IGNORECASE | re.DOTALL):
        fail(message)


require(r"operations director", decision + operating,
        "plan must address the operations director")
require(r"site coordinators?", decision + operating,
        "plan must address site coordinators")
require(r"(?=[\s\S]*(?:eight|8)[- ]week)(?=[\s\S]*in[- ]person)(?=[\s\S]*(?:weekly|each week|one session))",
        decision + operating,
        "plan must retain the eight-week, in-person, weekly orientation model")
require(r"(?:Riverside Pantry[- ]only|only\s+(?:at\s+)?Riverside Pantry)", decision,
        "Administrative Decision must state the corrected Riverside Pantry-only scope")
require(r"Tuesday.{0,45}6(?::00)?.{0,50}8(?::00)?.{0,20}(?:p\.?m\.?)",
        decision + operating,
        "plan must retain the Tuesday 6:00–8:00 p.m. schedule")
require(r"(?=[\s\S]*(?:one|1)\s+volunteer coordinator)(?=[\s\S]*(?:two|2)\s+peer mentors?)",
        decision + operating,
        "plan must retain one volunteer coordinator and two peer mentors per session")
require(r"web[- ]form|web form", decision + operating,
        "plan must retain web-form registration")
require(r"phone(?:[- ]registration|\s+registration|\s+option|\s+with)", decision + operating,
        "plan must retain phone registration")

# Northside may be discussed as a comparison or explicitly excluded. Reject only
# language that affirmatively puts it inside the final pilot's operating scope.
for sentence in re.split(r"(?<=[.!?])\s+|\n+", decision + operating):
    if "northside" not in sentence.casefold():
        continue
    if re.search(r"\b(?:not|exclude(?:d|s)?|rather than|instead of|comparison)\b",
                 sentence, re.IGNORECASE):
        continue
    if re.search(r"\b(?:pilot at|operate at|run at|serve|include|scope|both sites|each site)\b",
                 sentence, re.IGNORECASE):
        fail("the final operating scope affirmatively retains Northside Pantry")

# The synthesis must explain why Riverside is the corrected focus by comparing
# its recorded administrative performance with Northside and incorporating the
# feedback. Do not prescribe a single valid selection of the packet's figures.
require(r"\bRiverside(?: Pantry)?\b", synthesis,
        "Record Synthesis must discuss Riverside's recorded performance")
require(r"\bNorthside(?: Pantry)?\b", synthesis,
        "Record Synthesis must compare Riverside with Northside")
require(r"\b(?:lower|higher|longer|shorter|trail(?:s|ed)?|compar(?:e|ed|ison)|versus|vs\.?)\b",
        synthesis,
        "Record Synthesis must make an administrative comparison between the sites")

comparison_topics = (
    r"orientation.{0,35}attend|attendance.{0,35}orientation",
    r"first[- ]shift|shift.{0,30}(?:14\s+days?|completion)",
    r"processing.{0,30}(?:time|days?)|application.{0,30}processing",
)
if sum(bool(re.search(pattern, synthesis, re.IGNORECASE | re.DOTALL))
       for pattern in comparison_topics) < 2:
    fail("Record Synthesis must compare at least two recorded administrative measures")

feedback_findings = (
    r"\b47\s+(?:of|/)\s*124\b",
    r"\b66\s*%|weekday evening",
    r"\b18\b.{0,60}phone|phone.{0,60}\b18\b",
    r"\b13\b.{0,80}(?:unsure|application|next)",
    r"\b10\b.{0,60}(?:in[- ]person|forms?)",
    r"single contact|first[- ]shift process|reminder",
)
if not any(re.search(pattern, synthesis, re.IGNORECASE | re.DOTALL)
           for pattern in feedback_findings):
    fail("Record Synthesis must incorporate a finding from Volunteer Feedback")
if "[Volunteer Feedback]" not in synthesis:
    fail("feedback findings in Record Synthesis must cite [Volunteer Feedback]")

for label in ("[Volunteer Log]", "[Volunteer Feedback]", "[Operations Note]"):
    if label not in synthesis + operating + tracking + risks:
        fail(f"packet claims must cite {label}")

if not re.search(r"\b(?:recorded|observed|logged)\b", decision + synthesis,
                 re.IGNORECASE):
    fail("recorded findings must be distinguished from proposed targets")
if not re.search(r"\bproposed\s+(?:week\s*8\s+)?targets?\b", decision + synthesis + tracking,
                 re.IGNORECASE):
    fail("Week 8 figures must be identified as proposed targets")

limitation_categories = (
    # Orientation-sheet transcription or incomplete phone-in counts.
    r"(?:manual|hand[- ]transcrib|transcribed).{0,80}(?:record|sheet|log)|"
    r"phone.{0,50}(?:missing|absent|understat)",
    # The first-shift measure's platform coverage or short follow-up window.
    r"scheduling platform.{0,80}(?:only|does not|did not|omit|capture)|"
    r"(?:does not|cannot).{0,50}(?:long[- ]term|retention)",
    # The voluntary feedback sample's response and representation limits.
    r"(?:voluntary|nonprobability).{0,60}(?:questionnaire|survey|feedback|sample)|"
    r"(?:47\s+(?:of|/)\s*124|response.{0,30}(?:rate|group))|"
    r"(?:strong views|Riverside respondents).{0,60}(?:overrepresent|more than)|"
    r"not.{0,40}(?:organization[- ]wide|generaliz)",
)
if sum(bool(re.search(pattern, synthesis, re.IGNORECASE | re.DOTALL))
       for pattern in limitation_categories) < 2:
    fail("Record Synthesis must explain at least two packet-supported evidence limitations")

# Locate and validate the required Markdown table semantically.
table_lines = [line.strip() for line in tracking.splitlines()
               if line.strip().startswith("|")]
table_rows = [[cell.strip() for cell in line.strip().strip("|").split("|")]
              for line in table_lines]
header_indexes = [
    index for index, row in enumerate(table_rows)
    if [cell.casefold() for cell in row]
    == ["metric", "recorded baseline", "week 8 target"]
]
if len(header_indexes) != 1:
    fail("Tracking must contain one Markdown table with the required three columns")
header_index = header_indexes[0]
if header_index + 4 >= len(table_rows):
    fail("Tracking table must contain an alignment row and exactly three data rows")
alignment = table_rows[header_index + 1]
if len(alignment) != 3 or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in alignment):
    fail("Tracking table is missing a valid Markdown alignment row")
data_rows = table_rows[header_index + 2:]
if len(data_rows) != 3 or any(len(row) != 3 for row in data_rows):
    fail("Tracking table must contain exactly three data rows")

row_by_kind: dict[str, list[str]] = {}
for row in data_rows:
    metric = row[0].casefold()
    if "orientation" in metric and "attendance" in metric and "rate" in metric:
        kind = "attendance"
    elif "first" in metric and "shift" in metric and "14" in metric:
        kind = "first_shift"
    elif "median" in metric and "application" in metric and "processing" in metric:
        kind = "processing"
    else:
        fail(f"unexpected tracking metric: {row[0]!r}")
    if kind in row_by_kind:
        fail(f"duplicate tracking metric: {row[0]!r}")
    row_by_kind[kind] = row
if set(row_by_kind) != {"attendance", "first_shift", "processing"}:
    fail("Tracking table does not cover the three required metrics")

attendance_baseline, attendance_target = row_by_kind["attendance"][1:]
shift_baseline, shift_target = row_by_kind["first_shift"][1:]
processing_baseline, processing_target = row_by_kind["processing"][1:]
if not re.search(r"\b64\s*%", attendance_baseline):
    fail("orientation attendance must use Riverside's recorded 64% baseline")
if not (re.search(r"\b75\s*%", attendance_target)
        and re.search(r"(?:at least|or more|≥|>=)", attendance_target, re.IGNORECASE)):
    fail("orientation attendance must use the proposed target of at least 75%")
if not re.search(r"\b70\s*%", shift_baseline):
    fail("first-shift completion must use Riverside's recorded 70% baseline")
if not (re.search(r"\b80\s*%", shift_target)
        and re.search(r"(?:at least|or more|≥|>=)", shift_target, re.IGNORECASE)):
    fail("first-shift completion must use the proposed target of at least 80%")
if not re.search(r"\b5\s+business days?", processing_baseline, re.IGNORECASE):
    fail("application processing must use Riverside's five-business-day baseline")
if not (re.search(r"\b2\s+business days?", processing_target, re.IGNORECASE)
        and re.search(r"(?:or less|at most|≤|<=)", processing_target, re.IGNORECASE)):
    fail("application processing must use the proposed target of two business days or less")

risk_response_pairs = (
    (r"(?:web|phone).{0,70}(?:duplicate|omit|separate|stream)",
     r"(?:shared|single).{0,30}roster|reconcil"),
    (r"incomplete.{0,40}(?:application|field)",
     r"review.{0,50}(?:Monday|before)|contact.{0,50}before"),
    (r"(?:mentor absence|mentor.{0,30}absent)",
     r"confirm.{0,40}48\s*hours|reassign.{0,30}mentor"),
    (r"(?:personal details|privacy|full applicant roster)",
     r"(?:master roster|aggregate|only.{0,30}(?:need|necessary))"),
)
pairs_found = sum(
    bool(re.search(risk_pattern, risks, re.IGNORECASE | re.DOTALL)
         and re.search(response_pattern, risks, re.IGNORECASE | re.DOTALL))
    for risk_pattern, response_pattern in risk_response_pairs
)
if pairs_found < 2:
    fail("Risks and Responses must include at least two packet-supported risks with concrete responses")
if "[Operations Note]" not in risks:
    fail("administrative risks and responses must cite [Operations Note]")

source_lines = [line.strip() for line in sources.splitlines() if line.strip()]
expected_paths = {
    "evidence/01_volunteer_log.md",
    "evidence/02_volunteer_feedback.md",
    "evidence/03_operations_note.md",
}
if len(source_lines) != 3 or any(not line.startswith("- ") for line in source_lines):
    fail("Sources must consist of exactly three Markdown bullets")
listed_paths: list[str] = []
for line in source_lines:
    matches = [path for path in expected_paths if path in line]
    if len(matches) != 1:
        fail(f"source bullet must name exactly one evidence file: {line!r}")
    listed_paths.append(matches[0])
if set(listed_paths) != expected_paths:
    fail("Sources must list each evidence file exactly once")

allowed_top_level = {
    "evidence", ".protected", "volunteer_onboarding_plan.md", ".git", ".sandbox-home"
}
extras = sorted(path.name for path in ROOT.iterdir()
                if path.name not in allowed_top_level)
if extras:
    fail(f"unexpected extra top-level artifacts: {extras}")
expected_evidence = {
    "01_volunteer_log.md", "02_volunteer_feedback.md", "03_operations_note.md"
}
evidence_extras = sorted(
    path.name for path in (ROOT / "evidence").iterdir()
    if path.name not in expected_evidence
)
if evidence_extras:
    fail(f"unexpected artifacts under evidence/: {evidence_extras}")
protected_extras = sorted(
    path.name for path in (ROOT / ".protected").iterdir()
    if path.name != "verify.py"
)
if protected_extras:
    fail(f"unexpected artifacts under .protected/: {protected_extras}")

print(f"PASS: volunteer_onboarding_plan.md satisfies the corrected multi-turn plan ({len(words)} words)")
