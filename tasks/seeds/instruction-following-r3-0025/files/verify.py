#!/usr/bin/env python3
"""Protected acceptance checks for the urban-heat learning guide."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
GUIDE = ROOT / "urban_heat_learning_guide.md"


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def section(text: str, heading: str, next_heading: str | None) -> str:
    start_match = re.search(rf"^## {re.escape(heading)}\s*$", text, re.M)
    if start_match is None:
        fail(f"missing section {f'## {heading}'!r}")
    start = start_match.end()
    if next_heading is None:
        return text[start:]
    end_match = re.search(rf"^## {re.escape(next_heading)}\s*$", text[start:], re.M)
    if end_match is None:
        fail(f"missing section {f'## {next_heading}'!r}")
    return text[start : start + end_match.start()]


def bullets(body: str) -> list[str]:
    items: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^[-*+]\s+(.+)$", line)
        if match:
            items.append(match.group(1).strip())
        elif items and re.match(r"^\s{2,}\S", line):
            items[-1] += " " + line.strip()
    return items


def require_pattern(text: str, pattern: str, label: str) -> None:
    if not re.search(pattern, text, flags=re.IGNORECASE):
        fail(f"missing supported fact: {label}")


def square_bracket_citations(text: str) -> set[str]:
    bracketed = re.findall(r"\[([^]]+)\]", text)
    return set(re.findall(r"\bS\d+\b", " ".join(bracketed)))


SOURCE_TITLES = {
    "S1": "Block-scale temperature logger pilot",
    "S2": "Hot nights and sleep diary study",
    "S3": "Transit shade access audit",
    "S4": "Cooling-center activation memo",
    "S5": "Cool-roof housing pilot",
    "S6": "East Ward renter interviews",
}


def mentioned_sources(text: str) -> set[str]:
    mentions = set(re.findall(r"\bS\d+\b", text))
    folded = text.casefold()
    mentions.update(
        source_id
        for source_id, title in SOURCE_TITLES.items()
        if title.casefold() in folded
    )
    return mentions


def main() -> None:
    if not GUIDE.is_file():
        fail("urban_heat_learning_guide.md was not created")

    text = GUIDE.read_text(encoding="utf-8")
    if len(text) < 1900:
        fail("guide is too short to be the requested substantive result")

    expected_sections = [
        "Sourced facts",
        "Recommendations: four-week guide",
        "Open questions and uncertainty",
        "Source index",
    ]
    section_matches = [
        list(re.finditer(rf"^## {re.escape(name)}\s*$", text, re.M))
        for name in expected_sections
    ]
    if any(len(matches) != 1 for matches in section_matches):
        fail("each required level-two section must appear exactly once")
    positions = [matches[0].start() for matches in section_matches]
    if positions != sorted(positions):
        fail("required level-two sections are missing or out of order")

    facts_body = section(text, expected_sections[0], expected_sections[1])
    facts = bullets(facts_body)
    if len(facts) != 8:
        fail(f"Sourced facts must contain exactly 8 bullets, found {len(facts)}")

    valid_ids = {f"S{i}" for i in range(1, 7)}
    expected_fact_sources = ["S1", "S1", "S2", "S3", "S4", "S5", "S5", "S6"]
    for number, (bullet, expected_source) in enumerate(
        zip(facts, expected_fact_sources), 1
    ):
        citations = square_bracket_citations(bullet)
        if citations != {expected_source}:
            fail(
                f"sourced-fact bullet {number} must be the requested {expected_source} "
                "fact and cite that card in square brackets"
            )
        if re.search(r"\b(should|recommend|suggest|try|consider)\b", bullet, re.I):
            fail(f"sourced-fact bullet {number} mixes advice into evidence")

    require_pattern(facts[0], r"air[- ]temperature", "S1 measured quantity")
    require_pattern(facts[0], r"loggers?", "S1 measurement device")
    require_pattern(facts[0], r"(?:two|2)\s+met(?:re|er)s?", "S1 logger height")
    require_pattern(facts[0], r"15[- ]minute", "S1 recording interval")
    require_pattern(facts[1], r"18\s+(?:summer\s+)?days?", "S1 pilot duration")
    require_pattern(facts[1], r"(?:one|1|single)\s+neighbou?rhood", "S1 limited geographic coverage")
    require_pattern(facts[2], r"(?:forty-three|43)", "S2 participant count")
    require_pattern(facts[2], r"adults?", "S2 participant type")
    require_pattern(facts[2], r"self[- ]report", "S2 self-report method")
    require_pattern(facts[2], r"sleep\s+diar", "S2 diary method")
    require_pattern(facts[3], r"(?:three|3)\s+(?:of|/)\s*(?:the\s+)?12", "S3 observed count")
    require_pattern(facts[3], r"bus\s+stops?", "S3 audited locations")
    require_pattern(facts[3], r"shade", "S3 observed condition")
    require_pattern(facts[4], r"designated", "S4 designated branches")
    require_pattern(facts[4], r"librar", "S4 library branches")
    require_pattern(facts[4], r"activat(?:e|ed|ion).{0,80}cooling centers?|cooling centers?.{0,80}activat(?:e|ed|ion)", "S4 cooling-center activation")
    require_pattern(facts[4], r"heat alert", "S4 activation rule")
    require_pattern(facts[4], r"emergency manager", "S4 activation authority")
    if not (
        (
            re.search(r"\b24\b", facts[5])
            and re.search(r"(?:occupied\s+)?(?:apartment\s+)?buildings?", facts[5], re.I)
        )
        or re.search(r"(?:six|6)[- ]weeks?", facts[5], re.I)
    ):
        fail("missing supported fact: S5 pilot size or monitoring window")
    require_pattern(facts[6], r"average\s+afternoon\s+indoor\s+temperature", "S5 measured outcome")
    require_pattern(facts[6], r"1[.]1\s*(?:degrees?\s*)?(?:celsius|°\s*C)", "S5 reported difference")
    require_pattern(facts[6], r"lower", "S5 direction of difference")
    require_pattern(facts[6], r"matched", "S5 matched design")
    require_pattern(facts[6], r"comparison", "S5 comparison group")
    require_pattern(facts[7], r"(?:eight|8)", "S6 interview count")
    require_pattern(facts[7], r"convenience[- ]sample", "S6 sample type")
    require_pattern(facts[7], r"interviews?", "S6 interview method")
    require_pattern(facts[7], r"predictable\s+maintenance\s+access", "S6 maintenance priority")
    require_pattern(facts[7], r"multilingual\s+heat\s+notices", "S6 notice priority")

    guide_body = section(text, expected_sections[1], expected_sections[2])
    week_matches = list(
        re.finditer(
            r"^###\s+Week\s+(\d+)(?:\s*[—–:-]\s*|\s+)(.+?)\s*$",
            guide_body,
            re.I | re.M,
        )
    )
    if len(week_matches) != 4:
        fail(f"recommendations must contain exactly four numbered week subsections, found {len(week_matches)}")
    expected_topics = ["Measurement", "Governance", "Equity", "Interventions"]
    for index, (match, expected_topic) in enumerate(zip(week_matches, expected_topics), 1):
        if match.group(1) != str(index) or match.group(2).strip().casefold() != expected_topic.casefold():
            fail(f"Week {index} must be {expected_topic}")

    if re.search(r"^### Week 2\s*[—:-]\s*Health\s*$", guide_body, re.I | re.M):
        fail("the newest correction changed Week 2 from Health to Governance")

    allowed_readings = [{"S1"}, {"S4"}, {"S3", "S6"}, {"S5"}]
    activity_topics = [
        r"measur|logger|temperature|sampling|coverage|scope|season|interval|record|place|duration|height|cadence|method|limit",
        r"govern|activat|authorit|decid|trigger|role|heat alert|cooling center|librar|decision map",
        r"equit|shade|transit|bus stop|renter|access|multilingual|priorit|benefit|missing|sample|distribut|represent",
        r"intervention|cool roof|reflective roof|pilot|temperature|comparison|monitor|follow-up|cost|outcome|time horizon|implement|evaluat|effect",
    ]
    for index, match in enumerate(week_matches):
        block_start = match.end()
        block_end = week_matches[index + 1].start() if index + 1 < 4 else len(guide_body)
        block = guide_body[block_start:block_end]
        primary = re.findall(r"^[-*+]\s+\*\*Primary reading:\*\*\s*(.+)$", block, re.M | re.I)
        activities = re.findall(r"^[-*+]\s+\*\*Activity:\*\*\s*(.+)$", block, re.M | re.I)
        if len(primary) != 1 or len(activities) != 1:
            fail(f"Week {index + 1} needs exactly one bullet labeled Primary reading and one labeled Activity")
        readings = mentioned_sources(primary[0])
        if len(readings) != 1 or not readings.issubset(allowed_readings[index]):
            choices = " or ".join(sorted(allowed_readings[index]))
            fail(f"Week {index + 1} primary reading must be the matching card ({choices})")
        if not re.search(activity_topics[index], activities[0], re.I):
            fail(f"Week {index + 1} activity must be grounded in that week's topic")

    uncertainty_body = section(text, expected_sections[2], expected_sections[3])
    uncertainties = bullets(uncertainty_body)
    if len(uncertainties) != 3:
        fail(f"uncertainty section must contain exactly 3 bullets, found {len(uncertainties)}")
    for number, bullet in enumerate(uncertainties, 1):
        if "source cards do not establish" not in bullet.casefold():
            fail(f"uncertainty bullet {number} does not explicitly state the evidence gap")
        citations = square_bracket_citations(bullet)
        if not citations or not citations.issubset(valid_ids):
            fail(f"uncertainty bullet {number} has no source citation in square brackets")

    uncertainty_topics = [
        (
            re.compile(r"citywide|rest of the city|beyond (?:the )?(?:single|one) neighbou?rhood|generali[sz]", re.I),
            "S1",
            "citywide reach",
        ),
        (
            re.compile(r"(?:sleep|awakening|bedroom).{0,100}caus|caus.{0,100}(?:sleep|awakening|bedroom)", re.I),
            "S2",
            "sleep causality",
        ),
        (
            re.compile(r"(?:cooling|librar).{0,100}(?:fund|disability|accessib)|(?:fund|disability|accessib).{0,100}(?:cooling|librar)", re.I),
            "S4",
            "cooling-center funding or disability access",
        ),
    ]
    seen_topics: set[str] = set()
    for number, bullet in enumerate(uncertainties, 1):
        matches = [topic for topic in uncertainty_topics if topic[0].search(bullet)]
        if len(matches) != 1:
            fail(f"uncertainty bullet {number} must cover exactly one requested uncertainty")
        _, required_source, label = matches[0]
        if required_source not in square_bracket_citations(bullet):
            fail(f"the {label} uncertainty must cite {required_source}")
        seen_topics.add(label)
    if len(seen_topics) != 3:
        fail("the three uncertainty bullets must separately cover all requested gaps")

    index_body = section(text, expected_sections[3], None)
    index_lines = bullets(index_body)
    if len(index_lines) != 6:
        fail(f"source index must contain exactly 6 entries, found {len(index_lines)}")
    source_metadata = {
        "S1": (SOURCE_TITLES["S1"], "River City Climate Office", "2024"),
        "S2": (SOURCE_TITLES["S2"], "Mendez, Okafor, and Lin", "2023"),
        "S3": (SOURCE_TITLES["S3"], "Neighborhood Tree Lab", "2022"),
        "S4": (SOURCE_TITLES["S4"], "Municipal Preparedness Office", "2025"),
        "S5": (SOURCE_TITLES["S5"], "River City Housing Agency", "2021"),
        "S6": (SOURCE_TITLES["S6"], "East Ward Tenants Coalition", "2025"),
    }
    for source_id in sorted(valid_ids):
        matches = [
            line
            for line in index_lines
            if set(re.findall(r"\bS\d+\b", line)) == {source_id}
        ]
        if len(matches) != 1:
            fail(f"source index needs exactly one entry for {source_id}")
        line = matches[0]
        title, creator, year = source_metadata[source_id]
        if not all(item.casefold() in line.casefold() for item in (title, creator, year)):
            fail(f"source index entry {source_id} needs its title, publisher or authors, and year")

    print("PASS: urban heat learning guide satisfies all multi-turn constraints")


if __name__ == "__main__":
    main()
