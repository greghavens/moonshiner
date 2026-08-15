import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "travel_plan.md"

EXPECTED_HEADINGS = [
    "# Porto Long Weekend",
    "## Friday, October 16, 2026",
    "## Saturday, October 17, 2026",
    "## Sunday, October 18, 2026",
    "## Practical Notes",
]

DAY_HEADINGS = EXPECTED_HEADINGS[1:4]
TIMED_LINE = re.compile(
    r"^- (?P<hour>\d{2}):(?P<minute>\d{2}) — (?P<activity>\S.+?) — "
    r"(?P<location>\S.+?) — (?P<travel>\S.+)$"
)

KNOWN_LOCATIONS = {
    "Sao Bento Station",
    "Bolhao Market",
    "Porto Cathedral terrace",
    "Palacio da Bolsa",
    "Ribeira promenade",
    "Miradouro da Vitoria",
    "daTerra Baixa",
    "Mercado Ferreira Borges",
    "Igreja do Carmo",
    "Cordoaria Garden",
    "Casa da Musica",
    "Rotunda da Boavista",
    "Palacio de Cristal Gardens",
    "Bombarda galleries",
    "Epoca Porto",
    "Em Carne Viva",
    "Jardim do Morro",
    "Mosteiro da Serra do Pilar terrace",
    "Planet Cork at WOW",
    "WOW covered courtyard",
    "Gaia riverfront",
    "Cais de Gaia craft market",
    "Root & Vine",
}

TICKETED_LOCATIONS = {
    "Palacio da Bolsa",
    "Casa da Musica",
    "Planet Cork at WOW",
}

SCENIC_LOCATIONS = {
    "Porto Cathedral terrace",
    "Ribeira promenade",
    "Miradouro da Vitoria",
    "Palacio de Cristal Gardens",
    "Jardim do Morro",
    "Mosteiro da Serra do Pilar terrace",
    "Gaia riverfront",
}

MEAL_LOCATIONS = {
    "Bolhao Market",
    "daTerra Baixa",
    "Epoca Porto",
    "Em Carne Viva",
    "Root & Vine",
}

RAIN_LOCATIONS_BY_REGION = {
    "central": "Mercado Ferreira Borges",
    "west": "Bombarda galleries",
    "gaia": "WOW covered courtyard",
}

LOCATION_REGION = {
    location: region
    for region, locations in {
        "central": {
            "Sao Bento Station", "Bolhao Market", "Porto Cathedral terrace",
            "Palacio da Bolsa", "Ribeira promenade", "Miradouro da Vitoria",
            "daTerra Baixa", "Mercado Ferreira Borges",
        },
        "west": {
            "Igreja do Carmo", "Cordoaria Garden", "Casa da Musica",
            "Rotunda da Boavista", "Palacio de Cristal Gardens",
            "Bombarda galleries", "Epoca Porto", "Em Carne Viva",
        },
        "gaia": {
            "Jardim do Morro", "Mosteiro da Serra do Pilar terrace",
            "Planet Cork at WOW", "WOW covered courtyard", "Gaia riverfront",
            "Cais de Gaia craft market", "Root & Vine",
        },
    }.items()
    for location in locations
}


def split_sections(lines):
    positions = {line: lines.index(line) for line in EXPECTED_HEADINGS}
    sections = {}
    for current, following in zip(EXPECTED_HEADINGS, EXPECTED_HEADINGS[1:]):
        sections[current] = lines[positions[current] + 1 : positions[following]]
    sections[EXPECTED_HEADINGS[-1]] = lines[positions[EXPECTED_HEADINGS[-1]] + 1 :]
    return sections


class TravelPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PLAN.is_file():
            raise AssertionError("travel_plan.md must be created")
        cls.text = PLAN.read_text(encoding="utf-8")
        cls.lines = cls.text.splitlines()
        cls.sections = split_sections(cls.lines)

    def test_exact_headings_and_no_title_commentary(self):
        headings = [line for line in self.lines if line.startswith("#")]
        self.assertEqual(headings, EXPECTED_HEADINGS)
        self.assertEqual(self.lines[0], EXPECTED_HEADINGS[0])
        title_body = [line for line in self.sections[EXPECTED_HEADINGS[0]] if line.strip()]
        self.assertEqual(title_body, [])

    def test_corrected_destination_and_dates_replace_old_details(self):
        lowered = self.text.casefold()
        self.assertIn("porto", lowered)
        self.assertIn("sao bento", lowered)
        self.assertNotIn("lisbon", lowered)
        self.assertNotIn("rossio", lowered)
        self.assertNotIn("september", lowered)

    def test_each_day_is_substantive_and_structured(self):
        for heading in DAY_HEADINGS:
            with self.subTest(day=heading):
                content = [line for line in self.sections[heading] if line.strip()]
                self.assertTrue(all(line.startswith("- ") for line in content))

                timed = []
                rain = []
                for line in content:
                    match = TIMED_LINE.fullmatch(line)
                    if match:
                        timed.append(match)
                    elif line.startswith("- Rain alternative —"):
                        rain.append(line)
                    else:
                        self.fail(f"unexpected day-section line: {line}")
                self.assertGreaterEqual(len(timed), 6)
                self.assertEqual(len(rain), 1)
                self.assertEqual(content[-1], rain[0])

                minutes = [int(m["hour"]) * 60 + int(m["minute"]) for m in timed]
                self.assertTrue(all(0 <= int(m["hour"]) <= 23 for m in timed))
                self.assertTrue(all(0 <= int(m["minute"]) <= 59 for m in timed))
                self.assertTrue(all(first < second for first, second in zip(minutes, minutes[1:])))
                self.assertGreaterEqual(minutes[0], 9 * 60 + 30)
                self.assertLessEqual(minutes[-1], 20 * 60 + 30)

                locations = {m["location"] for m in timed}
                self.assertTrue(locations <= KNOWN_LOCATIONS)
                self.assertGreaterEqual(len(locations), 4)

                known_regions = {
                    LOCATION_REGION[location]
                    for location in locations
                    if location in LOCATION_REGION and location != "Sao Bento Station"
                }
                self.assertEqual(len(known_regions), 1)

                ticketed = [m for m in timed if "[ticketed indoor]" in m["activity"]]
                self.assertEqual(len(ticketed), 1)
                self.assertEqual("\n".join(content).count("[ticketed indoor]"), 1)

                scheduled_ticketed_locations = [
                    m for m in timed if m["location"] in TICKETED_LOCATIONS
                ]
                self.assertEqual(scheduled_ticketed_locations, ticketed)

                lunch = [m for m in timed if "lunch" in m["activity"].casefold()]
                dinner = [m for m in timed if "dinner" in m["activity"].casefold()]
                self.assertEqual(len(lunch), 1)
                self.assertEqual(len(dinner), 1)
                self.assertIn("vegetarian", lunch[0]["activity"].casefold())
                self.assertIn("vegetarian", dinner[0]["activity"].casefold())
                self.assertIn(lunch[0]["location"], MEAL_LOCATIONS)
                self.assertIn(dinner[0]["location"], MEAL_LOCATIONS)

                scenic = [m for m in timed if m["location"] in SCENIC_LOCATIONS]
                self.assertGreaterEqual(len(scenic), 1)
                scenic_terms = ("douro", "river view", "riverside", "riverfront")
                self.assertTrue(
                    any(term in m["activity"].casefold() for m in scenic for term in scenic_terms)
                )

                region = known_regions.pop()
                self.assertIn("replace", rain[0].casefold())
                self.assertIn(RAIN_LOCATIONS_BY_REGION[region].casefold(), rain[0].casefold())
                self.assertGreaterEqual(len(rain[0].split()), 8)

    def test_retained_interests_and_transport(self):
        lowered = self.text.casefold()
        self.assertIn("architecture", lowered)
        self.assertTrue("douro" in lowered or "river view" in lowered)

        all_timed = [
            TIMED_LINE.fullmatch(line)
            for heading in DAY_HEADINGS
            for line in self.sections[heading]
            if TIMED_LINE.fullmatch(line)
        ]
        self.assertIn("Bolhao Market", {match["location"] for match in all_timed})
        self.assertTrue(
            any("architecture" in match["activity"].casefold() for match in all_timed)
        )

        for heading in DAY_HEADINGS:
            timed_lines = [
                line for line in self.sections[heading] if TIMED_LINE.fullmatch(line)
            ]
            joined = " ".join(timed_lines).casefold()
            for forbidden in (
                "taxi", "uber", "ride-share", "rideshare", "private car",
                "rental car", "car service", "bicycle", "bike", "scooter",
                "motorcycle",
            ):
                self.assertNotIn(forbidden, joined)

    def test_practical_notes_preserve_every_other_constraint(self):
        notes = [line for line in self.sections["## Practical Notes"] if line.strip()]
        self.assertEqual(len(notes), 4)
        prefixes = ["- Travelers:", "- Food:", "- Mobility:", "- Transport:"]
        self.assertEqual([line.split(" ", 2)[:2] for line in notes], [p.split(" ", 2)[:2] for p in prefixes])

        joined = "\n".join(notes).casefold()
        self.assertRegex(joined, r"two adults|2 adults")
        self.assertIn("one traveler", joined)
        self.assertIn("vegetarian", joined)
        self.assertIn("no dietary restriction", joined)
        self.assertIn("steep", joined)
        self.assertIn("backtracking", joined)
        self.assertRegex(joined, r"relaxed|easy pace")
        self.assertRegex(joined, r"group|cluster")
        self.assertIn("09:30", joined)
        self.assertIn("20:30", joined)
        self.assertIn("walking", joined)
        self.assertIn("public transit", joined)


if __name__ == "__main__":
    unittest.main()
