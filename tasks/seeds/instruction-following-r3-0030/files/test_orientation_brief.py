from pathlib import Path
import re
import sys


OUTPUT = Path("deliverables/october_orientation_brief.md")
errors = []


def fail(message):
    errors.append(message)


def get_section(text, heading):
    match = re.search(
        rf"(?ims)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)", text
    )
    if not match:
        fail(f"missing level-two section: {heading}")
        return ""
    return match.group(1)


def line_with(section, *needles):
    for line in section.splitlines():
        lowered = line.lower()
        if all(needle.lower() in lowered for needle in needles):
            return line
    return None


def contains_date(text, iso_date, written_date):
    lowered = text.lower()
    return iso_date in lowered or written_date in lowered


def markdown_tables(section):
    """Return syntactically valid Markdown tables as (header, rows)."""
    lines = section.splitlines()
    tables = []
    index = 0
    while index < len(lines):
        if not re.match(r"^\s*\|.*\|\s*$", lines[index]):
            index += 1
            continue
        block = []
        while index < len(lines) and re.match(r"^\s*\|.*\|\s*$", lines[index]):
            block.append(lines[index])
            index += 1
        if len(block) < 3:
            continue
        parsed = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in block]
        if len({len(row) for row in parsed}) != 1:
            continue
        if not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in parsed[1]):
            continue
        tables.append((parsed[0], parsed[2:]))
    return tables


def find_schedule_table(section):
    for header, rows in markdown_tables(section):
        joined_rows = [" | ".join(row) for row in rows]
        has_first = any(
            contains_date(row, "2026-10-10", "october 10, 2026") for row in joined_rows
        )
        has_second = any(
            contains_date(row, "2026-10-18", "october 18, 2026") for row in joined_rows
        )
        if has_first and has_second:
            return header, rows
    return None


def schedule_row(rows, iso_date, written_date):
    matches = [
        " | ".join(row)
        for row in rows
        if contains_date(" | ".join(row), iso_date, written_date)
    ]
    return matches[0] if len(matches) == 1 else None


def clean_cell(cell):
    return re.sub(r"[*_`]", "", cell).strip()


def assignment_table(section):
    for header, rows in markdown_tables(section):
        normalized = [clean_cell(cell).lower() for cell in header]
        lead_indices = [i for i, cell in enumerate(normalized) if cell == "lead"]
        support_indices = [i for i, cell in enumerate(normalized) if cell == "support"]
        date_indices = [i for i, cell in enumerate(normalized) if "date" in cell]
        if len(lead_indices) == len(support_indices) == len(date_indices) == 1:
            return rows, date_indices[0], lead_indices[0], support_indices[0]
    return None


def role_near_name(line, role, name):
    role_pattern = re.escape(role)
    name_pattern = re.escape(name)
    return bool(
        re.search(rf"(?i){role_pattern}[^.;|]{{0,45}}{name_pattern}", line)
        or re.search(rf"(?i){name_pattern}[^.;|]{{0,45}}{role_pattern}", line)
    )


if not OUTPUT.is_file():
    fail(f"missing required deliverable: {OUTPUT}")
    text = ""
else:
    text = OUTPUT.read_text(encoding="utf-8")

if text and re.search(r"(?i)(october\s+17|2026-10-17|10/17/2026)", text):
    fail("the superseded October 17 date appears in the finished brief")

facts = get_section(text, "Sourced facts") if text else ""
recommendations = get_section(text, "Recommendations") if text else ""
uncertainties = get_section(text, "Uncertainties") if text else ""

if facts:
    schedule = find_schedule_table(facts)
    if not schedule:
        fail("Sourced facts lacks the requested Markdown table for the two sessions")
    else:
        _, rows = schedule
        dated_rows = [
            " | ".join(row)
            for row in rows
            if re.search(r"(?i)(2026-10-\d{2}|october\s+\d{1,2},\s*2026)", " | ".join(row))
        ]
        if len(dated_rows) != 2:
            fail("the confirmed schedule table must contain exactly the two recorded sessions")

        first = schedule_row(rows, "2026-10-10", "october 10, 2026")
        if not first or not all(
            re.search(pattern, first, re.IGNORECASE)
            for pattern in (
                r"\bsaturday\b",
                r"9:00",
                r"10:30",
                r"maple room",
                r"8:50",
                r"\b14\b",
                r"\[r1\]",
            )
        ):
            fail("Sourced facts lacks a complete, sourced Saturday October 10 schedule row")

        second = schedule_row(rows, "2026-10-18", "october 18, 2026")
        if not second or not all(
            re.search(pattern, second, re.IGNORECASE)
            for pattern in (
                r"\bsunday\b",
                r"9:00",
                r"10:30",
                r"garden room",
                r"8:50",
                r"\b10\b",
                r"\[r1\]",
                r"\[m3\]",
            )
        ):
            fail("Sourced facts lacks the complete, jointly cited Sunday October 18 schedule row")

    modules = (
        ("welcome and mission", "15"),
        ("safety and incident reporting", "20"),
        ("respectful guest service", "20"),
        ("role walkthrough", "25"),
        ("questions and sign-out", "10"),
    )
    module_positions = []
    for module, minutes in modules:
        match = re.search(re.escape(module), facts, re.IGNORECASE)
        if not match:
            fail(f"Sourced facts lacks the curriculum module: {module}")
            continue
        module_positions.append(match.start())
        line_start = facts.rfind("\n", 0, match.start()) + 1
        line_end = facts.find("\n", match.end())
        containing_line = facts[line_start:] if line_end == -1 else facts[line_start:line_end]
        if not re.search(rf"(?i)\b{minutes}\s+minutes?\b", containing_line):
            fail(f"Sourced facts gives no correct duration for: {module}")
        if not re.search(r"(?i)\[r2\]", containing_line):
            fail(f"Sourced facts gives no [R2] citation for: {module}")
    if len(module_positions) == len(modules) and module_positions != sorted(module_positions):
        fail("the curriculum modules are not in the recorded order")

    for proposed_name in ("Maya Singh", "Theo Brooks", "Lena Park", "Omar Reed"):
        if proposed_name.lower() in facts.lower():
            fail(f"proposed facilitator {proposed_name} belongs in Recommendations, not Sourced facts")

if recommendations:
    expected_assignments = (
        ("2026-10-10", "october 10, 2026", "Maya Singh", "Lena Park"),
        ("2026-10-18", "october 18, 2026", "Theo Brooks", "Omar Reed"),
    )
    known_names = {"Maya Singh", "Theo Brooks", "Lena Park", "Omar Reed"}
    table = assignment_table(recommendations)
    if table:
        rows, date_index, lead_index, support_index = table
        dated_rows = [
            row
            for row in rows
            if contains_date(row[date_index], "2026-10-10", "october 10, 2026")
            or contains_date(row[date_index], "2026-10-18", "october 18, 2026")
        ]
        if len(dated_rows) != 2:
            fail("facilitator table must have exactly one assignment row for each session")
        for iso_date, written_date, lead, support in expected_assignments:
            matches = [
                row
                for row in dated_rows
                if contains_date(row[date_index], iso_date, written_date)
            ]
            if (
                len(matches) != 1
                or clean_cell(matches[0][lead_index]).lower() != lead.lower()
                or clean_cell(matches[0][support_index]).lower() != support.lower()
            ):
                fail(f"Recommendations lacks the single available lead/support pair for {written_date}")
    else:
        for iso_date, written_date, lead, support in expected_assignments:
            dated_lines = [
                line
                for line in recommendations.splitlines()
                if contains_date(line, iso_date, written_date)
                and any(name.lower() in line.lower() for name in known_names)
            ]
            valid_lines = [
                line
                for line in dated_lines
                if role_near_name(line, "lead", lead)
                and role_near_name(line, "support", support)
                and {name for name in known_names if name.lower() in line.lower()} == {lead, support}
            ]
            if len(valid_lines) != 1:
                fail(f"Recommendations lacks exactly one labeled lead/support pair for {written_date}")

    checklist_items = [
        re.sub(r"^\s*(?:[-*+]\s+(?:\[[ xX]\]\s*)?|\d+[.)]\s+)", "", line)
        for line in recommendations.splitlines()
        if re.match(r"^\s*(?:[-*+]\s+(?:\[[ xX]\]\s*)?|\d+[.)]\s+)", line)
    ]
    outreach_rows = (
        ("volunteer", "nina flores"),
        ("facilitator", "nina flores"),
        ("site host", "devon hale"),
    )
    for audience, owner in outreach_rows:
        matches = [
            item
            for item in checklist_items
            if audience in item.lower().replace("-", " ") and owner in item.lower()
        ]
        if len(matches) != 1:
            fail(f"Recommendations lacks one checklist action naming the audience and owner for {audience}")
            continue
        item = matches[0]
        if not re.search(r"(?i)\b(send|email|brief|confirm|notify|remind|coordinate)\b", item):
            fail(f"the {audience} checklist item lacks an outreach action")
        if not re.search(
            r"(?i)\b(before|after|upon|when|morning|day|days|hour|hours|week|weeks|by)\b|no later than",
            item,
        ):
            fail(f"the {audience} checklist item lacks recommended send timing")

if uncertainties:
    rsvp_line = line_with(uncertainties, "rsvp")
    if not rsvp_line or not re.search(
        r"(?i)\b(not recorded|unknown|unconfirmed|not provided|unavailable)\b", rsvp_line
    ):
        fail("Uncertainties must say that RSVP totals are not known")

    access_line = None
    for candidate in uncertainties.splitlines():
        if re.search(r"(?i)accessib|accommodation", candidate):
            access_line = candidate
            break
    if not access_line or not re.search(
        r"(?i)\b(not recorded|unknown|unconfirmed|not provided|unavailable)\b",
        access_line,
    ):
        fail("Uncertainties must say that accessibility accommodations are not known")

if text:
    positions = [
        text.lower().find("## sourced facts"),
        text.lower().find("## recommendations"),
        text.lower().find("## uncertainties"),
    ]
    if all(position >= 0 for position in positions) and positions != sorted(positions):
        fail("required sections are not in the requested order")

if errors:
    print("FAIL")
    for error in errors:
        print(f"- {error}")
    sys.exit(1)

print("PASS: orientation brief preserves the request thread and corrected schedule")
