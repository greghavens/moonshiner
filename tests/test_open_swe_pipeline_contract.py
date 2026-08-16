"""One-pipeline contracts for imported Open-SWE tasks."""
from __future__ import annotations

import ast
import inspect
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import build_dataset  # noqa: E402
import export_hf_card  # noqa: E402
import generate_traces  # noqa: E402
import publish  # noqa: E402
import screen_traces  # noqa: E402
import trace_pipeline  # noqa: E402
import validate_hf_export  # noqa: E402
from runtimes.base import Runtime  # noqa: E402


class ImportedTasksUseOnePipeline(unittest.TestCase):
    def test_trace_task_passes_the_authored_prompt_directly_to_run_trace(self):
        source = inspect.getsource(generate_traces.trace_task)
        tree = ast.parse(source)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "run_trace"]
        self.assertEqual(len(calls), 1)
        prompt = next(keyword.value for keyword in calls[0].keywords
                      if keyword.arg == "prompt")
        self.assertIsInstance(prompt, ast.Name)
        self.assertEqual(prompt.id, "prompt")

    def test_source_identity_cannot_select_an_alternate_product_path(self):
        modules = (trace_pipeline, generate_traces, screen_traces,
                   build_dataset, validate_hf_export, publish, export_hf_card)
        forbidden = {
            "nvidia-open-swe", "swe-rebench", "instance_id", "image_name",
            "source_collection", "repository_language",
        }
        for module in modules:
            source = inspect.getsource(module).casefold()
            self.assertFalse(
                forbidden & {value for value in forbidden if value in source},
                module.__name__)

    def test_every_runtime_uses_the_same_trace_interface(self):
        signature = inspect.signature(Runtime.run_trace)
        self.assertEqual(
            list(signature.parameters),
            ["self", "seed", "workspace", "out_dir", "system_prompt", "prompt",
             "interaction", "security", "tools"])
        self.assertIs(trace_pipeline.trace_task, generate_traces.trace_task)
        self.assertTrue(callable(screen_traces.screen))
        self.assertTrue(callable(build_dataset.build_row))
        self.assertTrue(callable(validate_hf_export.main))
        self.assertTrue(callable(publish.main))
        self.assertTrue(callable(export_hf_card.build_card))


if __name__ == "__main__":
    unittest.main()
