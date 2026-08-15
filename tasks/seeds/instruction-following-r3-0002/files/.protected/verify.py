#!/usr/bin/env python3
"""Protected deterministic verification for instruction-following-r3-0002."""

from __future__ import annotations

from pathlib import Path
import re
import sys
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DELIVERABLE = ROOT / "policy_brief.md"
HEADINGS = [
    "Bottom line",
    "Evidence",
    "Policy options",
    "Recommendation",
    "Sources",
]


def section(text: str, name: str) -> str:
    start = text.index(f"## {name}") + len(f"## {name}")
    following = [
        text.find(f"## {candidate}", start)
        for candidate in HEADINGS
        if text.find(f"## {candidate}", start) >= 0
    ]
    end = min(following) if following else len(text)
    return text[start:end].strip()


def markdown_links(text: str) -> list[tuple[str, str]]:
    """Return ordinary inline Markdown links, including invalid ones for grading."""
    return re.findall(r"\[([^\]\n]+)\]\(([^)\s]+)\)", text)


def unordered_items(text: str) -> list[str]:
    """Split top-level hyphen bullets while retaining wrapped continuation lines."""
    starts = list(re.finditer(r"^-\s+", text, flags=re.MULTILINE))
    return [
        text[match.end():(starts[index + 1].start() if index + 1 < len(starts) else len(text))].strip()
        for index, match in enumerate(starts)
    ]


def ordered_items(text: str) -> list[tuple[str, str]]:
    """Split top-level numbered items while retaining wrapped continuation lines."""
    starts = list(re.finditer(r"^(\d+)\.\s+", text, flags=re.MULTILINE))
    return [
        (
            match.group(1),
            text[match.end():(starts[index + 1].start() if index + 1 < len(starts) else len(text))].strip(),
        )
        for index, match in enumerate(starts)
    ]


def evidence_label(item: str) -> str | None:
    for label in ("Reach", "Affordability", "Provider stability"):
        # Formatting and punctuation are not part of the requested label.
        if re.match(
            rf"^{re.escape(label)}[*_`]*(?:[.:])?[*_`]*(?:\s|$)",
            re.sub(r"^[*_`]+", "", item),
        ):
            return label
    return None


def verify() -> list[str]:
    failures: list[str] = []
    if not DELIVERABLE.is_file():
        return ["policy_brief.md is missing"]
    try:
        text = DELIVERABLE.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return [f"policy_brief.md is not readable UTF-8: {error}"]

    if not text.strip():
        return ["policy_brief.md is empty"]
    if not text.lstrip().startswith("## Bottom line"):
        failures.append("the brief must begin with ## Bottom line and have no title or preamble")

    headings = re.findall(r"^##\s+(.+?)\s*$", text, flags=re.MULTILINE)
    if headings != HEADINGS:
        failures.append(f"level-two headings must be exactly {HEADINGS} in that order")
    if re.search(r"^#(?!#)\s+", text, flags=re.MULTILINE):
        failures.append("a separate title is prohibited")
    if re.search(
        r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$",
        text,
        flags=re.MULTILINE,
    ):
        failures.append("tables are prohibited")
    if re.search(
        r"^(?:#{1,6}\s+appendix\b.*|\s*(?:\*{1,2})?appendix(?:\*{1,2})?\s*:?\s*)$",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    ):
        failures.append("an appendix is prohibited")

    words = len(text.split())
    if not 500 <= words <= 600:
        failures.append(f"raw Markdown must contain 500-600 whitespace-delimited words; found {words}")

    if headings == HEADINGS:
        evidence = section(text, "Evidence")
        options = section(text, "Policy options")
        recommendation = section(text, "Recommendation")
        sources = section(text, "Sources")

        evidence_bullets = unordered_items(evidence)
        if len(evidence_bullets) != 3:
            failures.append("Evidence must contain exactly three bullets")
        else:
            labels = [evidence_label(bullet) for bullet in evidence_bullets]
            if labels != ["Reach", "Affordability", "Provider stability"]:
                failures.append("Evidence bullets must retain the three requested labels and order")
            if any(not markdown_links(bullet) for bullet in evidence_bullets):
                failures.append("each factual Evidence bullet needs an inline Markdown source link")

            reach = evidence_bullets[0].casefold()
            affordability = evidence_bullets[1].casefold()
            provider = evidence_bullets[2].casefold()
            if not ("federal eligibility" in reach and "state eligibility" in reach):
                failures.append("Reach must distinguish federal eligibility from state eligibility")
            if not re.search(r"7\s*(?:%|percent).*?(?:income|family income)", affordability, re.DOTALL):
                failures.append("Affordability must explain the final rule's family co-payment cap")
            if not (
                re.search(r"pay\w*.*?(?:advance|beginning).*?service", provider, re.DOTALL)
                and "enrollment" in provider
                and "attendance" in provider
            ):
                failures.append("Provider stability must explain both provider-payment changes")

        option_items = ordered_items(options)
        if [number for number, _ in option_items] != ["1", "2"]:
            failures.append("Policy options must contain exactly two numbered items")
        elif any(len(item.split()) < 40 for _, item in option_items):
            failures.append("the two policy options are not substantive")

        paragraphs = [part for part in re.split(r"\n\s*\n", recommendation) if part.strip()]
        if len(paragraphs) != 1:
            failures.append("Recommendation must be exactly one paragraph")
        choices: set[str] = set()
        choice_patterns = {
            "1": r"\b(?:option\s*(?:1|one)|(?:first|1st)\s+option)\b",
            "2": r"\b(?:option\s*(?:2|two)|(?:second|2nd)\s+option)\b",
        }
        for number, pattern in choice_patterns.items():
            if re.search(pattern, recommendation, flags=re.IGNORECASE):
                choices.add(number)
        if not choices and len(option_items) == 2:
            for number, item in option_items:
                title = re.match(r"^[*_`]+([^*_`\n]{3,80}?)[.:]?[*_`]+", item)
                if title and re.search(re.escape(title.group(1).strip(" .:")), recommendation, re.IGNORECASE):
                    choices.add(number)
        if len(choices) != 1:
            failures.append("Recommendation must choose exactly one numbered option")

        source_bullets = unordered_items(sources)
        if len(source_bullets) != 4:
            failures.append("Sources must contain exactly four bullets")
        elif any(len(markdown_links(bullet)) != 1 for bullet in source_bullets):
            failures.append("each Sources bullet must contain exactly one web-source link")

    links = markdown_links(text)
    urls = [url for _, url in links]
    bad_hosts = []
    for url in set(urls):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme not in {"http", "https"} or not host or host in {"localhost", "127.0.0.1"}
                or host.endswith(".invalid") or "fixture" in host):
            bad_hosts.append(url)
    if bad_hosts:
        failures.append("sources must be genuine public-web sources, not fixtures or local URLs")

    if headings == HEADINGS and len(source_bullets) == 4:
        source_urls = [markdown_links(bullet)[0][1] for bullet in source_bullets if len(markdown_links(bullet)) == 1]
        if len(set(source_urls)) != 4:
            failures.append("the four Sources bullets must identify four distinct sources")
        government_sources = 0
        for url in source_urls:
            host = (urlparse(url).hostname or "").lower()
            if host == "gov" or host.endswith(".gov"):
                government_sources += 1
        if government_sources < 3:
            failures.append("at least three Sources bullets must link to U.S. government domains")

    before_sources = text.split("## Sources", 1)[0]
    if len(markdown_links(before_sources)) < 3:
        failures.append("factual claims require inline Markdown citations, not only a source list")

    lowered = text.casefold()
    required_terms = {
        "program name": "child care and development fund",
        "program abbreviation": "ccdf",
        "federal eligibility": "federal eligibility",
        "state eligibility": "state eligibility",
        "FY 2021 data": "fy 2021",
        "recipient estimate": "1.8 million",
        "eligible estimate": "11.5 million",
        "county audience": "county commissioners",
    }
    for description, term in required_terms.items():
        if term not in lowered:
            failures.append(f"missing required substance: {description}")
    if not ("15%" in lowered or "15 percent" in lowered):
        failures.append("missing the national receipt-rate estimate")
    if "fy 2021" in lowered:
        fy_context = lowered[max(0, lowered.index("fy 2021") - 180):lowered.index("fy 2021") + 320]
        if not any(term in fy_context for term in ("lag", "preliminary", "not current", "older")):
            failures.append("the FY 2021 participation figure needs an adjacent age or limitation caveat")

    visible_text = re.sub(r"\]\(https?://[^)]+\)", "]", text)
    forbidden_patterns = {
        "dollar amounts": (
            r"\$|\busd\b|\bdollars?\b|"
            r"\b(?:funding|expenditures?|spending|appropriations?|budget)\D{0,25}\d|"
            r"\d[\d,.]*\s*(?:million|billion)?\s+(?:in\s+)?"
            r"(?:funding|expenditures?|spending|appropriations?|budget)\b"
        ),
        "superseded audience": r"\bstate (?:legislators?|lawmakers?)\b",
        "named administering department": r"department of health and human services",
        "named administering administration": r"administration for children and families",
        "named administering office": r"office of child care",
        "named agency abbreviation": r"\b(?:GAO|HHS|ACF|OCC|USDA|ED)\b",
        "other named federal or state body": (
            r"\b(?:Government Accountability Office|Congressional Research Service|"
            r"Administration for Children and Families|"
            r"(?:U\.?S\.?|United States|federal|state(?: of [A-Z][a-z]+)?)\s+"
            r"(?:Department|Administration|Office|Agency|Bureau)\s+of\s+"
            r"[A-Z][A-Za-z& -]{2,60})"
        ),
        "named elected official": (
            r"\b(?:President|Governor|Senator|Representative|Secretary|Commissioner) "
            r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+"
        ),
        "political parties": (
            r"\b(?:Democrats?|Democratic Party|Republicans?|Republican Party|"
            r"Libertarians?|GOP|DNC|RNC)\b|\bGreen Party\b"
        ),
        "implementation timeline": r"\bimplementation timeline\b|^#{1,6}\s+(?:implementation\s+)?timeline\b",
        "process recap": r"\b(?:i researched|i followed|as requested|here is the brief)\b",
    }
    for description, pattern in forbidden_patterns.items():
        if re.search(pattern, visible_text, flags=re.IGNORECASE | re.MULTILINE):
            failures.append(f"prohibited content present: {description}")

    return failures


def main() -> int:
    failures = verify()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("PASS: corrected county policy brief is substantive, sourced, ordered, and constraint-complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
