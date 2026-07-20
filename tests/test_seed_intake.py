import json
import tempfile
import unittest
from pathlib import Path

from seed_intake import accepted_seed_ids


class AcceptedSeedIntakeTests(unittest.TestCase):
    def test_reads_only_marker_seed_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "A.json").write_text(json.dumps({"seed_ids": ["one", "two"]}))
            (root / "B.json").write_text(json.dumps({"seed_ids": ["three"]}))
            self.assertEqual(accepted_seed_ids(root), {"one", "two", "three"})

    def test_missing_or_empty_markers_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                accepted_seed_ids(root / "missing")
            with self.assertRaises(ValueError):
                accepted_seed_ids(root)

    def test_duplicate_or_malformed_ids_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "A.json").write_text(json.dumps({"seed_ids": ["same"]}))
            (root / "B.json").write_text(json.dumps({"seed_ids": ["same"]}))
            with self.assertRaises(ValueError):
                accepted_seed_ids(root)
            (root / "B.json").write_text(json.dumps({"seed_ids": []}))
            with self.assertRaises(ValueError):
                accepted_seed_ids(root)


if __name__ == "__main__":
    unittest.main()
