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
    def test_one_loader_contains_every_seed_once_in_catalog_priority(self):
        self.assertFalse(hasattr(common, "load_behavior_seeds"))
        seeds = common.load_seeds(include_holdout=True)
        ids = [seed["id"] for seed in seeds]
        expected = {path.parent.name for path in common.SEEDS_DIR.glob("*/task.json")}
        expected.update(path.stem for path in common.BEHAVIOR_SEEDS_DIR.glob(
            "behavior-*.json"))
        self.assertEqual(set(ids), expected)
        self.assertEqual(len(ids), len(set(ids)))
        first_repository_seed = next(
            index for index, seed in enumerate(seeds) if "_dir" in seed)
        last_tool_interaction = max(
            index for index, seed in enumerate(seeds)
            if common.uses_tool_interaction(seed))
        self.assertLess(last_tool_interaction, first_repository_seed)

    def test_holdout_excluded_by_default(self):
        holdout = set(common.CONFIG.get("holdout_tasks", []))
        self.assertTrue(holdout, "config should declare holdout tasks")
        ids = {seed["id"] for seed in common.load_seeds()}
        self.assertFalse(ids & holdout)

    def test_holdout_included_with_flag(self):
        holdout = set(common.CONFIG.get("holdout_tasks", []))
        ids = {seed["id"] for seed in common.load_seeds(include_holdout=True)}
        self.assertTrue(holdout <= ids)

    def test_seeds_carry_one_source_path_and_only_filter(self):
        seeds = common.load_seeds()
        self.assertTrue(all(("_dir" in seed) != ("_path" in seed) for seed in seeds))
        pick = common.load_seeds(only={"py-config-merge"})
        self.assertEqual([seed["id"] for seed in pick], ["py-config-merge"])

    def test_behavior_selection_by_category_and_tags(self):
        selected = common.select_seeds(
            categories={"parallel-same"}, tags={"execution:parallel"})
        self.assertTrue(selected)
        self.assertTrue(all(common.uses_tool_interaction(seed) for seed in selected))
        self.assertTrue(all(seed["category"] == "parallel-same" for seed in selected))

    def test_name_and_only_filters_apply_to_behavior_seeds(self):
        selected = common.select_seeds(
            only={"behavior-tool-selection-0001"}, name="Vendor onboarding")
        self.assertEqual([seed["id"] for seed in selected],
                         ["behavior-tool-selection-0001"])


if __name__ == "__main__":
    unittest.main()
