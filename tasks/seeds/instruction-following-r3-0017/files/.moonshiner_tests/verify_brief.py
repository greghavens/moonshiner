#!/usr/bin/env python3
"""Protected deterministic acceptance checks for the corrected RSV brief."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse


DELIVERABLE = Path("rsv_prevention_brief.md")
HEADINGS = [
    "# RSV Prevention Brief",
    "## Scope",
    "## Sourced facts",
    "## Options comparison",
    "## Questions for a clinician",
    "## Sources",
]
CDC_URL = "https://www.cdc.gov/rsv/hcp/vaccine-clinical-guidance/adults.html"
FDA_URLS = {
    "Arexvy": "https://www.fda.gov/vaccines-blood-biologics/arexvy",
    "Abrysvo": "https://www.fda.gov/vaccines-blood-biologics/abrysvo",
    "mResvia": "https://www.fda.gov/vaccines-blood-biologics/vaccines/mresvia",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


if not DELIVERABLE.is_file():
    fail("rsv_prevention_brief.md is missing")

try:
    text = DELIVERABLE.read_text(encoding="utf-8")
except UnicodeDecodeError:
    fail("deliverable must be UTF-8 text")

if "\r" in text:
    fail("use Unix line endings")

actual_headings = [
    line.strip() for line in text.splitlines()
    if re.fullmatch(r"#{1,6}\s+.+", line.strip())
]
if actual_headings != HEADINGS:
    fail(f"headings must be exactly {HEADINGS!r} in that order")

sections: dict[str, str] = {}
for index, heading in enumerate(HEADINGS[1:], start=1):
    start = text.index(heading) + len(heading)
    end = text.index(HEADINGS[index + 1]) if index + 1 < len(HEADINGS) else len(text)
    body = text[start:end].strip()
    if not body:
        fail(f"section {heading!r} is empty")
    sections[heading] = body

words = re.findall(r"\b[\w’'-]+\b", text, flags=re.UNICODE)
if not 350 <= len(words) <= 850:
    fail(f"word count {len(words)} is outside 350..850")

lower = text.casefold()
for marker in ("todo", "tbd", "placeholder", "example.com", "i would research"):
    if marker in lower:
        fail(f"unfinished or placeholder content found: {marker!r}")

scope = sections["## Scope"].casefold()
for required in (
    "69-year-old",
    "chronic lung disease",
    "continental united states",
    "never received",
    "august 15, 2026",
):
    if required not in scope:
        fail(f"Scope must retain corrected fact {required!r}")
if not ("educational" in scope and "medical advice" in scope):
    fail("Scope must identify the brief as educational, not individualized medical advice")

for excluded in (
    r"\binfant\b",
    r"\bbaby\b",
    r"\bchild(?:ren)?\b",
    r"pregnan",
    r"maternal",
    r"nirsevimab",
    r"clesrovimab",
):
    if re.search(excluded, lower):
        fail(f"superseded infant scope leaked into the final brief: {excluded!r}")

for out_of_scope in (
    r"\bribavirin\b",
    r"\bantiviral",
    r"\bdiagnostic test",
    r"\bhome treatment",
    r"\btreat(?:ing)? symptoms\b",
    r"\bwhen to seek (?:urgent|emergency)",
):
    if re.search(out_of_scope, lower):
        fail(f"diagnosis or treatment guidance is outside scope: {out_of_scope!r}")

facts_and_table = (
    sections["## Sourced facts"] + "\n" + sections["## Options comparison"]
).casefold()
fact_requirements = {
    "the 50–74 risk-based age band": r"50\s*[–-]\s*74",
    "increased-risk qualification": r"increased risk",
    "chronic lung risk factor": r"chronic lung|chronic respiratory",
    "single-dose guidance": r"single dose|one[- ](?:time|dose)",
    "not-annual guidance": r"not (?:currently )?an annual|not annual",
    "continental-U.S. timing": r"august\s*[–-]\s*october",
    "prior-dose consequence": r"already (?:received|had).{0,80}(?:should not|no additional|do not need|completed)",
    "no product preference": r"no (?:cdc )?preference|does not prefer",
}
for label, pattern in fact_requirements.items():
    if not re.search(pattern, facts_and_table, flags=re.DOTALL):
        fail(f"missing substantive fact: {label}")

uncertainty_patterns = (
    r"\b(?:uncertain(?:ty)?|unknown|unclear)\b",
    r"\b(?:limited|insufficient) (?:evidence|data)\b",
    r"(?:evidence|data|guidance|recommendations?).{0,140}"
    r"(?:uncertain|limited|being evaluated|could change|may change|ongoing)",
    r"(?:additional|future) (?:vaccine )?doses?.{0,140}"
    r"(?:evaluat|uncertain|unknown|may|could)",
    r"(?:no|not|without).{0,40}(?:direct )?head-to-head",
)
if not any(
    re.search(pattern, facts_and_table, flags=re.DOTALL)
    for pattern in uncertainty_patterns
):
    fail("Sourced facts or Options comparison must state an important uncertainty")

table = sections["## Options comparison"]
if not re.search(r"^\s*\|.+\|\s*$", table, flags=re.MULTILINE):
    fail("Options comparison must contain a Markdown table")
if not re.search(r"^\s*\|(?:\s*:?-+:?\s*\|){2,}\s*$", table, flags=re.MULTILINE):
    fail("Options comparison lacks a Markdown table separator")
table_rows = [
    line for line in table.splitlines()
    if re.match(r"^\s*\|.+\|\s*$", line)
]
for product, url in FDA_URLS.items():
    matching_rows = [
        line for line in table_rows
        if product.casefold() in line.casefold() and url in line
    ]
    if not matching_rows:
        fail(f"{product} must appear in a table row citing its official FDA page")
if CDC_URL not in sections["## Sourced facts"]:
    fail("Sourced facts must cite the current CDC adult guidance inline")

questions = [
    line for line in sections["## Questions for a clinician"].splitlines()
    if re.match(r"^\s*[-*]\s+", line)
]
if not 3 <= len(questions) <= 6:
    fail("Questions for a clinician must contain three to six bullet questions")
if any("?" not in line for line in questions):
    fail("every clinician-question bullet must be phrased as a question")

sources = sections["## Sources"]
source_entries = [
    line for line in sources.splitlines() if re.match(r"^\s*[-*]\s+", line)
]
if len(source_entries) < 4:
    fail("Sources must contain at least four source-list entries")
for required_url in (CDC_URL, *FDA_URLS.values()):
    if required_url not in sources:
        fail(f"Sources is missing {required_url}")
for line in source_entries:
    folded = line.casefold()
    if "accessed august 15, 2026" not in folded:
        fail("each source entry must include the access date August 15, 2026")
    if not re.search(r"\[[^]]+\]\(https://[^)]+\)", line):
        fail("each source entry must contain a linked page title")

required_source_metadata = {
    CDC_URL: ("rsv vaccine guidance for adults", ("cdc", "centers for disease control")),
    FDA_URLS["Arexvy"]: ("arexvy", ("fda", "food and drug administration")),
    FDA_URLS["Abrysvo"]: ("abrysvo", ("fda", "food and drug administration")),
    FDA_URLS["mResvia"]: ("mresvia", ("fda", "food and drug administration")),
}
for required_url, (title, agency_markers) in required_source_metadata.items():
    matching_entries = [line for line in source_entries if required_url in line]
    if not matching_entries:
        fail(f"Sources is missing a list entry for {required_url}")
    folded = matching_entries[0].casefold()
    if title not in folded:
        fail(f"source entry for {required_url} must include its page title")
    if not any(marker in folded for marker in agency_markers):
        fail(f"source entry for {required_url} must name its issuing agency")

urls = re.findall(r"https://[^\s)>]+", text)
if len(urls) < 8:
    fail("claims need inline primary-source links in addition to the source list")
search_result_hosts = {
    "www.google.com",
    "www.bing.com",
    "search.yahoo.com",
    "duckduckgo.com",
}
for url in urls:
    host = urlparse(url.rstrip(".,;")).hostname
    if host is None or host.endswith((".invalid", ".example")):
        fail(f"unreachable placeholder source URL: {url}")
    if host in search_result_hosts:
        fail(f"search-result links are not source pages: {url}")

print(f"PASS: {DELIVERABLE} satisfies the corrected multi-turn contract ({len(words)} words)")
