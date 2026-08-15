import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "final_event_plan.md"
EXPECTED_HEADINGS = [
    "## Event Snapshot",
    "## Run of Show",
    "## Roles and Contacts",
    "## Accessibility and Dietary Plan",
    "## Communications",
]


class FinalEventPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = PLAN.read_text(encoding="utf-8")
        cls.lower = cls.text.lower()
        cls.headings = re.findall(r"(?m)^#{1,6} .+$", cls.text)
        cls.sections = {}
        for index, heading in enumerate(EXPECTED_HEADINGS):
            heading_position = cls.text.find(heading)
            if heading_position == -1:
                cls.sections[heading] = ""
                continue
            start = heading_position + len(heading)
            if index + 1 < len(EXPECTED_HEADINGS):
                next_position = cls.text.find(EXPECTED_HEADINGS[index + 1], start)
                end = next_position if next_position != -1 else len(cls.text)
            else:
                end = len(cls.text)
            cls.sections[heading] = cls.text[start:end].lower()

    def test_exact_heading_contract(self):
        self.assertEqual(self.headings, EXPECTED_HEADINGS)
        self.assertTrue(self.text.startswith("## Event Snapshot\n"))
        forbidden_labels = re.compile(
            r"(?im)^\s*(?:\*\*)?(?:title|preface|conclusion|notes|change log)"
            r"(?:\*\*)?\s*:?\s*$"
        )
        self.assertIsNone(forbidden_labels.search(self.text))
        process_commentary = re.compile(
            r"(?i)(?:\b(?:clarification|correction|corrected)\b|"
            r"per (?:the|your) (?:clarification|correction)|"
            r"(?:clarified|corrected|updated|revised) (?:from|after|per)|"
            r"(?:earlier|previous) (?:message|request|version)|"
            r"i resolved|one week later|no longer available|replace only|"
            r"timing changes|this plan could|"
            r"(?:the|this) plan (?:should|will|would) (?:include|contain|describe))"
        )
        self.assertIsNone(process_commentary.search(self.text))

    def test_snapshot_uses_corrected_facts(self):
        section = self.sections["## Event Snapshot"]
        for required in (
            "saturday, october 25, 2025",
            "10:00 a.m.",
            "2:00 p.m.",
            "riverbend library community room",
            "214 cedar avenue",
            "54 registered residents",
        ):
            self.assertIn(required, section)
        for stale in ("october 18", "48 registered residents"):
            self.assertNotIn(stale, self.lower)

    def test_run_of_show_applies_only_schedule_corrections(self):
        section = self.sections["## Run of Show"]
        ordered_items = (
            "9:15 a.m.",
            "organizer setup",
            "9:45 a.m.",
            "doors and check-in",
            "10:00 a.m.",
            "welcome and safety note",
            "10:15 a.m.",
            "utility shutoff demonstration",
            "11:00 a.m.",
            "three facilitated breakout groups",
            "12:30 p.m.",
            "lunch",
            "1:10 p.m.",
            "neighborhood action mapping",
            "1:45 p.m.",
            "commitments and closing",
            "2:00 p.m.",
            "teardown",
        )
        cursor = -1
        for item in ordered_items:
            cursor = section.find(item, cursor + 1)
            self.assertNotEqual(cursor, -1, f"missing or out of order: {item}")
        for required in (
            "household readiness",
            "neighbor check-ins",
            "local resource mapping",
            "one reporter",
            "two next actions",
        ):
            self.assertIn(required, section)
        for stale in (
            "guest briefing",
            "captain elena ruiz",
            "12:15 p.m.",
            "1:00 p.m.",
        ):
            self.assertNotIn(stale, self.lower)

    def test_roles_preserve_owners_and_operational_details(self):
        section = self.sections["## Roles and Contacts"]
        requirements = {
            "maya chen": (
                "event lead",
                "check-in",
                "on-site decisions",
                "registration list",
                "emergency contact sheet",
                "urgent operational questions",
            ),
            "luis ortega": (
                "room setup",
                "av",
                "projector",
                "two handheld microphones",
                "presentation laptop",
                "before doors open",
                "utility shutoff demonstration",
            ),
            "priya shah": (
                "accessibility lead",
                "accessibility requests",
                "library front desk",
            ),
            "theo brooks": ("catering liaison", "meal issues", "lunch station"),
            "dana cole": ("five-minute warning", "breakouts", "closing"),
        }
        for person, details in requirements.items():
            self.assertIn(person, section)
            person_start = section.index(person)
            assignment_start = section.rfind("\n", 0, person_start) + 1
            following_people = [section.find(other, person_start + 1) for other in requirements if section.find(other, person_start + 1) != -1]
            person_end = min(following_people) if following_people else len(section)
            assignment = section[assignment_start:person_end]
            for detail in details:
                self.assertIn(detail, assignment, f"{person} is missing {detail}")

    def test_accessibility_and_meals_merge_clarification_and_correction(self):
        section = self.sections["## Accessibility and Dietary Plan"]
        for required in (
            "east side",
            "automatic doors",
            "same floor",
            "accessible restroom",
            "front-row seating",
            "microphone",
            "audience question",
            "plain-text digital copy",
            "54 boxed lunches",
            "36 vegetarian wraps",
            "18 chicken wraps",
            "gluten-free",
            "separately labeled",
            "nut-free",
            "salad",
            "water",
            "compostable serviceware",
        ):
            self.assertIn(required, section)
        self.assertRegex(section, r"\b(?:six|6) large-print handout sets\b")
        self.assertRegex(
            section,
            r"\b(?:eight|8)\b[\s\S]{0,100}\bvegetarian meals\b[\s\S]{0,100}"
            r"\bgluten-free\b[\s\S]{0,100}\bseparately labeled\b",
        )
        self.assertRegex(
            section,
            r"microphone[\s\S]{0,120}\bevery presentation\b[\s\S]{0,120}\baudience question",
        )
        self.assertRegex(
            section,
            r"(?:\b(?:whole|entire|all)\b[\s\S]{0,100}\b(?:order|lunches)\b[\s\S]{0,100}"
            r"\bnut-free\b|\bnut-free\b[\s\S]{0,100}\b(?:whole|entire|all)\b"
            r"[\s\S]{0,100}\b(?:order|lunches)\b)",
        )
        for stale in ("48 boxed lunches", "32 vegetarian", "16 chicken", "six gluten-free"):
            self.assertNotIn(stale, self.lower)
        self.assertNotRegex(
            self.lower,
            r"\b(?:six|6)\b\s+(?:(?:of\s+)?(?:the\s+)?)?(?:gluten-free\s+)?"
            r"vegetarian\s+(?:meals|wraps)[^.\n]{0,50}\bgluten-free\b",
        )
        for stale_pattern in (
            r"(?:\b48\b\s+(?:registered\s+)?(?:residents|attendees|participants|boxed lunches)|"
            r"\b(?:residents|attendees|participants|boxed lunches)\b\s*[:—-]\s*48\b)",
            r"(?:\b32\b\s+(?:boxed\s+)?vegetarian\s+(?:wraps|meals)|"
            r"\bvegetarian\s+(?:wraps|meals)\b\s*[:—-]\s*32\b)",
            r"(?:\b16\b\s+(?:boxed\s+)?chicken\s+(?:wraps|meals)|"
            r"\bchicken\s+(?:wraps|meals)\b\s*[:—-]\s*16\b)",
        ):
            self.assertNotRegex(self.lower, stale_pattern)

    def test_communications_use_corrected_dates_and_preserved_content(self):
        section = self.sections["## Communications"]
        for required in (
            "monday, october 20",
            "friday, october 17",
            "friday, october 24",
            "street parking is limited",
            "cedar avenue bus stop",
            "fragrance-free",
        ):
            self.assertIn(required, section)
        self.assertRegex(
            section,
            r"(?:friday, october 17[\s\S]{0,160}(?:dietary|accessibility)[\s\S]{0,160}"
            r"(?:request|response|deadline)|(?:dietary|accessibility)[\s\S]{0,160}"
            r"(?:request|response|deadline)[\s\S]{0,160}friday, october 17)",
        )
        self.assertRegex(
            section,
            r"(?:monday, october 20[\s\S]{0,100}main attendee reminder|"
            r"main attendee reminder[\s\S]{0,100}monday, october 20)",
        )
        self.assertRegex(
            section,
            r"(?:friday, october 24[\s\S]{0,100}day-before reminder|"
            r"day-before reminder[\s\S]{0,100}friday, october 24)",
        )
        self.assertRegex(
            section,
            r"(?:request[\s\S]{0,100}fragrance-free|fragrance-free[\s\S]{0,100}request)",
        )
        self.assertRegex(
            section,
            r"(?:cedar avenue bus stop[\s\S]{0,100}nearby|nearby[\s\S]{0,100}"
            r"cedar avenue bus stop)",
        )
        parking_promise = re.compile(
            r"on-site parking\s+(?:is|will be)\s+(?:available|provided|reserved|guaranteed)"
        )
        self.assertIsNone(parking_promise.search(section))
        for stale in ("monday, october 13", "friday, october 10"):
            self.assertNotIn(stale, self.lower)


if __name__ == "__main__":
    unittest.main()
