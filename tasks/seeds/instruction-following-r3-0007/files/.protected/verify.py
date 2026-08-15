#!/usr/bin/env python3
"""Deterministic acceptance checks for the accumulated handout request."""

from pathlib import Path
import re
import sys
from urllib.parse import urlparse


TARGET = Path("flu_home_guide.md")
EXPECTED_HEADINGS = [
    "# Flu at Home: Household Guide",
    "## At a glance",
    "## Reduce spread",
    "## When to get urgent help",
    "## Sources",
]


def finish(errors: list[str]) -> None:
    if errors:
        print("verification failed:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    print("verification passed: corrected adult flu handout is substantive and constrained")


errors: list[str] = []
if not TARGET.is_file():
    finish(["flu_home_guide.md is missing"])
if TARGET.is_symlink():
    finish(["flu_home_guide.md must be a regular workspace file, not a symlink"])

text = TARGET.read_text(encoding="utf-8")
if not text.endswith("\n"):
    errors.append("the handout must end with a newline")
if not text.startswith(EXPECTED_HEADINGS[0] + "\n"):
    errors.append("the requested title must be the first line")

# The trace harness creates this private home at the workspace root before the
# agent runs. It is harness state, not an artifact created in response to the
# user's instruction to produce only the handout.
allowed_root_entries = {".git", ".protected", ".sandbox-home", TARGET.name}
unexpected_entries = sorted(path.name for path in Path(".").iterdir() if path.name not in allowed_root_entries)
if unexpected_entries:
    errors.append("unexpected workspace artifact(s): " + ", ".join(unexpected_entries))

headings = re.findall(r"^#{1,6} .+$", text, flags=re.MULTILINE)
if headings != EXPECTED_HEADINGS:
    errors.append("title/headings must match the requested text and order, with no extras")
if re.search(r"^.+\n(?:=+|-+)\s*$", text, flags=re.MULTILINE) or re.search(r"<h[1-6]\b", text, flags=re.IGNORECASE):
    errors.append("Setext and HTML headings are not allowed")

parts = re.split(r"^## (At a glance|Reduce spread|When to get urgent help|Sources)\s*$", text, flags=re.MULTILINE)
if len(parts) != 9:
    finish(errors + ["the four required sections could not be parsed"])

sections = {parts[index]: parts[index + 1].strip() for index in range(1, 8, 2)}
at_glance = sections["At a glance"]
reduce_spread = sections["Reduce spread"]
urgent_help = sections["When to get urgent help"]
sources = sections["Sources"]

paragraphs = [block for block in re.split(r"\n\s*\n", at_glance) if block.strip()]
if len(paragraphs) != 1 or any(line.lstrip().startswith(("- ", "* ", "+ ")) for line in at_glance.splitlines()):
    errors.append("At a glance must be exactly one prose paragraph")

reduce_lines = [line for line in reduce_spread.splitlines() if line.strip()]
urgent_lines = [line for line in urgent_help.splitlines() if line.strip()]
source_lines = [line for line in sources.splitlines() if line.strip()]
if len(reduce_lines) != 5 or any(not line.startswith(("- ", "* ", "+ ")) for line in reduce_lines):
    errors.append("Reduce spread must have exactly five one-line Markdown bullets")
if len(urgent_lines) != 4 or any(not line.startswith(("- ", "* ", "+ ")) for line in urgent_lines):
    errors.append("When to get urgent help must have exactly four one-line Markdown bullets")


def distinct_topic_coverage(lines: list[str], topic_checks) -> bool:
    """Return whether each requested topic can be assigned to its own bullet."""
    candidates = [
        [
            index
            for index, line in enumerate(lines)
            if check(re.sub(r"\[[^\]]+\]\(https://[^)]+\)", "", line).casefold())
        ]
        for check in topic_checks
    ]

    def assign(topic_index: int, used_lines: set[int]) -> bool:
        if topic_index == len(candidates):
            return True
        return any(
            line_index not in used_lines and assign(topic_index + 1, used_lines | {line_index})
            for line_index in candidates[topic_index]
        )

    return assign(0, set())


spread_topic_checks = [
    lambda line: any(term in line for term in ("close", "contact", "distance", "face-to-face")),
    lambda line: "mask" in line,
    lambda line: any(term in line for term in ("air", "ventilat", "window")),
    lambda line: "hand" in line and any(term in line for term in ("cough", "sneeze", "tissue")),
    lambda line: any(term in line for term in ("clean", "disinfect")) and "surface" in line,
]
if len(reduce_lines) == 5 and not distinct_topic_coverage(reduce_lines, spread_topic_checks):
    errors.append("the five Reduce spread topics must each be covered by a distinct bullet")

sentence_end = re.compile(r"[.!?](?:\s*\[[^\]]+\]\(https://[^)]+\))?$")
for label, lines in (("Reduce spread", reduce_lines), ("When to get urgent help", urgent_lines)):
    if any(not sentence_end.search(line.strip()) for line in lines):
        errors.append(f"every {label} bullet must be a complete sentence")
    for line in lines:
        prose = re.sub(r"\[[^\]]+\]\(https://[^)]+\)", "", line[2:]).strip()
        if len(re.findall(r"[.!?](?=\s|$)", prose)) != 1:
            errors.append(f"every {label} bullet must contain exactly one sentence")
            break

urgent_sign_terms = (
    "breath", "chest", "abdomen", "pain", "pressure", "dizz", "confus",
    "arouse", "seizure", "urin", "muscle", "weak", "unstead", "fever",
    "cough", "chronic", "severe", "concerning",
)
for line in urgent_lines:
    prose = re.sub(r"\[[^\]]+\]\(https://[^)]+\)", "", line[2:]).casefold()
    if not any(term in prose for term in urgent_sign_terms):
        errors.append("every When to get urgent help bullet must state an adult warning sign")
        break

url_pattern = re.compile(r"\[([^\]]+)\]\((https://[^)\s]+)\)")
for label, body in (("At a glance", at_glance), ("Reduce spread", reduce_spread), ("When to get urgent help", urgent_help)):
    if not url_pattern.search(body):
        errors.append(f"{label} needs an inline Markdown citation")

source_matches = []
for line in source_lines:
    match = re.fullmatch(r"[-*+] \[([^\]]+)\]\((https://[^)\s]+)\)", line)
    if not match:
        errors.append("every Sources entry must be one descriptive HTTPS Markdown link")
    else:
        if len(re.findall(r"[A-Za-z]+", match.group(1))) < 2:
            errors.append("every Sources link needs a descriptive label")
        source_matches.append(match.groups())
if len(source_lines) != 3:
    errors.append("Sources must contain exactly three bullets")

source_urls = [url for _, url in source_matches]
source_page_ids = {
    (
        urlparse(url).hostname.casefold().removeprefix("www.") if urlparse(url).hostname else None,
        urlparse(url).path.rstrip("/"),
    )
    for url in source_urls
}
if len(source_page_ids) != 3:
    errors.append("the three source URLs must be distinct")

hosts = [urlparse(url).hostname.lower().removeprefix("www.") for url in source_urls if urlparse(url).hostname]
if len(hosts) != len(source_urls) or any(not host.endswith((".gov", ".mil")) for host in hosts):
    errors.append("all source links must point to official U.S. government health domains")
if sum(host == "cdc.gov" or host.endswith(".cdc.gov") for host in hosts) < 2:
    errors.append("at least two source links must be from CDC")

inline_urls = {url for _, url in url_pattern.findall(at_glance + "\n" + reduce_spread + "\n" + urgent_help)}
if source_urls and inline_urls != set(source_urls):
    errors.append("inline citations and Sources must use the same three-link source set")

countable = re.sub(r"https://[^)\s]+", "", text)
words = re.findall(r"\b[0-9A-Za-z]+(?:[’'-][0-9A-Za-z]+)*\b", countable)
if not 300 <= len(words) <= 360:
    errors.append(f"handout must contain 300–360 words by the verifier count; found {len(words)}")

lower = text.casefold()
if not re.search(r"\badult\b", lower):
    errors.append("the corrected adult audience must be explicit")
for pattern, description in [
    (r"\b(child|children|kid|kids|pediatric|paediatric|parent|parents|school|school-age|schoolchild)\b", "child/parent/school language"),
    (
        r"\b(oseltamivir|tamiflu|zanamivir|relenza|peramivir|rapivab|baloxavir|xofluza|acetaminophen|paracetamol|tylenol|ibuprofen|advil|motrin|aspirin|naproxen|aleve|dextromethorphan|guaifenesin|mucinex|pseudoephedrine|sudafed|phenylephrine|oxymetazoline|nyquil|dayquil)\b",
        "medication names",
    ),
    (
        r"\b(dose|doses|dosage|dosages|dosing)\b|\b\d+(?:\.\d+)?\s*(?:mcg|mg|g|ml|oz|tsp|tbsp|micrograms?|milligrams?|grams?|milliliters?|ounces?|teaspoons?|tablespoons?|tablets?|capsules?)\b",
        "dosage details",
    ),
    (
        r"\b(?:take|use|give|administer)\b[^.!?\n]{0,40}\b(?:medicine|medication|drug|pill|tablet|capsule)\b[^.!?\n]{0,40}\b(?:every|once|twice|daily|nightly|times?\s+(?:a|per)\s+day)\b",
        "dosing schedules",
    ),
    (
        r"\b(?:mix|combine|stir|brew|steep)\b[^.!?\n]{0,100}\b(?:honey|lemon|ginger|garlic|salt|tea|water)\b",
        "home-remedy recipes",
    ),
    (
        r"\b(?:you|the reader)\s+(?:certainly\s+|definitely\s+|clearly\s+)?(?:have|has)\s+(?:seasonal\s+)?(?:influenza|flu)\b|\b(?:these|those|your)\s+(?:signs|symptoms)\s+(?:prove|confirm|mean|show)\b[^.!?\n]{0,40}\b(?:influenza|flu)\b",
        "diagnostic claims",
    ),
    (r"\b(i searched|i researched|i browsed|research process)\b", "research-process narration"),
]:
    if re.search(pattern, lower):
        errors.append(f"prohibited {description} found")

no_fever = re.search(
    r"\b(?:no|without)\s+(?:a\s+)?fever\b(?![- ]reduc)|\b(?:fever[- ]free|free\s+of\s+fever)\b|\bfever\s+(?:has\s+)?(?:ended|gone|resolved|stopped)\b",
    at_glance,
    flags=re.IGNORECASE,
)
symptoms_improving = re.search(
    r"symptom\w*\s+(?:are\s+|have\s+been\s+)?(?:getting\s+)?better|improv",
    at_glance,
    flags=re.IGNORECASE,
)
if not (re.search(r"\b(?:24|twenty[- ]four)\b", at_glance, flags=re.IGNORECASE) and no_fever and symptoms_improving):
    errors.append("At a glance must state the symptom-improvement and 24-hour fever condition")
if not re.search(
    r"(?:without|not\s+(?:taking|using)|has\s+not\s+(?:taken|used)).{0,40}(?:fever[- ]reduc\w*|medicine|medication)",
    at_glance,
    flags=re.IGNORECASE,
):
    errors.append("the 24-hour fever condition must exclude use of fever-reducing medicine")
if not re.search(r"\b(normal\s+activit(?:y|ies)|resum\w*|return\w*|go\s+back)\b", at_glance, flags=re.IGNORECASE):
    errors.append("At a glance must connect both conditions to resuming normal activities")
five_day_period = re.search(
    r"(?:next|following)\s+(?:5|five)[- ]day|(?:5|five)\s+days",
    at_glance,
    flags=re.IGNORECASE,
)
added_precautions = re.search(
    r"\b(?:precaution|mask|distance|air|hygiene|contact)\w*\b",
    re.sub(r"\[[^\]]+\]\(https://[^)]+\)", "", at_glance),
    flags=re.IGNORECASE,
)
if not (five_day_period and added_precautions):
    errors.append("At a glance must state the following five-day precaution period")

reduce_prose = re.sub(r"\[[^\]]+\]\(https://[^)]+\)", "", reduce_spread).casefold()
for terms, description in [
    (("contact", "distance", "face-to-face"), "close contact or distance"),
    (("mask",), "masking"),
    (("air", "ventilat", "window"), "cleaner air"),
    (("hand",), "hand hygiene"),
    (("cough", "sneeze", "tissue"), "cough hygiene"),
    (("clean", "disinfect", "surface"), "shared-surface cleaning"),
]:
    if not any(term in reduce_prose for term in terms):
        errors.append(f"Reduce spread is missing {description}")

urgent_prose = re.sub(r"\[[^\]]+\]\(https://[^)]+\)", "", urgent_help).casefold()
for terms, description in [
    (("breath",), "breathing difficulty"),
    (("chest", "abdomen"), "chest or abdominal pain/pressure"),
    (("dizz", "confus", "arouse", "seizure"), "neurologic warning signs"),
    (("urin", "weak", "unstead"), "urination or severe weakness warning signs"),
    (("wors", "return"), "returning or worsening symptoms"),
]:
    if not any(term in urgent_prose for term in terms):
        errors.append(f"When to get urgent help is missing {description}")

finish(errors)
