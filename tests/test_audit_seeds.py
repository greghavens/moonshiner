"""Seed-integrity audit: completeness + reference-patch requirement/exemptions."""
import json
import pathlib
import sys
import tempfile
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

import audit_seeds as aud  # noqa: E402


def make_complete(parent, seed_id, patch=True):
    directory = parent / seed_id
    (directory / "files").mkdir(parents=True)
    (directory / "files" / "t.py").write_text("x")
    (directory / "task.json").write_text(json.dumps({
        "id": seed_id, "lang": "python", "category": "impl", "prompt": "p",
        "verify_cmd": "v", "test_files": ["t.py"]}))
    if patch:
        (directory / "reference_fix.patch").write_text("diff --git a/x b/x\n")
    return directory


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_complete_with_patch_passes(self):
        self.assertIsNone(aud.check(make_complete(self.root, "z-seed", patch=True)))

    def test_missing_patch_fails_for_non_exempt(self):
        why = aud.check(make_complete(self.root, "z-seed", patch=False))
        self.assertIn("reference_fix.patch", why)

    def test_pilot_seed_is_patch_exempt(self):
        exempt_id = sorted(aud.PILOT_EXEMPT)[0]
        self.assertIsNone(aud.check(make_complete(self.root, exempt_id, patch=False)))

    def test_incomplete_seed_fails(self):
        directory = make_complete(self.root, "z-seed")
        (directory / "task.json").unlink()
        self.assertIsNotNone(aud.check(directory))

    def test_holdouts_are_patch_exempt(self):
        self.assertTrue(set(aud.PATCH_EXEMPT) >= set(aud.PILOT_EXEMPT))


if __name__ == "__main__":
    unittest.main()
