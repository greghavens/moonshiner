"""Seed import: completeness detection and canonical/fallback resolution."""
import json
import pathlib
import sys
import tempfile
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

import import_seeds as imp  # noqa: E402


def make_seed(parent, seed_id="s1", test_files=("t.py",),
              with_task=True, with_files=True, bad_id=False):
    directory = parent / seed_id
    (directory / "files").mkdir(parents=True)
    if with_files:
        for name in test_files:
            (directory / "files" / name).write_text("x")
    if with_task:
        task = {"id": "other" if bad_id else seed_id, "lang": "python",
                "category": "impl", "prompt": "p", "verify_cmd": "v",
                "test_files": list(test_files)}
        (directory / "task.json").write_text(json.dumps(task))
    return directory


class SeedComplete(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_complete_seed(self):
        self.assertIsNone(imp.seed_complete(make_seed(self.root)))

    def test_absent_directory(self):
        self.assertIsNotNone(imp.seed_complete(self.root / "nope"))

    def test_missing_task_json(self):
        self.assertIsNotNone(imp.seed_complete(make_seed(self.root, with_task=False)))

    def test_id_mismatch(self):
        self.assertIsNotNone(imp.seed_complete(make_seed(self.root, bad_id=True)))

    def test_missing_protected_test_file(self):
        self.assertIsNotNone(imp.seed_complete(make_seed(self.root, with_files=False)))


class Resolve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.primary = base / "canonical"
        self.primary.mkdir()
        self.fallback = base / "fallback"
        self.fallback.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_prefers_complete_canonical(self):
        make_seed(self.primary, "a")
        make_seed(self.fallback, "a")
        chosen, prov, _ = imp.resolve("a", self.primary, self.fallback)
        self.assertEqual(prov, "primary")
        self.assertEqual(chosen, self.primary / "a")

    def test_falls_back_when_canonical_broken(self):
        make_seed(self.primary, "a", with_task=False)   # broken canonical
        make_seed(self.fallback, "a")                    # complete fallback
        chosen, prov, _ = imp.resolve("a", self.primary, self.fallback)
        self.assertEqual(prov, "fallback")
        self.assertEqual(chosen, self.fallback / "a")

    def test_invalid_when_broken_in_both(self):
        make_seed(self.primary, "a", with_task=False)
        make_seed(self.fallback, "a", with_task=False)
        _, prov, reason = imp.resolve("a", self.primary, self.fallback)
        self.assertEqual(prov, "invalid")
        self.assertTrue(reason)

    def test_invalid_when_no_fallback_configured(self):
        make_seed(self.primary, "a", with_task=False)
        _, prov, _ = imp.resolve("a", self.primary, None)
        self.assertEqual(prov, "invalid")


if __name__ == "__main__":
    unittest.main()
