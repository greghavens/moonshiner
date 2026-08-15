import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "shopping-note.md"


def table_cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


class ShoppingNoteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = OUTPUT.read_text(encoding="utf-8").strip()
        cls.blocks = re.split(r"\n\s*\n", cls.text)

    def test_contains_only_table_then_recommendation(self):
        self.assertEqual(len(self.blocks), 2)
        self.assertTrue(self.blocks[0].lstrip().startswith("|"))
        self.assertTrue(self.blocks[1].strip())
        self.assertNotRegex(self.text, r"(?m)^\s*#{1,6}\s+")

    def test_exact_columns_and_corrected_model_order(self):
        lines = self.blocks[0].splitlines()
        self.assertEqual(len(lines), 5)
        self.assertEqual(
            table_cells(lines[0]),
            [
                "Model",
                "Daily capacity",
                "First batch",
                "Dimensions",
                "Warranty",
                "Best for",
            ],
        )
        separator = table_cells(lines[1])
        self.assertEqual(len(separator), 6)
        self.assertTrue(all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator))
        self.assertEqual(
            [table_cells(line)[0] for line in lines[2:]],
            ["GlacierGo Lite", "ArcticDrop Mini", "PolarPeak Compact"],
        )

    def test_table_uses_all_required_source_facts(self):
        lines = self.blocks[0].splitlines()[2:]
        self.assertEqual(
            [table_cells(line) for line in lines],
            [
                [
                    "GlacierGo Lite",
                    "26 lb/day",
                    "7 minutes",
                    "8.7 × 11.6 × 12.3 in",
                    "18 months",
                    "fastest first batch",
                ],
                [
                    "ArcticDrop Mini",
                    "28 lb/day",
                    "9 minutes",
                    "9.1 × 12.2 × 12.8 in",
                    "2 years",
                    "highest daily output",
                ],
                [
                    "PolarPeak Compact",
                    "24 lb/day",
                    "8 minutes",
                    "8.5 × 11.0 × 12.0 in",
                    "1 year",
                    "narrowest counter space",
                ],
            ],
        )

    def test_recommendation_is_substantive_and_selects_one_top_pick(self):
        if len(self.blocks) != 2:
            self.fail("shopping-note.md must contain a table followed by one recommendation paragraph")
        recommendation = self.blocks[1]
        self.assertIn("GlacierGo Lite", recommendation)
        self.assertIn("top pick", recommendation.lower())
        self.assertRegex(
            recommendation,
            r"(?is)(?:GlacierGo Lite\b[^.!?]{0,120}\btop pick\b|\btop pick\b[^.!?]{0,120}\bGlacierGo Lite\b)",
        )
        self.assertNotRegex(recommendation, r"(?i)\btop picks\b")
        for other_model in ("ArcticDrop Mini", "PolarPeak Compact"):
            self.assertNotRegex(
                recommendation,
                rf"(?is)(?:{re.escape(other_model)}\b[^.!?]{{0,60}}\b(?:is|are|as)\b[^.!?]{{0,30}}\btop pick\b|\btop pick\b[^.!?]{{0,30}}(?:\bis\b|\bare\b|:)\s*(?:the\s+)?{re.escape(other_model)}\b)",
            )
        self.assertIn("7 minutes", recommendation)
        facts_used = sum(
            fact in recommendation
            for fact in ("7 minutes", "26 lb/day", "18 months", "8.7 × 11.6 × 12.3 in")
        )
        self.assertGreaterEqual(facts_used, 2)

    def test_omits_superseded_and_prohibited_details(self):
        lowered = self.text.lower()
        self.assertNotIn("$", self.text)
        for forbidden_word in (
            "price",
            "rating",
            "dba",
            "noise",
            "methodology",
            "footnote",
        ):
            self.assertNotRegex(lowered, rf"\b{forbidden_word}\b")
        for forbidden_phrase in (
            "runner-up",
            "runner up",
            "second place",
            "third place",
            "second-best",
            "third-best",
            "next best",
        ):
            self.assertNotIn(forbidden_phrase, lowered)
        self.assertNotRegex(lowered, r"\b(?:109|94|99)(?:\.\d{2})?\b")
        self.assertNotRegex(lowered, r"\b4\.[456](?:\s*(?:/|out of)\s*5)?\b")
        self.assertNotRegex(lowered, r"\b(?:39|41|43)\s*(?:dba?|decibels?)\b")
        self.assertNotRegex(self.text, r"\[(?:\^[^\]]+|\d+)\]")


if __name__ == "__main__":
    unittest.main()
