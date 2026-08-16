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

    def test_complete_oci_environment_uses_held_out_test_patch(self):
        directory = self.root / "oci-seed"
        patch = directory / "files" / ".moonshiner" / "test.patch"
        patch.parent.mkdir(parents=True)
        patch.write_text("diff --git a/test.py b/test.py\n")
        (directory / "task.json").write_text(json.dumps({
            "id": "oci-seed", "lang": "python", "category": "bug-fix",
            "prompt": "original prompt", "verify_cmd": "python3 -m unittest",
            "environment": {
                "type": "oci", "image": "registry.example/task@sha256:abc",
                "repository": "owner/repo", "base_commit": "a" * 40,
                "workspace": "/testbed",
                "test_patch": "files/.moonshiner/test.patch",
                "fail_to_pass": ["test_fix"], "pass_to_pass": [],
                "install_config": {
                    "base_image_name": "python", "docker_specs": None,
                    "install": [], "log_parser": "parse_log_pytest",
                    "test_cmd": "python3 -m unittest",
                },
            },
        }))
        self.assertIsNone(aud.check(directory))

    def test_pilot_seed_is_patch_exempt(self):
        exempt_id = sorted(aud.PILOT_EXEMPT)[0]
        self.assertIsNone(aud.check(make_complete(self.root, exempt_id, patch=False)))

    def test_incomplete_seed_fails(self):
        directory = make_complete(self.root, "z-seed")
        (directory / "task.json").unlink()
        self.assertIsNotNone(aud.check(directory))

    def test_runtime_home_makes_a_real_seed_fail_the_corpus_audit(self):
        directory = make_complete(self.root, "z-seed")
        skill = (directory / ".sandbox-home" / "codex" / "skills" /
                 ".system" / "openai-docs")
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("runtime state\n")
        self.assertEqual(
            "contains forbidden non-seed state: ['.sandbox-home']",
            aud.check(directory))

    def test_holdouts_are_patch_exempt(self):
        self.assertTrue(set(aud.PATCH_EXEMPT) >= set(aud.PILOT_EXEMPT))


if __name__ == "__main__":
    unittest.main()
