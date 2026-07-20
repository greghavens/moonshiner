import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_behavior_seeds import audit  # noqa: E402


class BehaviorSeedCorpusTests(unittest.TestCase):
    def test_exact_curriculum_is_valid(self):
        errors, report = audit()
        self.assertEqual(errors, [])
        self.assertEqual(report["seed_count"], 2000)
        self.assertGreaterEqual(report["parallel_stages"], 700)
        self.assertEqual(report["category_counts"]["web-research"], 150)
        self.assertEqual(report["category_counts"]["persistent-memory"], 150)
        self.assertEqual(report["tag_counts"]["round:2"], 1000)
        self.assertEqual(report["tag_counts"]["source:breadth-reserve"], 400)
        self.assertEqual(report["tag_counts"]["source:benchmark-informed"], 600)

    def test_all_seeds_are_non_code(self):
        for path in (ROOT / "tasks" / "behavior-seeds").glob("behavior-*.json"):
            serialized = path.read_text()
            seed = json.loads(serialized)
            self.assertEqual(seed["kind"], "tool_behavior")
            self.assertNotIn(seed.get("world"), {"coding", "software", "filesystem"})
            self.assertNotIn("b" + "fcl", serialized.casefold())


if __name__ == "__main__":
    unittest.main()
