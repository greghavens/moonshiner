#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / "policy_brief.md"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


if not BRIEF.is_file():
    fail("policy_brief.md is missing")

text = BRIEF.read_text(encoding="utf-8")
if not text.endswith("\n"):
    fail("policy_brief.md must end with a newline")

headings = re.findall(r"(?m)^#{1,6} .+$", text)
expected_headings = [
    "# Riverton Late-Shift Mobility Pilot",
    "## Decision",
    "## Evidence",
    "## Pilot Design",
    "## Measures",
    "## Risks and Mitigations",
    "## Sources",
]
if headings != expected_headings:
    fail(f"headings differ from the required ordered set: {headings!r}")

words = re.findall(r"\b[\w$%–.-]+\b", text, flags=re.UNICODE)
if not 600 <= len(words) <= 850:
    fail(f"brief must contain 600–850 words; found {len(words)}")

def section(name: str, next_name: str | None) -> str:
    start = text.index(name) + len(name)
    end = text.index(next_name, start) if next_name else len(text)
    return text[start:end]


decision = section("## Decision", "## Evidence")
evidence = section("## Evidence", "## Pilot Design")
pilot_design = section("## Pilot Design", "## Measures")
measures = section("## Measures", "## Risks and Mitigations")
risks = section("## Risks and Mitigations", "## Sources")
sources = section("## Sources", None)


def require(pattern: str, value: str, message: str) -> None:
    if not re.search(pattern, value, flags=re.IGNORECASE | re.DOTALL):
        fail(message)


require(r"Riverton City Council Transportation Committee", decision,
        "Decision must address the Riverton City Council Transportation Committee")
require(r"(?=[\s\S]*six[- ]month)(?=[\s\S]*fare[- ]free)(?=[\s\S]*on[- ]demand)(?=[\s\S]*shuttle)",
        decision, "Decision must recommend the retained six-month, fare-free, on-demand shuttle pilot")
require(r"(?:Northpoint[- ]only|only\s+(?:in\s+)?Northpoint)", decision,
        "Decision must state the corrected Northpoint-only service area unambiguously")
require(r"10(?::00)?\s*p\.?m\.?.{0,50}3(?::00)?\s*a\.?m\.?", decision + pilot_design,
        "brief must retain the 10:00 p.m.–3:00 a.m. service window")
require(r"(?=[\s\S]*(?:two|2)[\s\S]{0,80}vehicles?)(?=[\s\S]*wheelchair[- ]accessible)",
        decision + pilot_design,
        "brief must retain two wheelchair-accessible vehicles")
require(r"\bapp\b", decision + pilot_design, "brief must retain app booking")
require(r"phone(?:[- ]booking|\s+booking|\s+line)?", decision + pilot_design,
        "brief must retain phone booking")

# A valid brief may explicitly say that Eastbank is excluded, so only affirmative
# scope language in the decision and design sections is rejected.
for sentence in re.split(r"(?<=[.!?])\s+|\n+", decision + pilot_design):
    if "eastbank" not in sentence.casefold():
        continue
    if re.search(r"\b(?:not|exclude(?:d|s)?|rather than|instead of)\b", sentence, re.IGNORECASE):
        continue
    if re.search(r"\b(?:serve|serves|serving|service area|cover|covers|operate in|within|include|includes|including)\b",
                 sentence, re.IGNORECASE):
        fail("the final recommendation affirmatively retains Eastbank in the service area")

# The final synthesis must use all three packet sources and present the main
# quantitative basis for need, model preference, and the option comparison.
fact_patterns = {
    "Northpoint need and coverage": r"(?=[\s\S]*\b720\b)(?=[\s\S]*\b120\b)(?=[\s\S]*\b41\s*%)",
    "service preferences": r"(?=[\s\S]*\b214\b)(?=[\s\S]*\b62\s*%)(?=[\s\S]*\b24\s*%)",
    "projected option comparison": r"(?=[\s\S]*\$\s*286,?000\b)(?=[\s\S]*\$\s*410,?000\b)",
}
for label, pattern in fact_patterns.items():
    if not re.search(pattern, evidence, flags=re.IGNORECASE):
        fail(f"missing quantitative evidence synthesis: {label}")

for label in ("[Service Gap Scan]", "[Community Survey]", "[Options Memo]"):
    if label not in evidence + pilot_design + measures:
        fail(f"packet claims must cite {label}")

option_paragraphs = [
    paragraph for paragraph in re.split(r"\n\s*\n", evidence)
    if "[Options Memo]" in paragraph
]
if not any(
    re.search(r"\$\s*286,?000", paragraph)
    and re.search(r"\$\s*410,?000", paragraph)
    and re.search(r"\bproject(?:ed|ion|ions)\b", paragraph, re.IGNORECASE)
    for paragraph in option_paragraphs
):
    fail("the cited option cost comparison must be identified as projected")

observed_and_projected = decision + evidence
if not re.search(r"\b(?:observed|measured|reported|survey(?:ed)?)\b", observed_and_projected, re.IGNORECASE):
    fail("observed or reported packet findings must be distinguished from projections")
if not re.search(r"\bproject(?:ed|ion|ions)\b", observed_and_projected, re.IGNORECASE):
    fail("option-memo projections must be identified as projections")

limitation_patterns = (
    r"convenience sample",
    r"(?:does not|did not|cannot|can(?:no|')t) establish.{0,60}caus",
    r"endpoint sample.{0,60}(?:not|every|incomplete|limited)",
    r"exclude[sd]? spring",
    r"not.{0,30}citywide",
    r"(?:(?:voluntary|nonprobability).{0,30}survey|survey.{0,40}(?:voluntary|nonprobability))",
    r"strong views.{0,30}overrepresented",
    r"not.{0,40}verified.{0,40}(?:worker|late.shift)",
    r"neither (?:service )?option.{0,50}(?:piloted|operated)",
)
limitations_found = sum(bool(re.search(pattern, evidence, re.IGNORECASE | re.DOTALL))
                        for pattern in limitation_patterns)
if limitations_found < 2:
    fail("Evidence must explain at least two packet-supported evidence limitations")

# Find the required Markdown table and validate its three semantic rows without
# dictating capitalization, units, or one exact wording of the cells.
table_lines = [line.strip() for line in measures.splitlines() if line.strip().startswith("|")]
table_rows = [[cell.strip() for cell in line.strip().strip("|").split("|")]
              for line in table_lines]
header_indexes = [index for index, row in enumerate(table_rows)
                  if [cell.casefold() for cell in row] == ["measure", "baseline", "month 6 target"]]
if len(header_indexes) != 1:
    fail("Measures must contain one Markdown table with columns Measure, Baseline, and Month 6 target")
header_index = header_indexes[0]
if header_index + 4 >= len(table_rows):
    fail("Measures table must contain an alignment row and exactly three data rows")
alignment = table_rows[header_index + 1]
if len(alignment) != 3 or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in alignment):
    fail("Measures table is missing a valid Markdown alignment row")
data_rows = table_rows[header_index + 2:]
if len(data_rows) != 3 or any(len(row) != 3 for row in data_rows):
    fail("Measures table must contain exactly three data rows")

row_by_kind: dict[str, list[str]] = {}
for row in data_rows:
    measure = row[0].casefold()
    if "completed" in measure and "passenger trip" in measure:
        kind = "trips"
    elif "median" in measure and "pickup wait" in measure:
        kind = "wait"
    elif "missed" in measure and "shortened" in measure and "shift" in measure:
        kind = "shifts"
    else:
        fail(f"unexpected measure row: {row[0]!r}")
    if kind in row_by_kind:
        fail(f"duplicate measure row: {row[0]!r}")
    row_by_kind[kind] = row
if set(row_by_kind) != {"trips", "wait", "shifts"}:
    fail("Measures table does not cover the three required measures")

trips_baseline, trips_target = row_by_kind["trips"][1:]
wait_baseline, wait_target = row_by_kind["wait"][1:]
shift_baseline, shift_target = row_by_kind["shifts"][1:]
if not re.search(r"(?:\b0\b|\bzero\b)", trips_baseline, re.IGNORECASE):
    fail("completed passenger trips must use the packet baseline of 0")
if not (re.search(r"\b1,?000\b", trips_target)
        and re.search(r"(?:at least|or more|≥|>=)", trips_target, re.IGNORECASE)):
    fail("completed passenger trips must use the packet target of at least 1,000")
if not re.search(r"(?:not applicable|no applicable|no baseline|\bn\s*/?\s*a\b)", wait_baseline, re.IGNORECASE):
    fail("median pickup wait must use the packet's not-applicable baseline")
if not (re.search(r"\b20\b", wait_target)
        and re.search(r"(?:or less|at most|≤|<=)", wait_target, re.IGNORECASE)):
    fail("median pickup wait must use the packet target of 20 minutes or less")
if not re.search(r"\b2\.1\b", shift_baseline):
    fail("missed or shortened shifts must use the packet baseline of 2.1")
if not (re.search(r"\b1\.6\b", shift_target)
        and re.search(r"(?:or fewer|or less|at most|≤|<=)", shift_target, re.IGNORECASE)):
    fail("missed or shortened shifts must use the packet target of 1.6 or fewer")

risk_mitigation_pairs = (
    (r"(?:capacity|peak demand|increase.{0,30}wait)", r"(?:weekly|hourly demand|reposition)"),
    (r"(?:app outage|digital access|smartphone)", r"(?:phone line|phone[- ]booking|book.{0,20}phone)"),
    (r"(?:privacy|individual.{0,30}(?:data|record)|trip.{0,20}record)",
     r"(?:aggregat|stor(?:e|ed|ing).{0,30}separat)"),
)
pairs_found = sum(
    bool(re.search(risk_pattern, risks, re.IGNORECASE | re.DOTALL)
         and re.search(mitigation_pattern, risks, re.IGNORECASE | re.DOTALL))
    for risk_pattern, mitigation_pattern in risk_mitigation_pairs
)
if pairs_found < 2:
    fail("Risks and Mitigations must include at least two packet-supported risks with concrete mitigations")
if "[Options Memo]" not in risks:
    fail("packet-supported implementation risks and mitigations must cite [Options Memo]")

source_lines = [line.strip() for line in sources.splitlines() if line.strip()]
expected_paths = {
    "evidence/01_service_gap_scan.md",
    "evidence/02_community_survey.md",
    "evidence/03_options_memo.md",
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
    fail("Sources must list each of the three evidence files exactly once")

if re.search(r"\bthree[- ]month pre[- ]launch\b", text, re.IGNORECASE):
    fail("the packet does not support a three-month pre-launch baseline protocol")

allowed_top_level = {"evidence", ".protected", "policy_brief.md", ".git", ".sandbox-home"}
extras = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed_top_level)
if extras:
    fail(f"unexpected extra top-level artifacts: {extras}")
expected_evidence = {"01_service_gap_scan.md", "02_community_survey.md", "03_options_memo.md"}
evidence_extras = sorted(path.name for path in (ROOT / "evidence").iterdir()
                         if path.name not in expected_evidence)
if evidence_extras:
    fail(f"unexpected artifacts under evidence/: {evidence_extras}")
protected_extras = sorted(path.name for path in (ROOT / ".protected").iterdir()
                          if path.name != "verify.py")
if protected_extras:
    fail(f"unexpected artifacts under .protected/: {protected_extras}")

print(f"PASS: policy_brief.md satisfies the corrected multi-turn brief ({len(words)} words)")
