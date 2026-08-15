#!/usr/bin/env python3
"""Deterministic acceptance checks for the corrected housing brief."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / "housing_brief.md"
REQUIRED_HEADINGS = [
    "## Method and assumptions",
    "## Comparison",
    "## Recommendation",
    "## Caveats",
    "## Sources",
]
EXPECTED_COLUMNS = [
    "Neighborhood",
    "2BR asking rent",
    "Weekday 8:00 a.m. transit",
    "Grocery",
    "Green space",
    "Trade-off",
]
LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE_RE = re.compile(
    rf"\b(?:20\d{{2}}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])|{MONTH}\.?\s+\d{{1,2}},\s+20\d{{2}}|\d{{1,2}}\s+{MONTH}\.?\s+20\d{{2}})\b",
    re.IGNORECASE,
)
OFFICIAL_DOMAINS = {
    "chicago.gov",
    "chicagoparkdistrict.com",
    "cookcountyil.gov",
    "metra.com",
    "metrarail.com",
    "pacebus.com",
    "rtachicago.org",
    "transitchicago.com",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def section(text: str, heading: str) -> str:
    start = text.find(heading)
    if start < 0:
        fail(f"missing section {heading!r}")
    start += len(heading)
    next_heading = text.find("\n## ", start)
    return text[start:] if next_heading < 0 else text[start:next_heading]


def links(text: str) -> list[tuple[str, str]]:
    return LINK_RE.findall(text)


def urls(text: str) -> list[str]:
    return [url for _, url in links(text)]


def domain(url: str) -> str:
    return urlparse(url).netloc.casefold().removeprefix("www.")


def is_primary_or_official(anchor: str, url: str) -> bool:
    host = domain(url)
    if host.endswith(".gov") or any(
        host == official or host.endswith(f".{official}") for official in OFFICIAL_DOMAINS
    ):
        return True
    brand = host.split(".")[-2].replace("-", "") if "." in host else host.replace("-", "")
    anchor_text = re.sub(r"[^a-z0-9]", "", anchor.casefold())
    return len(brand) >= 4 and brand in anchor_text


def table_rows(comparison: str) -> tuple[list[str], list[list[str]]]:
    lines = [line.strip() for line in comparison.splitlines() if line.strip().startswith("|")]
    if len(lines) < 4:
        fail("comparison must contain a header, separator, and two data rows")

    def cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    header = cells(lines[0])
    if header != EXPECTED_COLUMNS:
        fail(f"comparison columns must be exactly {EXPECTED_COLUMNS!r}")
    separator = cells(lines[1])
    if len(separator) != len(EXPECTED_COLUMNS) or not all(
        re.fullmatch(r":?-{3,}:?", cell) for cell in separator
    ):
        fail("comparison table has an invalid Markdown separator")
    rows = [cells(line) for line in lines[2:]]
    if len(rows) != 2 or any(len(row) != len(EXPECTED_COLUMNS) for row in rows):
        fail("comparison table must contain exactly two six-cell data rows")
    return header, rows


def check_row(row: list[str]) -> None:
    neighborhood, rent, transit, grocery, green_space, trade_off = row
    if neighborhood not in {"Hyde Park", "Rogers Park"}:
        fail(f"unexpected neighborhood row {neighborhood!r}")
    if not re.search(r"\$\s?\d[\d,]*", rent):
        fail(f"{neighborhood}: rent cell lacks a dollar amount")
    if not re.search(r"\b(average|median|range)\b", rent, re.IGNORECASE):
        fail(f"{neighborhood}: rent cell must name its statistic")
    if not re.search(r"\b(?:2BR|2[ -]?bedroom|two[ -]bedroom)\b", rent, re.IGNORECASE):
        fail(f"{neighborhood}: rent statistic is not explicitly limited to two bedrooms")
    if not re.search(r"\b(?:20\d{2}|last\s+30\s+days|rolling\s+30.day)\b", rent, re.IGNORECASE):
        fail(f"{neighborhood}: rent cell lacks a source period")
    if not links(rent):
        fail(f"{neighborhood}: rent statistic needs an inline citation")

    if "Illinois Medical District" not in transit:
        fail(f"{neighborhood}: transit cell uses the wrong destination")
    if not re.search(r"8(?::00)?\s*a\.?m\.?", transit, re.IGNORECASE):
        fail(f"{neighborhood}: transit cell lacks the 8:00 a.m. scenario")
    if not re.search(r"\b\d{1,3}\s*[-–]\s*\d{1,3}\s*(?:min|minutes)\b", transit, re.IGNORECASE):
        fail(f"{neighborhood}: transit cell lacks a duration range")
    if not re.search(
        r"\b(?:no|zero|one|two|three|\d+)\s+(?:rail\s+)?transfers?\b",
        transit,
        re.IGNORECASE,
    ):
        fail(f"{neighborhood}: transit cell lacks an explicit transfer count")
    if not re.search(r"representative origin\b", transit, re.IGNORECASE):
        fail(f"{neighborhood}: transit cell does not identify its representative origin")
    if not re.search(r"\b(?:line|route|bus|train|metra)\b", transit, re.IGNORECASE):
        fail(f"{neighborhood}: transit cell does not name a route")
    if not links(transit):
        fail(f"{neighborhood}: live trip-planner result needs an inline citation")
    if not re.search(
        r"\b(?:trip planner|(?:trip|transit|route) plan|directions)\b",
        transit,
        re.IGNORECASE,
    ):
        fail(f"{neighborhood}: transit citation is not identified as a trip-planner result")

    if not links(grocery):
        fail(f"{neighborhood}: grocery cell needs a cited named option")
    if not re.search(
        r"\b(?:full[- ]service|supermarket|grocery store with (?:produce|bakery|pharmacy|meat|seafood))\b",
        grocery,
        re.IGNORECASE,
    ):
        fail(f"{neighborhood}: grocery option is not identified as full-service")
    if not links(green_space):
        fail(f"{neighborhood}: green-space cell needs a cited public option")
    if not re.search(
        r"\b(?:public|park district|city|municipal|forest preserve)\b",
        green_space,
        re.IGNORECASE,
    ):
        fail(f"{neighborhood}: green-space option is not identified as public")
    if len(trade_off.split()) < 8:
        fail(f"{neighborhood}: trade-off is not substantive")
    if not re.search(
        r"\b(?:interpretation|analysis|assessment|inference|judgment|my (?:take|read)|I (?:infer|weigh|view))\b",
        trade_off,
        re.IGNORECASE,
    ):
        fail(f"{neighborhood}: trade-off is not distinguished as interpretation")
    if len(links(" | ".join(row))) < 4:
        fail(f"{neighborhood}: row does not cite each factual comparison category")
    if not any(is_primary_or_official(anchor, url) for anchor, url in links(" | ".join(row))):
        fail(f"{neighborhood}: row lacks a primary or official source")


def main() -> None:
    if not BRIEF.is_file():
        fail("housing_brief.md was not created")
    text = BRIEF.read_text(encoding="utf-8")
    if len(text) < 2000:
        fail("housing brief is too short to be substantive")
    if not text.startswith("# Chicago two-bedroom rental brief\n"):
        fail("brief has the wrong title")
    folded = text.casefold()
    for superseded in ("lincoln square", "pilsen", "ogilvie"):
        if superseded in folded:
            fail(f"brief retained superseded scope: {superseded}")
    positions = [text.find(heading) for heading in REQUIRED_HEADINGS]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        fail("required H2 sections are missing or out of order")

    _, rows = table_rows(section(text, "## Comparison"))
    if {row[0] for row in rows} != {"Hyde Park", "Rogers Park"}:
        fail("comparison must cover exactly Hyde Park and Rogers Park")
    for row in rows:
        check_row(row)

    method = section(text, "## Method and assumptions")
    for term in ("asking", "representative origin", "weekday", "commute first", "rent second"):
        if term not in method.casefold():
            fail(f"method does not preserve clarified assumption: {term}")

    recommendation = section(text, "## Recommendation")
    choices = re.findall(
        r"\brecommend(?:ation)?(?:\s+is|\s*[:—-])?\s+\**(Hyde Park|Rogers Park)\b",
        recommendation,
        re.IGNORECASE,
    )
    if len(choices) != 1 or len({choice.casefold() for choice in choices}) != 1:
        fail("recommendation section must explicitly recommend exactly one neighborhood")
    if "commute" not in recommendation.casefold() or "rent" not in recommendation.casefold():
        fail("recommendation must apply the commute-first, rent-second rule")
    caveats = section(text, "## Caveats")
    for term in ("listing", "schedule"):
        if term not in caveats.casefold():
            fail(f"caveats must address {term} uncertainty")
    if not re.search(r"\b(?:trip planner|travel time|duration|transit)\b", caveats, re.IGNORECASE):
        fail("caveats must address transit-trip uncertainty")

    body = text[: text.find("## Sources")]
    body_links = links(body)
    if any(anchor.casefold().startswith(("http://", "https://")) for anchor, _ in body_links):
        fail("inline citations must use descriptive link text rather than raw URLs")
    body_urls = set(urls(body))
    domains = {domain(url) for url in body_urls}
    if len(domains) < 4:
        fail("brief needs at least four distinct source domains")
    if any(".invalid" in url or "localhost" in url or "127.0.0.1" in url for url in body_urls):
        fail("citations must point to real external sources")

    sources = section(text, "## Sources")
    source_lines = [line for line in sources.splitlines() if line.lstrip().startswith("-")]
    if not source_lines:
        fail("source log is empty")
    logged_urls: set[str] = set()
    for line in source_lines:
        line_links = links(line)
        if not line_links or not DATE_RE.search(line) or "access" not in line.casefold():
            fail("each source-log entry needs a linked page and an access date")
        stripped = line.lstrip()
        prefix = stripped[1 : stripped.find("[")].strip(" :—-")
        publisher_text = prefix or line_links[0][0]
        if len(re.findall(r"[A-Za-z]", publisher_text)) < 2:
            fail("each source-log entry must name its publisher")
        if not any(
            len(re.findall(r"[A-Za-z]", anchor)) >= 3
            and not anchor.casefold().startswith(("http://", "https://"))
            for anchor, _ in line_links
        ):
            fail("each source-log link must use the page title as descriptive text")
        logged_urls.update(url for _, url in line_links)
    if not body_urls.issubset(logged_urls):
        fail("source log omits one or more sources cited in the brief")

    print("PASS: corrected two-neighborhood housing brief is complete and substantive")


if __name__ == "__main__":
    main()
