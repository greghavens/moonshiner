#!/usr/bin/env python3
from __future__ import annotations

import csv
import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "vacuum_comparison.md"
SPECS = ROOT / "vacuum_specs.csv"
NOTES = ROOT / "support_notes.md"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def section(text: str, heading: str, next_heading: str | None) -> str:
    start = text.index(heading) + len(heading)
    end = text.index(next_heading, start) if next_heading else len(text)
    return text[start:end]


def parse_table(block: str, expected_header: list[str], context: str) -> list[list[str]]:
    lines = [line.strip() for line in block.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        fail(f"{context} table is missing")
    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines]
    if rows[0] != expected_header:
        fail(f"{context} has the wrong columns")
    if len(rows[1]) != len(expected_header) or not all(
        re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]
    ):
        fail(f"{context} has an invalid Markdown separator")
    if any(len(row) != len(expected_header) for row in rows[2:]):
        fail(f"{context} contains a malformed row")
    return rows[2:]


def money(value: str, context: str) -> Decimal:
    match = re.fullmatch(r"\$(\d+\.\d{2})", value)
    if not match:
        fail(f"{context} must be dollars with two decimal places")
    return Decimal(match.group(1))


def score(value: str, context: str) -> Decimal:
    if not re.fullmatch(r"\d+\.\d{2}", value):
        fail(f"{context} must have two decimal places")
    return Decimal(value)


if not REPORT.is_file():
    fail("vacuum_comparison.md is missing")

text = REPORT.read_text(encoding="utf-8")

for match in re.finditer(r"\$\d[\d,]*(?:\.\d+)?", text):
    if not re.fullmatch(r"\$(?:\d+|\d{1,3}(?:,\d{3})+)\.\d{2}", match.group(0)):
        fail("all dollar amounts must have two decimal places")

headings = re.findall(r"(?m)^#{1,6} .+$", text)
expected_headings = [
    "# Cordless Vacuum Comparison",
    "## Buyer requirements",
    "## Corrected shortlist",
    "## Weighted scorecard",
    "## Two-year ownership cost",
    "## Recommendation",
    "## Trade-offs",
    "## Sources",
]
if headings != expected_headings:
    fail("headings are missing, extra, or out of order")

words = re.findall(r"\b[\w$%+–.-]+\b", text, flags=re.UNICODE)
if not 500 <= len(words) <= 750:
    fail(f"comparison must contain 500–750 words; found {len(words)}")

with SPECS.open(newline="", encoding="utf-8") as handle:
    spec_rows = list(csv.DictReader(handle))
specs = {row["model"]: row for row in spec_rows}
if len(specs) != len(spec_rows):
    fail("vacuum_specs.csv contains duplicate model names")

eligible = {
    row["model"]: row
    for row in spec_rows
    if Decimal(row["price_usd"]) <= Decimal("400.00")
    and row["sealed_filtration"] == "yes"
    and row["anti_tangle"] == "yes"
    and row["user_replaceable_battery"] == "yes"
    and row["wall_drilling_required"] == "no"
}
if set(eligible) != {"Alder V9", "Cinder Flex S"}:
    fail("protected packet no longer yields the intended corrected shortlist")

sections = {
    "requirements": section(text, "## Buyer requirements", "## Corrected shortlist"),
    "shortlist": section(text, "## Corrected shortlist", "## Weighted scorecard"),
    "scorecard": section(text, "## Weighted scorecard", "## Two-year ownership cost"),
    "cost": section(text, "## Two-year ownership cost", "## Recommendation"),
    "recommendation": section(text, "## Recommendation", "## Trade-offs"),
    "tradeoffs": section(text, "## Trade-offs", "## Sources"),
    "sources": section(text, "## Sources", None),
}

requirement_lines = [
    line.strip() for line in sections["requirements"].splitlines() if line.strip()
]
if len(requirement_lines) != 6 or any(not line.startswith("- ") for line in requirement_lines):
    fail("Buyer requirements must contain exactly six bullets")
requirements_text = " ".join(requirement_lines).casefold()
requirement_patterns = {
    "home and floor mix": r"750.{0,40}mostly hard floors.{0,40}two low-pile rugs",
    "cat": r"one shedding cat",
    "corrected budget": r"\$400\.00.{0,20}before tax|before-tax.{0,20}\$400\.00",
    "no-drilling storage": r"(?:no|without).{0,25}drill",
    "required features": r"(?=.*sealed filtration)(?=.*anti-tangle)(?=.*user-replaceable battery)",
    "decision rule": r"highest.{0,30}weighted score",
}
for label, pattern in requirement_patterns.items():
    if not re.search(pattern, requirements_text, flags=re.IGNORECASE | re.DOTALL):
        fail(f"Buyer requirements omit {label}")

shortlist_rows = parse_table(
    sections["shortlist"],
    ["Model", "Price", "Weight", "Standard runtime", "Bin", "Charging storage", "Warranty"],
    "Corrected shortlist",
)
if len(shortlist_rows) != 2:
    fail("Corrected shortlist must contain exactly two models")
shortlist_by_model = {row[0]: row for row in shortlist_rows}
if set(shortlist_by_model) != set(eligible) or len(shortlist_by_model) != 2:
    fail("Corrected shortlist includes a missing, duplicate, or out-of-scope model")
for model, row in shortlist_by_model.items():
    source = eligible[model]
    if money(row[1], f"{model} price") != Decimal(source["price_usd"]):
        fail(f"{model} price differs from vacuum_specs.csv")
    expected = [
        f'{source["weight_lb"]} lb',
        f'{source["standard_runtime_min"]} min',
        f'{source["bin_ml"]} mL',
        source["charging_storage"],
        f'{source["warranty_years"]} years',
    ]
    if row[2:] != expected:
        fail(f"{model} shortlist facts differ from vacuum_specs.csv")

quant = Decimal("0.01")


def expected_scores(row: dict[str, str]) -> list[Decimal]:
    cleaning = sum(
        Decimal(row[key])
        for key in ("hard_floor_score", "low_pile_score", "pet_hair_score")
    ) / Decimal(3)
    usability = sum(
        Decimal(row[key])
        for key in ("runtime_score", "handling_score", "dock_score")
    ) / Decimal(3)
    filtration = Decimal(row["filtration_score"])
    noise = Decimal(row["noise_score"])
    support = Decimal(row["support_score"])
    overall = (
        cleaning * Decimal("0.40")
        + usability * Decimal("0.25")
        + filtration * Decimal("0.15")
        + noise * Decimal("0.10")
        + support * Decimal("0.10")
    )
    return [
        value.quantize(quant, rounding=ROUND_HALF_UP)
        for value in (cleaning, usability, filtration, noise, support, overall)
    ]


score_rows = parse_table(
    sections["scorecard"],
    [
        "Model",
        "Cleaning (40%)",
        "Usability (25%)",
        "Filtration (15%)",
        "Noise (10%)",
        "Support (10%)",
        "Overall",
    ],
    "Weighted scorecard",
)
if [row[0] for row in score_rows] != ["Alder V9", "Cinder Flex S"]:
    fail("scorecard must contain corrected-scope models in descending Overall order")
for row in score_rows:
    actual = [score(value, f"{row[0]} score") for value in row[1:]]
    if actual != expected_scores(eligible[row[0]]):
        fail(f"{row[0]} weighted scores are incorrect")

cost_rows = parse_table(
    sections["cost"],
    ["Model", "Purchase price", "Two annual filter sets", "Two-year subtotal"],
    "Two-year ownership cost",
)
cost_by_model = {row[0]: row for row in cost_rows}
if set(cost_by_model) != set(eligible) or len(cost_by_model) != 2:
    fail("ownership table must contain exactly the corrected-scope models")
for row in cost_rows:
    source = eligible[row[0]]
    purchase = Decimal(source["price_usd"])
    filters = Decimal(source["annual_filter_cost_usd"]) * 2
    expected = [purchase, filters, purchase + filters]
    actual = [money(value, f"{row[0]} ownership cost") for value in row[1:]]
    if actual != expected:
        fail(f"{row[0]} ownership-cost calculation is incorrect")

recommendation = sections["recommendation"]
recommendation_folded = recommendation.casefold()
for pattern, message in (
    (r"recommend.{0,40}alder v9|alder v9.{0,40}(?:winner|recommend)",
     "Recommendation must name Alder V9 as the winner"),
    (r"8\.87.{0,100}8\.80|8\.80.{0,100}8\.87",
     "Recommendation must explain the score difference"),
    (r"\$10\.00|ten-dollar|ten dollar",
     "Recommendation must explain the two-year ownership-cost difference"),
):
    if not re.search(pattern, recommendation_folded, flags=re.IGNORECASE | re.DOTALL):
        fail(message)
if not re.search(
    r"(?:750.square.foot|apartment|hard.floor|low.pile|rug|cat|pet.hair)",
    recommendation_folded,
):
    fail("Recommendation must connect the decision to the stated apartment use case")
if not re.search(
    r"(?:score|handling|lighter|weight|runtime|bin|filtration|noise|support|dock|cost)",
    recommendation_folded,
):
    fail("Recommendation must support the apartment-use rationale with packet evidence")

tradeoff_lines = [line.strip() for line in sections["tradeoffs"].splitlines() if line.strip()]
if len(tradeoff_lines) != 3 or any(not line.startswith("- ") for line in tradeoff_lines):
    fail("Trade-offs must contain exactly three bullets")
first, second, third = (line.casefold() for line in tradeoff_lines)
winner_advantages = (
    r"hard.?floor",
    r"handling",
    r"dock",
    r"filtration",
    r"support",
    r"lighter|weight",
    r"lower.{0,30}(?:price|purchase|cost)|(?:price|purchase|cost).{0,30}lower",
)
if "alder v9" not in first or sum(bool(re.search(pattern, first)) for pattern in winner_advantages) < 2:
    fail("first Trade-offs bullet must focus on the winner's sourced advantages")
runner_up_advantages = (
    r"low.?pile|rug",
    r"pet.?hair",
    r"cleaning",
    r"runtime",
    r"larger bin|bin capacity",
    r"noise|quieter",
)
if "cinder flex s" not in second or sum(
    bool(re.search(pattern, second)) for pattern in runner_up_advantages
) < 2:
    fail("second Trade-offs bullet must focus on the runner-up's sourced advantages")
for pattern, message in (
    (r"lab scores?.{0,40}(?:not|aren.t).{0,30}guarantee|not.{0,30}guarantee.{0,40}lab scores?",
     "third Trade-offs bullet must state the lab-score limitation"),
    (r"price.{0,30}snapshot.{0,30}not live|snapshot.{0,30}not.{0,20}live",
     "third Trade-offs bullet must state the price-snapshot limitation"),
    (r"tax", "third Trade-offs bullet must retain cost exclusions"),
    (r"shipping", "third Trade-offs bullet must retain cost exclusions"),
    (r"electricity", "third Trade-offs bullet must retain cost exclusions"),
    (r"replacement battery", "third Trade-offs bullet must retain the no-battery cost assumption"),
):
    if not re.search(pattern, third, flags=re.IGNORECASE | re.DOTALL):
        fail(message)

source_lines = [line.strip() for line in sections["sources"].splitlines() if line.strip()]
if source_lines != ["- `vacuum_specs.csv`", "- `support_notes.md`"]:
    fail("Sources must list exactly the two packet files in the requested order")

outside_models = sorted(set(specs) - set(eligible))
for model in outside_models:
    if model.casefold() in text.casefold():
        fail(f"out-of-scope model is mentioned: {model}")

if not NOTES.is_file():
    fail("support_notes.md is missing")

allowed_top_level = {".protected", "vacuum_specs.csv", "support_notes.md", "vacuum_comparison.md", ".git", ".sandbox-home"}
extras = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed_top_level)
if extras:
    fail(f"unexpected extra top-level artifacts: {extras}")
protected_extras = sorted(path.name for path in (ROOT / ".protected").iterdir() if path.name != "verify.py")
if protected_extras:
    fail(f"unexpected artifacts under .protected/: {protected_extras}")

print(f"PASS: corrected cordless-vacuum comparison is complete ({len(words)} words)")
