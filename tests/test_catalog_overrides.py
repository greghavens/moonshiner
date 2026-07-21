"""Exact catalog overrides correct known records without classification heuristics."""
import json
import pathlib
import sys
import tempfile
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import corpus  # noqa: E402


class CatalogOverrideTests(unittest.TestCase):
    def test_exact_security_override_removes_invalid_full_distill_program(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            seeds = root / "tasks" / "seeds"
            task = seeds / "csharp-authz-object-scope"
            task.mkdir(parents=True)
            (task / "task.json").write_text(json.dumps({
                "id": "csharp-authz-object-scope",
                "prompt": "Correct object-level authorization scope.",
                "program": "full-distill",
                "category": "authorization",
                "training_tags": ["object-authorization"],
            }))
            _, data = corpus.catalog(seeds)
        items = [item for values in data["categories"].values() for item in values]
        self.assertEqual(items[0]["program"], "Security")
        self.assertIn("Security", data["programs"])
        self.assertNotIn("full-distill", data["programs"])


if __name__ == "__main__":
    unittest.main()
