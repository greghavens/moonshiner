"""Moonshiner must not reconstruct a tools payload absent from native traces."""
import pathlib
import inspect
import sys
import tempfile
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from runtimes.claude_code import ClaudeCodeRuntime  # noqa: E402
from runtimes.codex import CodexRuntime  # noqa: E402
from runtimes.pi import PiRuntime  # noqa: E402
from runtimes.synthetic_correction import SyntheticCorrectionAdapter  # noqa: E402


class NoReconstructedSurface(unittest.TestCase):
    def test_adapters_expose_no_schema_reconstruction_api(self):
        for adapter in (
                ClaudeCodeRuntime, CodexRuntime, PiRuntime,
                SyntheticCorrectionAdapter):
            with self.subTest(adapter=adapter.__name__):
                self.assertFalse(hasattr(adapter, "tool_schemas"))

    def test_reconstruction_symbols_are_absent_from_runtime_sources(self):
        for path in (_ROOT / "src" / "runtimes").glob("*.py"):
            text = path.read_text()
            self.assertNotIn("TOOL_REGISTRY", text, path)
            self.assertNotIn("OFFERED_TOOLS", text, path)


class SharedTeacherEnvironment(unittest.TestCase):
    def test_every_harness_uses_one_workspace_confined_environment(self):
        self.assertIn("self.teacher_environment(workspace)",
                      inspect.getsource(ClaudeCodeRuntime.run_trace))
        self.assertIn("self.teacher_environment(workspace)",
                      inspect.getsource(CodexRuntime.run_trace))
        self.assertIn("self.teacher_environment(workspace)",
                      inspect.getsource(PiRuntime._run))
        with tempfile.TemporaryDirectory() as directory:
            workspace = pathlib.Path(directory)
            for adapter in (ClaudeCodeRuntime, CodexRuntime, PiRuntime):
                with self.subTest(adapter=adapter.__name__):
                    self.assertIs(adapter.teacher_environment,
                                  adapter.__mro__[1].teacher_environment)
                    environment = adapter.teacher_environment(workspace)
                    root = workspace / ".sandbox-home"
                    for key in ("HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
                                "XDG_DATA_HOME", "DOTNET_CLI_HOME",
                                "NUGET_PACKAGES", "GOCACHE", "GOMODCACHE",
                                "GOPATH"):
                        self.assertTrue(
                            pathlib.Path(environment[key]).is_relative_to(root))
                    self.assertEqual(
                        pathlib.Path(environment["CODEX_HOME"]),
                        pathlib.Path.home() / ".codex")
                    self.assertEqual(
                        pathlib.Path(environment["CLAUDE_CONFIG_DIR"]),
                        pathlib.Path.home() / ".claude")


if __name__ == "__main__":
    unittest.main()
