#!/usr/bin/env python3
"""Deterministic, protected acceptance checks for the comparison artifact."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path.cwd()
DELIVERABLE = ROOT / "comparison.md"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def section(text: str, heading: str, next_heading: str | None) -> str:
    start_match = re.search(rf"(?mi)^#{{1,6}}\s+{re.escape(heading)}\s*$", text)
    require(start_match is not None, f"missing Markdown heading: {heading}")
    start = start_match.end()
    if next_heading is None:
        return text[start:]
    end_match = re.search(rf"(?mi)^#{{1,6}}\s+{re.escape(next_heading)}\s*$", text[start:])
    require(end_match is not None, f"missing Markdown heading: {next_heading}")
    return text[start : start + end_match.start()]


require(DELIVERABLE.is_file(), "comparison.md was not created")
require(not DELIVERABLE.is_symlink(), "comparison.md must be a regular workspace file")
try:
    text = DELIVERABLE.read_text(encoding="utf-8")
except UnicodeDecodeError:
    fail("comparison.md must be UTF-8 text")

words = re.findall(r"\b[\w]+(?:[.’'/-][\w]+)*\b", text, flags=re.UNICODE)
require(450 <= len(words) <= 650, f"comparison must be 450-650 words; found {len(words)}")

heading_matches = list(
    re.finditer(r"(?mi)^#{1,6}\s+(Verified comparison|Recommendation|Uncertainties)\s*$", text)
)
require(len(heading_matches) == 3, "use each required Markdown heading exactly once")
require(
    [match.group(1).lower() for match in heading_matches]
    == ["verified comparison", "recommendation", "uncertainties"],
    "required headings are not in the requested order",
)

verified = section(text, "Verified comparison", "Recommendation")
recommendation = section(text, "Recommendation", "Uncertainties")
uncertainties = section(text, "Uncertainties", None)

require(re.search(r"(?m)^\s*\|.+\|\s*$", verified) is not None, "verified section needs a Markdown table")
require(
    re.search(r"(?m)^\s*\|?\s*:?-{3,}", verified) is not None,
    "comparison table is missing its separator row",
)
table_lines = [line for line in verified.splitlines() if "|" in line]
require(
    len(table_lines) == 5,
    "use one compact table containing one header, one separator, and exactly three product rows",
)

products = ("Kobo Libra Colour", "Kobo Clara Colour", "Kindle Paperwhite")
for product in products:
    rows = [line for line in verified.splitlines() if product.lower() in line.lower() and "|" in line]
    require(len(rows) == 1, f"verified table needs exactly one row for {product}")

libra_row = next(line for line in verified.splitlines() if "kobo libra colour" in line.lower() and "|" in line)
clara_row = next(line for line in verified.splitlines() if "kobo clara colour" in line.lower() and "|" in line)
kindle_row = next(line for line in verified.splitlines() if "kindle paperwhite" in line.lower() and "|" in line)

def has_all(line: str, fragments: tuple[str, ...]) -> bool:
    folded = line.casefold().replace(",", "")
    return all(fragment.casefold() in folded for fragment in fragments)


require(
    has_all(libra_row, ("$259.99", "7", "199.5", "32", "ipx8", "button", "[s1]")),
    "Kobo Libra Colour row is missing a requested sourced comparison fact",
)
require(
    re.search(r"comfortlight pro|colou?r temperature|warm", libra_row, re.I) is not None
    and re.search(r"overdrive|libby", libra_row, re.I) is not None,
    "Kobo Libra Colour row must document warm-light adjustment and a library route",
)
require(
    has_all(clara_row, ("$179.99", "6", "174", "16", "ipx8", "[s2]")),
    "Kobo Clara Colour row is missing a requested sourced comparison fact",
)
require(
    re.search(r"comfortlight pro|colou?r temperature|warm", clara_row, re.I) is not None
    and re.search(r"overdrive|libby", clara_row, re.I) is not None,
    "Kobo Clara Colour row must document warm-light adjustment and a library route",
)
require(
    re.search(r"buttons?.{0,35}(not (?:established|documented)|unknown)", clara_row, re.I) is not None,
    "Kobo Clara Colour row must preserve the source pack's uncertainty about physical buttons",
)
require(
    has_all(kindle_row, ("$159.99", "7", "211", "16", "ipx8", "warm", "libby", "[s3]", "[s4]", "[s5]", "[s6]")),
    "Kindle Paperwhite row is missing a requested sourced comparison fact",
)
require(
    re.search(r"buttons?.{0,35}(not (?:established|documented)|unknown)", kindle_row, re.I) is not None,
    "Kindle Paperwhite row must preserve the source pack's uncertainty about physical buttons",
)

valid_ids = {f"S{i}" for i in range(1, 7)}
used_ids = set(re.findall(r"\[(S\d+)\]", text))
require(used_ids, "no source citations found")
require(used_ids <= valid_ids, f"unknown source IDs used: {sorted(used_ids - valid_ids)}")

product_fact_marker = re.compile(
    r"\$|\b(?:inch|displays?|screens?|weigh(?:s|ing)?|weight|lighter|heavier|g|gb|storage|ipx8|"
    r"comfortlight|warm|touch|buttons?|usb|battery|library|libby|overdrive|prices?)\b",
    re.I,
)
for line_number, line in enumerate(text.splitlines(), start=1):
    if any(product.casefold() in line.casefold() for product in products) and product_fact_marker.search(line):
        require(
            re.search(r"\[S[1-6]\]", line) is not None,
            f"product claim on line {line_number} has no same-line source citation",
        )

require("$270" in text, "the corrected $270 budget is not applied")
require("$180" not in text, "the superseded $180 budget was retained")
require(
    re.search(r"screen size.{0,45}(more important|priority|outweighs).{0,45}(weight|lighter|lightness)", recommendation, re.I | re.S)
    or re.search(r"(weight|lighter|lightness).{0,45}(less important|secondary).{0,45}screen size", recommendation, re.I | re.S),
    "recommendation does not apply screen size over low weight",
)
require(
    re.search(r"buttons?.{0,35}(nice-to-have|preference|bonus)", recommendation, re.I | re.S),
    "page-turn buttons are not retained as a nice-to-have",
)
require(
    re.search(r"buttons?.{0,25}(must|required|non-negotiable)", text, re.I | re.S) is None,
    "page-turn buttons were incorrectly promoted to a requirement",
)

ranked = re.findall(
    r"(?m)^\s*([123])[.)]\s+[^\n]*(Kobo Libra Colour|Kobo Clara Colour|Kindle Paperwhite)",
    recommendation,
    flags=re.I,
)
require(len(ranked) == 3, "Recommendation must contain a numbered ranking of all three products")
require([number for number, _ in ranked] == ["1", "2", "3"], "ranking must run from 1 through 3")
rank_names = [name.casefold() for _, name in ranked]
require(len(set(rank_names)) == 3, "each compared product must appear exactly once in the ranking")
require(
    rank_names == ["kobo libra colour", "kindle paperwhite", "kobo clara colour"],
    "ranking does not reflect the corrected priorities and retained nice-to-have",
)
require(
    re.search(r"(winner|recommend|pick|choice)[^\n]{0,90}Kobo Libra Colour|Kobo Libra Colour[^\n]{0,90}(winner|recommend|pick|choice)", recommendation, re.I),
    "no clear Kobo Libra Colour winner is named",
)

require(
    re.search(r"\b(best|winner|recommend(?:ed|ation)?|rank(?:ed|ing)?)\b", verified, re.I) is None,
    "recommendation language appears in the verified-facts section",
)
for topic, pattern in {
    "price/tax variability": r"(price|tax).{0,70}(change|vary|uncertain|not (?:included|settled|guaranteed))",
    "library availability": r"(library|libby|overdrive).{0,100}(availability|available|vary|not every|select)",
    "unstated ergonomics": r"(handedness|case weight|ergonomic).{0,100}(unknown|not (?:given|stated|established)|uncertain)",
}.items():
    require(re.search(pattern, uncertainties, re.I | re.S) is not None, f"Uncertainties must address {topic}")

print(f"PASS: comparison.md satisfies the retained and corrected constraints ({len(words)} words)")
