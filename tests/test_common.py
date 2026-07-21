"""Core helpers: secret/path scrubbing, seed fingerprinting, corpus loading."""
import pathlib
import json
import sys
import tempfile
import unittest
from unittest import mock

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
    def test_installed_seed_loader_uses_corpus_catalog_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = pathlib.Path(directory) / "corpus"
            seeds = corpus / "tasks" / "seeds"
            behavior = corpus / "tasks" / "behavior-seeds"
            (seeds / "repo-seed").mkdir(parents=True)
            behavior.mkdir(parents=True)
            (seeds / "repo-seed" / "task.json").write_text(json.dumps({
                "id": "repo-seed", "category": "repo"
            }))
            (behavior / "behavior-first.json").write_text(json.dumps({
                "id": "behavior-first", "category": "behavior"
            }))
            (corpus / "SEED_CATALOG.json").write_text(json.dumps({
                "programs": {
                    "Repository": {"priority": 0},
                    "Behavior": {"priority": 1},
                },
                "categories": {
                    "repo": [{"id": "repo-seed", "program": "Repository"}],
                    "behavior": [{"id": "behavior-first", "program": "Behavior"}],
                },
            }))
            with mock.patch.object(common, "SEEDS_DIR", seeds), \
                    mock.patch.object(common, "BEHAVIOR_SEEDS_DIR", behavior):
                ordered = common.load_seeds(include_holdout=True)
        self.assertEqual([seed["id"] for seed in ordered],
                         ["repo-seed", "behavior-first"])

    def test_single_file_seed_needs_no_repository_fixture_directory(self):
        self.assertIsNone(common._seed_files({"id": "catalog-seed", "_path": pathlib.Path("seed.json")}))

    def test_one_loader_contains_every_seed_once_in_catalog_priority(self):
        self.assertFalse(hasattr(common, "load_behavior_seeds"))
        seeds = common.load_seeds(include_holdout=True)
        ids = [seed["id"] for seed in seeds]
        expected = {path.parent.name for path in common.SEEDS_DIR.glob("*/task.json")}
        expected.update(path.stem for path in common.BEHAVIOR_SEEDS_DIR.glob(
            "behavior-*.json"))
        self.assertEqual(set(ids), expected)
        self.assertEqual(len(ids), len(set(ids)))
        catalog = json.loads(
            (common.SEEDS_DIR.parents[1] / "SEED_CATALOG.json").read_text())
        programs = catalog.get("programs") or {}
        ranks = {item["id"]: int(programs.get(item.get("program"), {}).get(
                    "priority", 1_000_000))
                 for items in (catalog.get("categories") or {}).values()
                 for item in items}
        observed = [ranks.get(seed["id"], 1_000_000) for seed in seeds]
        self.assertEqual(observed, sorted(observed))

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

    def test_embedded_tool_results_are_never_executable_seed_contracts(self):
        reason = common.synthetic_tool_contract({
            "tool_results": {"search_1": {"records": []}},
            "initial_state": {},
        })
        self.assertIn("tool_results", reason)
        self.assertIn("initial_state", reason)

    def test_trace_pipeline_has_no_synthetic_dispatcher(self):
        source = (_ROOT / "src" / "trace_pipeline.py").read_text()
        self.assertNotIn("behavior_trace", source)
        self.assertNotIn("uses_tool_interaction", source)


if __name__ == "__main__":
    unittest.main()
