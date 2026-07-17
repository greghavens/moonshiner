"""Core helpers: secret/path scrubbing, seed fingerprinting, corpus loading."""
import pathlib
import sys
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

import common  # noqa: E402


class ScrubText(unittest.TestCase):
    def test_redacts_secret_and_runtime_path(self):
        text = ("token AKIAIOSFODNN7EXAMPLE in "
                "/var/tmp/moonshiner-pi-runtime/run-abc.1/x")
        out = common.scrub_text(text)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", out)
        self.assertIn("[REDACTED_SECRET]", out)
        self.assertNotIn("moonshiner-pi-runtime", out)
        self.assertIn("/runtime/x", out)

    def test_clean_text_is_unchanged(self):
        self.assertEqual(common.scrub_text("hello world"), "hello world")


class Fingerprint(unittest.TestCase):
    def test_stable_and_distinct(self):
        seeds = common.load_seeds()
        self.assertGreater(len(seeds), 100)
        first, second = seeds[0], seeds[1]
        self.assertEqual(common.seed_fingerprint(first),
                         common.seed_fingerprint(first))
        self.assertNotEqual(common.seed_fingerprint(first),
                            common.seed_fingerprint(second))


class LoadSeeds(unittest.TestCase):
    def test_holdout_excluded_by_default(self):
        holdout = set(common.CONFIG.get("holdout_tasks", []))
        self.assertTrue(holdout, "config should declare holdout tasks")
        ids = {seed["id"] for seed in common.load_seeds()}
        self.assertFalse(ids & holdout)

    def test_holdout_included_with_flag(self):
        holdout = set(common.CONFIG.get("holdout_tasks", []))
        ids = {seed["id"] for seed in common.load_seeds(include_holdout=True)}
        self.assertTrue(holdout <= ids)

    def test_seeds_carry_dir_and_only_filter(self):
        seeds = common.load_seeds()
        self.assertTrue(all("_dir" in seed for seed in seeds[:5]))
        pick = common.load_seeds(only={"py-config-merge"})
        self.assertEqual([seed["id"] for seed in pick], ["py-config-merge"])


if __name__ == "__main__":
    unittest.main()
