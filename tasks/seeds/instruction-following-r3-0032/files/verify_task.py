#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


OUTPUT = Path("final_brief.md")
EXPECTED_HEADINGS = [
    "# Extreme Heat Decision Memo",
    "## Decision",
    "## Evidence",
    "## Options",
    "## Recommendation",
    "## Implementation",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


if not OUTPUT.is_file():
    fail("final_brief.md was not created")

try:
    text = OUTPUT.read_text(encoding="utf-8")
except UnicodeDecodeError:
    fail("final_brief.md is not valid UTF-8")

if not text.strip():
    fail("final_brief.md is empty")

heading_lines = re.findall(r"(?m)^#{1,6} .+$", text)
if heading_lines != EXPECTED_HEADINGS:
    fail("heading text, order, or count does not match the requested contract")

if text.splitlines()[0] != EXPECTED_HEADINGS[0]:
    fail("content appears before the title")

word_count = len(re.findall(r"\b[\w’'-]+\b", text))
if not 650 <= word_count <= 850:
    fail(f"memo has {word_count} words; expected 650-850")

lower = text.casefold()
if "ward 2" not in lower:
    fail("the corrected Ward 2 scope is missing")
if "ward 4" in lower:
    fail("the superseded Ward 4 scope remains in the memo")
if not re.search(r"\brent(?:er|ers|al|er-household|er household)", lower):
    fail("the retained renter-household focus is missing")
if not re.search(r"\b18[ -](?:month|months)\b", lower):
    fail("the retained 18-month window is missing")

if not re.search(r"cool[ -]roof", lower):
    fail("cool-roof policy analysis is missing")
if not re.search(r"\b(?:tree|trees|tree-planting|canopy)\b", lower):
    fail("tree-planting policy analysis is missing")

# Both programs, rather than only one of them, must retain the voluntary scope.
voluntary_both = re.search(
    r"\b(?:both\s+(?:policies|programs|options|strategies).{0,50}voluntar\w*|"
    r"voluntar\w*.{0,50}both\s+(?:policies|programs|options|strategies))\b",
    lower,
    re.DOTALL,
)
voluntary_cool_roof = re.search(
    r"(?:voluntar\w*.{0,80}cool[ -]roof|cool[ -]roof.{0,80}voluntar\w*)",
    lower,
    re.DOTALL,
)
voluntary_trees = re.search(
    r"(?:voluntar\w*.{0,80}(?:tree|trees|tree[ -]planting)|"
    r"(?:tree|trees|tree[ -]planting).{0,80}voluntar\w*)",
    lower,
    re.DOTALL,
)
if not voluntary_both and not (voluntary_cool_roof and voluntary_trees):
    fail("the memo does not keep both policies voluntary")

cited_sources = set(re.findall(r"\[S([1-5])\]", text))
if len(cited_sources) < 4:
    fail("fewer than four packet sources are cited")

source_like_labels = set(re.findall(r"\[S([^\]]+)\]", text, re.IGNORECASE))
if any(label not in {"1", "2", "3", "4", "5"} for label in source_like_labels):
    fail("the memo cites a source label that is not in the packet")
if re.search(r"(?:https?://|www\.)", lower):
    fail("the memo cites material outside the supplied packet")

source_topic_patterns = {
    "1": r"\b(?:ward 2|renter|canopy|heat|service requests?|roof surface|demonstration)\b",
    "2": r"\b(?:shade|evapotranspiration|reflective|emissive|solar heat|heat transfer|complement|vary|variation)\w*\b",
    "3": r"\b(?:multifamily|low-slope|suitable|owners?|assessments?|technical assistance|contractors?|inspections?)\w*\b",
    "4": r"\b(?:right-of-way|plantable|trees?|shade|survival|watering|establishment care|nursery)\w*\b",
    "5": r"\b(?:tenants?|upper-floor|notice|disruption|survey|reach|building size|block|tenure)\w*\b",
}
substantive_sources = set()
for paragraph in re.split(r"\n\s*\n", text):
    paragraph_lower = paragraph.casefold()
    for source in re.findall(r"\[S([1-5])\]", paragraph):
        if re.search(source_topic_patterns[source], paragraph_lower):
            substantive_sources.add(source)
if len(substantive_sources) < 4:
    fail("fewer than four packet sources are substantively used")

quantitative_facts = [
    ("S1 renter share", r"\b68\s*(?:%|percent\b)", "1"),
    ("S1 city renter share", r"\b44\s*(?:%|percent\b)", "1"),
    ("S1 ward canopy", r"\b11\s*(?:%|percent\b)", "1"),
    ("S1 city canopy", r"\b24\s*(?:%|percent\b)", "1"),
    ("S1 hot days", r"\b42\s+days\b", "1"),
    ("S1 ward requests", r"\b31\b.{0,60}\bper\s+10,?000\b", "1"),
    ("S1 city requests", r"\b17\b.{0,60}\bper\s+10,?000\b", "1"),
    ("S1 roof demonstration", r"\b7[ -]degree|\bseven[ -]degree", "1"),
    ("S3 renter multifamily share", r"\b(?:71|seventy-one)\s*(?:%|percent\b)", "3"),
    ("S3 suitable roofs", r"\b63\s*(?:%|percent\b)", "3"),
    ("S3 prior assessments", r"\b54\s*(?:%|percent\b)", "3"),
    ("S3 roof capacity", r"\b140\b", "3"),
    ("S4 plantable sites", r"\b1,?120\b", "4"),
    ("S4 near-building sites", r"\b26\s*(?:%|percent\b)", "4"),
    ("S4 shade timing", r"\b(?:3|three)\s+to\s+(?:5|five)\b", "4"),
    ("S4 cared-for survival", r"\b78\s*(?:%|percent\b)", "4"),
    ("S4 uncared-for survival", r"\b52\s*(?:%|percent\b)", "4"),
    ("S4 planting capacity", r"\b420\b", "4"),
]

# A number is not a supported finding merely because it occurs somewhere in the
# memo: count it only when the paragraph also carries the correct packet label.
supported_quantitative_facts = set()
for paragraph in re.split(r"\n\s*\n", text):
    paragraph_lower = paragraph.casefold()
    paragraph_sources = set(re.findall(r"\[S([1-5])\]", paragraph))
    for name, pattern, source in quantitative_facts:
        if source in paragraph_sources and re.search(pattern, paragraph_lower):
            supported_quantitative_facts.add(name)
if len(supported_quantitative_facts) < 4:
    fail("fewer than four correctly cited packet-grounded quantitative findings are included")

for forbidden in (
    r"\$\s*\d", r"\b\d[\d,.]*\s+dollars?\b",
    r"\b(?:costs?|prices?|expenses?|budget)\b\s*(?:(?:is|are|of|at|totals?|:)\s*)?"
    r"(?:approximately\s+|about\s+|up to\s+)?\d[\d,.]*\b",
    r"\b\d[\d,.]*\s+(?:in\s+|of\s+)?(?:costs?|expenses?)\b",
    r"\blegal\b", r"\benforc(?:e|ed|ement|ing)\w*\b",
    r"\b(?:mandatory|required)\s+(?:participation|compliance)\b",
    r"\b(?:penalt\w*|fines?)\s+(?:for|on)\b", r"\bnoncompliance\b",
):
    if re.search(forbidden, lower):
        fail("memo includes a prohibited cost, legal, or enforcement detail")

sections = {}
for index, heading in enumerate(EXPECTED_HEADINGS[1:], start=1):
    start = text.index(heading) + len(heading)
    end = text.index(EXPECTED_HEADINGS[index + 1]) if index + 1 < len(EXPECTED_HEADINGS) else len(text)
    sections[heading] = text[start:end]

decision = sections["## Decision"].casefold()
recommendation = sections["## Recommendation"].casefold()
role_scope = decision + recommendation
cool_roof_lead = re.search(
    r"(?:cool[ -]roof.{0,120}\blead\w*|\blead\w*.{0,120}cool[ -]roof)",
    role_scope,
    re.DOTALL,
)
cool_roof_complement = re.search(
    r"(?:cool[ -]roof.{0,120}\bcomplement\w*|\bcomplement\w*.{0,120}cool[ -]roof)",
    role_scope,
    re.DOTALL,
)
tree_lead = re.search(
    r"(?:(?:tree|trees|tree[ -]planting).{0,120}\blead\w*|"
    r"\blead\w*.{0,120}(?:tree|trees|tree[ -]planting))",
    role_scope,
    re.DOTALL,
)
tree_complement = re.search(
    r"(?:(?:tree|trees|tree[ -]planting).{0,120}\bcomplement\w*|"
    r"\bcomplement\w*.{0,120}(?:tree|trees|tree[ -]planting))",
    role_scope,
    re.DOTALL,
)
if not ((cool_roof_lead and tree_complement) or (tree_lead and cool_roof_complement)):
    fail("the memo does not identify lead and complementary policies")

options = sections["## Options"].casefold()
coverage_patterns = {
    "benefits": r"\b(?:benefit|advantage|strength|improv|reduc|lower|shade)\w*\b",
    "limitations": r"\b(?:limit|constraint|drawback|risk|vary|variation|delay|depend)\w*\b|\bnot every\b|\bcannot\b",
    "equity": r"\b(?:equit|renter|tenant|distribution|reach|access)\w*\b|\bregardless of (?:a resident's |resident )?tenure\b",
    "feasibility": r"\b(?:feasib|capacity|suitable|contractor|participat|deliver|qualif|plantable)\w*\b",
}
for concept, pattern in coverage_patterns.items():
    if not re.search(pattern, lower):
        fail(f"the memo does not substantively cover {concept}")

implementation = sections["## Implementation"].casefold()
if not re.search(
    r"\b(?:18[ -](?:month|months)|(?:by|through|within|at)\s+month\s+18|"
    r"months?\s+\d+\s*(?:-|–|to|through)\s*18)\b",
    implementation,
):
    fail("the implementation section does not apply the retained 18-month window")

implementation_metrics = [
    r"\b(?:roof projects?|completed roofs?|roof installations?|buildings? assessed|technical assessments?)\b",
    r"\b(?:unit counts?|renter households?|tenant reach)\b",
    r"\b(?:tenant surveys?|survey response|tenant reports?|comfort|tenant notices?|notice delivery)\b",
    r"\b(?:owner participation|participation rate|owner assessments?)\b",
    r"\b(?:trees? planted|tree sites?|tree survival|survival observations?|canopy coverage|shaded routes?)\b",
    r"\b(?:establishment care|care completion)\b",
    r"\b(?:block distribution|site distribution|share of (?:tree )?sites?)\b",
]
if sum(bool(re.search(pattern, implementation)) for pattern in implementation_metrics) < 2:
    fail("implementation provides fewer than two concrete outcome measures")

print(f"PASS: final_brief.md satisfies the multi-turn memo contract ({word_count} words)")
