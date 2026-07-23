"""Every trace comes from the configured, unmodified agent harness."""
import inspect
import pathlib
import sys
import unittest
import json
import tempfile
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import generate_traces  # noqa: E402
import seed_pipeline  # noqa: E402
import trace_pipeline  # noqa: E402
from runtimes import (REGISTRY, get_judge, get_seed_author, get_teacher,
                      runtime_names, source_runtime_names)  # noqa: E402


class HarnessContract(unittest.TestCase):
    def test_current_supported_trace_harnesses_are_explicit(self):
        self.assertEqual(set(runtime_names()), {"claude-code", "codex", "pi"})
        self.assertEqual(set(source_runtime_names()), {"codex", "pi"})

    def test_claude_code_is_judge_only(self):
        config = {"teacher": {"runtime": "claude-code", "model": "claude"},
                  "judge": {"runtime": "claude-code", "model": "claude"},
                  "seed_author": {"runtime": "claude-code", "model": "claude"},
                  "runtimes": {"claude-code": {}}}
        with self.assertRaisesRegex(SystemExit, "judge-only"):
            get_teacher(config)
        with self.assertRaisesRegex(SystemExit, "judge-only"):
            get_seed_author(config)
        self.assertEqual(get_judge(config).name, "claude-code")

    def test_pipeline_calls_the_selected_runtime_adapter(self):
        source = inspect.getsource(generate_traces.trace_task)
        self.assertIn("teacher.run_trace(", source)
        self.assertNotIn("behavior_trace", source)
        self.assertNotIn("openrouter", source.casefold())

    def test_trace_task_passes_authored_prompt_to_harness_byte_for_byte(self):
        teacher = mock.Mock()
        teacher.name = "native-harness"
        teacher.role = {"model": "model", "reasoning": "xhigh"}
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            seed_dir = root / "seed"
            seed_dir.mkdir()
            (seed_dir / "task.json").write_text("{}\n")
            seed = {
                "id": "seed",
                "prompt": "Research this exact task, then complete it.",
                "research": {"required": True},
                "_dir": seed_dir,
            }
            workspace = root / "workspace"
            workspace.mkdir()
            raw = root / "native.jsonl"
            raw.write_text("{}\n")
            teacher.run_trace.return_value = SimpleNamespace(
                unavailable=None, safeguard_refusal=False, return_code=0,
                timed_out=False, stream_success=True, error=None,
                raw_path=raw, trace_format="native-v1", duration_s=1,
                observed_model="model", observed_models=["model"],
                model_attested=True, model_fallback=False, usage={},
                provenance={})
            with mock.patch.object(generate_traces, "materialize",
                                   return_value=workspace), \
                 mock.patch.object(generate_traces, "run_setup",
                                   return_value=(True, "")), \
                 mock.patch.object(generate_traces, "protected_hashes",
                                   return_value={}), \
                 mock.patch.object(generate_traces, "clear_runtime_caches"), \
                 mock.patch.object(generate_traces, "run_verify",
                                   return_value=(True, "")), \
                 mock.patch.object(generate_traces, "git_diff",
                                   return_value=""):
                generate_traces.trace_task(
                    seed, teacher, force=True,
                    feedback="Judge feedback must not alter the prompt.",
                    traces_root=root / "traces")
        self.assertEqual(
            teacher.run_trace.call_args.kwargs["prompt"], seed["prompt"])

    def test_generic_pipeline_has_no_runtime_specific_dispatch(self):
        source = inspect.getsource(trace_pipeline)
        self.assertNotIn("behavior_trace", source)
        self.assertNotIn("PiRuntime", source)
        self.assertNotIn("ClaudeCodeRuntime", source)
        self.assertNotIn("CodexRuntime", source)

    def test_reauthoring_prompt_is_harness_agnostic(self):
        prompt = seed_pipeline.REAUTHOR_SYSTEM.casefold()
        self.assertIn("selected unmodified agent harness", prompt)
        self.assertNotIn("pi-harness", prompt)
        self.assertNotIn("pi's", prompt)

    def test_each_registered_harness_has_a_native_trace_format(self):
        for name, runtime in REGISTRY.items():
            with self.subTest(runtime=name):
                self.assertTrue(runtime.trace_formats)
                self.assertTrue(callable(runtime.run_trace))

    def test_existing_registered_harness_trace_is_judged_before_retrace(self):
        with tempfile.TemporaryDirectory() as directory:
            traces = pathlib.Path(directory) / "traces"
            (traces / "meta").mkdir(parents=True)
            (traces / "raw").mkdir()
            raw = traces / "raw" / "seed.jsonl"
            raw.write_text("{}\n")
            (traces / "meta" / "seed.json").write_text(json.dumps({
                "id": "seed", "passed": True,
                "trace_format": "codex-exec-events",
                "raw_path": "traces/raw/seed.jsonl"}))
            with mock.patch.object(trace_pipeline, "TRACES", traces):
                self.assertTrue(trace_pipeline.existing_harness_trace("seed"))

    def test_synthetic_or_missing_trace_cannot_take_judge_first_path(self):
        with tempfile.TemporaryDirectory() as directory:
            traces = pathlib.Path(directory) / "traces"
            (traces / "meta").mkdir(parents=True)
            (traces / "meta" / "seed.json").write_text(json.dumps({
                "id": "seed", "passed": True,
                "trace_format": "moonshiner-behavior-openai-v1",
                "raw_path": "traces/raw/seed.jsonl"}))
            with mock.patch.object(trace_pipeline, "TRACES", traces):
                self.assertFalse(trace_pipeline.existing_harness_trace("seed"))

    def test_claim_processor_checks_existing_trace_before_teacher_call(self):
        source = inspect.getsource(trace_pipeline.main)
        self.assertLess(source.index("existing_harness_trace(seed[\"id\"])") ,
                        source.index("record = trace_task("))


if __name__ == "__main__":
    unittest.main()
