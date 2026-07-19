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
        self.assertEqual(report["seed_count"], 1000)
        self.assertGreaterEqual(report["parallel_stages"], 370)
        self.assertEqual(report["category_counts"]["web-research"], 100)
        self.assertEqual(report["category_counts"]["persistent-memory"], 100)

    def test_all_seeds_are_non_code(self):
        for path in (ROOT / "tasks" / "behavior-seeds").glob("behavior-*.json"):
            seed = json.loads(path.read_text())
            self.assertEqual(seed["kind"], "tool_behavior")
            self.assertNotIn(seed.get("world"), {"coding", "software", "filesystem"})


if __name__ == "__main__":
    unittest.main()
