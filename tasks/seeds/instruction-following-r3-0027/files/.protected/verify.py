from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "heat_safety_brief.md"


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


if not TARGET.is_file():
    fail("heat_safety_brief.md is missing")

text = TARGET.read_text(encoding="utf-8")
words = re.findall(r"\b[\w’'-]+\b", text)
if not 700 <= len(words) <= 900:
    fail(f"brief must contain 700-900 words; found {len(words)}")

expected_headings = [
    "# Heat-Safety Brief for Family Caregivers",
    "## Audience and Scope",
    "## Sourced Facts",
    "## Recommendations",
    "## Action Table",
    "## Uncertainty and Limits",
    "## Emergency Signs",
    "## Sources",
]
actual_headings = [line.strip() for line in text.splitlines() if line.startswith("#")]
if actual_headings != expected_headings:
    fail("headings are missing, extra, or out of order")

lower = text.lower()

sections: dict[str, str] = {}
for index, heading in enumerate(expected_headings[1:], start=1):
    start = text.index(heading) + len(heading)
    end = text.index(expected_headings[index + 1]) if index + 1 < len(expected_headings) else len(text)
    sections[heading] = text[start:end]

audience_lower = sections["## Audience and Scope"].lower()
if not re.search(r"family caregivers?", audience_lower):
    fail("Audience and Scope must identify family caregivers")
if not re.search(r"(?:age[sd]?\s*)?65\s*(?:and|or)\s*older|65\+", audience_lower):
    fail("Audience and Scope must identify adults age 65 and older")
if not re.search(r"\b(only|solely|exclusive(?:ly)?)\b|\bnot for\b|\bdoes not (?:address|cover)\b", audience_lower):
    fail("Audience and Scope must make the family-caregiver-only scope explicit")
for match in re.finditer(r"senior[- ]center staff", lower):
    context = lower[max(0, match.start() - 50):match.start()]
    if not re.search(r"(?:\bnot(?: for)?|\brather than|\binstead of|\bexclud(?:e[sd]?|ing).{0,25})\s*$", context):
        fail("the brief addresses the superseded senior-center-staff audience")

facts = sections["## Sourced Facts"]
recommendations = sections["## Recommendations"]
limits = sections["## Uncertainty and Limits"]
emergency = sections["## Emergency Signs"]

labels = ["[CDC Older Adults]", "[NIOSH Heat Illness]", "[NWS Heat Tools]"]
for label in labels:
    if label not in facts:
        fail(f"Sourced Facts must cite {label}")

facts_lower = facts.lower()
source_fact_terms = {
    "[CDC Older Adults]": ("twice daily", "air-conditioned", "water pills", "prescription medicines", "sudden temperature"),
    "[NIOSH Heat Illness]": ("heat exhaustion", "heat stroke", "heat-stroke"),
    "[NWS Heat Tools]": ("heat index", "heat alert", "warm nights", "shade and light wind"),
}
for label, terms in source_fact_terms.items():
    if not any(term in facts_lower for term in terms):
        fail(f"Sourced Facts lacks substantive packet content for {label}")

rec_lower = recommendations.lower()
recommendation_components = {
    "check-in plan": re.search(r"check[- ]in", rec_lower),
    "access to a cool place": re.search(r"cool (?:place|location|building)|air-conditioned (?:place|location|home|building)", rec_lower),
    "pharmacist-or-clinician discussion": "pharmacist" in rec_lower and "clinician" in rec_lower,
}
for component, present in recommendation_components.items():
    if not present:
        fail(f"Recommendations is missing the retained {component} component")
if not re.search(r"recommendation.{0,80}synthesi[sz]ed from the packet", rec_lower, re.DOTALL):
    fail("Recommendations must identify the three-part approach as a packet synthesis")
if not re.search(r"not.{0,80}(directly tested|tested.{0,30}(program|approach))", rec_lower, re.DOTALL):
    fail("Recommendations must say the combined approach was not directly tested")
has_medication_caution = (
    re.search(r"\bmedication", rec_lower)
    and re.search(r"\b(change|stop|skip|adjust)", rec_lower)
    and re.search(r"\b(do not|don't|never|should not|must not|on their own|without.{0,30}(?:pharmacist|clinician))\b", rec_lower, re.DOTALL)
)
if not has_medication_caution:
    fail("medication self-change caution is missing")

table_lines = [line.strip() for line in sections["## Action Table"].splitlines() if line.strip().startswith("|")]
if len(table_lines) != 6:
    fail("Action Table must contain one header, one divider, and exactly four data rows")
if [cell.strip() for cell in table_lines[0].strip("|").split("|")] != ["Action", "Who acts", "When", "Why"]:
    fail("Action Table columns are incorrect")
divider_cells = [cell.strip() for cell in table_lines[1].strip("|").split("|")]
if len(divider_cells) != 4 or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in divider_cells):
    fail("Action Table is missing a valid four-column Markdown divider")
if any(len(line.strip("|").split("|")) != 4 for line in table_lines[2:]):
    fail("each Action Table data row must contain four columns")
actions = [line.strip("|").split("|", 1)[0].strip() for line in table_lines[2:]]
if actions != ["Before hot weather", "During a heat alert", "At each check-in", "If emergency signs appear"]:
    fail("Action Table rows are missing or out of order")

limits_lower = limits.lower()
limit_checks = [
    re.search(r"(individual|particular person).{0,100}(vary|differ|predict|response|risk)", limits_lower, re.DOTALL),
    re.search(r"heat index.{0,120}(shade|sun|indoors|inside)", limits_lower, re.DOTALL),
    re.search(r"(does not|did not|no).{0,120}(compare|evaluate|test|establish).{0,100}(check-in|combined|program|approach|frequency|schedule)", limits_lower, re.DOTALL),
    re.search(r"(local|community).{0,100}(cooling|transport|hours|access|resource|backup)", limits_lower, re.DOTALL),
    re.search(r"(symptom|sign).{0,100}(overlap|diagnos|severity)", limits_lower, re.DOTALL),
    re.search(r"(fluid|medicine|medication|clinical|clinician).{0,100}(individual|cannot determine|guidance|assessment|need)", limits_lower, re.DOTALL),
    re.search(r"(universal|same).{0,100}(heat[- ]index|threshold|number|response)", limits_lower, re.DOTALL),
    re.search(r"(forecast|alert).{0,100}(vary|inside|indoor|individual|medical risk)", limits_lower, re.DOTALL),
]
if sum(bool(item) for item in limit_checks) < 3:
    fail("Uncertainty and Limits must state at least three packet-supported limitations")
if not re.search(r"\b(uncertain|uncertainty|unknown|cannot determine|does not establish)\b", limits_lower):
    fail("uncertainty must be stated explicitly")

emergency_lower = emergency.lower()
if "call 911" not in emergency_lower:
    fail("Emergency Signs must direct the reader to call 911")
emergency_signs = [
    "confusion",
    "altered mental status",
    "slurred speech",
    "loss of consciousness",
    "seizure",
    "very high body temperature",
    "hot dry skin",
    "profuse sweating",
]
if sum(sign in emergency_lower for sign in emergency_signs) < 2:
    fail("Emergency Signs must identify at least two packet-supported heat-stroke signs")

source_lines = [line.strip() for line in sections["## Sources"].splitlines() if line.strip()]
expected_sources = [
    "- `evidence/01_cdc_older_adults.md`",
    "- `evidence/02_niosh_heat_illness.md`",
    "- `evidence/03_nws_heat_tools.md`",
]
if source_lines != expected_sources:
    fail("Sources must contain exactly the three evidence filenames as Markdown bullets")

allowed_files = {
    ".protected/verify.py",
    "evidence/01_cdc_older_adults.md",
    "evidence/02_niosh_heat_illness.md",
    "evidence/03_nws_heat_tools.md",
    "heat_safety_brief.md",
}
workspace_files = {
    path.relative_to(ROOT).as_posix()
    for path in ROOT.rglob("*")
    if path.is_file()
    and ".git" not in path.relative_to(ROOT).parts
    and ".sandbox-home" not in path.relative_to(ROOT).parts
}
unexpected = sorted(workspace_files - allowed_files)
if unexpected:
    fail(f"unexpected file(s) created: {', '.join(unexpected)}")

print("PASS: heat_safety_brief.md satisfies the accumulated multi-turn instructions")
sys.exit(0)
