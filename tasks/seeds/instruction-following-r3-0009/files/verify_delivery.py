from __future__ import annotations

import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "housing_shortlist.md"
RESEARCH = ROOT / "research"


def fail(message: str) -> None:
    raise AssertionError(message)


def load_packet() -> tuple[list[dict[str, str]], dict[str, int]]:
    with (RESEARCH / "listing_snapshots.csv").open(newline="", encoding="utf-8") as handle:
        listings = list(csv.DictReader(handle))
    with (RESEARCH / "transit_times.csv").open(newline="", encoding="utf-8") as handle:
        transit = {
            row["listing_id"]: int(row["minutes"])
            for row in csv.DictReader(handle)
            if row["destination"] == "Clark/Lake"
            and row["departure_window"] == "weekday 08:30"
        }
    return listings, transit


def compared_total(row: dict[str, str]) -> int:
    parking = int(row["parking_monthly"]) if row["parking_required"] == "yes" else 0
    return int(row["base_rent"]) + parking


def corrected_shortlist(
    listings: list[dict[str, str]], transit: dict[str, int]
) -> list[dict[str, str]]:
    deadline = date.fromisoformat("2026-09-15")
    qualifying = []
    for row in listings:
        lease_terms = {int(term) for term in row["lease_terms_months"].split("|")}
        minutes = transit[row["listing_id"]]
        if (
            int(row["bedrooms"]) == 2
            and date.fromisoformat(row["available_on"]) <= deadline
            and 12 in lease_terms
            and row["cats_allowed"] == "yes"
            and row["laundry"] == "in_unit"
            and minutes <= 30
        ):
            qualifying.append(row)
    qualifying.sort(
        key=lambda row: (
            transit[row["listing_id"]],
            compared_total(row),
            row["property_name"],
        )
    )
    return qualifying[:3]


def section(text: str, heading: str, next_heading: str | None) -> str:
    start_token = f"## {heading}"
    start = text.index(start_token) + len(start_token)
    end = text.index(f"## {next_heading}", start) if next_heading else len(text)
    return text[start:end].strip()


def parse_table(block: str) -> list[list[str]]:
    lines = [line.strip() for line in block.splitlines() if line.strip().startswith("|")]
    if len(lines) != 5:
        fail("Comparison must contain one header, one separator, and exactly three rows")
    expected_header = [
        "Rank",
        "Property",
        "Neighborhood",
        "Base rent",
        "Required parking",
        "Compared total",
        "Available",
        "Transit to Clark/Lake",
    ]

    def cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip("|").split("|")]

    if cells(lines[0]) != expected_header:
        fail("Comparison table columns or column order changed")
    separators = cells(lines[1])
    if len(separators) != len(expected_header) or not all(
        re.fullmatch(r":?-{3,}:?", cell) for cell in separators
    ):
        fail("Comparison table separator is malformed")
    return [cells(line) for line in lines[2:]]


def plain_cell(value: str) -> str:
    """Remove harmless inline Markdown used to emphasize a whole table cell."""
    value = value.strip()
    for marker in ("**", "__", "`"):
        value = value.replace(marker, "")
    return value.strip()


def parse_money(value: str, *, zero_words: bool = False) -> int:
    value = plain_cell(value).lower()
    if zero_words and value in {"none", "not required", "n/a", "na", "—", "-"}:
        return 0
    match = re.fullmatch(
        r"\$?\s*([\d,]+)(?:\.00)?(?:\s*(?:/\s*mo(?:nth)?|per month|monthly))?",
        value,
    )
    if not match:
        fail(f"Could not read monthly dollar amount: {value!r}")
    return int(match.group(1).replace(",", ""))


def parse_date(value: str) -> date:
    value = plain_cell(value)
    value = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", value, flags=re.IGNORECASE)
    for fmt in (
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%b. %d, %Y",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    fail(f"Could not read availability date: {value!r}")


def parse_minutes(value: str) -> int:
    value = plain_cell(value).lower()
    match = re.fullmatch(r"(\d+)\s*(?:min|mins|minute|minutes)", value)
    if not match:
        fail(f"Could not read transit duration: {value!r}")
    return int(match.group(1))


def has_labeled_money(text: str, label: str, amount: int) -> bool:
    digits = f"{amount:,}".replace(",", ",?")
    money = rf"(?<![\d,])\$?\s*{digits}(?:\.00)?(?!\d)"
    return bool(
        re.search(rf"(?:{label})[^\n]{{0,32}}{money}", text, flags=re.IGNORECASE)
        or re.search(rf"{money}[^\n]{{0,32}}(?:{label})", text, flags=re.IGNORECASE)
    )


def contains_date(text: str, expected: date) -> bool:
    month = expected.strftime("%B")
    short_month = expected.strftime("%b")
    patterns = (
        re.escape(expected.isoformat()),
        rf"{month}\s+0?{expected.day}(?:st|nd|rd|th)?[,]?\s+{expected.year}",
        rf"{short_month}\.?\s+0?{expected.day}(?:st|nd|rd|th)?[,]?\s+{expected.year}",
        rf"0?{expected.month}/0?{expected.day}/{expected.year}",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def has_in_unit_laundry(text: str) -> bool:
    return bool(
        re.search(r"\bin[- ]unit laundry\b", text, flags=re.IGNORECASE)
        or re.search(
            r"\bin[- ]unit (?:washer(?:\s*/\s*dryer)?|w\s*/\s*d)\b",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"\blaundry\b.{0,20}\b(?:inside|in) (?:the )?(?:home|unit|apartment)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def has_twelve_month_term(text: str) -> bool:
    return bool(
        re.search(
            r"\b12[- ]months?(?: lease| term)?\b|\btwelve[- ]months?(?: lease| term)?\b",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(r"\bone[- ]year (?:lease|term)\b", text, flags=re.IGNORECASE)
    )


def has_cats_allowed(text: str) -> bool:
    return bool(
        re.search(
            r"\bcats?\b.{0,20}\b(?:allowed|accepted|permitted|welcome)\b|"
            r"\b(?:allows|accepts|permits|welcomes)\b.{0,20}\bcats?\b|"
            r"\bcat[- ]friendly\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def has_balcony_distinction(text: str, balcony: bool) -> bool:
    negative = bool(
        re.search(
            r"\b(?:no|without) (?:a |private )?balcony\b|"
            r"\b(?:lacks|lacking) (?:a |private )?balcony\b|"
            r"\bbalcony\b.{0,12}\b(?:absent|none|no|not available|not included|unavailable)\b",
            text,
            flags=re.IGNORECASE,
        )
    )
    if balcony:
        positive = bool(
            re.search(
                r"\b(?:a|has|with|offers|includes|features)\b.{0,16}\bbalcony\b|"
                r"\bbalcony\b.{0,16}\b(?:available|included|positive|advantage|benefit)\b",
                text,
                flags=re.IGNORECASE,
            )
        )
        return positive and not negative
    return negative


def main() -> None:
    if not OUTPUT.is_file():
        fail("housing_shortlist.md was not created")
    text = OUTPUT.read_text(encoding="utf-8")

    if not text.startswith("## Shortlist\n"):
        fail("The deliverable must begin with ## Shortlist and no introduction")
    headings = re.findall(r"^## (.+?)\s*$", text, flags=re.MULTILINE)
    expected_headings = ["Shortlist", "Comparison", "Trade-offs", "Method"]
    if headings != expected_headings:
        fail(f"Level-two section order must be {expected_headings!r}")

    listings, transit = load_packet()
    selected = corrected_shortlist(listings, transit)
    expected_names = [row["property_name"] for row in selected]
    if expected_names != ["Meridian Lofts", "Birchline Court", "Aster Row"]:
        fail("Protected packet no longer produces the intended corrected shortlist")

    shortlist = section(text, "Shortlist", "Comparison")
    shortlist_headings = re.findall(
        r"^### ([1-3])\. (.+?) — (.+?)\s*$", shortlist, flags=re.MULTILINE
    )
    expected_shortlist_headings = [
        (str(rank), row["property_name"], row["neighborhood"])
        for rank, row in enumerate(selected, 1)
    ]
    if shortlist_headings != expected_shortlist_headings:
        fail("Shortlist must have the corrected three properties in ranked heading order")
    all_shortlist_headings = re.findall(r"^### (.+?)\s*$", shortlist, flags=re.MULTILINE)
    if len(all_shortlist_headings) != 3:
        fail("Shortlist must contain exactly the three requested ranked level-three headings")
    if re.findall(r"^### (.+?)\s*$", text, flags=re.MULTILINE) != all_shortlist_headings:
        fail("Ranked property headings must be the only level-three headings")

    for index, row in enumerate(selected):
        start = shortlist.index(f"### {index + 1}. ")
        end = (
            shortlist.index(f"### {index + 2}. ", start)
            if index + 1 < len(selected)
            else len(shortlist)
        )
        entry = shortlist[start:end]
        parking = int(row["parking_monthly"]) if row["parking_required"] == "yes" else 0
        expected_date = date.fromisoformat(row["available_on"])
        parking_stated = (
            has_labeled_money(
                entry,
                r"required[- ]parking(?: amount| charge| cost)?",
                parking,
            )
            if parking
            else bool(
                re.search(
                    r"\brequired parking\b.{0,15}(?:\$?\s*0\b|none|not required)|"
                    r"\b(?:no|without) required parking\b",
                    entry,
                    flags=re.IGNORECASE,
                )
            )
        )
        fact_checks = {
            "exactly two bedrooms": bool(
                re.search(
                    r"\b(?:exactly )?(?:two|2)[- ]bedrooms?\b|\b2[- ]BR\b",
                    entry,
                    flags=re.IGNORECASE,
                )
            ),
            "base rent": has_labeled_money(
                entry,
                r"base rent",
                int(row["base_rent"]),
            ),
            "required parking": parking_stated,
            "compared monthly total": has_labeled_money(
                entry,
                r"compared (?:monthly )?(?:total|cost)|compared total|monthly housing cost",
                compared_total(row),
            ),
            "availability date": contains_date(entry, expected_date),
            "transit time": bool(
                re.search(
                    rf"\b{transit[row['listing_id']]}\s*(?:min|mins|minute|minutes)\b",
                    entry,
                    flags=re.IGNORECASE,
                )
                and "clark/lake" in entry.lower()
            ),
            "12-month term": has_twelve_month_term(entry),
            "cats allowed": has_cats_allowed(entry),
            "in-unit laundry": has_in_unit_laundry(entry),
        }
        missing = [fact for fact, present in fact_checks.items() if not present]
        if missing:
            fail(f"{row['property_name']} is missing decision facts: {missing}")

    comparison = section(text, "Comparison", "Trade-offs")
    actual_rows = parse_table(comparison)
    expected_rows = []
    for rank, row in enumerate(selected, 1):
        parking = int(row["parking_monthly"]) if row["parking_required"] == "yes" else 0
        expected_rows.append(
            [
                rank,
                row["property_name"],
                row["neighborhood"],
                int(row["base_rent"]),
                parking,
                compared_total(row),
                date.fromisoformat(row["available_on"]),
                transit[row["listing_id"]],
            ]
        )
    for actual, expected in zip(actual_rows, expected_rows):
        if len(actual) != 8:
            fail("Every Comparison row must have exactly eight cells")
        normalized = [
            int(plain_cell(actual[0])),
            plain_cell(actual[1]),
            plain_cell(actual[2]),
            parse_money(actual[3]),
            parse_money(actual[4], zero_words=True),
            parse_money(actual[5]),
            parse_date(actual[6]),
            parse_minutes(actual[7]),
        ]
        if normalized != expected:
            fail("Comparison facts do not match the corrected packet-derived ranking")

    tradeoffs = section(text, "Trade-offs", "Method")
    bullet_starts = list(re.finditer(r"^[-*+]\s+", tradeoffs, flags=re.MULTILINE))
    bullets = [
        tradeoffs[
            match.end() : (
                bullet_starts[index + 1].start()
                if index + 1 < len(bullet_starts)
                else len(tradeoffs)
            )
        ].strip()
        for index, match in enumerate(bullet_starts)
    ]
    if len(bullets) != 3 or any(
        name not in body for name, body in zip(expected_names, bullets)
    ):
        fail("Trade-offs must contain exactly one named bullet per result in rank order")
    for row, body in zip(selected, bullets):
        if not has_balcony_distinction(body, row["balcony"] == "yes"):
            fail(f"{row['property_name']} trade-off must state its packet-backed balcony distinction")

    method = section(text, "Method", None)
    for source in (
        "listing_snapshots.csv",
        "transit_times.csv",
        "neighborhood_notes.md",
    ):
        if source not in method:
            fail(f"Method must name {source}")
    transit_ranking = re.search(
        r"\b(?:shortest|fastest|lowest|least|ascending)\b.{0,24}"
        r"\b(?:transit|commute|trip)\b|"
        r"\b(?:transit|commute|trip)\b.{0,24}\bascending\b",
        method,
        flags=re.IGNORECASE,
    )
    cost_ranking = re.search(
        r"\b(?:lower|lowest|least|cheaper|smallest|ascending)\b.{0,32}\b(?:cost|total)\b|"
        r"\b(?:cost|total)\b.{0,24}\bascending\b",
        method,
        flags=re.IGNORECASE,
    )
    name_ranking = re.search(
        r"\bproperty name\b|\balphabetical(?:ly)?\b",
        method,
        flags=re.IGNORECASE,
    )
    optional_parking_excluded = bool(
        re.search(
            r"\b(?:exclude|excluding|excluded|do not add|not add|ignore|ignoring|omit|omitting|omitted)\b"
            r".{0,24}\boptional parking\b|"
            r"\bno optional parking\b|"
            r"\boptional parking\b.{0,24}"
            r"\b(?:excluded|not added|ignored|omitted|not counted)\b",
            method,
            flags=re.IGNORECASE,
        )
    )
    parking_formula = bool(
        re.search(
            r"\bbase rent\b.{0,24}(?:\bplus\b|\+).{0,24}"
            r"(?:\brequired parking\b|\bparking (?:only )?when required\b)",
            method,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"\b(?:required parking only|only required parking|parking (?:only )?when required)\b",
            method,
            flags=re.IGNORECASE,
        )
    )
    method_checks = {
        "packet as-of date": contains_date(method, date(2026, 8, 12)),
        "exactly two bedrooms": bool(
            re.search(
                r"\b(?:exactly )?(?:two|2)[- ]bedrooms?\b|\b2[- ]BR\b",
                method,
                flags=re.IGNORECASE,
            )
        ),
        "availability deadline": bool(
            contains_date(method, date(2026, 9, 15))
            and re.search(
                r"\b(?:on or before|before or on|no later than|by)\b",
                method,
                flags=re.IGNORECASE,
            )
        ),
        "12-month term": has_twelve_month_term(method),
        "cats allowed": has_cats_allowed(method),
        "in-unit laundry": has_in_unit_laundry(method),
        "30-minute maximum": bool(
            re.search(
                r"(?:\b30[- ](?:min|mins|minute|minutes)\b.{0,24}"
                r"\b(?:or less|maximum|max|at most|no more than|ceiling)\b)|"
                r"(?:\b(?:maximum|max|at most|no more than|ceiling|within)\b.{0,24}"
                r"\b30[- ](?:min|mins|minute|minutes)\b)|"
                r"(?:(?:≤|<=)\s*30\s*(?:min|mins|minute|minutes)\b)",
                method,
                flags=re.IGNORECASE,
            )
        ),
        "weekday 8:30 a.m. transit to Clark/Lake": bool(
            "clark/lake" in method.lower()
            and re.search(r"\bweekday\b", method, flags=re.IGNORECASE)
            and re.search(
                r"\b(?:08:30(?!\s*p\.?m\.?)|8:30\s*a\.?m\.?)(?!\w)",
                method,
                flags=re.IGNORECASE,
            )
        ),
        "cost formula": bool(
            "base rent" in method.lower()
            and parking_formula
            and ("optional parking" not in method.lower() or optional_parking_excluded)
        ),
        "ranking rule in the requested order": bool(
            transit_ranking
            and cost_ranking
            and name_ranking
            and transit_ranking.start() < cost_ranking.start() < name_ranking.start()
        ),
    }
    missing_method = [fact for fact, present in method_checks.items() if not present]
    if missing_method:
        fail(f"Method did not retain the final constraints or ranking rule: {missing_method}")

    for row in listings:
        prohibited_values = (
            row["listing_id"],
            row["street_address"],
            row["contact_phone"],
            row["contact_email"],
        )
        for value in prohibited_values:
            if value.lower() in text.lower():
                fail(f"Deliverable exposes prohibited packet detail: {value}")
        fee = re.escape(row["application_fee"])
        if re.search(rf"(?<!\d){fee}(?!\d)", text):
            fail("Deliverable exposes an application-fee amount")
    if re.search(r"https?://|www\.|\[[^\]]+\]\([^)]+\)", text, flags=re.IGNORECASE):
        fail("Deliverable must omit source links")
    if re.search(r"\bCHI-\d{2}-\d+\b", text, flags=re.IGNORECASE):
        fail("Deliverable must omit listing IDs")
    if re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text, flags=re.IGNORECASE):
        fail("Deliverable must omit email addresses")
    if re.search(
        r"(?<!\d)(?:\+?1[-.\s]?)?(?:(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}|\d{10})(?!\d)",
        text,
    ):
        fail("Deliverable must omit phone numbers")
    if re.search(r"^#(?!#)", text, flags=re.MULTILINE):
        fail("Do not add a title or other level-one heading")
    if "conclusion" in headings or "rejected" in " ".join(headings).lower():
        fail("Do not add a conclusion or rejected-options section")

    print("housing shortlist delivery verified")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
