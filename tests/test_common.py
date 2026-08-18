"""Core helpers: secret/path scrubbing, seed fingerprinting, corpus loading."""
import pathlib
import json
import subprocess
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

    def test_does_not_manufacture_var_tilde_path(self):
        path = "/var/home/venom/work/file"
        scrubbed = common.scrub_text(path)
        self.assertNotIn("/var~", scrubbed)

    def test_scrubs_home_directory(self):
        from pathlib import Path
        home = str(Path.home())
        path = home + "/secrets/key.pem"
        scrubbed = common.scrub_text(path)
        self.assertNotIn(home, scrubbed)
        self.assertIn("/home/user/secrets/key.pem", scrubbed)


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
    def test_trace_only_project_uses_newer_bundled_corpus(self):
        self.assertFalse(common.prefer_active_corpus(
            True, False, "2026.07.20.1", "2026.07.21.1"))
        self.assertTrue(common.prefer_active_corpus(
            True, True, "2026.07.20.1", "2026.07.21.1"))

    def test_installed_seed_loader_uses_corpus_catalog_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = pathlib.Path(directory) / "corpus"
            seeds = corpus / "tasks" / "seeds"
            (seeds / "repo-seed").mkdir(parents=True)
            (seeds / "behavior-first").mkdir(parents=True)
            (seeds / "repo-seed" / "task.json").write_text(json.dumps({
                "id": "repo-seed", "category": "repo"
            }))
            (seeds / "behavior-first" / "task.json").write_text(json.dumps({
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
            with mock.patch.object(common, "SEEDS_DIR", seeds):
                ordered = common.load_seeds(include_holdout=True)
        self.assertEqual([seed["id"] for seed in ordered],
                         ["repo-seed", "behavior-first"])

    def test_one_loader_contains_every_seed_once_in_catalog_priority(self):
        self.assertFalse(hasattr(common, "load_behavior_seeds"))
        self.assertFalse(hasattr(common, "BEHAVIOR_SEEDS_DIR"))
        self.assertFalse((_ROOT / "tasks" / "behavior-seeds").exists())
        seeds = common.load_seeds(include_holdout=True)
        ids = [seed["id"] for seed in seeds]
        expected = {path.parent.name for path in common.SEEDS_DIR.glob("*/task.json")}
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

    def test_authored_seed_metadata_is_the_only_loaded_record(self):
        matches = [seed for seed in common.load_seeds(include_holdout=True)
                   if seed["id"] == "behavior-dependency-planning-0001"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["lang"], "English")
        self.assertNotIn("initial_state", matches[0])

    def test_every_pipeline_consumer_uses_the_same_loader(self):
        import build_dataset
        import generate_traces
        import seed_queue
        import trace_queue_cli
        for module in (build_dataset, generate_traces, seed_queue, trace_queue_cli):
            self.assertIs(module.load_seeds, common.load_seeds)

    def test_holdout_excluded_by_default(self):
        seeds = common.load_seeds(include_holdout=True)
        representatives = {
            seeds[0]["id"],
            seeds[-1]["id"],
        }
        with mock.patch.dict(common.CONFIG,
                             {"holdout_tasks": sorted(representatives)}):
            ids = {seed["id"] for seed in common.load_seeds()}
        self.assertTrue(representatives.isdisjoint(ids))

    def test_holdout_included_with_flag(self):
        seed_id = common.load_seeds(include_holdout=True)[0]["id"]
        with mock.patch.dict(common.CONFIG, {"holdout_tasks": [seed_id]}):
            ids = {seed["id"] for seed in common.load_seeds(include_holdout=True)}
        self.assertIn(seed_id, ids)

    def test_seeds_carry_one_source_path_and_only_filter(self):
        seeds = common.load_seeds()
        self.assertTrue(all("_dir" in seed and "_path" not in seed for seed in seeds))
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


class TheSandboxHomeIsNeverSeedContent(unittest.TestCase):
    """A run's throwaway HOME must not make a verified workspace look dirty.

    pwsh writes StartupProfileData and telemetry.uuid into $HOME the moment it
    starts. With HOME pointed inside the workspace, a seed that verified
    perfectly was rejected for an unclean tree, and every PowerShell seed in
    the corpus would have gone the same way.
    """

    def test_the_cache_sweep_removes_it(self):
        import subprocess
        from common import clear_runtime_caches
        with tempfile.TemporaryDirectory() as tmp:
            workspace = pathlib.Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
            cache = workspace / ".sandbox-home" / ".cache" / "powershell"
            cache.mkdir(parents=True)
            (cache / "telemetry.uuid").write_text("x")
            (workspace / "keep.ps1").write_text("# seed content")
            removed = clear_runtime_caches(workspace)
            self.assertIn(".sandbox-home", removed)
            self.assertFalse((workspace / ".sandbox-home").exists())
            self.assertTrue((workspace / "keep.ps1").exists(),
                            "seed content must survive the sweep")

    def test_every_runtime_home_is_excluded_from_captured_diffs(self):
        """A 254 MB patch of base85 cargo cache OOM-killed a whole queue."""
        from common import DIFF_EXCLUDE_PATTERNS, RUNTIME_CACHE_DIR_NAMES
        for name in (".sandbox-home", ".toolchain"):
            self.assertIn(f"**/{name}/**", DIFF_EXCLUDE_PATTERNS)
            self.assertIn(name, RUNTIME_CACHE_DIR_NAMES)


class PowerShellSandboxMounts(unittest.TestCase):
    def test_home_local_runtime_and_modules_use_neutral_read_only_mounts(self):
        import toolchains
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            workspace = root / "workspace"; workspace.mkdir()
            runtime = root / "home" / "powershell"; runtime.mkdir(parents=True)
            executable = runtime / "pwsh"; executable.write_text("")
            modules = root / "state" / "Modules"; modules.mkdir(parents=True)
            completed = subprocess.CompletedProcess([], 0, "", "")
            with mock.patch.object(common.shutil, "which", return_value="/usr/bin/bwrap"), \
                 mock.patch.object(toolchains, "powershell_runtime",
                                   return_value=executable), \
                 mock.patch.object(toolchains, "powershell_module_root",
                                   return_value=modules), \
                 mock.patch.object(toolchains, "effective_path",
                                   return_value="/usr/bin:/bin"), \
                 mock.patch("runtimes.base.run_with_inactivity_timeout",
                            return_value=completed) as run:
                common._sandboxed_command(["pwsh", "-Version"], workspace, 10)
        command = run.call_args.args[0]
        self.assertIn(["--ro-bind", str(runtime),
                       toolchains.POWERSHELL_RUNTIME_MOUNT],
                      [command[index:index + 3]
                       for index in range(len(command) - 2)])
        self.assertIn(["--ro-bind", str(modules),
                       toolchains.POWERSHELL_MODULES_MOUNT],
                      [command[index:index + 3]
                       for index in range(len(command) - 2)])
        path_index = command.index("PATH")
        self.assertTrue(command[path_index + 1].startswith(
            toolchains.POWERSHELL_RUNTIME_MOUNT + ":"))
        module_index = command.index("PSModulePath")
        self.assertTrue(command[module_index + 1].startswith(
            toolchains.POWERSHELL_MODULES_MOUNT + ":"))


class VerifyCommandExecution(unittest.TestCase):
    """A seed's acceptance line is a shell line when it uses shell syntax."""

    def test_a_plain_command_runs_as_argv(self):
        self.assertEqual(common.verify_argv("python3 -B .protected/verify.py"),
                         ["python3", "-B", ".protected/verify.py"])

    def test_a_quoted_pattern_keeps_its_single_argument(self):
        self.assertEqual(
            common.verify_argv("python3 -m unittest discover -p 'test_*.py'"),
            ["python3", "-m", "unittest", "discover", "-p", "test_*.py"])

    def test_a_chained_command_gets_a_shell(self):
        # Executed as argv, `&&` reaches go as an import path and the seed can
        # never pass: `malformed import path "&&": invalid char '&'`.
        command = "go vet ./... && go test -race ./..."
        self.assertEqual(common.verify_argv(command), ["/bin/sh", "-c", command])

    def test_a_leading_assignment_gets_a_shell(self):
        command = "cache=$(mktemp -d) && go test ./..."
        self.assertEqual(common.verify_argv(command), ["/bin/sh", "-c", command])

    def test_a_pipeline_gets_a_shell(self):
        command = "printf '%s' abc | sha256sum -c"
        self.assertEqual(common.verify_argv(command), ["/bin/sh", "-c", command])

    def test_unbalanced_quoting_is_left_for_the_shell_to_report(self):
        self.assertEqual(common.verify_argv('echo "unclosed'),
                         ["/bin/sh", "-c", 'echo "unclosed'])

