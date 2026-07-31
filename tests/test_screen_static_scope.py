"""The static scan judges trace integrity, never the environment."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from screen_traces import static_action_findings  # noqa: E402


class TheScanDoesNotGuessAtTheEnvironment(unittest.TestCase):
    """The sandbox confines what a run can reach. This scan only guessed.

    It read `ls docs 2>/dev/null` as a write escaping the workspace and threw
    away 159 traces that had verified and passed. Rejecting network use also
    contradicted the web-research seeds, which require real fetches.
    """

    def test_no_environment_reason_is_ever_raised(self):
        actions = [
            {"tool": "bash", "command": "ls docs .moonshiner 2>/dev/null", "path": ""},
            {"tool": "write", "command": "", "path": "/tmp/scratch.txt"},
            {"tool": "bash", "command": "echo x >/tmp/scratch.txt", "path": ""},
            {"tool": "bash", "command": "mktemp -d", "path": ""},
            {"tool": "bash", "command": "pip install requests", "path": ""},
            {"tool": "bash", "command": "curl https://example.com", "path": ""},
        ]
        self.assertEqual([], static_action_findings(actions))

    def test_trace_integrity_is_still_enforced(self):
        agent = [{"tool": "bash", "command": "claude --print 'do it'", "path": ""}]
        kinds = {f["kind"] for f in static_action_findings(agent)}
        self.assertIn("launches_coding_agent", kinds,
                      "a trace must never spawn another coding agent")
