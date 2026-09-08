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


class AnAgentNameInsideOrdinaryTextIsNotALaunch(unittest.TestCase):
    """Nine finished traces were discarded over text that ran no agent.

    The scan matched the agent names anywhere they appeared, so a sandbox
    path (``/tmp/claude-1000/...``) and the letters ``pi`` inside a grep
    character class both read as a nested agent. The trace was thrown away
    with that as its only failed gate, after the author had already written
    and verified the fix.
    """

    def test_an_agent_name_inside_a_path_or_word_is_not_a_launch(self):
        actions = [
            {"tool": "bash", "command": "cat /tmp/claude-1000/task/out.log",
             "path": ""},
            {"tool": "bash", "command": "tail -n 5 /tmp/claude/x/run.log",
             "path": ""},
            {"tool": "bash", "command": "cat notes-claude.md", "path": ""},
            {"tool": "bash", "command": "cat src/a.py | grep -n '[Aa]pi[a-z]*'",
             "path": ""},
            {"tool": "bash", "command": "echo https://api.example.com/pipeline",
             "path": ""},
        ]
        self.assertEqual([], static_action_findings(actions))

    def test_a_real_invocation_is_still_reported(self):
        for command in ("claude -p 'go'", "codex exec 'go'", "aider --yes",
                        "/usr/local/bin/claude -p x", "echo hi | claude",
                        "timeout 60 claude -p x", "cd work && pi run"):
            with self.subTest(command=command):
                kinds = {finding["kind"] for finding in static_action_findings(
                    [{"tool": "bash", "command": command, "path": ""}])}
                self.assertIn("launches_coding_agent", kinds,
                              "a trace must never spawn another coding agent")
