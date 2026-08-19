from __future__ import annotations

import re
import sys
from datetime import date
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
DELIVERABLE = ROOT / "housing_shortlist.md"
CANDIDATES = ("Kutzky Park", "Slatterly Park", "Meadow Park")
EXPECTED_HEADINGS = [
    "# Rochester Two-Bedroom Housing Shortlist",
    "## Scope and method",
    "## Sourced facts",
    "### Kutzky Park",
    "### Slatterly Park",
    "### Meadow Park",
    "## Weighted comparison",
    "## Recommendation",
    "## Uncertainty and next checks",
    "## Sources",
]
WEIGHTS = (0.35, 0.30, 0.20, 0.15)


def section(text: str, heading: str, next_heading: str | None) -> str:
    start = text.index(heading) + len(heading)
    end = text.index(next_heading, start) if next_heading else len(text)
    return text[start:end]


def markdown_links(text: str) -> list[tuple[str, str]]:
    return re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", text)


def public_web_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    blocked_names = ("localhost", ".localhost", ".invalid", ".test", ".example")
    if host == "localhost" or any(host.endswith(suffix) for suffix in blocked_names[1:]):
        return False
    try:
        address = ip_address(host)
    except ValueError:
        return "." in host
    return address.is_global


def government_flood_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").casefold().rstrip(".")
    return host.endswith((".gov", ".us")) or (host.endswith(".arcgis.com") and "fema" in host)


def main() -> int:
    errors: list[str] = []
    extra_entries = sorted(path.name for path in ROOT.iterdir() if path.name not in {".protected", ".git", "housing_shortlist.md"})
    if extra_entries:
        errors.append(f"no files other than housing_shortlist.md may be created; found: {', '.join(extra_entries)}")
    if not DELIVERABLE.is_file():
        print("FAIL: housing_shortlist.md is missing", file=sys.stderr)
        return 1

    text = DELIVERABLE.read_text(encoding="utf-8")
    headings = re.findall(r"(?m)^#{1,6} .+$", text)
    if headings != EXPECTED_HEADINGS:
        print("FAIL: headings are not exactly the required set and order", file=sys.stderr)
        return 1

    words = re.findall(r"\b[\w’'-]+\b", re.sub(r"https?://\S+", "", text))
    if not 800 <= len(words) <= 1100:
        errors.append(f"body must contain 800-1100 words; found {len(words)}")

    accessed_match = re.search(r"\*\*Research accessed:\*\* (\d{4}-\d{2}-\d{2})", text)
    if not accessed_match:
        errors.append("Scope and method must contain '**Research accessed:** YYYY-MM-DD'")
        accessed = None
    else:
        accessed = accessed_match.group(1)
        try:
            date.fromisoformat(accessed)
        except ValueError:
            errors.append("Research accessed date is not a real calendar date")

    scope_required = (
        "November 2026",
        "two adults",
        "one cat",
        "two-bedroom",
        "$2,400",
        "one car",
        "Gonda Building",
        "20 minutes",
        "quieter",
        "1 is weakest",
        "5 is strongest",
    )
    scope = section(text, "## Scope and method", "## Sourced facts")
    for phrase in scope_required:
        if phrase.casefold() not in scope.casefold():
            errors.append(f"Scope and method is missing retained constraint: {phrase}")

    facts = section(text, "## Sourced facts", "## Weighted comparison")
    if re.search(r"\b(recommend|best match|winner|should choose)\b", facts, re.I):
        errors.append("Sourced facts contains recommendation language")

    listing_count = 0
    for index, candidate in enumerate(CANDIDATES):
        next_heading = f"### {CANDIDATES[index + 1]}" if index + 1 < len(CANDIDATES) else "## Weighted comparison"
        block = section(text, f"### {candidate}", next_heading)
        listing_lines = re.findall(r"(?m)^- \*\*Listing ([12]) \(2BR\):\*\* (.+)$", block)
        if [number for number, _ in listing_lines] != ["1", "2"]:
            errors.append(f"{candidate} must have exactly Listing 1 and Listing 2 bullets")
        listing_count += len(listing_lines)
        for number, detail in listing_lines:
            rent_match = re.search(r"\$(\d[\d,]*)(?:\.\d{2})?(?:\+)?(?:/month| per month)", detail)
            if not rent_match:
                errors.append(f"{candidate} Listing {number} lacks an advertised monthly rent")
            elif int(rent_match.group(1).replace(",", "")) > 2400:
                errors.append(f"{candidate} Listing {number} exceeds the $2,400 advertised-rent ceiling")
            if not re.search(r"\b(cats allowed|cats not allowed|not stated)\b", detail, re.I):
                errors.append(f"{candidate} Listing {number} lacks an allowed cat-status label")
            if accessed and accessed not in detail:
                errors.append(f"{candidate} Listing {number} does not use the research-access date")
            if len(markdown_links(detail)) != 1:
                errors.append(f"{candidate} Listing {number} must contain one live Markdown link")
            else:
                label, url = markdown_links(detail)[0]
                if len(re.findall(r"[A-Za-z0-9]+", label)) < 2:
                    errors.append(f"{candidate} Listing {number} must identify a property or address in its link label")
                if not public_web_url(url):
                    errors.append(f"{candidate} Listing {number} does not use a public web URL")

        for label in (
            "Weekday 8:00 a.m. drive",
            "Grocery",
            "Flood screen",
        ):
            matches = re.findall(rf"(?m)^- \*\*{re.escape(label)}:\*\* (.+)$", block)
            if len(matches) != 1:
                errors.append(f"{candidate} needs exactly one '{label}' fact bullet")
            elif not markdown_links(matches[0]):
                errors.append(f"{candidate} '{label}' bullet needs an inline source link")

        drive_lines = re.findall(r"(?m)^- \*\*Weekday 8:00 a\.m\. drive:\*\* (.+)$", block)
        if drive_lines:
            drive = drive_lines[0]
            minute_values = []
            for match in re.finditer(r"(?:(\d{1,2})\s*[–-]\s*)?(\d{1,2})\s*minutes?\b", drive, re.I):
                minute_values.extend(int(value) for value in match.groups() if value is not None)
            if not minute_values:
                errors.append(f"{candidate} drive fact needs a numeric minute estimate")
            elif max(minute_values) > 20 and not re.search(r"\b(exceeds?|misses?|over|above|fails?)\b", drive, re.I):
                errors.append(f"{candidate} drive estimate over 20 minutes must be identified as missing the target")
            if "representative" not in drive.casefold():
                errors.append(f"{candidate} drive estimate must identify its origin as representative")

        grocery_lines = re.findall(r"(?m)^- \*\*Grocery:\*\* (.+)$", block)
        if grocery_lines and not re.search(r"\b(store|grocery|grocer|co-op|market)\b", grocery_lines[0], re.I):
            errors.append(f"{candidate} grocery fact does not identify a practical grocery option")

        flood_lines = re.findall(r"(?m)^- \*\*Flood screen:\*\* (.+)$", block)
        if flood_lines:
            flood = flood_lines[0]
            flood_urls = [url for _, url in markdown_links(flood)]
            if not any(government_flood_url(url) for url in flood_urls):
                errors.append(f"{candidate} flood screen must cite a government source")
            if not re.search(r"\b(parcel|address|listing|unit)\b", flood, re.I) or not re.search(
                r"\b(not|cannot|only|needs?|requires?)\b", flood, re.I
            ):
                errors.append(f"{candidate} flood screen must disclaim property-level certainty")
        if len(markdown_links(block)) < 5:
            errors.append(f"{candidate} needs citations for both listings, drive, grocery, and flood facts")

    if listing_count != 6:
        errors.append(f"expected six listing bullets; found {listing_count}")

    comparison = section(text, "## Weighted comparison", "## Recommendation")
    header = "| Candidate | Rent (35%) | Weekday drive (30%) | Groceries (20%) | Flood (15%) | Weighted total (/5) |"
    if comparison.count(header) != 1:
        errors.append("Weighted comparison table header is missing or duplicated")
    if len(re.findall(r"(?m)^\|(?:\s*:?-+:?\s*\|){5}\s*:?-+:?\s*\|$", comparison)) != 1:
        errors.append("Weighted comparison must contain exactly one Markdown table")

    row_pattern = re.compile(
        r"(?m)^\|\s*(Kutzky Park|Slatterly Park|Meadow Park)\s*"
        r"\|\s*([1-5])\s*\|\s*([1-5])\s*\|\s*([1-5])\s*"
        r"\|\s*([1-5])\s*\|\s*([0-5](?:\.\d{1,2})?)\s*\|$"
    )
    parsed: dict[str, float] = {}
    rows = row_pattern.findall(comparison)
    if len(rows) != 3 or {row[0] for row in rows} != set(CANDIDATES):
        errors.append("Weighted comparison must contain exactly one numeric row per corrected candidate")
    else:
        for name, *numbers in rows:
            scores = [int(value) for value in numbers[:4]]
            shown = float(numbers[4])
            calculated = sum(score * weight for score, weight in zip(scores, WEIGHTS))
            if abs(shown - calculated) > 0.011:
                errors.append(f"{name} weighted total is {shown:.2f}, expected {calculated:.2f}")
            parsed[name] = shown
    for phrase in ("35%", "30%", "20%", "15%", "quiet", "qualitative"):
        if phrase.casefold() not in comparison.casefold():
            errors.append(f"Weighted comparison is missing scoring explanation for: {phrase}")

    recommendation = section(text, "## Recommendation", "## Uncertainty and next checks")
    winner_matches = re.findall(r"(?m)^\*\*Best match: (Kutzky Park|Slatterly Park|Meadow Park)\*\*$", recommendation)
    if len(winner_matches) != 1:
        errors.append("Recommendation must identify exactly one corrected candidate with the required Best match line")
    elif parsed:
        highest = max(parsed.values())
        if parsed[winner_matches[0]] < highest - 0.011:
            errors.append("Best match does not have a highest weighted total")
    if not re.search(r"\b(trade-?off|however|but|while)\b", recommendation, re.I):
        errors.append("Recommendation must explain a tradeoff")
    if re.search(r"\b(safe|safest|crime-free|guaranteed)\b", recommendation, re.I):
        errors.append("Recommendation makes a prohibited safety or certainty claim")

    uncertainty = section(text, "## Uncertainty and next checks", "## Sources")
    uncertainty_groups = {
        "listing/rent volatility": ("listing", "rent", "change"),
        "route variability": ("route", "traffic", "vari"),
        "parcel flood check": ("parcel", "flood", "address"),
        "cat policy verification": ("cat", "confirm", "lease"),
        "quietness limits": ("quiet", "visit", "noise"),
    }
    for label, terms in uncertainty_groups.items():
        if not all(term.casefold() in uncertainty.casefold() for term in terms):
            errors.append(f"Uncertainty section does not substantively address {label}")

    sources = section(text, "## Sources", None)
    source_lines = re.findall(r"(?m)^- \[[^\]]+\]\((https?://[^)\s]+)\)(?: .*)?$", sources)
    if len(source_lines) < 8:
        errors.append("Sources must contain at least eight live Markdown source bullets")
    if len(set(source_lines)) != len(source_lines):
        errors.append("Sources contains duplicate URLs")
    all_urls = [url for _, url in markdown_links(text)]
    if any(not public_web_url(url) for url in all_urls):
        errors.append("all research links must point to real external sources")
    body_urls = {url for _, url in markdown_links(text[: text.index("## Sources")])}
    if body_urls != set(source_lines):
        missing = sorted(body_urls - set(source_lines))
        unused = sorted(set(source_lines) - body_urls)
        if missing:
            errors.append("Sources omits pages cited in the report body")
        if unused:
            errors.append("Sources includes pages not cited in the report body")

    superseded = (
        "Minneapolis",
        "Saint Paul",
        "Northeast Minneapolis",
        "Macalester-Groveland",
        "Longfellow",
        "University of Minnesota",
        "UMN",
        "East Bank",
        "car-free",
        "St. Paul",
    )
    for phrase in superseded:
        if phrase.casefold() in text.casefold():
            errors.append(f"deliverable mentions superseded detail: {phrase}")
    if re.search(r"\btransit\b", text, re.I):
        errors.append("deliverable retains the superseded transit plan")

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: housing_shortlist.md satisfies the corrected multi-turn brief")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
