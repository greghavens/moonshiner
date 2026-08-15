#!/usr/bin/env python3
"""Deterministically verify the corrected housing-research deliverable."""

from __future__ import annotations

import csv
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "housing_recommendation.md"
OPTIONS = ROOT / "housing_options.csv"
COMMUTES = ROOT / "commute_times.csv"

FINAL_MOVE_IN = "2026-09-15"
ALLOWED_NEIGHBORHOODS = {"Old Harbor", "River Market"}
EXPECTED_HEADINGS = [
    "# Alder Bay Rental Shortlist",
    "## Search criteria",
    "## Ranked shortlist",
    "## Recommendation",
    "## Trade-offs and checks",
]
EXPECTED_CRITERIA = [
    "- Move-in: 2026-09-15",
    "- Allowed neighborhoods: Old Harbor or River Market",
    "- Bedrooms: Exactly 2",
    "- Floor: Above ground floor",
    "- Cats: Allowed",
    "- Laundry: In-unit",
    "- Lease term: 12 months",
    "- Parking: Optional",
    "- Transit: 35 minutes or less to Bayview Medical Center for a weekday 08:30 arrival",
    "- Ranking: Lowest monthly total, then shortest commute",
]
EXPECTED_COLUMNS = [
    "rank",
    "listing id",
    "property / unit",
    "neighborhood",
    "available",
    "rent",
    "mandatory recurring fees",
    "monthly total",
    "transit commute",
    "lease term",
    "why it qualifies",
]


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def clean(value: str) -> str:
    value = re.sub(r"[`*_]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def split_row(line: str) -> list[str]:
    return [clean(cell) for cell in line.strip().strip("|").split("|")]


def first_number(value: str, context: str) -> Decimal:
    match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", value)
    if not match:
        fail(f"{context} must contain a number")
    try:
        return Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        fail(f"{context} contains an invalid number")


def exact_money(value: str, context: str) -> Decimal:
    match = re.match(
        r"^\$\s*((?:\d+|\d{1,3}(?:,\d{3})+)\.\d{2})(?:\s|$)",
        clean(value),
    )
    if not match:
        fail(f"{context} must start with a dollar amount using two decimal places")
    return Decimal(match.group(1).replace(",", ""))


def has_minutes(value: str, expected: int) -> bool:
    return bool(
        re.search(
            rf"(?<!\d){expected}\s*(?:minutes?|mins?)(?!\w)",
            value,
            re.I,
        )
    )


def fee_components(value: str) -> list[tuple[str, Decimal]]:
    components: list[tuple[str, Decimal]] = []
    for part in value.split(";"):
        match = re.fullmatch(r"\s*(.+?)\s+\$(\d+(?:\.\d+)?)\s*", part)
        if not match:
            fail("the protected packet contains an invalid fee breakdown")
        components.append((match.group(1), Decimal(match.group(2))))
    return components


def section(lines: list[str], heading: str, next_heading: str | None) -> list[str]:
    start = lines.index(heading) + 1
    end = lines.index(next_heading) if next_heading else len(lines)
    return [line for line in lines[start:end] if line.strip()]


def required_table(block: list[str]) -> list[list[str]]:
    for index, line in enumerate(block[:-1]):
        if "|" not in line:
            continue
        headers = [cell.casefold() for cell in split_row(line)]
        if headers != EXPECTED_COLUMNS:
            continue
        separator = split_row(block[index + 1])
        if len(separator) != len(headers) or not all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in separator
        ):
            fail("the shortlist table needs a valid Markdown separator row")
        rows: list[list[str]] = []
        for candidate in block[index + 2 :]:
            if "|" not in candidate:
                break
            row = split_row(candidate)
            if len(row) != len(headers):
                fail("every shortlist row must have all eleven requested cells")
            rows.append(row)
        return rows
    fail("the exact requested shortlist table was not found")


if not REPORT.is_file():
    fail("housing_recommendation.md is missing")
text = REPORT.read_text(encoding="utf-8")
if "2026-10-01" in text:
    fail("the report mentions the superseded move-in date")

lines = text.splitlines()
headings = [line for line in lines if line.startswith("#")]
if headings != EXPECTED_HEADINGS:
    fail("headings are missing, extra, or out of order")

money_mentions = re.findall(r"\$\s*\d[\d,]*(?:\.\d+)?", text)
if not money_mentions or any(
    not re.fullmatch(r"\$\s*\d[\d,]*\.\d{2}", mention)
    for mention in money_mentions
):
    fail("every dollar amount must use exactly two decimal places")

table_starts = []
for index, line in enumerate(lines[:-1]):
    if "|" not in line or "|" not in lines[index + 1]:
        continue
    header = split_row(line)
    separator = split_row(lines[index + 1])
    if len(header) >= 2 and len(header) == len(separator) and all(
        re.fullmatch(r":?-{3,}:?", cell) for cell in separator
    ):
        table_starts.append(index)
if len(table_starts) != 1:
    fail("the report must contain exactly one Markdown table")

criteria = section(lines, "## Search criteria", "## Ranked shortlist")
if criteria != EXPECTED_CRITERIA:
    fail("Search criteria must contain exactly the ten retained final criteria in order")

with OPTIONS.open(newline="", encoding="utf-8") as handle:
    listings = list(csv.DictReader(handle))
with COMMUTES.open(newline="", encoding="utf-8") as handle:
    commute_rows = list(csv.DictReader(handle))
commutes = {row["listing_id"]: row for row in commute_rows}
if len(commutes) != len(commute_rows):
    fail("commute_times.csv contains duplicate listing IDs")


def qualifies(item: dict[str, str]) -> bool:
    commute = commutes.get(item["listing_id"])
    return bool(
        commute
        and item["neighborhood"] in ALLOWED_NEIGHBORHOODS
        and int(item["bedrooms"]) == 2
        and int(item["floor"]) > 1
        and item["available_date"] <= FINAL_MOVE_IN
        and int(item["lease_months"]) == 12
        and item["cat_allowed"].casefold() == "yes"
        and item["in_unit_laundry"].casefold() == "yes"
        and commute["destination"] == "Bayview Medical Center"
        and commute["weekday_arrival"] == "08:30"
        and int(commute["transit_minutes"]) <= 35
    )


eligible = [item for item in listings if qualifies(item)]
eligible.sort(
    key=lambda item: (
        Decimal(item["monthly_rent_usd"])
        + Decimal(item["mandatory_monthly_fees_usd"]),
        int(commutes[item["listing_id"]]["transit_minutes"]),
        item["listing_id"],
    )
)
if len(eligible) != 3:
    fail("the protected packet no longer yields exactly three corrected finalists")

ranked_block = section(lines, "## Ranked shortlist", "## Recommendation")
rows = required_table(ranked_block)
if len(rows) != 3:
    fail(f"the shortlist table must have exactly three rows, found {len(rows)}")

for rank, (row, item) in enumerate(zip(rows, eligible), start=1):
    listing_id = item["listing_id"]
    commute = commutes[listing_id]
    rent = Decimal(item["monthly_rent_usd"])
    fees = Decimal(item["mandatory_monthly_fees_usd"])
    total = rent + fees
    if first_number(row[0], "rank") != rank:
        fail(f"row {rank} has the wrong rank")
    if row[1] != listing_id:
        fail(f"rank {rank} must be {listing_id} under the retained cost-first rule")
    expected_name = f"{item['property']} / {item['unit']}"
    if row[2].casefold() != expected_name.casefold():
        fail(f"incorrect property or unit for {listing_id}")
    if row[3] != item["neighborhood"]:
        fail(f"incorrect neighborhood for {listing_id}")
    if row[4] != item["available_date"]:
        fail(f"incorrect availability date for {listing_id}")
    if exact_money(row[5], f"{listing_id} rent") != rent:
        fail(f"incorrect rent for {listing_id}")
    if exact_money(row[6], f"{listing_id} mandatory fees") != fees:
        fail(f"incorrect mandatory fee total for {listing_id}")
    fee_cell = row[6].casefold()
    for label, amount in fee_components(item["fee_breakdown"]):
        label_pattern = re.escape(label.casefold())
        amount_pattern = re.escape(f"${amount:.2f}")
        if not (
            re.search(rf"{label_pattern}\s*:?[ ]*{amount_pattern}", fee_cell)
            or re.search(rf"{amount_pattern}\s*:?[ ]*{label_pattern}", fee_cell)
        ):
            fail(f"{listing_id} is missing the supplied {label} fee")
    if exact_money(row[7], f"{listing_id} monthly total") != total:
        fail(f"incorrect monthly total for {listing_id}")
    if not has_minutes(row[8], int(commute["transit_minutes"])):
        fail(f"incorrect commute time for {listing_id}")
    if commute["route"].casefold() not in row[8].casefold():
        fail(f"{listing_id} is missing the supplied transit route")
    if first_number(row[9], f"{listing_id} lease term") != Decimal("12"):
        fail(f"incorrect lease term for {listing_id}")
    rationale = row[10].casefold()
    rationale_patterns = (
        r"(?:two|2)[ -]bedroom",
        r"cat|feline",
        r"in[ -]unit",
        r"above (?:the )?ground|floor [2-9]",
        r"availab",
        r"12[ -]month|lease",
        r"transit|commute",
    )
    if not all(re.search(pattern, rationale) for pattern in rationale_patterns):
        fail(f"{listing_id} needs a substantive hard-filter explanation")
    if item["neighborhood"].casefold() not in rationale:
        fail(f"{listing_id} must explain that it is in an allowed neighborhood")

eligible_ids = {item["listing_id"] for item in eligible}
rejected_ids = {item["listing_id"] for item in listings} - eligible_ids
rejected_names = {
    item["property"]
    for item in listings
    if item["listing_id"] in rejected_ids
}
if any(rejected_id.casefold() in text.casefold() for rejected_id in rejected_ids) or any(
    name.casefold() in text.casefold() for name in rejected_names
):
    fail("the report mentions a rejected listing")

recommendation = " ".join(
    section(lines, "## Recommendation", "## Trade-offs and checks")
)
sentences = re.findall(r"[^.!?\n][^.!?]*[.!?](?=\s|$)", recommendation)
if not 2 <= len(sentences) <= 3:
    fail("Recommendation must contain two or three complete sentences")
winner = eligible[0]
if winner["property"].casefold() not in recommendation.casefold():
    fail("Recommendation must name the first-ranked property")
if not re.search(r"trade-?off|\bbut\b|however|although|longest|higher", recommendation, re.I):
    fail("Recommendation must explain a meaningful trade-off")
if not re.search(
    r"\b(?:because|advantage|benefit|lowest|lower|cheaper|less|saves?|earlier|parking|garage)\b",
    recommendation,
    re.I,
):
    fail("Recommendation must explain a meaningful advantage")
if not re.search(
    r"\$|cost|monthly|commute|minute|parking|garage|available|2026-09-10|river market",
    recommendation,
    re.I,
):
    fail("Recommendation must support its advantage and trade-off with packet facts")

tradeoffs = section(lines, "## Trade-offs and checks", None)
if len(tradeoffs) != 3 or any(not line.startswith("- ") for line in tradeoffs):
    fail("Trade-offs and checks must contain exactly three bullets")
for bullet, item in zip(tradeoffs, eligible):
    if item["listing_id"] not in bullet:
        fail("trade-off bullets must name finalist IDs in rank order")
    if not re.search(r"\$|cost|fee|commute|minute|cheaper|higher|lower|shorter|longer", bullet, re.I):
        fail(f"the {item['listing_id']} bullet needs a cost or commute comparison")
    if not re.search(
        r"\b(?:more|less|cheaper|costlier|higher|lower|shorter|longer|lowest|highest|shortest|longest|saves?|relative|than|versus|compared|difference)\b|\bvs\.?",
        bullet,
        re.I,
    ):
        fail(f"the {item['listing_id']} bullet must make an actual comparison")

print("PASS: housing recommendation satisfies the corrected multi-turn brief")
