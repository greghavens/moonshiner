"""Static scope must flag real escapes and nothing else."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from screen_traces import _redirects_outside, static_action_findings  # noqa: E402


class DiscardingOutputIsNotAnEscape(unittest.TestCase):
    """`2>/dev/null` is the commonest idiom in a shell.

    Treating it as a write outside the workspace rejected every trace that
    used it — which was every VCF trace — so nothing was ever accepted and
    nothing was ever published.
    """

    def test_the_null_device_is_not_an_escape(self):
        for command in ("ls tools .moonshiner 2>/dev/null",
                        "pwsh -File t.ps1 >/dev/null 2>&1",
                        "cmd 1>/dev/stdout 2>/dev/stderr"):
            self.assertFalse(_redirects_outside(command), command)

    def test_a_real_escape_is_still_caught(self):
        for command in ("echo x >/tmp/evil.sh",
                        "echo x >>~/.bashrc",
                        "echo x > ../outside.txt"):
            self.assertTrue(_redirects_outside(command), command)

    def test_a_listing_is_never_a_write(self):
        actions = [{"tool": "bash", "command": "ls -la; ls docs .moonshiner 2>/dev/null",
                    "path": ""}]
        kinds = {f["kind"] for f in static_action_findings(actions)}
        self.assertNotIn("outside_workspace_write", kinds)
