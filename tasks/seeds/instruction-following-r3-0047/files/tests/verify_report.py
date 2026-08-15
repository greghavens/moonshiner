#!/usr/bin/env python3
"""Protected, deterministic checks for the final researched handout."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse


REPORT = Path("allergy-options.md")
EXPECTED_HEADINGS = [
    "## Bottom line",
    "## Side-by-side",
    "## Safety for this situation",
    "## When to get help",
    "## Sources",
]
LINK_RE = re.compile(
    r"\[([^\]\n]+)\]\((https://[^)\s]+)(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\)"
)
MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Sept|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
ACCESS_DATE_RE = re.compile(
    rf"\b(?:accessed(?:\s+on)?|access\s+date\s*:?)\s+(?:"
    rf"\d{{4}}[-/]\d{{1,2}}[-/]\d{{1,2}}|"
    rf"\d{{1,2}}/\d{{1,2}}/\d{{4}}|"
    rf"{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?[,]?\s+\d{{4}}|"
    rf"\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH}[,]?\s+\d{{4}}"
    rf")\b",
    flags=re.IGNORECASE,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        raise SystemExit(1)


def section(text: str, heading: str, next_heading: str | None) -> str:
    body = text.split(heading, 1)[1]
    return body if next_heading is None else body.split(next_heading, 1)[0]


def cells(line: str) -> list[str]:
    parts = line.strip().split("|")
    if parts and not parts[0]:
        parts.pop(0)
    if parts and not parts[-1]:
        parts.pop()
    return [part.strip() for part in parts]


def separator_row(parts: list[str]) -> bool:
    return len(parts) >= 2 and all(
        re.fullmatch(r":?-{3,}:?", part) is not None for part in parts
    )


require(REPORT.is_file(), "allergy-options.md was not created")
text = REPORT.read_text(encoding="utf-8")
lower = text.casefold()
lines = text.splitlines()

# CommonMark permits up to three leading spaces on ATX headings. Detect them all,
# as well as setext headings, so indented extra headings cannot evade the brief.
atx_headings = [
    line.strip()
    for line in lines
    if re.match(r"^ {0,3}#{1,6}(?:[ \t]+|$)", line)
]
require(
    atx_headings == EXPECTED_HEADINGS,
    "the five required headings must be the only ATX headings and stay in order",
)
for index, line in enumerate(lines[1:], start=1):
    if re.fullmatch(r" {0,3}(?:=+|-+)[ \t]*", line) and lines[index - 1].strip():
        require(False, "setext headings are not allowed in addition to the five headings")

words = re.findall(r"\b[\w’'-]+\b", text, flags=re.UNICODE)
require(
    500 <= len(words) <= 850,
    f"handout must contain 500–850 words; found {len(words)}",
)

links = LINK_RE.findall(text)
require(len(links) == 4, f"expected exactly four Markdown links; found {len(links)}")
urls = [url for _, url in links]
require(len(set(urls)) == 4, "all four source URLs must be distinct")
for url in urls:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    require(
        parsed.scheme == "https" and (host == "gov" or host.endswith(".gov")),
        f"source is not an HTTPS U.S. government URL: {url}",
    )

sources = section(text, "## Sources", None)
require(
    len(LINK_RE.findall(sources)) == 4,
    "all four links must appear in Sources",
)
require(
    len(ACCESS_DATE_RE.findall(sources)) == 4,
    "each of the four sources must include a recognizable access date",
)
without_markdown_links = LINK_RE.sub("", text)
require(
    re.search(r"https?://", without_markdown_links, flags=re.IGNORECASE) is None,
    "URLs must occur only in the four HTTPS Markdown links",
)

# The later correction supersedes only the age; every other supplied fact remains.
require(
    re.search(r"\b(?:age\s*:?\s*38|38(?:[- ]year[- ]old|\s+years?\s+old))\b", lower)
    is not None,
    "the corrected age of 38 must be used",
)
require(
    re.search(r"\b(?:age\s*:?\s*68|68(?:[- ]year[- ]old|\s+years?\s+old))\b", lower)
    is None,
    "the superseded age of 68 must not remain",
)

joint_no_kidney_liver = r"\b(?:no|without)\s+(?:known\s+)?kidney\s+(?:or|and)\s+liver\s+disease\b"
retained_details = {
    "lisinopril": r"\blisinopril\b",
    "10 mg": r"\b10\s*mg\b",
    "controlled high blood pressure": r"\b(?:(?:well[- ]?)?controlled\s+(?:high\s+blood\s+pressure|hypertension)|(?:high\s+blood\s+pressure|hypertension)\s+(?:is|remains)\s+(?:well[- ]?)?controlled)\b",
    "no kidney disease": rf"(?:\b(?:no|without)\s+(?:known\s+)?kidney\s+disease\b|{joint_no_kidney_liver})",
    "no liver disease": rf"(?:\b(?:no|without)\s+(?:known\s+)?liver\s+disease\b|{joint_no_kidney_liver})",
    "no asthma": r"\b(?:no|does\s+not\s+have|doesn't\s+have|without)\s+asthma\b",
    "sneezing": r"\bsneez(?:e|es|ing)\b",
    "itchy nose": r"\bitchy\s+nose\b",
    "itchy watery eyes": r"\b(?:itchy,?\s+watery|watery,?\s+itchy)\s+eyes\b",
    "weekday driving": r"\b(?:driv\w*.{0,40}weekdays?|weekdays?.{0,40}driv\w*)\b",
}
for label, pattern in retained_details.items():
    require(re.search(pattern, lower, flags=re.DOTALL) is not None, f"missing retained detail: {label}")

for medicine in ("cetirizine", "loratadine"):
    require(lower.count(medicine) >= 3, f"comparison is not substantive for {medicine}")

side_by_side = section(text, "## Side-by-side", "## Safety for this situation")
side_lines = side_by_side.splitlines()
table_parts: list[list[str]] | None = None
for index in range(len(side_lines) - 2):
    header = cells(side_lines[index])
    divider = cells(side_lines[index + 1])
    if "|" in side_lines[index] and separator_row(divider) and len(header) == len(divider):
        rows: list[list[str]] = []
        for candidate in side_lines[index + 2 :]:
            if "|" not in candidate:
                break
            row = cells(candidate)
            if len(row) != len(header):
                break
            rows.append(row)
        if rows:
            table_parts = [header, divider, *rows]
            break
require(table_parts is not None, "Side-by-side must contain a valid Markdown comparison table")
table_text = " ".join(" ".join(row) for row in table_parts or []).casefold()
require("cetirizine" in table_text, "comparison table must include cetirizine")
require("loratadine" in table_text, "comparison table must include loratadine")

bottom_line = section(text, "## Bottom line", "## Side-by-side").casefold()
loratadine_choice = re.search(
    r"\bloratadine\b.{0,90}\b(?:start\w*|first|prefer\w*|better|practical)\b.{0,35}\b(?:choice|option|fit)\b",
    bottom_line,
    flags=re.DOTALL,
) or re.search(
    r"\b(?:start\w*(?:\s+choice)?(?:\s+is|\s+with)?|choose|prefer)\b.{0,45}\bloratadine\b",
    bottom_line,
    flags=re.DOTALL,
)
require(loratadine_choice is not None, "Bottom line must make loratadine the clear starting choice")
require(re.search(r"\bdriv\w*\b", bottom_line) is not None, "Bottom line must connect the choice to daytime driving")
drowsiness_rationale = (
    re.search(
        r"\bloratadine\b.{0,100}\b(?:less|lower)\b.{0,45}\b(?:drows\w*|sleep\w*|sedat\w*)\b",
        bottom_line,
        flags=re.DOTALL,
    )
    or re.search(
        r"\b(?:less|lower)\b.{0,45}\b(?:drows\w*|sleep\w*|sedat\w*)\b.{0,100}\bloratadine\b",
        bottom_line,
        flags=re.DOTALL,
    )
    or re.search(
        r"\bcetirizine\b.{0,120}\b(?:warn\w*|drows\w*|sleep\w*|sedat\w*)\b.{0,80}\bdriv\w*\b",
        bottom_line,
        flags=re.DOTALL,
    )
)
require(drowsiness_rationale is not None, "Bottom line must explain the driving choice using comparative drowsiness guidance")
require(
    re.search(r"\b(?:uncertain\w*|var(?:y|ies)|individual|not\s+universally|no\s+guarantee|does\s+not\s+guarantee)\b", bottom_line)
    is not None,
    "Bottom line must state uncertainty or individual variation",
)
require(
    re.search(
        r"(?:\bloratadine\b.{0,80}\b(?:may|can|could|possible)\b.{0,40}\b(?:drows\w*|sleep\w*|sedat\w*)\b|"
        r"\b(?:drows\w*|sleep\w*|sedat\w*)\b.{0,80}\bloratadine\b|"
        r"\bboth\b.{0,80}\b(?:drows\w*|sleep\w*|sedat\w*)\b)",
        bottom_line,
        flags=re.DOTALL,
    )
    is not None,
    "Bottom line must not imply that loratadine guarantees freedom from drowsiness",
)

# Practical use must go beyond mentioning a label or drowsiness as keywords.
require(re.search(r"\b(?:drug\s+facts|package\s+label|product\s+label)\b", lower) is not None, "handout must identify the consumer label to follow")
require(
    re.search(r"\b(?:read|follow|use)\b.{0,55}\b(?:drug\s+facts|package\s+label|product\s+label|directed|directions|dosing)\b", lower, flags=re.DOTALL)
    is not None,
    "handout must give actionable label-use guidance",
)
safe_first_trial = re.search(
    r"\bfirst\s+(?:dose|time)\b.{0,100}\b(?:not|avoid|won't|will\s+not)\b.{0,30}\bdriv\w*\b",
    lower,
    flags=re.DOTALL,
) or re.search(
    r"\b(?:not|avoid|don't|shouldn't)\b.{0,30}\bdriv\w*\b.{0,80}\buntil\b.{0,35}\b(?:know|known|affect\w*|response)\b",
    lower,
    flags=re.DOTALL,
)
require(
    safe_first_trial is not None,
    "handout must say to avoid driving until the medicine's effects are known",
)
no_drive_if_drowsy = re.search(
    r"\b(?:if|when)\b.{0,55}\b(?:drows\w*|sleep\w*|sedat\w*)\b.{0,80}\b(?:not|avoid|don't|shouldn't)\b.{0,30}\bdriv\w*\b",
    lower,
    flags=re.DOTALL,
) or re.search(
    r"\b(?:not|avoid|don't|shouldn't)\b.{0,30}\bdriv\w*\b.{0,65}\b(?:if|when)\b.{0,35}\b(?:drows\w*|sleep\w*|sedat\w*)\b",
    lower,
    flags=re.DOTALL,
)
require(no_drive_if_drowsy is not None, "handout must say not to drive if drowsy or sleepy")

safety = section(text, "## Safety for this situation", "## When to get help").casefold()
require("lisinopril" in safety, "Safety for this situation must address lisinopril")
require(
    re.search(r"\b(?:interaction\w*|medication\s+list|medicine\s+list|current\s+medications?|other\s+(?:medications?|medicines)|medicines?\s+he\s+takes)\b", safety) is not None,
    "Safety for this situation must address lisinopril in a medication-interaction context",
)

when_help = section(text, "## When to get help", "## Sources").casefold()
require(
    re.search(r"\b911\b", when_help) is not None
    and re.search(
        r"\b(?:(?:trouble|difficulty|problems?)\s+(?:breathing|swallowing)|"
        r"shortness\s+of\s+breath|(?:cannot|can't)\s+breathe|"
        r"(?:face|lip|lips|tongue|throat)\s+swelling|collapse\w*|unresponsive)\b",
        when_help,
    )
    is not None,
    "When to get help must include concrete emergency guidance",
)
require(
    re.search(r"\b(?:pharmacist|clinician|doctor|prescriber|nurse|health\s*care\s+professional|medical\s+professional)\b", when_help) is not None
    and re.search(
        r"\b(?:remain|persist|continue|not\s+improve|doesn't\s+improve|troublesome|interfere|side\s+effects?|unsure|questions?)\b",
        when_help,
    )
    is not None,
    "When to get help must include routine professional-help guidance",
)

# This is intentionally a broad, deterministic guard over common allergy-drug
# names; it enforces the two-medicine scope without relying on network state.
prohibited_terms = (
    "claritin", "zyrtec", "alavert", "aller-tec", "wal-itin", "allegra",
    "benadryl", "xyzal", "flonase", "nasacort", "rhinocort", "nasonex",
    "singulair", "sudafed", "afrin", "fexofenadine", "diphenhydramine",
    "levocetirizine", "desloratadine", "chlorpheniramine", "brompheniramine",
    "clemastine", "hydroxyzine", "azelastine", "olopatadine", "ketotifen",
    "fluticasone", "triamcinolone", "budesonide", "mometasone", "cromolyn",
    "montelukast", "pseudoephedrine", "phenylephrine", "oxymetazoline",
)
for prohibited in prohibited_terms:
    require(
        re.search(rf"(?<![\w-]){re.escape(prohibited)}(?![\w-])", lower) is None,
        f"prohibited brand or other allergy medicine named: {prohibited}",
    )

print("PASS: allergy-options.md satisfies the corrected multi-turn brief")
