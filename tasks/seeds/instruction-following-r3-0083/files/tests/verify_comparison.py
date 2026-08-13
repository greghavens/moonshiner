#!/usr/bin/env python3
"""Deterministic acceptance checks for the final comparison report."""

from __future__ import annotations

import csv
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "comparison.md"
CATALOG = ROOT / "product_catalog.csv"
EXPECTED_HEADERS = [
    "rank",
    "model",
    "regular price",
    "width (in)",
    "capacity (qt)",
    "warranty",
    "controls",
    "noise (dba)",
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


def table_from(markdown: str) -> tuple[list[str], list[list[str]]]:
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
        return headers, rows
    fail("could not find the table with the exact requested columns and order")


def section(markdown: str, heading: str) -> str:
    pattern = re.compile(
        rf"(?ims)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)"
    )
    match = pattern.search(markdown)
    if not match:
        fail(f"missing the required {heading!r} section")
    body = match.group(1).strip()
    sentences = re.findall(r"[^.!?\n][^.!?]*[.!?](?=\s|$)", body)
    if not 2 <= len(sentences) <= 3:
        fail(f"the {heading!r} section must contain two or three complete sentences")
    return clean_cell(body)


def require_advantage_and_tradeoff(body: str, heading: str) -> None:
    """Require product-based reasoning and an expressed contrast, without dictating it."""
    product_fact = re.compile(
        r"quiet|noise|dba|price|cost|\$|width|compact|capacity|quart|"
        r"warranty|dishwasher|basket|control|dial|button",
        re.IGNORECASE,
    )
    tradeoff = re.compile(
        r"trade-?off|drawback|downside|\bbut\b|however|although|\byet\b|"
        r"more expensive|costs?\b.{0,30}\bmore|noisier|louder|smaller|"
        r"shorter|wider|higher",
        re.IGNORECASE,
    )
    if not product_fact.search(body):
        fail(f"the {heading!r} section must explain a product-based advantage")
    if not tradeoff.search(body):
        fail(f"the {heading!r} section must explain a meaningful tradeoff")


def main() -> None:
    if not REPORT.is_file():
        fail("comparison.md was not created")
    markdown = REPORT.read_text(encoding="utf-8")
    if len(markdown.strip()) < 600:
        fail("comparison.md is not a substantive completed comparison")

    with CATALOG.open(newline="", encoding="utf-8") as source:
        catalog = list(csv.DictReader(source))

    eligible = [
        item
        for item in catalog
        if Decimal(item["width_in"]) <= Decimal("14.5")
        and Decimal(item["capacity_qt"]) >= Decimal("5")
        and Decimal(item["warranty_years"]) >= Decimal("1")
        and item["dishwasher_safe_basket"].casefold() == "yes"
        and item["controls"].casefold() != "touch-only"
    ]
    eligible.sort(
        key=lambda item: (
            Decimal(item["noise_dba"]),
            Decimal(item["regular_price_usd"]),
        )
    )
    if len(eligible) != 3:
        fail("protected catalog no longer yields exactly three eligible finalists")

    _, rows = table_from(markdown)
    if len(rows) != 3:
        fail(f"the comparison table must contain exactly three finalists, found {len(rows)}")

    for position, (row, item) in enumerate(zip(rows, eligible), start=1):
        if number(row[0], "rank") != position:
            fail(f"row {position} has the wrong rank")
        if row[1].casefold() != item["model"].casefold():
            fail(
                f"rank {position} must be {item['model']!r} under the retained noise-first rule"
            )
        if number(row[2], "regular price") != Decimal(item["regular_price_usd"]):
            fail(f"{item['model']} must show its regular price, not its promotional price")
        if number(row[3], "width") != Decimal(item["width_in"]):
            fail(f"incorrect width for {item['model']}")
        if number(row[4], "capacity") != Decimal(item["capacity_qt"]):
            fail(f"incorrect capacity for {item['model']}")
        if number(row[5], "warranty") != Decimal(item["warranty_years"]):
            fail(f"incorrect warranty for {item['model']}")
        if clean_cell(row[6]).casefold() != item["controls"].casefold():
            fail(f"incorrect controls for {item['model']}")
        if number(row[7], "noise") != Decimal(item["noise_dba"]):
            fail(f"incorrect noise value for {item['model']}")
        if len(row[8]) < 10:
            fail(f"the Why it fits cell for {item['model']} must explain its fit")

    recommendation = section(markdown, "Recommendation")
    runner_up = section(markdown, "Runner-up")
    if eligible[0]["model"].casefold() not in recommendation.casefold():
        fail("Recommendation must name the noise-first winner")
    require_advantage_and_tradeoff(recommendation, "Recommendation")
    if eligible[1]["model"].casefold() not in runner_up.casefold():
        fail("Runner-up must name the second-ranked eligible model")
    require_advantage_and_tradeoff(runner_up, "Runner-up")

    print("PASS: comparison.md satisfies all retained and corrected requirements")


if __name__ == "__main__":
    main()
