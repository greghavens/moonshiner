#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "knowledge_map.md"

EXPECTED_HEADINGS = [
    "# Six-Week Personal Knowledge Map",
    "## Focus",
    "## Linked Evidence",
    "## Evergreen Notes",
    "## Experiments",
    "## Review Rhythm",
    "## Source Index",
]

SOURCE_FILES = {
    "inbox/SLP-01_sleep-window.md": "809b2396f5190cd16c1c82aa5b727fcb3cb78d1a4878fef6eb9d31b55381cfc8",
    "inbox/LGT-02_morning-light.md": "c3feaf87204d635fc31dd65f10654161504a2083c07af4a9666699d084518276",
    "inbox/NTF-03_notification-audit.md": "f588236d99221a971412e794c7e835481b0c07945d48c98f175283a2d49ba956",
    "inbox/FCS-04_focus-blocks.md": "027479a9be19622482618bf75ac9265866379d0ef43e7f3cebe12120aa948317",
    "inbox/MOV-05_midday-walks.md": "b816199b5518bcc41eeae4c7576c8a5227acddfe9260915c54e7dcbf2211db7f",
    "inbox/RVW-06_weekly-reviews.md": "f9ae91ca2a3ea0a5f861d625f4e204123c90b0161ec3fd33bed196afe2f344d5",
}

SOURCE_INDEX = [
    ("SLP-01", "Consistent sleep window"),
    ("LGT-02", "Morning outdoor light trial"),
    ("NTF-03", "Notification interruption audit"),
    ("FCS-04", "Phone placement and focus blocks"),
    ("MOV-05", "Midday walking and afternoon energy"),
    ("RVW-06", "Sunday review pattern"),
]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def section_bodies(text: str) -> dict[str, str]:
    lines = text.splitlines()
    positions = [lines.index(heading) for heading in EXPECTED_HEADINGS]
    result: dict[str, str] = {}
    for index, heading in enumerate(EXPECTED_HEADINGS):
        end = positions[index + 1] if index + 1 < len(positions) else len(lines)
        result[heading] = "\n".join(lines[positions[index] + 1 : end]).strip()
    return result


def nonempty_lines(body: str) -> list[str]:
    return [line.strip() for line in body.splitlines() if line.strip()]


def parse_table(body: str, name: str, expected_header: list[str], expected_rows: int) -> list[list[str]]:
    lines = nonempty_lines(body)
    if len(lines) != expected_rows + 2 or any(not line.startswith("|") or not line.endswith("|") for line in lines):
        fail(f"{name} must contain only one Markdown table with {expected_rows} data rows")

    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines]
    if rows[0] != expected_header:
        fail(f"{name} table columns or order are incorrect")
    if len(rows[1]) != len(expected_header) or any(not re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]):
        fail(f"{name} table separator is invalid")
    if any(len(row) != len(expected_header) or any(not cell for cell in row) for row in rows[2:]):
        fail(f"{name} contains an empty or malformed data cell")
    return rows[2:]


for relative_path, expected_hash in SOURCE_FILES.items():
    source = ROOT / relative_path
    if not source.is_file():
        fail(f"missing protected source {relative_path}")
    actual_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        fail(f"protected source was modified: {relative_path}")

if not OUTPUT.is_file():
    fail("knowledge_map.md was not created")
if OUTPUT.is_symlink():
    fail("knowledge_map.md must be a self-contained regular file")

allowed_root_entries = {".git", ".protected", ".sandbox-home", "inbox", "knowledge_map.md"}
unexpected_root_entries = {path.name for path in ROOT.iterdir()} - allowed_root_entries
if unexpected_root_entries:
    fail(f"additional files or directories were created: {sorted(unexpected_root_entries)}")
actual_inbox_entries = {path.name for path in (ROOT / "inbox").iterdir()}
expected_inbox_entries = {Path(relative_path).name for relative_path in SOURCE_FILES}
if actual_inbox_entries != expected_inbox_entries:
    fail("the inbox contains an added, removed, or renamed entry")

text = OUTPUT.read_text(encoding="utf-8")
if not text.endswith("\n"):
    fail("knowledge_map.md must end with a newline")
if re.search(r"https?://|\[[^\]]+\]\([^\)]+\)", text, re.IGNORECASE):
    fail("external links and Markdown links are not allowed")
if "Reducing digital distraction" in text:
    fail("the superseded primary-theme wording is still present")

word_count = len(re.findall(r"\b[\w’'-]+\b", text, re.UNICODE))
if not 700 <= word_count <= 1000:
    fail(f"document must contain 700–1,000 words; found {word_count}")

actual_headings = re.findall(r"(?m)^#{1,6} .+$", text)
if actual_headings != EXPECTED_HEADINGS:
    fail("headings must exactly match the requested headings and order")
if text.splitlines()[0] != EXPECTED_HEADINGS[0]:
    fail("text appears before the title")

sections = section_bodies(text)

focus_lines = nonempty_lines(sections["## Focus"])
if len(focus_lines) != 4 or any(not line.startswith("- ") for line in focus_lines):
    fail("Focus must contain exactly the four requested retained decisions")
focus_checks = [
    (r"primary theme.{0,20}sleep consistency", "corrected primary theme"),
    (r"(?:time )?horizon.{0,20}six weeks", "six-week horizon"),
    (r"`#sleep`\s*,?\s*`#attention`\s*,?\s*`#energy`", "ordered tags"),
    (r"all six.{0,40}(?:inbox )?notes.{0,80}digital distraction.{0,40}energy.{0,40}support", "retained scope"),
]
for line, (pattern, description) in zip(focus_lines, focus_checks):
    if not re.search(pattern, line, re.IGNORECASE):
        fail(f"Focus omits or misorders the {description}")
expected_tags = ["#sleep", "#attention", "#energy"]
if re.findall(r"`(#[A-Za-z][\w-]*)`", focus_lines[2]) != expected_tags:
    fail("the retained tag set or order is incorrect")
if not set(re.findall(r"`(#[A-Za-z][\w-]*)`", text)).issubset(expected_tags):
    fail("the document adds a tag outside the retained tag set")

linked_rows = parse_table(
    sections["## Linked Evidence"],
    "Linked Evidence",
    ["Connection", "Supporting notes", "What the notes show", "Caveat"],
    4,
)
linked_text = " ".join(" ".join(row) for row in linked_rows)
for note_id, _ in SOURCE_INDEX:
    if note_id not in linked_text:
        fail(f"Linked Evidence does not cite {note_id}")
for index, row in enumerate(linked_rows, start=1):
    if not re.search(r"\bObserved:", row[2], re.IGNORECASE) or not re.search(
        r"\bHypothesis:", row[2], re.IGNORECASE
    ):
        fail(f"Linked Evidence row {index} does not distinguish observation from hypothesis")
    if not re.search(
        r"small|non-random|subjective|selection bias|uncontrolled|cannot|uneven|varied|caus|difficulty",
        row[3],
        re.IGNORECASE,
    ):
        fail(f"Linked Evidence row {index} lacks a substantive evidence caveat")

quantitative_checks = [
    (r"\b14 nights\b", "sleep-log total"),
    (r"\beight nights\b.{0,180}\bmedian\b.{0,30}\b4/5\b", "aligned-night result"),
    (r"\bsix irregular nights\b.{0,80}\bmedian\b.{0,30}\b2/5\b", "irregular-night result"),
    (r"20[ -]minute.{0,90}\b10 mornings\b", "morning-walk sample"),
    (r"\bseven\b.{0,50}\b4/5\b.{0,40}\b10:00\b", "morning-walk alertness result"),
    (r"\bfive mornings without\b.{0,30}\b(?:one|once)\b", "no-morning-walk comparison"),
    (r"\bfive weekdays\b.{0,80}\bmedian\b.{0,30}\b63\b.{0,20}\bnon-call alerts\b", "alert audit"),
    (r"\b11\b.{0,40}\binterruptions\b.{0,50}\bmedian\b.{0,20}\b18[ -]minute\b", "resettling sample"),
    (r"\b12 planned 50[ -]minute blocks\b.{0,30}\b09:30\b", "focus-block sample"),
    (r"\bseven of nine\b.{0,80}\bcompleted\b.{0,40}\bone of three\b", "phone-placement comparison"),
    (r"\beight workdays\b.{0,70}\b15[ -]minute\b.{0,30}\b12:45\b.{0,80}\bsix\b.{0,40}\b15:30\b.{0,30}\b4/5\b", "midday-walk result"),
    (r"\bfive workdays without\b.{0,35}\b(?:one|once)\b", "no-midday-walk comparison"),
    (r"\bsix completed Sunday reviews\b.{0,80}\bfour of five\b.{0,80}\bone of three skipped reviews\b", "review comparison"),
]
document_text = " ".join(text.split())
for pattern, description in quantitative_checks:
    if not re.search(pattern, document_text, re.IGNORECASE):
        fail(f"the document omits the source-supported {description}")

for pattern, description in [
    (r"subjective.{0,100}workload.{0,40}caffeine|workload.{0,40}caffeine.{0,100}subjective", "sleep-log caveats"),
    (r"sleep duration.{0,100}weather|weather.{0,100}sleep duration", "morning-light caveats"),
    (r"selection bias", "notification-audit selection bias"),
    (r"block difficulty", "focus-block difficulty caveat"),
    (r"lunch timing.{0,80}morning workload|morning workload.{0,80}lunch timing", "midday-walk caveats"),
    (r"week difficulty.{0,80}capacity|capacity.{0,80}week difficulty", "weekly-review caveats"),
]:
    if not re.search(pattern, document_text, re.IGNORECASE):
        fail(f"the document omits the {description}")

evergreen_lines = nonempty_lines(sections["## Evergreen Notes"])
if len(evergreen_lines) != 4 or any(not line.startswith("- ") for line in evergreen_lines):
    fail("Evergreen Notes must contain exactly four bullets")
for index, line in enumerate(evergreen_lines, start=1):
    if len(re.findall(r"\b[\w’'-]+\b", line, re.UNICODE)) < 30:
        fail(f"Evergreen Note {index} is not substantive")
    if not any(note_id in line for note_id, _ in SOURCE_INDEX):
        fail(f"Evergreen Note {index} lacks a note-ID citation")

experiment_rows = parse_table(
    sections["## Experiments"],
    "Experiments",
    ["Experiment", "Cue", "Action", "Measure", "Review"],
    3,
)
experiment_text = " ".join(" ".join(row) for row in experiment_rows)
per_experiment_checks = {
    "sleep": [
        (r"sleep", "sleep experiment"),
        (r"22:45\s*[–-]\s*23:15|23:00.{0,15}(?:15 minutes|15 min|±\s*15)", "sleep window"),
        (r"07:00", "wake time"),
        (r"energy", "next-day energy measure"),
        (r"median", "weekly energy summary"),
    ],
    "focus": [
        (r"focus", "focus experiment"),
        (r"09:30", "focus start"),
        (r"50[ -]minute|50 minutes", "focus duration"),
        (r"phone.{0,40}another room", "phone placement"),
        (r"Do Not Disturb", "Do Not Disturb setting"),
        (r"12:30", "first notification batch"),
        (r"17:00", "second notification batch"),
        (r"complet", "block-completion measure"),
        (r"interrupt", "interruption measure"),
    ],
    "walk": [
        (r"walk", "walk experiment"),
        (r"12:45", "walk time"),
        (r"15[ -]minute|15 minutes", "walk duration"),
        (r"15:30", "afternoon rating time"),
        (r"energy", "afternoon energy measure"),
        (r"1\s*[–-]\s*5", "energy scale"),
    ],
}
classified_rows: dict[str, list[str]] = {}
row_classifiers = {"sleep": r"22:45|23:00", "focus": r"09:30", "walk": r"12:45"}
for row in experiment_rows:
    row_text = " ".join(row)
    matches = [name for name, pattern in row_classifiers.items() if re.search(pattern, row_text)]
    if len(matches) != 1 or matches[0] in classified_rows:
        fail("Experiments must contain one distinct sleep, focus, and walk experiment")
    classified_rows[matches[0]] = row
if set(classified_rows) != set(per_experiment_checks):
    fail("Experiments must contain one distinct sleep, focus, and walk experiment")
for name, checks in per_experiment_checks.items():
    row_text = " ".join(classified_rows[name])
    for pattern, description in checks:
        if not re.search(pattern, row_text, re.IGNORECASE):
            fail(f"the {name} experiment omits the source-supported {description}")
if not re.search(r"Sunday.{0,15}18:00", experiment_text, re.IGNORECASE):
    fail("Experiments omit the retained weekly review time")

supported_clock_times = {
    "07:00",
    "09:30",
    "10:00",
    "12:30",
    "12:45",
    "15:30",
    "17:00",
    "18:00",
    "22:45",
    "23:00",
    "23:15",
}
used_clock_times = set(re.findall(r"(?<!\d)\d{1,2}:\d{2}(?!\d)", experiment_text))
unsupported_clock_times = used_clock_times - supported_clock_times
if unsupported_clock_times:
    fail(f"Experiments add unsupported clock times: {sorted(unsupported_clock_times)}")

review_lines = nonempty_lines(sections["## Review Rhythm"])
if len(review_lines) != 4 or any(not line.startswith("- ") for line in review_lines):
    fail("Review Rhythm must contain exactly four bullets")
review_text = " ".join(review_lines)
for pattern, description in [
    (r"Sunday.{0,30}18:00", "Sunday 18:00 timing"),
    (r"scorecard", "weekly scorecard"),
    (r"small|non-random|subjective|caus", "evidence limitations"),
    (r"separate|separately", "separate experiment measures"),
]:
    if not re.search(pattern, review_text, re.IGNORECASE):
        fail(f"Review Rhythm omits {description}")

source_lines = nonempty_lines(sections["## Source Index"])
if len(source_lines) != 6 or any(not line.startswith("- ") for line in source_lines):
    fail("Source Index must contain exactly six bullets")
for line, (note_id, title) in zip(source_lines, SOURCE_INDEX):
    if note_id not in line or title not in line:
        fail(f"Source Index entry is missing or out of order: {note_id} — {title}")
    remainder = line.split(title, 1)[1].strip(" :—–-")
    if len(re.findall(r"\b[\w’'-]+\b", remainder, re.UNICODE)) < 5:
        fail(f"Source Index entry does not explain the role of {note_id}")

print("PASS: knowledge_map.md satisfies the accumulated multi-turn constraints")
