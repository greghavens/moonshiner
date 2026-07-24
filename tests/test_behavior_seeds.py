import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
class BehaviorSeedCorpusTests(unittest.TestCase):
    def test_all_seeds_are_non_code(self):
        for path in (ROOT / "tasks" / "seeds").glob("*/task.json"):
            serialized = path.read_text()
            seed = json.loads(serialized)
            if seed.get("kind") != "tool_behavior":
                continue
            self.assertEqual(seed["kind"], "tool_behavior")
            self.assertNotIn(seed.get("world"), {"coding", "software", "filesystem"})
            self.assertNotIn("b" + "fcl", serialized.casefold())


if __name__ == "__main__":
    unittest.main()
