#!/usr/bin/env python3
"""Protected, deterministic acceptance checks for the council brief."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


BRIEF = Path("council_brief.md")
MARKDOWN_WEB_LINK = re.compile(r"\[[^\]]+\]\((https?://[^)]+)\)", re.IGNORECASE)
HEADINGS = [
    "# Council Brief: Municipal Food-Waste Diversion",
    "## Decision",
    "## Sourced Findings",
    "### California",
    "### Seattle, Washington",
    "### Massachusetts",
    "### Cross-Jurisdiction Lessons",
    "## Recommendation",
    "## Equity and Implementation",
    "## Uncertainties and Evidence Gaps",
    "## Sources",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def visible_word_count(markdown: str) -> int:
    visible = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", markdown)
    visible = re.sub(r"https?://\S+", "", visible, flags=re.IGNORECASE)
    return len(re.findall(r"\b[\w’'-]+\b", visible, flags=re.UNICODE))


def canonical_url(url: str) -> str:
    """Treat fragments, tracking queries, and a trailing slash as one source."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/") or "/"
    tracking_keys = {"fbclid", "gclid"}
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in tracking_keys
        )
    )
    return urlunparse(("https", host, path, "", query, ""))


def section(text: str, heading: str, following: list[str]) -> str:
    start_match = re.search(
        rf"^{re.escape(heading)}[ \t]*$", text, flags=re.MULTILINE
    )
    if not start_match:
        fail(f"section heading is missing: {heading}")
    start = start_match.end()
    stops = []
    for item in following:
        stop_match = re.search(
            rf"^{re.escape(item)}[ \t]*$", text[start:], flags=re.MULTILINE
        )
        if stop_match:
            stops.append(start + stop_match.start())
    return text[start : min(stops) if stops else len(text)]


if not BRIEF.is_file():
    fail("council_brief.md is missing")

text = BRIEF.read_text(encoding="utf-8")
if "\r" in text:
    fail("brief must use normal UTF-8 Unix newlines")

heading_positions = {}
for heading in HEADINGS:
    matches = list(
        re.finditer(rf"^{re.escape(heading)}[ \t]*$", text, flags=re.MULTILINE)
    )
    if len(matches) != 1:
        fail(f"required heading must appear exactly once: {heading}")
    heading_positions[heading] = matches[0].start()
positions = [heading_positions[heading] for heading in HEADINGS]
if positions != sorted(positions):
    fail("required headings are not in the requested order")

# The requested length applies to the substantive brief, not its bibliography.
substantive_text = text[: heading_positions["## Sources"]]
words = visible_word_count(substantive_text)
if not 750 <= words <= 900:
    fail(f"visible substantive brief length is {words} words; expected 750–900")

if re.search(r"\bVermont\b", text, flags=re.IGNORECASE):
    fail("superseded Vermont comparison remains in the final brief")

findings = section(text, "## Sourced Findings", ["## Recommendation"])
recommendation = section(text, "## Recommendation", ["## Equity and Implementation"])
equity = section(text, "## Equity and Implementation", ["## Uncertainties and Evidence Gaps"])
uncertainty = section(text, "## Uncertainties and Evidence Gaps", ["## Sources"])
sources = section(text, "## Sources", [])

for sentence in re.split(r"(?<=[.!?])\s+|\n+", findings):
    names_target_city = re.search(
        r"\b(?:(?:this|our)\s+(?:city|council|municipality|ordinance|program)|(?:the\s+)?council(?:members)?|staff)\b",
        sentence,
        flags=re.IGNORECASE,
    )
    prescribes = re.search(
        r"\b(?:should|must|ought|could|recommend\w*|propos\w*|adopt\w*|enact\w*|need(?:s)?\s+to)\b",
        sentence,
        flags=re.IGNORECASE,
    )
    if names_target_city and prescribes:
        fail("prescriptive language for the subject city appears in Sourced Findings")
if not re.search(
    r"\b(?:recommend\w*|adopt\w*|enact\w*|approv\w*|implement\w*|propos\w*)\b",
    recommendation,
    flags=re.IGNORECASE,
):
    fail("Recommendation does not state a recommended action")
if not re.search(r"\bpackage\b", recommendation, flags=re.IGNORECASE):
    fail("Recommendation does not present the requested policy package")

jurisdictions = {
    "California": (
        "### California",
        "### Seattle, Washington",
        lambda host: host == "calrecycle.ca.gov" or host.endswith(".calrecycle.ca.gov"),
    ),
    "Seattle": (
        "### Seattle, Washington",
        "### Massachusetts",
        lambda host: host == "seattle.gov" or host.endswith(".seattle.gov"),
    ),
    "Massachusetts": (
        "### Massachusetts",
        "### Cross-Jurisdiction Lessons",
        lambda host: host == "mass.gov" or host.endswith(".mass.gov"),
    ),
}
for name, (start_heading, end_heading, authority_matcher) in jurisdictions.items():
    body = section(text, start_heading, [end_heading])
    body_links = MARKDOWN_WEB_LINK.findall(body)
    if not body_links:
        fail(f"{name} section lacks an inline web citation")
    body_hosts = {(urlparse(url).hostname or "").lower() for url in body_links}
    if not any(authority_matcher(host) for host in body_hosts):
        fail(f"{name} section lacks an inline citation to its official government source")
    has_dated_detail = bool(re.search(r"\b(?:19|20)\d{2}\b", body))
    has_quantitative_detail = bool(
        re.search(
            r"(?:\b\d+(?:\.\d+)?\s*%|\$\s*\d|\b(?:half|one-half)\s+(?:ton|percent)|\b\d+(?:\.\d+)?\s+(?:tons?|miles?|warnings?))",
            body,
            flags=re.IGNORECASE,
        )
    )
    if not (has_dated_detail or has_quantitative_detail):
        fail(f"{name} section lacks a dated rule or quantitative program detail")

evidence_links = MARKDOWN_WEB_LINK.findall(substantive_text)
unique_evidence_links = {canonical_url(url) for url in evidence_links}
government_evidence_links = {
    canonical_url(url)
    for url in evidence_links
    if (urlparse(url).hostname or "").lower().endswith(".gov")
}
if len(government_evidence_links) < 6:
    fail("brief body must use at least six distinct linked government sources")

required_authorities = {
    "California": lambda host: host == "calrecycle.ca.gov" or host.endswith(".calrecycle.ca.gov"),
    "Seattle": lambda host: host == "seattle.gov" or host.endswith(".seattle.gov"),
    "Massachusetts": lambda host: host == "mass.gov" or host.endswith(".mass.gov"),
    "U.S. EPA": lambda host: host == "epa.gov" or host.endswith(".epa.gov"),
}
hosts = {(urlparse(url).hostname or "").lower() for url in evidence_links}
for authority, matcher in required_authorities.items():
    if not any(matcher(host) for host in hosts):
        fail(f"source set is missing an official {authority} source")

source_entries = [
    line
    for line in sources.splitlines()
    if re.match(r"\s*(?:[-*+]\s+|\d+[.)]\s+)", line)
    and MARKDOWN_WEB_LINK.search(line)
]
listed_source_links = {
    canonical_url(url)
    for url in MARKDOWN_WEB_LINK.findall("\n".join(source_entries))
}
if unique_evidence_links - listed_source_links:
    fail("every linked source used in the brief must appear in the Sources list")

cross_lessons = section(
    text, "### Cross-Jurisdiction Lessons", ["## Recommendation"]
)
if "Inference:" not in cross_lessons:
    fail("Cross-Jurisdiction Lessons must label a transferability inference")

stage_positions = []
for stage in ("Preparation", "Launch", "Evaluation"):
    match = re.search(rf"\b{stage}\b", recommendation, flags=re.IGNORECASE)
    if not match:
        fail(f"three-stage recommendation is missing {stage}")
    stage_positions.append(match.start())
if stage_positions != sorted(stage_positions):
    fail("recommendation stages must be sequenced as preparation, launch, evaluation")

if not re.search(r"\bindicators?\b", recommendation, flags=re.IGNORECASE):
    fail("Recommendation must identify the four measures as indicators")

indicator_concepts = {
    "access or participation": r"\b(?:access|participation)\b",
    "contamination": r"\bcontamination\b",
    "tons diverted": r"\btons?\s+diverted\b|\bdiverted\s+tons?\b",
    "edible-food recovery": r"\bedible[- ]food\s+(?:recovery|rescued)|\b(?:recovered|rescued)\s+edible[- ]food\b",
}
for label, pattern in indicator_concepts.items():
    if not re.search(pattern, recommendation, flags=re.IGNORECASE):
        fail(f"recommendation is missing the {label} indicator")

equity_concepts = {
    "multifamily renters": r"\b(?:multifamily|multi-family)\b.*\brenters?\b|\brenters?\b.*\b(?:multifamily|multi-family)\b",
    "small food businesses": r"\bsmall\s+(?:food\s+)?business",
    "multilingual access": r"\bmultilingual\b|\blanguage access\b",
    "collection capacity": r"\bcollection\b",
    "processing capacity": r"\bprocessing\s+capacity\b",
    "food-rescue partners": r"\bfood[- ]rescue\b|\bdonation\s+(?:partners?|organizations?)\b",
}
for label, pattern in equity_concepts.items():
    if not re.search(pattern, equity, flags=re.IGNORECASE | re.DOTALL):
        fail(f"Equity and Implementation omits {label}")

gap_lines = [line for line in uncertainty.splitlines() if re.match(r"\s*[-*]\s+", line)]
material_gap_lines = [
    line
    for line in gap_lines
    if re.search(
        r"\b(?:unknown|uncertain|gap|need(?:ed|s)?|lack(?:ing|s)?|unavailable|requires?|not known|not established)\b",
        line,
        flags=re.IGNORECASE,
    )
]
if len(material_gap_lines) < 3:
    fail("Uncertainties and Evidence Gaps needs at least three explicit bullet points")
if not re.search(r"\b(?:unknown|uncertain|cannot|need|gap)\b", uncertainty, flags=re.IGNORECASE):
    fail("uncertainty section does not state the limits of the evidence")

# Reject explicit numerical estimates asserted for the subject city while allowing
# proposed targets and dates. Comparator facts are confined to Sourced Findings.
non_findings = text.replace(findings, "")
for sentence in re.split(r"(?<=[.!?])\s+|\n+", non_findings):
    has_local_metric = re.search(
        r"\b(?:local|municipal|municipality(?:'s|’s)?|city(?:'s|’s)?)\b",
        sentence,
        flags=re.IGNORECASE,
    ) and re.search(
        r"\b(?:costs?|tonnage|capacity|participation)\b",
        sentence,
        flags=re.IGNORECASE,
    )
    has_measured_value = re.search(
        r"(?:\$\s*\d|\b\d+(?:\.\d+)?\s*(?:%|percent|tons?))",
        sentence,
        flags=re.IGNORECASE,
    )
    asserts_estimate = re.search(
        r"\b(?:is|are|has|have|estimate[sd]?|project(?:ed|s)?|forecast(?:ed|s)?|assume[sd]?|expect(?:ed|s)?|approximately|currently|will be)\b",
        sentence,
        flags=re.IGNORECASE,
    )
    is_target = re.search(r"\b(?:target|goal|threshold)\b", sentence, flags=re.IGNORECASE)
    if has_local_metric and has_measured_value and asserts_estimate and not is_target:
        fail("brief invents a numerical local cost, tonnage, capacity, or participation estimate")

print(
    "PASS: council_brief.md satisfies the corrected retained contract "
    f"({words} substantive words, {len(government_evidence_links)} government sources)"
)
