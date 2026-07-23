"""Moonshiner must not reconstruct a tools payload absent from native traces."""
import pathlib
import sys
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


if __name__ == "__main__":
    unittest.main()
