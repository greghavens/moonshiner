"""Claude Code login discovery, preflight, and in-tool repair.

Regression cover for a silent outage: ``~/.claude`` was renamed, the OAuth
login went with it, and nothing noticed. ``preflight`` accepted
``require_auth`` and ignored it, so ``doctor`` reported a ready harness; the
queue only learned the truth after claiming a seed, where an unauthenticated
harness counts as an infrastructure failure and stops the whole run.
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import control_cli  # noqa: E402
from runtimes import claude_code  # noqa: E402
from runtimes.claude_code import ClaudeCodeRuntime  # noqa: E402

LOGIN = json.dumps({"claudeAiOauth": {"accessToken": "not-a-real-token"}})


def _runtime(runtime_config=None):
    config = {"runtimes": {"claude-code": runtime_config or {}}}
    return ClaudeCodeRuntime(config, {"model": "claude-fable-5"})


class _Home(unittest.TestCase):
    """A throwaway HOME with no ambient CLAUDE_CONFIG_DIR or token."""

    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.home,
                        ignore_errors=True)
        environ = mock.patch.dict(os.environ, {"HOME": str(self.home)},
                                  clear=False)
        environ.start()
        self.addCleanup(environ.stop)
        for name in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN"):
            os.environ.pop(name, None)
        home = mock.patch.object(pathlib.Path, "home",
                                 staticmethod(lambda: self.home))
        home.start()
        self.addCleanup(home.stop)

    def _write(self, directory: str, text: str = LOGIN) -> pathlib.Path:
        path = self.home / directory / claude_code.CREDENTIAL_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path


class Discovery(_Home):
    def test_the_active_config_directory_is_the_login(self):
        expected = self._write(".claude")
        self.assertEqual(claude_code.account_credential(), expected)

    def test_no_login_resolves_to_nothing(self):
        (self.home / ".claude").mkdir()
        self.assertIsNone(claude_code.account_credential())

    def test_claude_config_dir_overrides_the_default_home(self):
        expected = self._write("elsewhere")
        with mock.patch.dict(os.environ,
                             {"CLAUDE_CONFIG_DIR": str(self.home / "elsewhere")}):
            self.assertEqual(claude_code.account_credential(), expected)

    def test_a_renamed_config_directory_is_reported_not_adopted(self):
        """The exact outage: the login is fine, the directory name is not."""
        displaced = self._write(".claude-broken")
        (self.home / ".claude").mkdir()
        self.assertIsNone(claude_code.account_credential())
        self.assertEqual(claude_code.displaced_credentials(), [displaced])

    def test_the_active_directory_never_reports_itself_as_displaced(self):
        self._write(".claude")
        self.assertEqual(claude_code.displaced_credentials(), [])

    def test_adopting_installs_the_login_private(self):
        source = self._write(".claude-broken")
        destination = claude_code.adopt_credential(source)
        self.assertEqual(destination, self.home / ".claude" / ".credentials.json")
        self.assertEqual(destination.read_text(), LOGIN)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)


class Preflight(_Home):
    def setUp(self):
        super().setUp()
        which = mock.patch("runtimes.claude_code.shutil.which",
                           return_value="/usr/bin/claude")
        which.start()
        self.addCleanup(which.stop)
        run = mock.patch("runtimes.claude_code.subprocess.run")
        run.start()
        self.addCleanup(run.stop)

    def test_a_missing_login_fails_preflight(self):
        (self.home / ".claude").mkdir()
        with self.assertRaises(SystemExit) as caught:
            _runtime().preflight(require_auth=True)
        self.assertIn("not authenticated", str(caught.exception))

    def test_the_failure_names_the_displaced_login_and_the_repair(self):
        self._write(".claude-broken")
        with self.assertRaises(SystemExit) as caught:
            _runtime().preflight(require_auth=True)
        message = str(caught.exception)
        self.assertIn(".claude-broken", message)
        self.assertIn("moonshiner auth set claude-code", message)

    def test_a_present_login_passes(self):
        self._write(".claude")
        _runtime().preflight(require_auth=True)

    def test_an_oauth_token_in_the_environment_passes(self):
        (self.home / ".claude").mkdir()
        with mock.patch.dict(os.environ,
                             {"CLAUDE_CODE_OAUTH_TOKEN": "not-a-real-token"}):
            _runtime().preflight(require_auth=True)

    def test_the_cli_check_still_runs_without_require_auth(self):
        (self.home / ".claude").mkdir()
        _runtime().preflight()


class SandboxBinding(_Home):
    def test_the_bind_source_is_the_path_preflight_validated(self):
        """A check that resolves a different file than the bind is worthless."""
        expected = self._write("elsewhere")
        sandbox = str(self.home / "workspace" / ".sandbox-home" / "claude")
        with mock.patch.dict(os.environ,
                             {"CLAUDE_CONFIG_DIR": str(self.home / "elsewhere")}):
            self.assertEqual(claude_code.account_credential(), expected)
            bindings = ClaudeCodeRuntime._auth_bindings(
                {"CLAUDE_CONFIG_DIR": sandbox})
        self.assertEqual(
            bindings,
            ((expected, pathlib.Path(sandbox) / ".credentials.json"),))

    def test_nothing_is_bound_when_there_is_no_login(self):
        (self.home / ".claude").mkdir()
        self.assertEqual(
            ClaudeCodeRuntime._auth_bindings({"CLAUDE_CONFIG_DIR": "/x"}), ())


class AuthCommand(_Home):
    def _run(self, argv):
        with mock.patch("builtins.print") as printed:
            code = control_cli.auth_main(argv)
        return code, "\n".join(str(call.args[0]) for call in
                               printed.call_args_list if call.args)

    def test_claude_code_is_a_credential_target(self):
        provider, _ = control_cli._credential_target("claude-code")
        self.assertEqual(provider, "claude-code")

    def test_status_reports_the_active_login(self):
        self._write(".claude")
        code, output = self._run(["status", "claude-code"])
        self.assertEqual(code, 0)
        self.assertIn("configured via", output)

    def test_status_fails_and_points_at_the_displaced_login(self):
        self._write(".claude-broken")
        code, output = self._run(["status", "claude-code"])
        self.assertEqual(code, 1)
        self.assertIn(".claude-broken", output)
        self.assertIn("moonshiner auth set claude-code", output)

    def test_set_adopts_the_displaced_login(self):
        self._write(".claude-broken")
        code, output = self._run(["set", "claude-code"])
        self.assertEqual(code, 0)
        self.assertIn("adopted", output)
        self.assertEqual(claude_code.account_credential(),
                         self.home / ".claude" / ".credentials.json")

    def test_set_refuses_to_guess_between_several_logins(self):
        self._write(".claude-broken")
        self._write(".claude.bak")
        code, _ = self._run(["set", "claude-code"])
        self.assertEqual(code, 2)
        self.assertIsNone(claude_code.account_credential())

    def test_set_from_names_one_explicitly(self):
        chosen = self._write(".claude.bak")
        self._write(".claude-broken")
        code, _ = self._run(["set", "claude-code", "--from", str(chosen)])
        self.assertEqual(code, 0)
        self.assertEqual(
            (self.home / ".claude" / ".credentials.json").read_text(), LOGIN)

    def test_set_leaves_a_working_login_alone(self):
        self._write(".claude")
        self._write(".claude-broken", "stale")
        code, output = self._run(["set", "claude-code"])
        self.assertEqual(code, 0)
        self.assertIn("already configured", output)
        self.assertEqual(
            (self.home / ".claude" / ".credentials.json").read_text(), LOGIN)

    def test_remove_does_not_silently_destroy_a_cli_login(self):
        self._write(".claude")
        code, _ = self._run(["remove", "claude-code"])
        self.assertEqual(code, 2)
        self.assertIsNotNone(claude_code.account_credential())


if __name__ == "__main__":
    unittest.main()
