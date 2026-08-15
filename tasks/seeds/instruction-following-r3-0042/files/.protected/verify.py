#!/usr/bin/env python3
"""Deterministic verifier for the corrected heat-policy brief."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / "policy_brief.md"

HEADINGS = [
    "# Riverton Three-Year Heat Mitigation Brief",
    "## Decision",
    "## Evidence comparison",
    "## East Ward scope",
    "## Three-year program design",
    "## Measures",
    "## Limitations",
    "## Sources",
]

EVIDENCE_HASHES = {
    "evidence/01_housing_heat_scan.md": "e4affdb96e1532294943f7b36e41e90f3e96c4bdcb24958fa118d38d60581d50",
    "evidence/02_resident_survey.md": "5cfa350eaf161a36e65aa24da4ae92dc52b09c31c97b5ba9741b0fda9eac5a55",
    "evidence/03_options_memo.md": "215edf979781b347bc68ac531e3cb0c35e9ad9a01796e09dd09f42c13831d9a3",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def section(text: str, heading: str, next_heading: str | None) -> str:
    start = text.index(heading) + len(heading)
    end = text.index(next_heading, start) if next_heading else len(text)
    return text[start:end].strip()


def split_table_row(line: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def table_rows(block: str, header: list[str]) -> list[list[str]]:
    """Return the sole Markdown table in a section without dictating pipe style."""
    lines = block.splitlines()
    tables: list[tuple[int, list[str]]] = []
    for index in range(len(lines) - 1):
        possible_header = split_table_row(lines[index])
        possible_rule = split_table_row(lines[index + 1])
        if len(possible_header) < 2 or len(possible_rule) != len(possible_header):
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in possible_rule):
            tables.append((index, possible_header))

    if len(tables) != 1:
        fail(f"section must contain exactly one Markdown table; found {len(tables)}")
    start, actual_header = tables[0]
    if actual_header != header:
        fail(f"table columns differ from the required schema: {actual_header}")

    rows: list[list[str]] = []
    for line in lines[start + 2 :]:
        if not line.strip() or "|" not in line:
            break
        rows.append(split_table_row(line))
    return rows


def bullet_items(block: str) -> list[str]:
    """Recognize any standard Markdown unordered-list marker."""
    return [
        match.group(1).strip()
        for line in block.splitlines()
        if (match := re.fullmatch(r" {0,3}[-+*]\s+(.+?)\s*", line))
    ]


def normalized_label(item: str) -> tuple[str, str]:
    if ":" not in item:
        return "", ""
    label, value = item.split(":", 1)
    return (
        re.sub(r"[*_`]", "", label).strip(),
        re.sub(r"[*_`]", "", value).strip().removesuffix("."),
    )


def main() -> int:
    if not BRIEF.is_file():
        fail("policy_brief.md is missing")
    if BRIEF.is_symlink():
        fail("policy_brief.md must be a self-contained workspace file")
    try:
        text = BRIEF.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        fail("policy_brief.md must be UTF-8 text")

    for relative_path, expected_hash in EVIDENCE_HASHES.items():
        packet_file = ROOT / relative_path
        if not packet_file.is_file():
            fail(f"packet file is missing: {relative_path}")
        if packet_file.is_symlink():
            fail(f"packet file must remain self-contained: {relative_path}")
        actual_hash = hashlib.sha256(packet_file.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            fail(f"packet file was modified: {relative_path}")

    actual_headings = [line for line in text.splitlines() if line.startswith("#")]
    if actual_headings != HEADINGS:
        fail(f"headings differ from the exact required order: {actual_headings}")
    words = re.findall(r"\b[\w][\w’'-]*\b", text, flags=re.UNICODE)
    if not 650 <= len(words) <= 850:
        fail(f"brief must contain 650–850 words; found {len(words)}")

    folded = text.casefold()
    for phrase in (
        "east ward only",
        "existing multifamily",
        "option a",
        "trees and vegetation",
        "three-year",
    ):
        if phrase not in folded:
            fail(f"missing retained decision: {phrase}")
    if "http://" in folded or "https://" in folded or "www." in folded:
        fail("the packet-only brief must not cite outside web sources")
    allowed_citations = {
        "[Housing Heat Scan]",
        "[Resident Survey]",
        "[Options Memo]",
    }
    citations = set(re.findall(r"\[[^\]\n]+\]", text))
    unexpected_citations = sorted(citations - allowed_citations)
    if unexpected_citations:
        fail(f"brief uses citation labels outside the packet: {unexpected_citations}")

    decision = section(text, "## Decision", "## Evidence comparison")
    decision_bullets = bullet_items(decision)
    expected_labels = [
        "Audience",
        "Lead intervention",
        "Complementary measure",
        "Geography",
        "Horizon",
    ]
    parsed_decisions = [normalized_label(item) for item in decision_bullets]
    if len(parsed_decisions) != 5 or [item[0] for item in parsed_decisions] != expected_labels:
        fail("Decision must contain exactly the five required labeled bullets")
    if (
        "option a" not in parsed_decisions[1][1].casefold()
        or not re.search(r"cool[ -]roof", parsed_decisions[1][1], flags=re.I)
    ):
        fail("Decision must preserve Option A as the cool-roof lead")
    if "trees and vegetation" not in parsed_decisions[2][1].casefold():
        fail("Decision must preserve trees and vegetation as complementary")
    if parsed_decisions[3][1].casefold() != "east ward only":
        fail("Decision must use the corrected geography exactly")
    if "climate committee" not in parsed_decisions[0][1].casefold():
        fail("Decision must retain the committee audience")
    normalized_horizon = re.sub(r"[-–—]", " ", parsed_decisions[4][1].casefold())
    if not re.search(r"\b(?:three|3) years?\b", normalized_horizon):
        fail("Decision must retain the three-year horizon")

    evidence = section(text, "## Evidence comparison", "## East Ward scope")
    evidence_rows = table_rows(
        evidence,
        ["Intervention", "Observed support", "Projection", "Limit"],
    )
    if len(evidence_rows) != 2 or [row[0] for row in evidence_rows] != [
        "Cool roofs",
        "Trees and vegetation",
    ]:
        fail("Evidence comparison must have exactly the two required rows in order")
    if any(len(row) != 4 or any(not cell for cell in row) for row in evidence_rows):
        fail("every Evidence comparison cell must be substantive")
    if not any(
        label in evidence_rows[0][1]
        for label in ("[Housing Heat Scan]", "[Resident Survey]")
    ):
        fail("Cool roofs observed support must cite its packet evidence")
    if "[Resident Survey]" not in evidence_rows[1][1]:
        fail("Trees and vegetation observed support must cite the Resident Survey")
    for row in evidence_rows:
        projection = row[2]
        if "[Options Memo]" not in projection:
            fail(f"{row[0]} projection must cite the Options Memo")
    if not re.search(r"not observed|not (?:a )?guarantee|did not|does not|no three-year", evidence, re.I):
        fail("Evidence comparison must distinguish projections from observed results")

    scope = section(text, "## East Ward scope", "## Three-year program design")
    if "east ward" not in scope.casefold():
        fail("scope section must substantively discuss East Ward")
    for excluded_area in ("southbank", "north hill"):
        if excluded_area in scope.casefold():
            fail(f"scope section must discuss only East Ward, not {excluded_area}")
    if not any(label in scope for label in ("[Housing Heat Scan]", "[Resident Survey]")):
        fail("East Ward scope must cite its packet evidence")

    design = section(text, "## Three-year program design", "## Measures")
    numbered_items = re.findall(r"(?m)^ {0,3}(\d+)[.)]\s+(.+)$", design)
    years = []
    for number, item in numbered_items:
        plain_item = re.sub(r"^[*_`]+|[*_`]+$", "", item.strip())
        match = re.match(r"(Year [1-3])\b", plain_item)
        years.append((number, match.group(1) if match else ""))
    if years != [("1", "Year 1"), ("2", "Year 2"), ("3", "Year 3")]:
        fail("program design must have exactly three numbered, labeled years")
    if "[Options Memo]" not in design:
        fail("program design must cite the packet's planning memo")
    operational_terms = (
        ("monitor", "baseline", "screen"),
        ("roof", "tree", "plant"),
        ("roof", "tree", "plant", "evaluat", "report"),
    )
    for item, alternatives in zip(numbered_items, operational_terms):
        if not any(term in item[1].casefold() for term in alternatives):
            fail(f"{item[0]}-item program design is not operational enough")

    measures = section(text, "## Measures", "## Limitations")
    measure_rows = table_rows(
        measures, ["Measure", "Baseline", "Year 3 target"]
    )
    if len(measure_rows) != 3 or [row[0] for row in measure_rows] != [
        "Roof area treated",
        "Maximum overnight indoor temperature",
        "Tree survival",
    ]:
        fail("Measures table must contain exactly the three required rows in order")
    if any(len(row) != 3 or any(not cell for cell in row) for row in measure_rows):
        fail("every Measures table cell must be substantive")
    value_checks = [
        (measure_rows[0][1], ("0", "square feet")),
        (measure_rows[0][2], ("210,000", "square feet")),
        (measure_rows[1][1], ("84.8°F", "median")),
        (measure_rows[1][2], ("82.8°F", "or lower")),
        (measure_rows[2][1], ("not applicable",)),
        (measure_rows[2][2], ("at least 85%", "120 trees")),
    ]
    for cell, required_parts in value_checks:
        if not all(part.casefold() in cell.casefold() for part in required_parts):
            fail(f"Measures table has an incorrect packet value: {cell}")
    if "proposed target" not in measures.casefold():
        fail("Measures must identify Year 3 values as proposed targets")
    if "[Options Memo]" not in measures:
        fail("Measures must cite the Options Memo")

    limitations = section(text, "## Limitations", "## Sources")
    limitation_bullets = [item.casefold() for item in bullet_items(limitations)]
    if len(limitation_bullets) < 3:
        fail("Limitations must contain at least three bullets")
    concepts = {
        "sampling": r"sampl|voluntar|represent",
        "causality": r"caus|attribut|prove|program effect",
        "projections": r"project|model|planning|target|guarantee",
    }
    for name, pattern in concepts.items():
        if not any(re.search(pattern, bullet) for bullet in limitation_bullets):
            fail(f"Limitations are missing the required {name} issue")

    causal_text = " ".join((evidence, scope, design, measures, limitations))
    if not re.search(r"(?:not|no|did not|does not|cannot|would not).{0,80}(?:prov|caus|attribut|program effect)", causal_text, re.I):
        fail("brief must state that the evidence does not prove program causation")
    confounders = ("weather", "occupancy", "ventilation", "air-conditioning", "building work")
    if sum(term in causal_text.casefold() for term in confounders) < 2:
        fail("causal explanation must identify why temperature change cannot be attributed")

    body = text[: text.index("## Sources")]
    for label in ("[Housing Heat Scan]", "[Resident Survey]", "[Options Memo]"):
        if label not in body:
            fail(f"source label must cite packet claims in the body: {label}")

    sources = section(text, "## Sources", None)
    source_items = bullet_items(sources)
    expected_sources = [
        "evidence/01_housing_heat_scan.md",
        "evidence/02_resident_survey.md",
        "evidence/03_options_memo.md",
    ]
    listed_markdown_paths = re.findall(r"(?:[\w.-]+/)*[\w.-]+\.md", sources)
    if listed_markdown_paths != expected_sources:
        fail("Sources must list exactly the three evidence files in filename order")
    non_list_source_lines = [
        line for line in sources.splitlines() if line.strip() and not re.fullmatch(r" {0,3}[-+*]\s+.+", line)
    ]
    if non_list_source_lines:
        fail("Sources must contain only the three Markdown bullets")
    allowed_files = {
        ".protected/verify.py",
        "evidence/01_housing_heat_scan.md",
        "evidence/02_resident_survey.md",
        "evidence/03_options_memo.md",
        "policy_brief.md",
    }
    actual_files = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.relative_to(ROOT).parts
        and ".sandbox-home" not in path.relative_to(ROOT).parts
    }
    extras = sorted(actual_files - allowed_files)
    if extras:
        fail(f"unexpected additional workspace artifacts: {extras}")

    print("PASS: policy brief satisfies the corrected accumulated contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
