#!/usr/bin/env python3
from pathlib import Path
import re
import sys


PLAN = Path("weekend_dispatch_plan.md")
HEADINGS = [
    "# Coverage",
    "# Dispatch Schedule",
    "# Exception Playbook",
    "# Communications",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


if not PLAN.is_file():
    fail("weekend_dispatch_plan.md was not created")

try:
    text = PLAN.read_text(encoding="utf-8")
except UnicodeDecodeError:
    fail("plan is not valid UTF-8")

lines = text.splitlines()
observed_headings = [line for line in lines if line.startswith("#")]
if observed_headings != HEADINGS:
    fail(f"headings must be exactly {HEADINGS!r} in order")

nonempty = [line for line in lines if line.strip()]
if not nonempty or nonempty[0] != HEADINGS[0]:
    fail("content appears before the first required heading")
for line in nonempty:
    if line not in HEADINGS and not line.startswith("- "):
        fail(f"non-heading content must be a bullet: {line!r}")

sections = {}
for index, heading in enumerate(HEADINGS):
    start = lines.index(heading) + 1
    end = lines.index(HEADINGS[index + 1]) if index + 1 < len(HEADINGS) else len(lines)
    body = "\n".join(lines[start:end]).strip()
    bullets = [line for line in lines[start:end] if line.startswith("- ")]
    if not body:
        fail(f"{heading} is empty")
    sections[heading] = body
    if not bullets:
        fail(f"{heading} must contain plan bullets")


def folded(value: str) -> str:
    return (value.casefold().replace("–", "-").replace("—", "-")
            .replace("`", "").replace("*", ""))


def require(section: str, label: str, patterns) -> None:
    haystack = folded(sections[section])
    for pattern in patterns:
        if re.search(pattern, haystack, flags=re.DOTALL) is None:
            fail(f"{section} is missing or changed: {label}")


def require_bullet(section: str, label: str, patterns) -> None:
    for line in sections[section].splitlines():
        candidate = folded(line)
        if line.startswith("- ") and all(
                re.search(pattern, candidate) is not None for pattern in patterns):
            return
    fail(f"{section} is missing or changed: {label}")


def has_role(segment: str, person: str, role: str) -> bool:
    patterns = [
        rf"\b{role}[ \t]*(?:is|:|-)?[ \t]*{person}\b",
        rf"\b{person}[ \t]+(?:is[ \t]+)?(?:the[ \t]+)?(?:wave[ \t]+)?{role}\b",
        rf"\b{person}[ \t]*\([ \t]*{role}[ \t]*\)",
    ]
    return any(re.search(pattern, segment) is not None for pattern in patterns)


require("# Coverage", "dock window and reserved doors", [
    r"05:30\s*-\s*17:30", r"doors?\s+3", r"(?:and|&)\s+4", r"reserv",
])
for label, patterns in [
    ("Talia's staffing hours", [r"\btalia\b", r"05:30\s*-\s*14:00"]),
    ("Minh's staffing hours", [r"\bminh\b", r"06:00\s*-\s*15:00"]),
    ("Reece's staffing hours", [r"\breece\b", r"08:00\s*-\s*17:00"]),
    ("Priya's remote staffing hours", [
        r"\bpriya\b", r"\bremote\b", r"09:00\s*-\s*17:00",
    ]),
    ("opening lead", [r"opening lead", r"\btalia\b"]),
    ("closing lead", [r"closing lead", r"\breece\b"]),
]:
    require_bullet("# Coverage", label, patterns)

all_times_are_pacific = re.search(
    r"\ball\s+(?:listed\s+)?times\b[^\n]*(?:\bpt\b|pacific time)",
    folded(text),
) is not None
if not all_times_are_pacific:
    current_section = None
    for line in lines:
        if line in HEADINGS:
            current_section = line
            continue
        if re.search(r"\b(?:[01]\d|2[0-3]):[0-5]\d\b", line):
            section_declares_pacific = current_section is not None and re.search(
                r"\btimes?\b[^\n]*(?:\bpt\b|pacific time)|"
                r"(?:\bpt\b|pacific time)[^\n]*\btimes?\b",
                folded(sections[current_section]),
            ) is not None
            if (re.search(r"\bpt\b|pacific time", folded(line)) is None
                    and not section_declares_pacific):
                fail(f"clock time is not identified as Pacific Time: {line!r}")

schedule = folded(sections["# Dispatch Schedule"])
wave1_at = schedule.find("wave 1")
wave2_at = schedule.find("wave 2")
if wave1_at < 0 or wave2_at <= wave1_at:
    fail("Dispatch Schedule must present Wave 1 before Wave 2")
wave1 = schedule[wave1_at:wave2_at]
wave2 = schedule[wave2_at:]

for label, segment, patterns in [
    ("Wave 1", wave1, [
        r"redwood ground", r"staging.*07:15", r"pickup.*08:00",
        r"220\s+orders", r"140\s+(?:northern california|norcal)",
        r"80\s+mountain",
    ]),
    ("Wave 2", wave2, [
        r"redwood ground", r"staging.*13:00", r"pickup.*14:00",
        r"160\s+orders", r"90\s+(?:pacific northwest|pnw)",
        r"70\s+southwest",
    ]),
]:
    for pattern in patterns:
        if re.search(pattern, segment, flags=re.DOTALL) is None:
            fail(f"{label} is missing or changed: {pattern}")

for label, segment, owner, backup in [
    ("Wave 1", wave1, "talia", "minh"),
    ("Wave 2", wave2, "minh", "reece"),
]:
    if not has_role(segment, owner, "owner"):
        fail(f"{label} is missing or changed: owner {owner.title()}")
    if not has_role(segment, backup, "backup"):
        fail(f"{label} is missing or changed: backup {backup.title()}")

if "bluepeak" in schedule:
    fail("superseded Wave 2 carrier remains in the plan")
if has_role(wave2, "reece", "owner") or has_role(wave2, "talia", "backup"):
    fail("superseded Wave 2 ownership remains in the plan")
wave_numbers = set(re.findall(r"\bwave\s+(\d+)\b", schedule))
if wave_numbers != {"1", "2"}:
    fail("Dispatch Schedule must contain exactly Wave 1 and Wave 2")
manifest_terms = [r"manifest", r"30\s+minutes", r"before", r"pickup"]
manifest_shared = (
    all(re.search(pattern, schedule, flags=re.DOTALL) is not None
        for pattern in manifest_terms)
    and re.search(r"\b(?:each|every|both)\b", schedule) is not None
    and re.search(r"\bwaves?\b", schedule) is not None
)
manifest_per_wave = all(
    re.search(
        rf"wave\s+{number}.*manifest.*30\s+minutes.*before.*pickup",
        schedule,
        flags=re.DOTALL,
    ) is not None
    for number in (1, 2)
)
if not (manifest_shared or manifest_per_wave):
    fail("# Dispatch Schedule is missing or changed: lock each manifest 30 minutes before pickup")

require("# Exception Playbook", "scan hold and recount", [
    r"outbound scan completion", r"staging deadline",
    r"below\s+98%|under\s+98%|<\s*98%", r"hold(?:\s+the)?\s+wave",
    r"wave owner", r"15[- ]minute", r"recount", r"(?:before|prior to) release",
])
require("# Exception Playbook", "carrier no-show response", [
    r"carrier no-show|carrier no show", r"call\s+(?:the\s+)?carrier desk",
    r"within\s+10\s+minutes", r"recovery eta",
    r"(?:exceeds|over|>)\s*45\s+minutes", r"backup owner",
    r"opens?\s+(?:the\s+)?(?:overflow\s+)?door\s+5", r"moves?\s+(?:the\s+)?staged freight",
])
require("# Exception Playbook", "volume surge response", [
    r"actual order volume", r"planned wave volume",
    r"(?:at least|>=)\s*12%\s+above|12%\s+or more\s+above|"
    r"(?:exceeds|is over)[^\n]*planned wave volume[^\n]*by\s+(?:at least\s+)?12%",
    r"priya[^\n]*pulls?\s+(?:the\s+)?reserve[- ]labor list",
    r"wave owner[^\n]*opens?\s+(?:packing\s+)?lane\s+c",
])
require("# Exception Playbook", "damage response", [
    r"damage exceptions", r"(?:exceed|more than|>)\s*5\s+cartons",
    r"pause\s+(?:the\s+)?affected pallet", r"priya[^\n]*open(?:s)?\s+(?:an\s+)?incident ticket",
    r"(?:before|prior to)\s+(?:it\s+is\s+)?load(?:ed|ing)",
])

require("# Communications", "channel and update cadence", [
    r"#reno-weekend-ops", r"05:45", r"immediately after each pickup", r"16:45",
])
require_bullet("# Communications", "escalation chain", [
    r"talia.*owen.*logistics duty manager",
])
require("# Communications", "update fields", [
    r"(?:every|each|all)[^\n]*(?:update|post)|update fields",
    r"scan percentage", r"staged order count", r"current exception count", r"carrier eta",
])

print("PASS: corrected Saturday dispatch plan is complete and constraints were retained")
