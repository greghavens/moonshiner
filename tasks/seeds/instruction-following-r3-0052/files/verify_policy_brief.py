#!/usr/bin/env python3
"""Deterministic acceptance checks for the policy-research deliverable."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse


OUTPUT = Path("lead_service_line_brief.md")
CUTOFF_DATE = "2026-08-15"
HEADINGS = [
    "# Chicago Lead Service Line Replacement Brief",
    "## Decision summary",
    "## Current policy baseline",
    "## Chicago conditions",
    "## Lessons from the comparator",
    "## Recommended Chicago action plan",
    "## Equity and accountability measures",
    "## Draft committee statement",
    "## Sources consulted",
]
OFFICIAL_SUFFIXES = (
    "gov",
    "chicagofed.org",
)
GENERIC_LINK_LABELS = {"click here", "here", "link", "source", "website"}


def fail(message: str) -> None:
    raise AssertionError(message)


def words(value: str) -> list[str]:
    # Count visible Markdown prose, not the path/query fragments inside link URLs.
    visible = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    return re.findall(r"\b[\w’'-]+\b", visible, flags=re.UNICODE)


def section(text: str, heading: str, next_heading: str | None) -> str:
    start = text.index(heading) + len(heading)
    end = text.index(next_heading, start) if next_heading else len(text)
    return text[start:end].strip()


def host_matches(hostname: str, suffix: str) -> bool:
    """Match a host or its subdomain, without accepting names such as notepa.gov."""
    return hostname == suffix or hostname.endswith("." + suffix)


def main() -> None:
    if not OUTPUT.is_file():
        fail("lead_service_line_brief.md is missing")

    text = OUTPUT.read_text(encoding="utf-8")
    count = len(words(text))
    if not 1300 <= count <= 1700:
        fail(f"brief must be 1,300–1,700 words; found {count}")

    positions: list[int] = []
    for heading in HEADINGS:
        matches = list(re.finditer(rf"(?m)^{re.escape(heading)}\s*$", text))
        if len(matches) != 1:
            fail(f"required heading must appear exactly once: {heading}")
        positions.append(matches[0].start())
    if positions != sorted(positions):
        fail("required headings are out of order")

    lower = text.lower()
    if lower.count("chicago") < 12:
        fail("the brief is not substantively focused on Chicago")
    if lower.count("newark") < 5 or "new jersey" not in lower:
        fail("the corrected Newark, New Jersey comparator is not substantive")
    if "milwaukee" in lower:
        fail("the superseded Milwaukee comparator must not remain")
    if not re.search(rf"research cutoff[^\n]{{0,40}}{CUTOFF_DATE}", text, re.I):
        fail(f"state the requested research cutoff date: {CUTOFF_DATE}")

    baseline = section(text, HEADINGS[2], HEADINGS[3])
    if "Lead and Copper Rule Improvements" not in baseline:
        fail("the policy baseline must address the Lead and Copper Rule Improvements")
    if not re.search(r"November\s+1,\s+2027", baseline, re.I):
        fail("the current-policy section must identify the LCRI compliance date")
    if not re.search(r"\b10[- ]year|within 10 years", baseline, re.I):
        fail("the current-policy section must explain the replacement timeframe")
    if not re.search(r"\b(binding|required|requires|must)\b", baseline, re.I):
        fail("the policy baseline must identify binding or required duties")
    if not re.search(r"\b(proposal|recommendation|minimum legal duties|not (?:settled|required))\b", baseline, re.I):
        fail("distinguish binding requirements from proposals or recommendations")

    chicago = section(text, HEADINGS[3], HEADINGS[4])

    comparator = section(text, HEADINGS[4], HEADINGS[5])
    if not re.search(r"\b(lesson|transfer|copy|warning|Chicago)\b", comparator, re.I):
        fail("the comparator must draw a lesson for Chicago")

    analytical_prose = text[: positions[5]]
    if not re.search(r"\b(uncertaint\w*|unknown|not settled|not establish\w*)\b", analytical_prose, re.I):
        fail("identify an important uncertainty")
    if not re.search(r"\b(trade[- ]?offs?|however|while|but|risk)\b", analytical_prose, re.I):
        fail("identify a material trade-off or risk")

    action = section(text, HEADINGS[5], HEADINGS[6])
    action_matches = list(re.finditer(r"(?m)^\s*([1-5])[.)]\s+", action))
    if not 3 <= len(action_matches) <= 5:
        fail("the action plan must contain three to five numbered actions")
    if [int(match.group(1)) for match in action_matches] != list(range(1, len(action_matches) + 1)):
        fail("action numbers must be consecutive starting at 1")
    for index, match in enumerate(action_matches):
        end = action_matches[index + 1].start() if index + 1 < len(action_matches) else len(action)
        item = action[match.start():end]
        if not re.search(r"\b(owner|ownership|lead:)\b", item, re.I):
            fail(f"action {index + 1} lacks ownership")
        if not re.search(
            r"\b(day|days|month|months|year|years|quarter|202\d|within|before|after|during|beginning|immediately)\b",
            item,
            re.I,
        ):
            fail(f"action {index + 1} lacks timing")

    metrics = section(text, HEADINGS[6], HEADINGS[7])
    metric_lines = [
        line for line in metrics.splitlines()
        if re.match(r"^\s*[-*]\s+", line) and re.search(r"\d", line)
    ]
    if len(metric_lines) < 4:
        fail("provide at least four bulleted equity/accountability metrics with numeric targets")

    statement = section(text, HEADINGS[7], HEADINGS[8])
    statement_count = len(words(statement))
    if not 250 <= statement_count <= 400:
        fail(f"committee statement must be 250–400 words; found {statement_count}")
    if not re.search(r"\b(ask|urge|request|recommend|adopt|approve)\b", statement, re.I):
        fail("committee statement must make a concrete ask")
    if not re.search(
        r"\b(ask|urge|request|recommend)\b[^.!?]{0,160}\b(establish|adopt|approve|require|direct|fund)\b",
        statement,
        re.I,
    ):
        fail("committee statement needs a concrete requested action")

    link_pairs = re.findall(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)", text)
    links = [url for _, url in link_pairs]
    unique_links = list(dict.fromkeys(links))
    if len(unique_links) < 8:
        fail("cite at least eight distinct web sources with descriptive Markdown links")
    for label, url in link_pairs:
        if label.strip().lower() in GENERIC_LINK_LABELS or len(words(label)) < 2:
            fail(f"source link needs a descriptive label: {label}")
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (
            not hostname
            or parsed.username
            or parsed.password
            or hostname in {"localhost", "example.com", "example.org", "example.net"}
            or hostname.endswith((".invalid", ".localhost", ".example"))
        ):
            fail(f"source URL is not a real public web location: {url}")
    prose = text[: text.index(HEADINGS[8])]
    if len(set(re.findall(r"\[[^\]\n]+\]\((https?://[^\s)]+)\)", prose))) < 6:
        fail("material claims need inline links, not only a bibliography")
    for evidence_heading, next_heading in zip(HEADINGS[1:5], HEADINGS[2:6]):
        evidence_section = section(text, evidence_heading, next_heading)
        if not re.search(r"\[[^\]\n]+\]\(https?://[^\s)]+\)", evidence_section):
            fail(f"section needs at least one inline source: {evidence_heading}")

    official = {
        url for url in unique_links
        if any(
            host_matches((urlparse(url).hostname or "").lower().rstrip("."), suffix)
            for suffix in OFFICIAL_SUFFIXES
        )
    }
    if len(official) < 5:
        fail("cite at least five government or public-agency sources")
    if not any(re.match(r"https?://doi\.org/10\.\d{4,9}/\S+$", url, re.I) for url in unique_links):
        fail("cite at least one peer-reviewed source using its DOI link")

    sources = section(text, HEADINGS[8], None)
    source_entries = [line for line in sources.splitlines() if re.match(r"^\s*[-*]\s+", line)]
    if len(source_entries) < 8:
        fail("the annotated source list must contain at least eight entries")
    listed_links: list[str] = []
    peer_reviewed_doi = False
    for entry in source_entries:
        if not re.search(r"\b(Government|Public agency|Peer-reviewed)\b", entry, re.I):
            fail(f"source entry lacks a source-type label: {entry}")
        if not re.search(rf"accessed\s+{CUTOFF_DATE}", entry, re.I):
            fail(f"source entry lacks the requested access date: {entry}")
        entry_links = re.findall(r"\[[^\]\n]+\]\((https?://[^\s)]+)\)", entry)
        if not entry_links:
            fail(f"source entry lacks a web source link: {entry}")
        listed_links.extend(entry_links)
        if re.search(r"\bPeer-reviewed\b", entry, re.I) and any(
            re.match(r"https?://doi\.org/10\.\d{4,9}/\S+$", url, re.I) for url in entry_links
        ):
            peer_reviewed_doi = True
        if not re.search(r"\),\s*.{5,};\s*accessed", entry, re.I):
            fail(f"source entry lacks a brief annotation: {entry}")
    if len(set(listed_links)) < 8:
        fail("the annotated list must contain at least eight distinct linked sources")
    if set(unique_links) - set(listed_links):
        fail("every cited source must appear in the annotated source list")
    if not peer_reviewed_doi:
        fail("a DOI source must be labeled Peer-reviewed in the source list")

    forbidden = (
        "todo",
        "tbd",
        "insert citation",
        "submitted on behalf",
        "submission confirmation",
        "confirmation number",
        "emailed to the committee",
    )
    for phrase in forbidden:
        if phrase in lower:
            fail(f"unfinished or unauthorized-action language found: {phrase}")

    print(f"policy brief accepted: {count} words, {len(unique_links)} sources")


if __name__ == "__main__":
    main()
