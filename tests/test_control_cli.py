"""Human authentication commands resolve providers, not harness names."""
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import control_cli


class CredentialTargets(unittest.TestCase):
    CONFIG = {"runtimes": {
        "pi-openrouter": {"provider": "openrouter", "display_provider": "OpenRouter"},
        "codex": {"cli": "codex"},
    }}

    def test_provider_name_resolves_pi_profile(self):
        with mock.patch.object(control_cli, "CONFIG", self.CONFIG):
            provider, config = control_cli._credential_target("openrouter")
        self.assertEqual(provider, "openrouter")
        self.assertEqual(config["display_provider"], "OpenRouter")

    def test_cli_harness_remains_a_compatibility_alias(self):
        with mock.patch.object(control_cli, "CONFIG", self.CONFIG):
            provider, _ = control_cli._credential_target("codex")
        self.assertEqual(provider, "codex")


if __name__ == "__main__":
    unittest.main()


class DoctorCoversSeedAuthoring(unittest.TestCase):
    """A project that authors seeds has four runtimes, not two.

    Preflighting only the tracing pair reported a healthy system while every
    seed failed on an unauthenticated or missing seed runtime.
    """

    def _run(self, seed_authoring):
        import control_cli
        ready = mock.Mock(name="ready")
        ready.name = "codex"
        ready.role = {"model": "gpt-5.6-sol"}
        config = {"pipeline": {"queues": {"seed_authoring": seed_authoring}}}
        with mock.patch.dict(control_cli.CONFIG, config, clear=False), \
             mock.patch.object(control_cli, "get_teacher", return_value=ready), \
             mock.patch.object(control_cli, "get_judge", return_value=ready), \
             mock.patch.object(control_cli, "get_seed_author", return_value=ready) as author, \
             mock.patch.object(control_cli, "get_seed_judge", return_value=ready) as judge, \
             mock.patch.object(control_cli, "trace_harness_alternatives",
                               return_value=[]), \
             mock.patch("builtins.print"):
            control_cli.doctor_main([])
        return author, judge

    def test_the_seed_runtimes_are_checked_when_the_queue_is_on(self):
        author, judge = self._run(True)
        author.assert_called_once()
        judge.assert_called_once()

    def test_they_are_not_checked_when_the_queue_is_off(self):
        author, judge = self._run(False)
        author.assert_not_called()
        judge.assert_not_called()


class DoctorCoversAlternativeTraceHarnesses(unittest.TestCase):
    """A harness in ``harness_order`` is only authenticated once chosen.

    That happens mid-run with a seed already claimed, where the failure is
    terminal and stops the queue. Doctor has to ask the question earlier.
    """

    def _run(self, alternative):
        import control_cli
        ready = mock.Mock(name="ready")
        ready.name = "opencode"
        ready.role = {"model": "anthropic/claude-fable-5"}
        config = {"pipeline": {"queues": {"seed_authoring": False}}}
        with mock.patch.dict(control_cli.CONFIG, config, clear=False), \
             mock.patch.object(control_cli, "get_teacher", return_value=ready), \
             mock.patch.object(control_cli, "get_judge", return_value=ready), \
             mock.patch.object(control_cli, "trace_harness_alternatives",
                               return_value=[alternative]), \
             mock.patch("builtins.print") as printed:
            code = control_cli.doctor_main([])
        return code, "\n".join(str(call.args[0]) for call in
                               printed.call_args_list if call.args)

    @staticmethod
    def _alternative(error=None):
        runtime = mock.Mock(name="alternative")
        runtime.name = "claude-code"
        runtime.role = {"model": "claude-fable-5"}
        runtime.preflight.side_effect = error
        return runtime

    def test_an_unauthenticated_alternative_fails_the_check(self):
        alternative = self._alternative(SystemExit("claude-code not authenticated"))
        code, output = self._run(alternative)
        alternative.preflight.assert_called_once_with(require_auth=True)
        self.assertEqual(code, 1)
        self.assertIn("trace harness claude-code", output)
        self.assertIn("not authenticated", output)

    def test_a_healthy_alternative_passes(self):
        code, output = self._run(self._alternative())
        self.assertEqual(code, 0)
        self.assertIn("trace harness claude-code", output)
