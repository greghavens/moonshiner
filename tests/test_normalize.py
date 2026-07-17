"""Runtime-agnostic routing: trace_format -> the adapter that normalizes it."""
import pathlib
import sys
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

import normalize  # noqa: E402

KNOWN = ("claude-stream-json", "codex-exec-events", "codex-rollout",
         "pi-coding-agent-json-v3")


class Routing(unittest.TestCase):
    def test_every_known_format_routes_to_a_parser(self):
        for fmt in KNOWN:
            self.assertIn(fmt, normalize._BY_FORMAT)
            parser = normalize.parser_for(fmt)
            self.assertTrue(hasattr(parser, "parse_stream"))
            self.assertTrue(hasattr(parser, "tool_schemas"))

    def test_unknown_format_raises_valueerror(self):
        with self.assertRaises(ValueError):
            normalize.parser_for("no-such-format")

    def test_registry_covers_all_runtimes(self):
        # Every registered adapter must round-trip through parser_for.
        for fmt, cls in normalize._BY_FORMAT.items():
            self.assertIs(normalize.parser_for(fmt), cls)


if __name__ == "__main__":
    unittest.main()
