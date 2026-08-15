#!/usr/bin/env python3
from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "replenishment_release.md"
STOCK = ROOT / "replenishment.csv"
ROUTES = ROOT / "route_windows.csv"

SERVICE_DATE = "2026-09-22"
HUB = "North Hub"
ROUTE = "Standard AM"
SCOPE = ["N01", "N03"]


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def read_csv(path: Path) -> list[dict[str, str]]:
    require(path.is_file(), f"missing protected fixture: {path.name}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def section(lines: list[str], start_heading: str, end_heading: str | None) -> list[str]:
    start = lines.index(start_heading) + 1
    end = lines.index(end_heading) if end_heading is not None else len(lines)
    return lines[start:end]


def parse_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    nonblank = [line.strip() for line in lines if line.strip()]
    require(len(nonblank) >= 2, "expected a Markdown table")
    require(all(line.startswith("|") and line.endswith("|") for line in nonblank),
            "table section contains non-table content")

    def cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip("|").split("|")]

    header = cells(nonblank[0])
    separator = cells(nonblank[1])
    require(len(separator) == len(header), "table separator has the wrong width")
    require(all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator),
            "invalid Markdown table separator")
    rows = [cells(line) for line in nonblank[2:]]
    require(all(len(row) == len(header) for row in rows), "table row has the wrong width")
    return header, rows


def release_quantity(on_hand: int, par: int, case_pack: int) -> int:
    shortfall = max(0, par - on_hand)
    return ((shortfall + case_pack - 1) // case_pack) * case_pack


def money(value: Decimal) -> str:
    return f"${value:.2f}"


def verify() -> None:
    require(OUTPUT.is_file(), "replenishment_release.md was not created")
    text = OUTPUT.read_text(encoding="utf-8")
    require(text.endswith("\n"), "output must end with a newline")
    lines = text.splitlines()

    forbidden = ["N02", "Birch Corner", "Flex PM", "2026-09-23", "South Hub", "S01", "Delta Point"]
    for value in forbidden:
        require(value not in text, f"superseded or out-of-scope value appears: {value}")

    stock_rows = read_csv(STOCK)
    route_rows = read_csv(ROUTES)
    names: dict[str, str] = {}
    stock_by_store: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in stock_rows:
        names[row["store_code"]] = row["store_name"]
        stock_by_store[row["store_code"]].append(row)

    selected_routes = [
        row for row in route_rows
        if row["service_date"] == SERVICE_DATE
        and row["hub"] == HUB
        and row["route"] == ROUTE
        and row["store_code"] in SCOPE
    ]
    require([row["store_code"] for row in selected_routes] == SCOPE,
            "protected route fixture does not contain the corrected scope in route order")

    store_headings = [f"### {code} — {names[code]}" for code in SCOPE]
    expected_headings = [
        "# North Hub Replenishment Release",
        "## Release parameters",
        "## Route schedule",
        "## Store releases",
        *store_headings,
        "## Release totals",
        "## Operations notes",
    ]
    actual_headings = [line for line in lines if line.startswith("#")]
    require(actual_headings == expected_headings, "headings are missing, extra, or out of order")
    require(lines[0] == expected_headings[0], "the title must be the first line")

    parameter_lines = [
        line.strip() for line in section(lines, "## Release parameters", "## Route schedule")
        if line.strip()
    ]
    require(parameter_lines == [
        "- Service date: 2026-09-22",
        "- Hub: North Hub",
        "- Route: Standard AM",
        "- Stores: N01, N03",
        "- Quantity rule: Smallest case-pack multiple that brings projected stock to at least par",
        "- Pricing: Extended cost equals release quantity multiplied by unit cost",
    ], "Release parameters must contain exactly the six requested bullets")

    route_header, route_table = parse_table(
        section(lines, "## Route schedule", "## Store releases")
    )
    require(route_header == ["Store", "Delivery window", "Receiving point"],
            "Route schedule columns are incorrect")
    expected_route_table = [
        [f'{row["store_code"]} — {row["store_name"]}', row["delivery_window"], row["receiving_point"]]
        for row in selected_routes
    ]
    require(route_table == expected_route_table,
            "Route schedule does not match the selected source rows in route order")

    totals: list[list[str]] = []
    for index, code in enumerate(SCOPE):
        heading = store_headings[index]
        next_heading = store_headings[index + 1] if index + 1 < len(store_headings) else "## Release totals"
        body = [line for line in section(lines, heading, next_heading) if line.strip()]
        subtotal_lines = [line.strip() for line in body if line.strip().startswith("**")]
        table_lines = [line for line in body if line.strip().startswith("|")]
        require(len(subtotal_lines) == 1, f"{code} must have exactly one bold subtotal")
        require(len(body) == len(table_lines) + 1, f"{code} contains content besides its table and subtotal")

        header, rows = parse_table(table_lines)
        require(header == [
            "SKU", "Item", "On hand", "Par", "Case pack", "Release qty",
            "Projected", "Unit cost", "Extended cost",
        ], f"{code} release table columns are incorrect")

        expected_rows: list[list[str]] = []
        units = 0
        cost = Decimal("0")
        for item in stock_by_store[code]:
            on_hand = int(item["on_hand"])
            par = int(item["par"])
            case_pack = int(item["case_pack"])
            quantity = release_quantity(on_hand, par, case_pack)
            if quantity == 0:
                continue
            unit_cost = Decimal(item["unit_cost"])
            extended = unit_cost * quantity
            expected_rows.append([
                item["sku"], item["item"], str(on_hand), str(par), str(case_pack),
                str(quantity), str(on_hand + quantity), money(unit_cost), money(extended),
            ])
            units += quantity
            cost += extended

        require(rows == expected_rows,
                f"{code} rows do not preserve the source order and required calculations")
        require(subtotal_lines[0] == f"**Store subtotal: {units} units; {money(cost)}**",
                f"{code} subtotal is incorrect")
        totals.append([f"{code} — {names[code]}", str(len(expected_rows)), str(units), money(cost)])

    total_header, total_rows = parse_table(
        section(lines, "## Release totals", "## Operations notes")
    )
    require(total_header == ["Store", "Line items", "Units", "Extended cost"],
            "Release totals columns are incorrect")
    grand_lines = sum(int(row[1]) for row in totals)
    grand_units = sum(int(row[2]) for row in totals)
    grand_cost = sum((Decimal(row[3][1:]) for row in totals), Decimal("0"))
    expected_totals = totals + [["Grand total", str(grand_lines), str(grand_units), money(grand_cost)]]
    require(total_rows == expected_totals, "Release totals do not match the store releases")

    note_lines = [
        line.strip() for line in section(lines, "## Operations notes", None) if line.strip()
    ]
    require(note_lines == [
        "- Stage release lines in source order within each store.",
        "- Zero-release rows are omitted because on-hand stock already meets par.",
    ], "Operations notes must contain exactly the two requested bullets")


if __name__ == "__main__":
    try:
        verify()
    except (VerificationError, ValueError, KeyError, csv.Error) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("PASS: replenishment_release.md matches the corrected scope and retained constraints")
