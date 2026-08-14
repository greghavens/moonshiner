"""Executable contracts for capability-based native trace-harness selection.

The tests deliberately use real files, SQLite ledgers, subprocesses, and
Runtime implementations.  They do not mock a harness, queue, or pipeline
function, and they never contact a model or the network.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import audit_seeds  # noqa: E402
import build_dataset  # noqa: E402
import corpus  # noqa: E402
import expand_next_steps  # noqa: E402
import export_hf_card  # noqa: E402
import export_hf_next_steps  # noqa: E402
import generate_traces  # noqa: E402
import publish  # noqa: E402
import runtimes  # noqa: E402
import screen_traces  # noqa: E402
import trace_pipeline  # noqa: E402
import validate_hf_export  # noqa: E402
from runtimes.base import ReviewResult, Runtime, TraceResult  # noqa: E402


TEST_TMP = pathlib.Path(os.environ.get(
    "TMPDIR", ROOT / ".moonshiner" / "test-tmp")).resolve()
TEST_TMP.mkdir(parents=True, exist_ok=True)


def accepted_verdict() -> dict:
    return {
        **{name: {"found": False, "detail": ""}
           for name in screen_traces.REVIEW_CATEGORIES},
        "requirements": [{"requirement": "complete the task", "status": "met"}],
        "verdict": "accept",
    }


class _ExecutableRuntime(Runtime):
    trace_formats = ("codex-exec-events",)
    provided_capabilities: frozenset[str] = frozenset()
    calls: list[dict] = []

    def trace_capabilities(self) -> frozenset[str]:
        return self.provided_capabilities

    def preflight(self, *, require_auth: bool = False) -> None:
        cli = pathlib.Path(str(self.runtime_config.get("cli") or ""))
        if not cli.is_file() or not os.access(cli, os.X_OK):
            raise SystemExit(f"test harness is not installed: {cli}")
        if require_auth and self.runtime_config.get("authenticated", True) is False:
            raise RuntimeError("test harness is not authenticated")

    def run_trace(self, seed: dict, workspace: pathlib.Path, *,
                  out_dir: pathlib.Path, system_prompt: str, prompt: str,
                  interaction: list[str] | None = None,
                  security: bool = False,
                  tools: list[str] | None = None) -> TraceResult:
        type(self).calls.append({
            "seed": seed["id"], "prompt": prompt,
            "interaction": interaction, "workspace": str(workspace),
        })
        out_dir.mkdir(parents=True, exist_ok=True)
        raw = out_dir / f"{seed['id']}.{self.name}.jsonl"
        raw.write_text(
            json.dumps({"type": "thread.started", "thread_id": self.name}) + "\n" +
            json.dumps({"type": "item.completed", "item": {
                "type": "agent_message", "text": "completed"}}) + "\n" +
            json.dumps({"type": "turn.completed", "usage": {}}) + "\n")
        failure = self.runtime_config.get("failure")
        if failure == "timeout":
            return TraceResult(
                raw_path=raw, trace_format=self.trace_formats[0],
                return_code=None, timed_out=True, stream_success=False,
                model_attested=True, error="inactive runtime")
        if failure == "nonzero":
            return TraceResult(
                raw_path=raw, trace_format=self.trace_formats[0],
                return_code=7, stream_success=False,
                model_attested=True, error="runtime exited 7")
        (workspace / "answer.txt").write_text("completed\n")
        return TraceResult(
            raw_path=raw, trace_format=self.trace_formats[0],
            return_code=0, stream_success=True,
            observed_model=str(self.role["model"]),
            observed_models=[str(self.role["model"])],
            model_attested=True, usage={"input_tokens": 1},
            provenance={"native_test_runtime": self.name})

    def run_review(self, instruction: str, workspace: pathlib.Path, *,
                   out_dir: pathlib.Path, schema: dict | None = None,
                   read_only: bool = True) -> ReviewResult:
        return ReviewResult(
            raw_text=json.dumps(accepted_verdict()), verdict=accepted_verdict(),
            return_code=0, observed_model=str(self.role["model"]),
            model_attested=True)

    @staticmethod
    def parse_stream(path: pathlib.Path, workspace: str | None
                     ) -> tuple[list[dict], dict]:
        return ([{"role": "assistant", "content": "completed"}], {})


class HarnessA(_ExecutableRuntime):
    name = "test-harness-a"
    provided_capabilities = frozenset({"workspace_write", "alpha"})
    calls: list[dict] = []


class HarnessB(_ExecutableRuntime):
    name = "test-harness-b"
    provided_capabilities = frozenset(
        {"workspace_write", "multi_turn", "live_web_research"})
    calls: list[dict] = []


class HarnessC(_ExecutableRuntime):
    name = "test-harness-c"
    provided_capabilities = frozenset({"workspace_write", "multi_turn"})
    calls: list[dict] = []


class AcceptingJudge(_ExecutableRuntime):
    name = "test-judge"
    calls: list[dict] = []


class CapabilityResolverContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="capability-selection-", dir=TEST_TMP)
        self.root = pathlib.Path(self.temp.name)
        self.cli = self.root / "installed-harness"
        self.cli.write_text("#!/bin/sh\nexit 0\n")
        self.cli.chmod(0o700)
        self.original_registry = dict(runtimes.REGISTRY)
        runtimes.REGISTRY.update({
            HarnessA.name: HarnessA,
            HarnessB.name: HarnessB,
            HarnessC.name: HarnessC,
        })
        for runtime in (HarnessA, HarnessB, HarnessC):
            runtime.calls.clear()

    def tearDown(self):
        runtimes.REGISTRY.clear()
        runtimes.REGISTRY.update(self.original_registry)
        self.temp.cleanup()

    def config(self, order: list[str], *, missing: set[str] | None = None,
               failures: dict[str, str] | None = None) -> dict:
        missing = missing or set()
        failures = failures or {}
        configured = {}
        for name in (HarnessA.name, HarnessB.name, HarnessC.name):
            configured[name] = {
                "cli": str(self.root / f"missing-{name}")
                       if name in missing else str(self.cli),
            }
            if name in failures:
                configured[name]["failure"] = failures[name]
        return {
            "teacher": {"runtime": HarnessA.name, "model": "same-model",
                        "reasoning": "xhigh", "timeout_s": 30},
            "runtimes": configured,
            "pipeline": {"trace": {"harness_order": order}},
        }

    def resolve(self, seed: dict, config: dict):
        configured = runtimes.get_teacher(config)
        return runtimes.resolve_trace_harness(
            seed, configured_teacher=configured, config=config)

    def test_seed_without_capabilities_keeps_configured_teacher(self):
        config = self.config([HarnessB.name, HarnessC.name])
        configured = runtimes.get_teacher(config)
        selected, resolution = runtimes.resolve_trace_harness(
            {"id": "plain"}, configured_teacher=configured, config=config)
        self.assertIs(selected, configured)
        self.assertEqual(selected.name, HarnessA.name)
        self.assertEqual(resolution["mode"], "configured_default")

    def test_required_capabilities_filter_incompatible_harnesses(self):
        selected, _ = self.resolve({
            "id": "required",
            "required_harness_capabilities": ["live_web_research"],
        }, self.config([HarnessA.name, HarnessC.name, HarnessB.name]))
        self.assertEqual(selected.name, HarnessB.name)

    def test_preference_score_then_order_is_deterministic(self):
        seed = {
            "id": "preferred",
            "required_harness_capabilities": ["workspace_write"],
            "preferred_harness_capabilities": [
                "multi_turn", "live_web_research"],
        }
        selected, resolution = self.resolve(
            seed, self.config([HarnessC.name, HarnessB.name, HarnessA.name]))
        self.assertEqual(selected.name, HarnessB.name)
        self.assertEqual(resolution["matched_preferred"],
                         ["multi_turn", "live_web_research"])

        tie_seed = {
            "id": "tie",
            "required_harness_capabilities": ["workspace_write"],
            "preferred_harness_capabilities": ["multi_turn"],
        }
        selected, _ = self.resolve(
            tie_seed, self.config([HarnessC.name, HarnessB.name]))
        self.assertEqual(selected.name, HarnessC.name)

    def test_missing_preferred_harness_uses_only_compatible_fallback(self):
        seed = {
            "id": "missing",
            "required_harness_capabilities": ["workspace_write"],
            "preferred_harness_capabilities": ["live_web_research"],
        }
        selected, _ = self.resolve(
            seed,
            self.config([HarnessB.name, HarnessC.name, HarnessA.name],
                        missing={HarnessB.name}))
        self.assertEqual(selected.name, HarnessC.name)
        self.assertTrue(
            {"workspace_write"} <= set(selected.trace_capabilities()))

    def test_non_capability_identity_never_changes_selection(self):
        config = self.config([HarnessC.name, HarnessB.name])
        base = {
            "required_harness_capabilities": ["workspace_write"],
            "preferred_harness_capabilities": ["multi_turn"],
        }
        identities = [
            {"id": "vcf90-9999", "category": "Security",
             "training_tags": ["provider:imaginary"], "model": "other",
             "provider": "elsewhere", "version": "999", "prompt": "Claude Codex Pi"},
            {"id": "ordinary", "category": "Building", "training_tags": [],
             "model": "same-model", "provider": "local", "version": "1",
             "prompt": "unrelated"},
        ]
        selected = [self.resolve({**base, **identity}, config)[0].name
                    for identity in identities]
        self.assertEqual(selected, [HarnessC.name, HarnessC.name])

    def _seed(self, seed_id: str, **metadata) -> dict:
        directory = self.root / seed_id
        files = directory / "files"
        files.mkdir(parents=True)
        (files / "baseline.txt").write_text("baseline\n")
        seed = {
            "id": seed_id, "lang": "English", "category": "Building",
            "prompt": "\x00Exact prompt\r\nwith trailing spaces  \n",
            "verify_cmd": "test -f answer.txt", "_dir": directory,
            **metadata,
        }
        (directory / "task.json").write_text(json.dumps({
            key: value for key, value in seed.items() if key != "_dir"}))
        return seed

    def test_trace_task_passes_prompt_byte_for_byte_to_selected_adapter(self):
        seed = self._seed(
            "prompt-exact",
            required_harness_capabilities=["multi_turn"],
            preferred_harness_capabilities=["live_web_research"])
        traces = self.root / "prompt-traces"
        configured = runtimes.get_teacher(
            self.config([HarnessC.name, HarnessB.name]))
        record = generate_traces.trace_task(
            seed, configured, force=True, traces_root=traces)
        self.assertEqual(HarnessB.calls[-1]["prompt"], seed["prompt"])
        self.assertEqual(record["prompt"], seed["prompt"])
        self.assertEqual(
            record["teacher"]["provenance"]["capability_resolution"]["runtime"],
            HarnessB.name)

    def test_paid_runtime_failure_never_invokes_second_harness_or_judge(self):
        seed = self._seed(
            "runtime-failure",
            required_harness_capabilities=["workspace_write"])
        config = self.config(
            [HarnessA.name, HarnessB.name],
            failures={HarnessA.name: "nonzero"})
        configured = runtimes.get_teacher(config)
        with self.assertRaises(runtimes.TraceHarnessInfrastructureFailure):
            generate_traces.trace_task(
                seed, configured, force=True,
                traces_root=self.root / "failure-traces")
        self.assertEqual(len(HarnessA.calls), 1)
        self.assertEqual(HarnessB.calls, [])
        self.assertEqual(AcceptingJudge.calls, [])


class QueueFailClosedContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="capability-queue-", dir=TEST_TMP)
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_no_compatible_harness_exits_78_pending_without_attempt_or_next_claim(self):
        bundle = self.root / "bundle"
        project = self.root / "project"
        state = self.root / "state"
        model_data = self.root / "model-data"
        for directory in (bundle / "tasks" / "seeds", bundle / "schemas",
                          project, state, model_data, self.root / "tmp"):
            directory.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "schemas" / "review_verdict.schema.json",
                     bundle / "schemas" / "review_verdict.schema.json")
        config = {
            "teacher": {"runtime": "codex", "model": "not-called",
                        "reasoning": "xhigh", "timeout_s": 30},
            "judge": {"runtime": "codex", "model": "not-called",
                      "reasoning": "xhigh", "timeout_s": 30},
            "runtimes": {
                "codex": {"cli": str(self.root / "missing-codex")},
                "missing-runtime": {"cli": str(self.root / "missing-runtime")},
            },
            "pipeline": {"trace": {
                "harness_order": ["missing-runtime"], "max_attempts": 3,
                "workers": 1, "step_down_reasoning_on_failure": True,
                "retry_order": "immediate"}},
            "publish": {"hf_dataset": None}, "holdout_tasks": [],
        }
        (bundle / "config.json").write_text(json.dumps(config))
        categories = {"Building": []}
        for index, seed_id in enumerate(("capability-first", "must-stay-pending")):
            directory = bundle / "tasks" / "seeds" / seed_id
            (directory / "files").mkdir(parents=True)
            task = {
                "id": seed_id, "lang": "English", "category": "Building",
                "prompt": "must not call a model", "verify_cmd": "true",
            }
            if index == 0:
                task["required_harness_capabilities"] = ["never-provided"]
            (directory / "task.json").write_text(json.dumps(task))
            (directory / "reference_fix.patch").write_text("\n")
            categories["Building"].append({
                "id": seed_id, "program": "Test", "category": "Building"})
        (bundle / "SEED_CATALOG.json").write_text(json.dumps({
            "programs": {"Test": {"priority": 0}}, "categories": categories}))

        environment = dict(os.environ)
        environment.update({
            "MOONSHINER_BUNDLE_ROOT": str(bundle),
            "MOONSHINER_HOME": str(state),
            "XDG_DATA_HOME": str(model_data),
            "TMPDIR": str(self.root / "tmp"),
            "MOONSHINER_SINGLE_TRACE": "1",
            "PYTHONPATH": str(ROOT / "src"),
        })
        completed = subprocess.run(
            [sys.executable, "-c",
             "import trace_pipeline; raise SystemExit("
             "trace_pipeline.main(['--all','--yes','--workers','1']))"],
            cwd=project, env=environment, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 78, completed.stderr)
        ledger = state / "runs" / "moonshiner.sqlite3"
        self.assertTrue(ledger.is_file())
        db = sqlite3.connect(ledger)
        try:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT model_calls FROM runs").fetchone()[0], 0)
            rows = db.execute(
                "SELECT seed_id,status,attempts FROM jobs ORDER BY seed_id").fetchall()
        finally:
            db.close()
        self.assertEqual(rows, [
            ("capability-first", "pending", 0),
            ("must-stay-pending", "pending", 0),
        ])

    def test_parent_queue_failure_does_not_launch_another_seed(self):
        fake_python = self.root / "runtime" / "bin" / "python"
        fake_python.parent.mkdir(parents=True)
        fake_python.write_text("")
        executable = fake_python.parent / "moonshiner"
        log = self.root / "claims.jsonl"
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "with open(os.environ['CLAIM_LOG'], 'a') as out:\n"
            "    out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "raise SystemExit(78)\n")
        executable.chmod(0o700)
        original_executable = trace_pipeline.sys.executable
        original_log = os.environ.get("CLAIM_LOG")
        trace_pipeline.sys.executable = str(fake_python)
        os.environ["CLAIM_LOG"] = str(log)
        try:
            args = type("Args", (), {"max_attempts": 3, "workers": 1})()
            result = trace_pipeline._run_individual_trace_jobs(
                [{"id": "first"}, {"id": "second"}], args, 1)
        finally:
            trace_pipeline.sys.executable = original_executable
            if original_log is None:
                os.environ.pop("CLAIM_LOG", None)
            else:
                os.environ["CLAIM_LOG"] = original_log
        self.assertEqual(result, 78)
        claims = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0][claims[0].index("--only") + 1], "first")


class SharedPipelineContract(CapabilityResolverContract):
    def test_multiple_harnesses_traverse_the_same_downstream_functions(self):
        traces = self.root / "shared-traces"
        judge_config = {
            "judge": {"runtime": AcceptingJudge.name, "model": "judge-model",
                      "reasoning": "xhigh"},
            "runtimes": {AcceptingJudge.name: {"cli": str(self.cli)}},
        }
        judge = AcceptingJudge(judge_config, judge_config["judge"])
        published_rows = []
        for runtime_name, requirement in (
                (HarnessA.name, "alpha"),
                (HarnessB.name, "live_web_research")):
            seed = self._seed(
                f"shared-{runtime_name}",
                required_harness_capabilities=[requirement])
            config = self.config([HarnessA.name, HarnessB.name])
            configured = runtimes.get_teacher(config)
            info = generate_traces.trace_task(
                seed, configured, force=True, traces_root=traces)
            review = screen_traces.screen(seed, judge, traces_root=traces)
            self.assertTrue(review["accepted"])
            whole, error = build_dataset.build_row(seed, info, traces_root=traces)
            self.assertIsNone(error)
            derived = expand_next_steps.expand_record(whole)[0]
            published_rows.append(export_hf_next_steps.build_row(derived, "train"))

        export = self.root / "published" / "traces.jsonl"
        export.parent.mkdir(parents=True)
        export.write_text("".join(
            json.dumps(row) + "\n" for row in published_rows))
        self.assertEqual(export_hf_next_steps.validate_export(export)["trajectories"], 2)
        self.assertEqual(validate_hf_export.validate(export), 2)

        card = export.parent / "README.md"
        card.write_text(export_hf_card.build_card(
            published_rows,
            config={
                "teacher": {"runtime": HarnessA.name, "model": "same-model",
                            "reasoning": "xhigh"},
                "judge": {"runtime": AcceptingJudge.name, "model": "judge-model"},
                "runtimes": {}, "pipeline": {"trace": {}},
                "publish": {"format": "jsonl"},
            }, publish_dir=export.parent))
        self.assertEqual(
            publish.publication_files(export.parent, "jsonl", include_jsonl=True),
            [card, export])
        source = pathlib.Path(trace_pipeline.__file__).read_text()
        self.assertEqual(source.count("record = trace_task("), 1)
        self.assertEqual(source.count("review = screen("), 1)
        for module in (screen_traces, build_dataset, export_hf_next_steps,
                       validate_hf_export, publish, export_hf_card):
            text = pathlib.Path(module.__file__).read_text()
            self.assertNotIn("required_harness_capabilities", text)
            self.assertNotIn("preferred_harness_capabilities", text)


class CatalogAndConfigurationContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="capability-catalog-", dir=TEST_TMP)
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _complete_seed(self, task: dict) -> pathlib.Path:
        directory = self.root / "tasks" / "seeds" / task["id"]
        (directory / "files").mkdir(parents=True)
        (directory / "files" / "baseline.txt").write_text("baseline\n")
        (directory / "reference_fix.patch").write_text("patch\n")
        (directory / "task.json").write_text(json.dumps(task))
        return directory

    def test_catalog_preserves_only_explicit_capability_lists(self):
        seeds = self.root / "tasks" / "seeds"
        explicit = {
            "id": "explicit", "category": "Building", "prompt": "do it",
            "required_harness_capabilities": ["workspace_write"],
            "preferred_harness_capabilities": ["multi_turn"],
        }
        self._complete_seed(explicit)
        self._complete_seed({
            "id": "legacy", "category": "Building", "prompt": "do it"})
        _, generated = corpus.catalog(seeds)
        items = {item["id"]: item
                 for values in generated["categories"].values() for item in values}
        self.assertEqual(items["explicit"]["required_harness_capabilities"],
                         ["workspace_write"])
        self.assertEqual(items["explicit"]["preferred_harness_capabilities"],
                         ["multi_turn"])
        self.assertNotIn("required_harness_capabilities", items["legacy"])
        self.assertNotIn("preferred_harness_capabilities", items["legacy"])

    def test_corpus_audit_rejects_invalid_capability_metadata(self):
        valid = self._complete_seed({
            "id": "valid", "category": "Building", "prompt": "do it",
            "required_harness_capabilities": ["workspace_write"],
            "preferred_harness_capabilities": [],
        })
        self.assertIsNone(audit_seeds.check(valid))
        invalid = self._complete_seed({
            "id": "invalid", "category": "Building", "prompt": "do it",
            "required_harness_capabilities": "workspace_write",
        })
        self.assertIn("required_harness_capabilities", audit_seeds.check(invalid))
        invalid_entry = self._complete_seed({
            "id": "invalid-entry", "category": "Building", "prompt": "do it",
            "preferred_harness_capabilities": ["multi_turn", 7],
        })
        self.assertIn("preferred_harness_capabilities",
                      audit_seeds.check(invalid_entry))

    def test_shipped_harness_order_defaults_to_empty_list(self):
        config = json.loads((ROOT / "config.json").read_text())
        self.assertEqual(config["pipeline"]["trace"]["harness_order"], [])


if __name__ == "__main__":
    unittest.main()
