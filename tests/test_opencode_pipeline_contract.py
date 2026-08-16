"""OpenCode remains an adapter in Moonshiner's one existing product path."""
from __future__ import annotations

import ast
import inspect
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import build_dataset  # noqa: E402
import export_hf  # noqa: E402
import export_hf_card  # noqa: E402
import generate_traces  # noqa: E402
import normalize  # noqa: E402
import publish  # noqa: E402
import screen_traces  # noqa: E402
import trace_pipeline  # noqa: E402
import validate_hf_export  # noqa: E402
from runtimes import REGISTRY  # noqa: E402
from runtimes.opencode import OpenCodeRuntime  # noqa: E402


class OneOpenCodePipeline(unittest.TestCase):
    def test_opencode_is_only_a_native_adapter_and_normalizer(self):
        self.assertIs(REGISTRY["opencode"], OpenCodeRuntime)
        self.assertIs(normalize.parser_for("opencode-session-v1"), OpenCodeRuntime)
        self.assertIs(trace_pipeline.trace_task, generate_traces.trace_task)

    def test_no_downstream_phase_dispatches_on_opencode_or_provider(self):
        for module in (trace_pipeline, screen_traces, build_dataset,
                       validate_hf_export, publish, export_hf, export_hf_card):
            with self.subTest(module=module.__name__):
                tree = ast.parse(inspect.getsource(module))
                conditions = "\n".join(
                    ast.unparse(node.test).casefold()
                    for node in ast.walk(tree) if isinstance(node, ast.If))
                self.assertNotIn("opencoderuntime", conditions)
                self.assertNotIn("opencode-session-v1", conditions)
                self.assertNotIn("zenmux", conditions)
                self.assertNotIn("openrouter", conditions)

    def test_identity_fields_cannot_select_a_second_product_path(self):
        source = inspect.getsource(generate_traces.trace_task).casefold()
        for identity in ("provider", "category", "tags", "repository",
                         "language", "source_collection"):
            with self.subTest(identity=identity):
                self.assertNotIn(f"if seed.get(\"{identity}\")", source)
        self.assertEqual(source.count("teacher.run_trace("), 1)


if __name__ == "__main__":
    unittest.main()
