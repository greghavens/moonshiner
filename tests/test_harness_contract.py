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
        self.assertEqual(set(runtime_names()),
                         {"claude-code", "codex", "opencode", "pi"})
        self.assertEqual(set(source_runtime_names()), set(runtime_names()))

    def test_claude_code_can_fill_every_runtime_role(self):
        config = {"teacher": {"runtime": "claude-code", "model": "claude"},
                  "judge": {"runtime": "claude-code", "model": "claude"},
                  "seed_author": {"runtime": "claude-code", "model": "claude"},
                  "runtimes": {"claude-code": {}}}
        self.assertEqual(get_teacher(config).name, "claude-code")
        self.assertEqual(get_seed_author(config).name, "claude-code")
        self.assertEqual(get_judge(config).name, "claude-code")

    def test_claude_code_seed_author_reaches_the_runtime(self):
        config = {
            "seed_author": {"runtime": "claude-code", "model": "claude-opus-5"},
            "runtimes": {"claude-code": {"cli": "claude"}},
        }
        runtime = get_seed_author(config)
        completed = SimpleNamespace(
            returncode=0, stderr="", stdout=(
                '{"type":"system","subtype":"init","model":"claude-opus-5"}\n'
                '{"type":"result","subtype":"success","usage":{}}\n'))
        with tempfile.TemporaryDirectory() as directory:
            workspace = pathlib.Path(directory) / "workspace"
            output = pathlib.Path(directory) / "output"
            workspace.mkdir(); output.mkdir()
            with mock.patch.object(runtime, "require_persistent_workspace",
                                   return_value=workspace), \
                 mock.patch("runtimes.claude_code.run_with_inactivity_timeout",
                            return_value=completed) as launch:
                result = runtime.run_trace(
                    {"id": "seed"}, workspace, out_dir=output,
                    system_prompt="author", prompt="build the seed")
        self.assertEqual(result.return_code, 0)
        self.assertIn("claude-opus-5", launch.call_args.args[0])

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
            traces = root / "traces"
            (traces / "meta").mkdir(parents=True)
            (traces / "raw").mkdir()
            seed_dir = root / "seed"
            seed_dir.mkdir()
            (seed_dir / "task.json").write_text("{}\n")
            seed = {
                "id": "seed",
                "prompt": "Research this exact task, then complete it.",
                "research": {"required": True},
                "reference_setup": "definitely-missing-reference-command",
                "_dir": seed_dir,
            }
            workspace = root / "workspace"
            workspace.mkdir()
            old_raw = traces / "raw" / "seed.old.jsonl"
            old_raw.write_text('{"old":true}\n')
            (traces / "meta" / "seed.json").write_text(json.dumps({
                "id": "seed", "passed": True, "trace_format": "native-v1",
                "raw_path": "traces/raw/seed.old.jsonl"}))
            raw = traces / "raw" / "seed.events.jsonl"
            raw.write_text('{"fresh":true}\n')
            teacher.run_trace.return_value = SimpleNamespace(
                unavailable=None, safeguard_refusal=False, return_code=0,
                blocked_on_question=False,
                timed_out=False, stream_success=True, error=None,
                raw_path=raw, trace_format="native-v1", duration_s=1,
                observed_model="model", observed_models=["model"],
                model_attested=True, model_fallback=False, usage={},
                provenance={})
            with mock.patch.object(generate_traces, "materialize",
                                   return_value=workspace), \
                 mock.patch.object(generate_traces, "protected_hashes",
                                   return_value={}), \
                 mock.patch.object(generate_traces, "clear_runtime_caches"), \
                 mock.patch.object(generate_traces, "run_verify",
                                   return_value=(True, "")), \
                 mock.patch.object(generate_traces, "git_diff",
                                   return_value=""):
                record = generate_traces.trace_task(
                    seed, teacher, force=True,
                    feedback="Judge feedback must not alter the prompt.",
                    traces_root=traces)
        teacher.run_trace.assert_called_once()
        self.assertEqual(
            teacher.run_trace.call_args.kwargs["prompt"], seed["prompt"])
        self.assertEqual(record["raw_sha256"],
                         generate_traces._sha256('{"fresh":true}\n'))

    def _blocked_trace(self, error: str, **flags) -> dict:
        """Trace one seed against a teacher that stopped for ``error``."""
        teacher = mock.Mock()
        teacher.name = "native-harness"
        teacher.role = {"model": "model", "reasoning": "xhigh"}
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            traces = root / "traces"
            (traces / "meta").mkdir(parents=True)
            (traces / "raw").mkdir()
            seed_dir = root / "seed"
            seed_dir.mkdir()
            (seed_dir / "task.json").write_text("{}\n")
            seed = {"id": "seed", "prompt": "Do the task.", "_dir": seed_dir}
            workspace = root / "workspace"
            workspace.mkdir()
            raw = traces / "raw" / "seed.events.jsonl"
            raw.write_text("{}\n")
            result = {"unavailable": None, "safeguard_refusal": False,
                      "blocked_on_question": False, "return_code": 1,
                      "timed_out": False, "stream_success": False,
                      "error": error, "raw_path": raw,
                      "trace_format": "native-v1", "duration_s": 1,
                      "observed_model": None, "observed_models": [],
                      "model_attested": False, "model_fallback": False,
                      "usage": {}, "provenance": {}}
            result.update(flags)
            teacher.run_trace.return_value = SimpleNamespace(**result)
            with mock.patch.object(generate_traces, "materialize",
                                   return_value=workspace), \
                 mock.patch.object(generate_traces, "protected_hashes",
                                   return_value={}):
                return generate_traces.trace_task(
                    seed, teacher, force=True, traces_root=traces)

    def test_a_safeguard_refusal_defers_the_seed_and_records_why(self):
        # The queue must survive a seed the provider's filter dislikes: this
        # returns a deferral record rather than raising the infrastructure
        # failure that stops tracing and blocks the supervisor from restarting.
        blocked = "OpenCode assistant error: {'name': 'ContentFilterError'}"
        record = self._blocked_trace(blocked, safeguard_refusal=True)
        self.assertTrue(record["deferred_safeguard_refusal"])
        self.assertEqual(record["deferral_reason"], blocked)
        self.assertIsNone(record["passed"])

    def test_a_question_no_one_can_answer_defers_the_seed_too(self):
        # An agent that stops to ask the operator something is not a broken
        # harness — it is one seed whose prompt invites a question. Raising
        # here would stop the queue over a seed the next pass may well pass.
        asked = ("OpenCode teacher asked the operator a question and a "
                 "headless trace cannot answer it: Which archive?")
        record = self._blocked_trace(asked, blocked_on_question=True)
        self.assertTrue(record["deferred_interactive_question"])
        self.assertEqual(record["deferral_reason"], asked)
        self.assertIsNone(record["passed"])
        # Kept apart from a refusal so provenance says which one happened.
        self.assertNotIn("deferred_safeguard_refusal", record)

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
    def test_claim_processor_has_only_the_forced_native_trace_path(self):
        source = inspect.getsource(trace_pipeline.main)
        self.assertNotIn("existing_harness_trace", source)
        self.assertEqual(source.count("record = trace_task("), 1)
        self.assertIn("record = trace_task(seed, attempt_teacher, force=True", source)


class _Captured(Exception):
    """Stop an adapter at its launch boundary, holding the judge's input."""


# Where each adapter hands the review off, and whether that is a module
# function or a method on the runtime. Everything passed at this point — argv,
# stdin, keyword arguments — plus every file the adapter wrote into the
# workspace is what the judge gets to read.
JUDGE_LAUNCH = {
    "claude-code": ("runtimes.claude_code.run_with_inactivity_timeout", False),
    "codex": ("runtimes.codex.run_with_inactivity_timeout", False),
    "opencode": ("_run_server_session", True),
    "pi": ("_run", True),
}


class EveryJudgeIsShownTheSchemaItMustMatch(unittest.TestCase):
    """``run_review`` takes a schema so the harness can hold the judge to it.

    The reviewer prompt ends by asking for "the JSON verdict object required by
    the schema" and never states the schema, because stating it is the
    adapter's job — natively where the harness supports it, in the prompt where
    it does not. An adapter that accepts ``schema`` and drops it leaves the
    judge guessing: OpenCode and Pi both did, silently. The judge answers in a
    shape of its own, ``validate_reviewer_verdict`` finds none of the five
    categories, and every reviewed trace fails as malformed three re-reviews
    deep before the queue stops on an infrastructure failure the judge did not
    cause.
    """

    SCHEMA = {"type": "object",
              "required": ["verdict", "added_scope"],
              "properties": {"verdict": {"enum": ["accept", "reject"]},
                             "added_scope": {"type": "array"}}}
    INSTRUCTION = "Review this workspace and return a verdict."

    def _judge_input(self, name: str, runtime) -> str:
        """Everything the judge can read when *runtime* reviews with a schema."""
        seen: list[str] = []

        def capture(*args, **kwargs):
            seen.extend(str(value) for value in args)
            seen.extend(str(value) for value in kwargs.values())
            raise _Captured

        target, is_method = JUDGE_LAUNCH[name]
        launch = (mock.patch.object(runtime, target, capture) if is_method
                  else mock.patch(target, capture))
        with tempfile.TemporaryDirectory() as directory:
            workspace = pathlib.Path(directory) / "workspace"
            out_dir = pathlib.Path(directory) / "review"
            workspace.mkdir(); out_dir.mkdir()
            with mock.patch.object(runtime, "require_persistent_workspace",
                                   return_value=workspace), launch:
                with self.assertRaises(_Captured):
                    runtime.run_review(self.INSTRUCTION, workspace,
                                       out_dir=out_dir, schema=self.SCHEMA)
            # Codex states the shape natively, by writing it out and pointing
            # --output-schema at the file; that counts, so read what was left.
            seen += [path.read_text(errors="replace")
                     for path in sorted(workspace.rglob("*")) if path.is_file()]
        return "\n".join(seen)

    def _runtime(self, name: str):
        role = {"model": "model", "reasoning": "xhigh", "timeout_s": 60}
        config = {"judge": dict(role, runtime=name),
                  "runtimes": {name: {"provider": "provider",
                                      "base_url": "http://127.0.0.1:1",
                                      "api_key_env": "MOONSHINER_TEST_KEY"}}}
        return REGISTRY[name](config, config["judge"])

    def test_no_adapter_accepts_a_schema_and_drops_it(self):
        for name in sorted(REGISTRY):
            with self.subTest(runtime=name):
                shown = self._judge_input(name, self._runtime(name))
                self.assertIn("added_scope", shown)
                self.assertIn(self.INSTRUCTION, shown)

    def test_a_review_without_a_schema_is_left_alone(self):
        # Not every caller has a schema, and inventing one for them would put
        # words in the judge's mouth that the reviewer prompt never asked for.
        runtime = self._runtime("claude-code")
        with mock.patch.object(self, "SCHEMA", None):
            shown = self._judge_input("claude-code", runtime)
        self.assertIn(self.INSTRUCTION, shown)
        self.assertNotIn("added_scope", shown)

    def test_every_registered_harness_is_covered(self):
        # A new adapter with no entry here is an untested judge, which is how
        # both silent droppers survived: nothing walked the whole registry.
        self.assertEqual(set(JUDGE_LAUNCH), set(REGISTRY))


if __name__ == "__main__":
    unittest.main()
