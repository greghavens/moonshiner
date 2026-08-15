from __future__ import annotations

import csv
import hashlib
import re
from collections import defaultdict
from pathlib import Path


FIXTURE_ROOT = Path(__file__).resolve().parents[1]
# Fixtures are beneath files/ while authoring, but are staged at the workspace
# root by the trace harness. Resolve both layouts relative to this script.
if FIXTURE_ROOT.name == "files" and (FIXTURE_ROOT.parent / "task.json").is_file():
    WORKSPACE_ROOT = FIXTURE_ROOT.parent
else:
    WORKSPACE_ROOT = FIXTURE_ROOT

DOC = WORKSPACE_ROOT / "board_followup.md"
ATTENDANCE = FIXTURE_ROOT / "governance" / "attendance.csv"
NOTES = FIXTURE_ROOT / "governance" / "meeting_notes.md"


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def has_association(line: str, left: str, right_pattern: str) -> bool:
    """Accept either natural ordering while keeping a name tied to its role."""
    escaped_left = re.escape(left)
    return bool(
        re.search(
            rf"(?:{escaped_left}.{{0,80}}{right_pattern}|"
            rf"{right_pattern}.{{0,80}}{escaped_left})",
            line,
            flags=re.IGNORECASE,
        )
    )


def has_labeled_count(line: str, count: int, label: str) -> bool:
    count_first = (
        rf"\b{count}\s+(?:(?:board\s+)?members?\s+)?(?:were\s+)?{label}\b"
    )
    label_first = (
        rf"\b{label}(?:\s+(?:board\s+)?members?)?(?:\s+total)?"
        rf"\s*(?::|=|was|were)?\s*{count}\b"
    )
    return bool(re.search(rf"(?:{count_first}|{label_first})", line, re.IGNORECASE))


if not DOC.is_file():
    fail("board_followup.md was not created")
if not ATTENDANCE.is_file() or not NOTES.is_file():
    fail("the governance packet is missing")

expected_hashes = {
    ATTENDANCE: "7acc6f69ec2fc0738679aba06af3aa2cd5bbddcd24f7710052a7569c42aa3c62",
    NOTES: "13d0ad32b9f65068ed8c69b5b3cd83ecbfb1dd2de0fb829bdd495d4f1522929d",
}
for source, expected_hash in expected_hashes.items():
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected_hash:
        fail(f"governance source was modified: {source.name}")

with ATTENDANCE.open(newline="", encoding="utf-8") as handle:
    attendance_rows = list(csv.DictReader(handle))
if len(attendance_rows) != 8:
    fail("the attendance register was modified")
present_count = sum(row["attendance_status"].startswith("Present") for row in attendance_rows)
absent_count = sum(row["attendance_status"].startswith("Absent") for row in attendance_rows)
if (present_count, absent_count) != (6, 2):
    fail("internal attendance totals are inconsistent")

text = DOC.read_text(encoding="utf-8")
if not text.strip():
    fail("board_followup.md is empty")
if "http://" in text or "https://" in text:
    fail("external links are not allowed")

expected_headings = [
    "# Meeting Record",
    "# Decisions",
    "# Action Register",
    "# Next Meeting",
]
lines = text.splitlines()
headings = [line for line in lines if line.startswith("#")]
if headings != expected_headings:
    fail(f"headings must be exactly {expected_headings}, found {headings}")
if not text.startswith("# Meeting Record\n"):
    fail("content appears before the first required heading")

positions = [lines.index(heading) for heading in expected_headings]
sections: dict[str, list[str]] = {}
for index, heading in enumerate(expected_headings):
    end = positions[index + 1] if index + 1 < len(positions) else len(lines)
    sections[heading] = [
        line for line in lines[positions[index] + 1 : end] if line.strip()
    ]

meeting = sections["# Meeting Record"]
if len(meeting) != 5 or any(not line.startswith("- ") for line in meeting):
    fail("Meeting Record must contain exactly five bullets and no other content")
meeting_requirements = {
    1: ("Lakeside Literacy Network", "2026-09-17"),
    3: ("Community Room B", "Mercer Library"),
}
for bullet_number, phrases in meeting_requirements.items():
    bullet = meeting[bullet_number - 1]
    lowered = bullet.lower()
    for phrase in phrases:
        if phrase.lower() not in lowered:
            fail(f"Meeting Record bullet {bullet_number} is missing {phrase}")

if not has_association(meeting[1], "18:00", r"called\s+to\s+order"):
    fail("Meeting Record bullet 2 does not associate 18:00 with called to order")
if not has_association(meeting[1], "19:42", r"adjourn\w*"):
    fail("Meeting Record bullet 2 does not associate 19:42 with adjournment")

if not has_association(meeting[3], "Amara Cole", r"presid\w*"):
    fail("Meeting Record bullet 4 does not identify Amara Cole as presiding")
if not has_association(meeting[3], "Marcus Lee", r"record\w*"):
    fail("Meeting Record bullet 4 does not identify Marcus Lee as recording")

attendance_bullet = meeting[4]
if not has_labeled_count(attendance_bullet, present_count, "present"):
    fail(f"Meeting Record bullet 5 is missing the total of {present_count} present")
if not has_labeled_count(attendance_bullet, absent_count, "absent"):
    fail(f"Meeting Record bullet 5 is missing the total of {absent_count} absent")

members_by_status: dict[str, set[str]] = defaultdict(set)
all_member_names: set[str] = set()
for row in attendance_rows:
    members_by_status[row["attendance_status"]].add(row["name"])
    all_member_names.add(row["name"])

# Find status-led groups without confusing the totals phrase ("2 absent") with
# the later `Absent` category. Each member must occur once and inside the group
# introduced by that member's packet status.
status_alternatives = "|".join(
    re.escape(status) for status in sorted(members_by_status, key=len, reverse=True)
)
status_markers: list[tuple[str, int, int]] = []
for match in re.finditer(status_alternatives, attendance_bullet, re.IGNORECASE):
    matched_status = next(
        status
        for status in members_by_status
        if status.lower() == match.group(0).lower()
    )
    status_markers.append((matched_status, match.start(), match.end()))

for name in all_member_names:
    if attendance_bullet.lower().count(name.lower()) != 1:
        fail(f"Meeting Record bullet 5 must name {name} exactly once")

for status, expected_names in members_by_status.items():
    matching_groups = []
    for marker_number, (marker_status, _, marker_end) in enumerate(status_markers):
        if marker_status != status:
            continue
        group_end = (
            status_markers[marker_number + 1][1]
            if marker_number + 1 < len(status_markers)
            else len(attendance_bullet)
        )
        group = attendance_bullet[marker_end:group_end]
        grouped_names = {
            name for name in all_member_names if name.lower() in group.lower()
        }
        if grouped_names == expected_names:
            matching_groups.append(group)

    # Also accept an individually paired form such as
    # `Amara Cole (Present in person)` instead of a status-led list.
    individually_paired = all(
        re.search(
            rf"{re.escape(name)}\s*(?:\(|\[|:|[–—-])\s*"
            rf"{re.escape(status)}\b",
            attendance_bullet,
            re.IGNORECASE,
        )
        for name in expected_names
    )
    if len(matching_groups) != 1 and not individually_paired:
        fail(
            "Meeting Record bullet 5 does not group every member under "
            f"the packet status {status}"
        )

decisions = sections["# Decisions"]
if len(decisions) != 3:
    fail("Decisions must contain exactly three numbered items")
decision_requirements = [
    (
        "D-01",
        "Adopt the FY2027 literacy program calendar as circulated",
        "Sofia Reyes",
        "Devon Brooks",
        "6-0-0",
    ),
    (
        "D-02",
        "Hold the fall tutor orientation on 2026-10-24 at Mercer Library",
        "Noah Kim",
        "Elena Park",
        "5-0-1",
        "Marcus Lee abstained",
    ),
    (
        "D-03",
        "Approve the updated records retention schedule effective 2026-10-01",
        "Elena Park",
        "Marcus Lee",
        "6-0-0",
    ),
]
for number, (item, phrases) in enumerate(zip(decisions, decision_requirements), start=1):
    decision_id, action, mover, seconder, vote, *extra = phrases
    if not item.lower().startswith(f"{number}. {decision_id}".lower()):
        fail("Decisions are not numbered in decision-ID order")
    if action.lower() not in item.lower():
        fail(f"decision item {number} is missing the adopted action")
    if not has_association(item, mover, r"mov(?:e|ed|er)\w*"):
        fail(f"decision item {number} does not identify the mover")
    if not has_association(item, seconder, r"second(?:ed|er)\w*"):
        fail(f"decision item {number} does not identify the seconder")
    if not has_association(item, vote, r"vote\w*"):
        fail(f"decision item {number} does not identify the recorded vote")
    for phrase in extra:
        if phrase.lower() not in item.lower():
            fail(f"decision item {number} is missing {phrase}")

action_lines = sections["# Action Register"]
if len(action_lines) != 8 or any(not line.startswith("|") for line in action_lines):
    fail("Action Register must contain only one header, one separator, and six rows")
expected_columns = ["ID", "Owner", "Action", "Due", "Status"]
if cells(action_lines[0]) != expected_columns:
    fail("Action Register columns are incorrect")
if any(not re.fullmatch(r":?-{3,}:?", cell) for cell in cells(action_lines[1])):
    fail("Action Register separator is malformed")

expected_actions = [
    [
        "A-01",
        "Sofia Reyes",
        "Publish the approved FY2027 literacy program calendar.",
        "2026-09-23",
        "In progress",
    ],
    [
        "A-02",
        "Noah Kim",
        "Reserve the Community Lab and confirm the accessibility layout for tutor orientation.",
        "2026-09-25",
        "Not started",
    ],
    [
        "A-03",
        "Devon Brooks",
        "Send the tutor orientation invitation and RSVP form.",
        "2026-09-28",
        "Not started",
    ],
    [
        "A-04",
        "Elena Park",
        "File the signed records retention schedule and replace the board index copy.",
        "2026-10-02",
        "Not started",
    ],
    [
        "A-05",
        "Amara Cole",
        "Circulate the September decision summary to absent directors.",
        "2026-09-19",
        "Complete",
    ],
    [
        "A-06",
        "Elena Park",
        "Reconcile the attendance register with the remote participation log.",
        "2026-09-22",
        "In progress",
    ],
]
actual_actions = [cells(line) for line in action_lines[2:]]
if actual_actions != expected_actions:
    fail("Action Register does not preserve all six actions with the corrected A-04 owner")

next_meeting = sections["# Next Meeting"]
if len(next_meeting) != 3 or any(not line.startswith("- ") for line in next_meeting):
    fail("Next Meeting must contain exactly three bullets and no other content")
next_requirements = [
    ("2026-10-15", "18:00", "Community Room B", "Mercer Library"),
    (
        "committee dashboards",
        "tutor-orientation RSVP count",
        "2026-10-12",
        "12:00",
    ),
    ("first-quarter enrollment snapshot", "tutor-orientation readiness review"),
]
for number, (bullet, phrases) in enumerate(zip(next_meeting, next_requirements), start=1):
    for phrase in phrases:
        if phrase.lower() not in bullet.lower():
            fail(f"Next Meeting bullet {number} is missing {phrase}")

print("PASS: board_followup.md satisfies the corrected multi-turn requirements")
