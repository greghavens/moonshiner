#!/usr/bin/env python3
"""Deterministic acceptance checks for the final speaker comparison."""

from __future__ import annotations

import csv
import hashlib
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "comparison.md"
CATALOG = ROOT / "product_catalog.csv"
EXPECTED_CATALOG_SHA256 = (
    "10882391ac346398da3fec488653f9b60785de6c25fdde605ac72573b9e9efc8"
)
EXPECTED_HEADINGS = [
    "# Portable Bluetooth Speaker Comparison",
    "## Finalists",
    "## Recommendation",
    "## Runner-up",
]
EXPECTED_HEADERS = [
    "rank",
    "model",
    "regular price",
    "weight (lb)",
    "battery (hr)",
    "warranty",
    "ip rating",
    "charging",
    "why it fits",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def clean_cell(value: str) -> str:
    value = re.sub(r"[`*_]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def split_row(line: str) -> list[str]:
    return [clean_cell(cell) for cell in line.strip().strip("|").split("|")]


def number(value: str, label: str) -> Decimal:
    match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
    if not match:
        fail(f"{label} must contain a number; got {value!r}")
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        fail(f"{label} is not numeric: {value!r}")


def table_from(markdown: str) -> list[list[str]]:
    lines = markdown.splitlines()
    for index, line in enumerate(lines[:-1]):
        if not line.lstrip().startswith("|"):
            continue
        headers = [cell.casefold() for cell in split_row(line)]
        if headers != EXPECTED_HEADERS:
            continue
        separator = split_row(lines[index + 1])
        if len(separator) != len(headers) or not all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in separator
        ):
            fail("the required table needs a valid Markdown separator row")
        rows: list[list[str]] = []
        for candidate in lines[index + 2 :]:
            if not candidate.lstrip().startswith("|"):
                break
            row = split_row(candidate)
            if len(row) != len(headers):
                fail("every comparison row must have all nine requested cells")
            rows.append(row)
        return rows
    fail("could not find the table with the exact requested columns and order")


def section_body(markdown: str, heading: str) -> str:
    pattern = re.compile(
        rf"(?ims)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)"
    )
    match = pattern.search(markdown)
    if not match:
        fail(f"missing the required {heading!r} section")
    return match.group(1).strip()


def closing_section(markdown: str, heading: str) -> str:
    body = section_body(markdown, heading)
    normalized = re.sub(r"\s+", " ", body).strip()
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    if (
        not 2 <= len(sentences) <= 3
        or any(not re.search(r"[.!?]$", sentence) for sentence in sentences)
    ):
        fail(f"the {heading!r} section must contain two or three complete sentences")
    return clean_cell(body)


def reject_promotional_prices(markdown: str, catalog: list[dict[str, str]]) -> None:
    for item in catalog:
        promo = re.escape(item["promo_price_usd"])
        labeled_promo = re.compile(
            rf"(?:promo(?:tional)?|sale|discount(?:ed)?)\s+"
            rf"(?:price\s+)?(?:is\s+)?\$?\s*{promo}\b|"
            rf"\$\s*{promo}\b.{{0,24}}(?:promo(?:tional)?|sale|discount(?:ed)?)",
            re.IGNORECASE,
        )
        if labeled_promo.search(markdown):
            fail("the report must not disclose promotional prices")


def reject_superseded_ranking(markdown: str) -> None:
    price_first = re.compile(
        r"\bprice[- ]first\b|"
        r"\brank(?:ed|ing)?\b.{0,50}\bby\s+(?:the\s+)?(?:lowest|lower)\s+"
        r"(?:regular\s+)?price\b|"
        r"\b(?:lowest|lower)\s+(?:regular\s+)?price\b.{0,30}"
        r"\b(?:primary|primarily|first)\b",
        re.IGNORECASE,
    )
    if price_first.search(markdown):
        fail("the report must not mention the superseded price-first ranking")


def require_advantage_and_tradeoff(body: str, heading: str) -> None:
    product_fact = re.compile(
        r"battery|hour|price|cost|\$|weight|lb|warranty|ip6[78]|"
        r"water|dust|usb-c|charging|stereo",
        re.IGNORECASE,
    )
    tradeoff = re.compile(
        r"trade-?off|drawback|downside|\bbut\b|however|although|\byet\b|"
        r"more expensive|costs?\b.{0,30}\bmore|heavier|shorter|lower|lacks?",
        re.IGNORECASE,
    )
    if not product_fact.search(body):
        fail(f"the {heading!r} section must explain a catalog-grounded advantage")
    if not tradeoff.search(body):
        fail(f"the {heading!r} section must explain a meaningful tradeoff")


def main() -> None:
    if not REPORT.is_file():
        fail("comparison.md was not created")
    markdown = REPORT.read_text(encoding="utf-8")
    if not CATALOG.is_file():
        fail("product_catalog.csv is missing")
    if hashlib.sha256(CATALOG.read_bytes()).hexdigest() != EXPECTED_CATALOG_SHA256:
        fail("product_catalog.csv must remain unchanged")

    headings = [
        line.strip()
        for line in markdown.splitlines()
        if re.fullmatch(r"#{1,6}\s+.+", line.strip())
    ]
    if headings != EXPECTED_HEADINGS:
        fail("the report headings must exactly match the requested text and order")

    with CATALOG.open(newline="", encoding="utf-8") as source:
        catalog = list(csv.DictReader(source))
    reject_promotional_prices(markdown, catalog)
    reject_superseded_ranking(markdown)

    eligible = [
        item
        for item in catalog
        if Decimal(item["weight_lb"]) <= Decimal("2.2")
        and Decimal(item["battery_hours"]) >= Decimal("12")
        and Decimal(item["warranty_years"]) >= Decimal("1")
        and item["ip_rating"].casefold() in {"ip67", "ip68"}
        and item["charging"].casefold() == "usb-c"
    ]
    eligible.sort(
        key=lambda item: (
            -Decimal(item["battery_hours"]),
            Decimal(item["regular_price_usd"]),
        )
    )
    if len(eligible) != 3:
        fail("protected catalog no longer yields exactly three eligible finalists")

    finalists = section_body(markdown, "Finalists")
    rows = table_from(finalists)
    if len(rows) != 3:
        fail(f"the comparison table must contain exactly three finalists, found {len(rows)}")

    for position, (row, item) in enumerate(zip(rows, eligible), start=1):
        if number(row[0], "rank") != position:
            fail(f"row {position} has the wrong rank")
        if row[1].casefold() != item["model"].casefold():
            fail(
                f"rank {position} must be {item['model']!r} under the corrected battery-first rule"
            )
        shown_price = number(row[2], "regular price")
        if shown_price != Decimal(item["regular_price_usd"]):
            fail(f"{item['model']} must show its regular price, not its promotional price")
        if number(row[3], "weight") != Decimal(item["weight_lb"]):
            fail(f"incorrect weight for {item['model']}")
        if number(row[4], "battery") != Decimal(item["battery_hours"]):
            fail(f"incorrect battery life for {item['model']}")
        if number(row[5], "warranty") != Decimal(item["warranty_years"]):
            fail(f"incorrect warranty for {item['model']}")
        if row[6].casefold() != item["ip_rating"].casefold():
            fail(f"incorrect IP rating for {item['model']}")
        if row[7].casefold() != item["charging"].casefold():
            fail(f"incorrect charging field for {item['model']}")
        fit_fact = re.compile(
            r"battery|hour|price|cost|\$|weight|lb|warranty|ip6[78]|"
            r"water|dust|usb-c|charging|eligible|eligibility|qualif|filter|"
            r"criteria|requirement|constraint|outdoor|protection|endurance|"
            r"lightest|expensive",
            re.IGNORECASE,
        )
        if len(row[8]) < 30 or not fit_fact.search(row[8]):
            fail(
                f"the Why it fits cell for {item['model']} must explain its fit "
                "using a relevant catalog fact"
            )

    recommendation = closing_section(markdown, "Recommendation")
    runner_up = closing_section(markdown, "Runner-up")
    if eligible[0]["model"].casefold() not in recommendation.casefold():
        fail("Recommendation must name the corrected battery-first winner")
    require_advantage_and_tradeoff(recommendation, "Recommendation")
    if eligible[1]["model"].casefold() not in runner_up.casefold():
        fail("Runner-up must name the second-ranked eligible model")
    require_advantage_and_tradeoff(runner_up, "Runner-up")

    print("PASS: comparison.md satisfies the clarified and corrected requirements")


if __name__ == "__main__":
    main()
