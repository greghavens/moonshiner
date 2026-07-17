"""Every exported row must carry the full tool surface the teacher was offered.

Each runtime adapter declares ``OFFERED_TOOLS`` — the complete action space its
teacher runs with — and ``tool_schemas`` must return that whole surface (unioned
with any extra tool actually observed), never just the tools a given trace
happened to call. These are pure, offline, model-free assertions on that
contract, plus the ``normalize`` routing that the build/export path depends on.
"""
import importlib
import pathlib
import sys
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

import normalize  # noqa: E402


def _names(schemas):
    return [schema["function"]["name"] for schema in schemas]


# (module, class name, the trace_formats that route to it)
_SPECS = [
    ("runtimes.codex", "CodexRuntime", ("codex-rollout", "codex-exec-events")),
    ("runtimes.pi", "PiRuntime", ("pi-coding-agent-json-v3",)),
    ("runtimes.claude_code", "ClaudeCodeRuntime", ("claude-stream-json",)),
]


def _adapters():
    """(class_name, runtime class, list(OFFERED_TOOLS), trace_formats)."""
    out = []
    for module_name, class_name, formats in _SPECS:
        module = importlib.import_module(module_name)
        out.append((class_name, getattr(module, class_name),
                    list(module.OFFERED_TOOLS), formats))
    return out


class OfferedSurface(unittest.TestCase):
    def test_offered_tools_is_declared_and_nonempty(self):
        for name, _runtime, offered, _formats in _adapters():
            with self.subTest(runtime=name):
                self.assertTrue(offered, "OFFERED_TOOLS must be non-empty")
                self.assertEqual(len(offered), len(set(offered)),
                                 "OFFERED_TOOLS must have no duplicates")

    def test_empty_trace_still_lists_the_full_surface(self):
        # A trace that called nothing must still carry the whole action space.
        for name, runtime, offered, _formats in _adapters():
            with self.subTest(runtime=name):
                self.assertEqual(_names(runtime.tool_schemas([])), offered)

    def test_every_schema_is_wellformed(self):
        for name, runtime, _offered, _formats in _adapters():
            with self.subTest(runtime=name):
                for schema in runtime.tool_schemas([]):
                    self.assertEqual(schema.get("type"), "function")
                    self.assertTrue(schema["function"]["name"])

    def test_observed_extra_tool_is_unioned_after_the_offered_set(self):
        extra = "z_custom_probe_tool"  # guaranteed outside every OFFERED set
        messages = [{"role": "assistant",
                     "tool_calls": [{"function": {"name": extra}}]}]
        for name, runtime, offered, _formats in _adapters():
            with self.subTest(runtime=name):
                self.assertNotIn(extra, offered)
                self.assertEqual(_names(runtime.tool_schemas(messages)),
                                 offered + [extra])

    def test_observed_offered_tool_is_not_duplicated(self):
        for name, runtime, offered, _formats in _adapters():
            with self.subTest(runtime=name):
                messages = [{"role": "assistant",
                             "tool_calls": [{"function": {"name": offered[0]}}]}]
                self.assertEqual(_names(runtime.tool_schemas(messages)), offered)

    def test_normalize_routes_each_format_to_the_full_surface(self):
        for name, _runtime, offered, formats in _adapters():
            for fmt in formats:
                with self.subTest(runtime=name, trace_format=fmt):
                    self.assertEqual(
                        _names(normalize.tool_schemas_for(fmt, [])), offered)


if __name__ == "__main__":
    unittest.main()
