"""Executable contracts for reasoning-aware native trace-harness selection.

The tests deliberately use real files, SQLite ledgers, subprocesses, and
Runtime implementations.  They do not mock a harness, queue, or pipeline
function, and they never contact a model or the network.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
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
    reasoning_capture = False
    calls: list[dict] = []

    def captures_reasoning(self) -> bool:
        return self.reasoning_capture

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
    calls: list[dict] = []


class HarnessB(_ExecutableRuntime):
    name = "test-harness-b"
    reasoning_capture = True
    calls: list[dict] = []


class HarnessC(_ExecutableRuntime):
    name = "test-harness-c"
    calls: list[dict] = []


class AcceptingJudge(_ExecutableRuntime):
    name = "test-judge"
    calls: list[dict] = []


class ReasoningResolverContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="reasoning-selection-", dir=TEST_TMP)
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

    def test_seed_without_reasoning_requirement_keeps_configured_teacher(self):
        config = self.config([HarnessB.name, HarnessC.name])
        configured = runtimes.get_teacher(config)
        selected, resolution = runtimes.resolve_trace_harness(
            {"id": "plain"}, configured_teacher=configured, config=config)
        self.assertIs(selected, configured)
        self.assertEqual(selected.name, HarnessA.name)
        self.assertEqual(resolution["mode"], "configured_default")

    def test_reasoning_requirement_selects_a_reasoning_capture_harness(self):
        selected, _ = self.resolve({
            "id": "required",
            "requires_reasoning": True,
        }, self.config([HarnessA.name, HarnessC.name, HarnessB.name]))
        self.assertEqual(selected.name, HarnessB.name)

    def test_unavailable_reasoning_harness_is_not_selected(self):
        seed = {"id": "required", "requires_reasoning": True}
        config = self.config(
            [HarnessA.name, HarnessB.name], missing={HarnessB.name})
        self.assertFalse(runtimes.seed_reasoning_is_available(seed, config))
        with self.assertRaisesRegex(
                runtimes.NoCompatibleTraceHarness, "requires captured reasoning"):
            self.resolve(seed, config)

    def test_an_alternative_harness_uses_the_model_its_own_provider_names(self):
        config = self.config([HarnessB.name, HarnessA.name])
        config["runtimes"][HarnessB.name]["trace_model"] = "vendor/same-model"
        config["runtimes"][HarnessB.name]["trace_reasoning"] = "high"
        selected, resolution = self.resolve({
            "id": "alternative",
            "requires_reasoning": True,
        }, config)
        self.assertEqual(selected.name, HarnessB.name)
        self.assertEqual(selected.role["model"], "vendor/same-model")
        self.assertEqual(selected.role["reasoning"], "high")
        self.assertEqual(resolution["model"], "vendor/same-model")

    def test_a_harness_without_its_own_model_still_inherits_the_teachers(self):
        selected, resolution = self.resolve({
            "id": "inherited",
            "requires_reasoning": True,
        }, self.config([HarnessB.name, HarnessA.name]))
        self.assertEqual(selected.name, HarnessB.name)
        self.assertEqual(resolution["model"], "same-model")

    def test_the_configured_teacher_keeps_the_model_it_was_configured_with(self):
        config = self.config([HarnessA.name])
        config["teacher"]["runtime"] = HarnessB.name
        config["runtimes"][HarnessB.name]["trace_model"] = "never-substituted"
        selected, resolution = self.resolve({
            "id": "configured",
            "requires_reasoning": True,
        }, config)
        self.assertEqual(selected.name, HarnessB.name)
        self.assertEqual(selected.role["model"], "same-model")
        self.assertEqual(resolution["model"], "same-model")

    def test_seed_harness_identity_is_forbidden(self):
        with self.assertRaisesRegex(
                runtimes.TraceHarnessInfrastructureFailure,
                "harness bindings are forbidden"):
            self.resolve({"id": "bound", "harness": HarnessB.name},
                         self.config([HarnessB.name]))

    def test_removed_harness_metadata_fields_are_forbidden(self):
        config = self.config([HarnessB.name])
        with self.assertRaisesRegex(
                runtimes.TraceHarnessInfrastructureFailure,
                "removed seed field required_harness_capabilities"):
            self.resolve({
                "id": "wrong",
                "required_harness_capabilities": ["live_web_research"],
            }, config)
        with self.assertRaisesRegex(
                runtimes.TraceHarnessInfrastructureFailure,
                "removed seed field preferred_harness_capabilities"):
            self.resolve({
                "id": "preferred", "preferred_harness_capabilities": [
                    "reasoning_capture"],
            }, config)

    def test_unrelated_identity_never_changes_selection(self):
        config = self.config([HarnessC.name, HarnessB.name])
        base = {
            "requires_reasoning": True,
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
        self.assertEqual(selected, [HarnessB.name, HarnessB.name])

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
            requires_reasoning=True)
        traces = self.root / "prompt-traces"
        configured = runtimes.get_teacher(
            self.config([HarnessC.name, HarnessB.name]))
        record = generate_traces.trace_task(
            seed, configured, force=True, traces_root=traces)
        self.assertEqual(HarnessB.calls[-1]["prompt"], seed["prompt"])
        self.assertEqual(record["prompt"], seed["prompt"])
        self.assertEqual(
            record["teacher"]["provenance"]["reasoning_resolution"]["runtime"],
            HarnessB.name)

    def test_paid_runtime_failure_never_invokes_second_harness_or_judge(self):
        seed = self._seed("runtime-failure")
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
            prefix="reasoning-queue-", dir=TEST_TMP)
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

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


class SharedPipelineContract(ReasoningResolverContract):
    def test_multiple_harnesses_traverse_the_same_downstream_functions(self):
        traces = self.root / "shared-traces"
        judge_config = {
            "judge": {"runtime": AcceptingJudge.name, "model": "judge-model",
                      "reasoning": "xhigh"},
            "runtimes": {AcceptingJudge.name: {"cli": str(self.cli)}},
        }
        judge = AcceptingJudge(judge_config, judge_config["judge"])
        published_rows = []
        for runtime_name, requires_reasoning in (
                (HarnessA.name, False),
                (HarnessB.name, True)):
            metadata = ({"requires_reasoning": True}
                        if requires_reasoning else {})
            seed = self._seed(f"shared-{runtime_name}", **metadata)
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
        # A garbled verdict is re-reviewed by calling the same screen() again,
        # so count spellings rather than calls: what has to stay singular is
        # the screening path every harness goes down, not how many times the
        # queue is allowed to walk it.
        self.assertEqual(set(re.findall(r"screen\([^)]*\)", source)),
                         {"screen(seed, worker_judge)"})
        for module in (screen_traces, build_dataset, export_hf_next_steps,
                       validate_hf_export, publish, export_hf_card):
            text = pathlib.Path(module.__file__).read_text()
            self.assertNotIn("required_harness_capabilities", text)
            self.assertNotIn("preferred_harness_capabilities", text)


class CatalogAndConfigurationContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="reasoning-catalog-", dir=TEST_TMP)
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

    def test_catalog_preserves_only_the_reasoning_requirement(self):
        seeds = self.root / "tasks" / "seeds"
        explicit = {
            "id": "explicit", "category": "Building", "prompt": "do it",
            "requires_reasoning": True,
        }
        self._complete_seed(explicit)
        self._complete_seed({
            "id": "legacy", "category": "Building", "prompt": "do it"})
        _, generated = corpus.catalog(seeds)
        items = {item["id"]: item
                 for values in generated["categories"].values() for item in values}
        self.assertIs(items["explicit"]["requires_reasoning"], True)
        self.assertIs(items["legacy"]["requires_reasoning"], False)
        self.assertNotIn("harness", items["explicit"])
        self.assertNotIn("required_harness_capabilities", items["explicit"])
        self.assertNotIn("preferred_harness_capabilities", items["explicit"])
        self.assertNotIn("harness", items["legacy"])
        self.assertNotIn("required_harness_capabilities", items["legacy"])
        self.assertNotIn("preferred_harness_capabilities", items["legacy"])

    def test_corpus_audit_allows_only_the_reasoning_requirement(self):
        valid = self._complete_seed({
            "id": "valid", "category": "Building", "prompt": "do it",
            "requires_reasoning": True,
        })
        self.assertIsNone(audit_seeds.check(valid))
        invalid = self._complete_seed({
            "id": "invalid", "category": "Building", "prompt": "do it",
            "required_harness_capabilities": ["live_web_research"],
        })
        self.assertIn("required_harness_capabilities", audit_seeds.check(invalid))
        invalid_entry = self._complete_seed({
            "id": "invalid-entry", "category": "Building", "prompt": "do it",
            "preferred_harness_capabilities": ["reasoning_capture"],
        })
        self.assertIn("preferred_harness_capabilities",
                      audit_seeds.check(invalid_entry))
        invalid_reasoning = self._complete_seed({
            "id": "invalid-reasoning", "category": "Building",
            "prompt": "do it", "requires_reasoning": "yes",
        })
        self.assertIn("requires_reasoning must be true or false",
                      audit_seeds.check(invalid_reasoning))

    def test_corpus_audit_rejects_every_exact_harness_binding(self):
        bound = self._complete_seed({
            "id": "bound", "category": "Building",
            "prompt": "do it", "harness": "opencode"})
        self.assertIn("harness is forbidden", audit_seeds.check(bound))

    def test_shipped_prompts_do_not_name_an_agent_harness(self):
        named = []
        for task_path in sorted((ROOT / "tasks" / "seeds").glob("*/task.json")):
            task = json.loads(task_path.read_text())
            prompt = str(task.get("prompt") or "")
            if re.search(r"\b(?:Pi|Codex|Claude Code|OpenCode)\b", prompt,
                         re.IGNORECASE):
                named.append(task["id"])
        # This seed is about a Raspberry Pi device, not an agent harness.
        self.assertEqual(named, ["rb-hivelog"])

    def test_shipped_harness_order_defaults_to_empty_list(self):
        config = json.loads((ROOT / "config.json").read_text())
        self.assertEqual(config["pipeline"]["trace"]["harness_order"], [])

    def test_shipped_seed_requirements_are_boolean_and_only_twelve_are_true(self):
        required = []
        for task_path in sorted((ROOT / "tasks" / "seeds").glob("*/task.json")):
            task = json.loads(task_path.read_text())
            self.assertNotIn("harness", task)
            self.assertNotIn("required_harness_capabilities", task)
            self.assertNotIn("preferred_harness_capabilities", task)
            value = task.get("requires_reasoning", False)
            self.assertIsInstance(value, bool)
            if value:
                required.append(task["id"])
        self.assertEqual(len(required), 12)

    def test_only_opencode_and_pi_advertise_reasoning_capture(self):
        config = json.loads((ROOT / "config.json").read_text())
        captures = {}
        for name, cls in runtimes.REGISTRY.items():
            runtime = cls(config, {**config["teacher"], "runtime": name})
            runtime.runtime_config = (config.get("runtimes") or {}).get(name, {})
            captures[name] = runtime.captures_reasoning()
        self.assertEqual(captures, {
            "claude-code": False,
            "codex": False,
            "opencode": True,
            "pi": True,
            "vllm": False,
        })


class AlternativeHarnessDiscovery(unittest.TestCase):
    """Doctor needs the same reasoning alternatives selection will build.

    Selection authenticates a harness only after choosing it, mid-run, where
    the failure stops the queue. Enumerating the alternatives up front is what
    lets that failure be reported while nothing is at stake.
    """

    CONFIG = {
        "teacher": {"runtime": "codex", "model": "glm-5.3",
                    "reasoning": "default"},
        "runtimes": {
            "codex": {"cli": "codex"},
            "pi": {"cli": "pi", "trace_model": "zai/glm-5.3"},
        },
        "pipeline": {"trace": {"harness_order": ["codex", "pi"]}},
    }

    def test_the_configured_teacher_is_not_its_own_alternative(self):
        found = runtimes.trace_harness_alternatives(self.CONFIG)
        self.assertEqual([runtime.name for runtime in found], ["pi"])

    def test_an_alternative_carries_its_own_model_spelling(self):
        alternative, = runtimes.trace_harness_alternatives(self.CONFIG)
        self.assertEqual(alternative.role["model"], "zai/glm-5.3")

    def test_an_unset_order_yields_nothing(self):
        config = {**self.CONFIG, "pipeline": {"trace": {}}}
        self.assertEqual(runtimes.trace_harness_alternatives(config), [])

    def test_a_name_with_no_runtime_block_is_skipped(self):
        config = {**self.CONFIG,
                  "pipeline": {"trace": {"harness_order": ["codex", "nope"]}}}
        self.assertEqual(runtimes.trace_harness_alternatives(config), [])


if __name__ == "__main__":
    unittest.main()
