"""The single-process orchestrator: phase registry integrity and run planning."""
import pathlib
import sys
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

import moonshiner as m  # noqa: E402
import configuration  # noqa: E402
import run_state  # noqa: E402


class FrontDoor(unittest.TestCase):
    def test_help_leads_with_normal_jobs_not_phases(self):
        text = m._help()
        self.assertIn("moonshiner setup", text)
        self.assertIn("AUTHOR SEEDS", text)
        self.assertIn("moonshiner dataset build", text)
        self.assertNotIn("sec-generate", text)

    def test_no_arguments_sets_up_then_starts_all_enabled_queues(self):
        start = mock.Mock(return_value=0)
        with mock.patch.object(configuration, "confirm_project", return_value=True), \
             mock.patch.object(m, "_configured", return_value=False), \
             mock.patch.object(m, "_setup", return_value=0), \
             mock.patch.object(m, "_start_default_queues", start):
            self.assertEqual(m.main([]), 0)
        start.assert_called_once_with()

    def test_provider_presets_include_endpoint_protocol_and_key(self):
        for provider in ("openrouter", "openai", "anthropic"):
            preset = m.PROVIDER_PRESETS[provider]
            self.assertIn("base_url", preset)
            self.assertIn("api", preset)
            self.assertIn("key_env", preset)

    def test_parallelism_and_publish_batch_bounds_fail_closed(self):
        with self.assertRaises(SystemExit):
            m._config(["set", "pipeline.trace.workers", "0"])
        with self.assertRaises(SystemExit):
            m._config(["set", "publish.batch_size", "1001"])

    def test_status_reports_project_even_before_first_ledger_run(self):
        db = mock.Mock()
        service_result = mock.Mock(stdout="", returncode=0)
        seed_status = {"total": 0, "sol_authored": 0, "remaining": 0,
                       "model": None}
        with mock.patch.object(run_state, "connect", return_value=db), \
             mock.patch.object(run_state, "summaries", return_value=[]), \
             mock.patch("seed_inventory.authored_ids", return_value=set()), \
             mock.patch("seed_inventory.planned_ids", return_value=set()), \
             mock.patch("seed_inventory.trace_state", return_value={
                 "target": set(), "accepted": set(), "active": set(),
                 "waiting": set(), "exhausted": set()}), \
             mock.patch.object(m.subprocess, "run", return_value=service_result), \
             mock.patch("builtins.print") as output:
            self.assertEqual(m._status([]), 0)
        self.assertTrue(any(call.args == ("Moonshiner status",)
                            for call in output.call_args_list))

    def test_argless_phase_does_not_receive_console_arguments(self):
        phase = m.Phase("build", 1, "Build", "fake", takes_argv=False)
        module = mock.Mock()
        module.main.side_effect = lambda: 0 if sys.argv == ["moonshiner build"] else 1
        with mock.patch.object(m.importlib, "import_module", return_value=module), \
             mock.patch.object(sys, "argv", ["moonshiner", "dataset", "build"]):
            self.assertEqual(m._dispatch(phase, []), 0)
            self.assertEqual(sys.argv, ["moonshiner", "dataset", "build"])

    def test_service_stop_targets_exact_moonshiner_unit(self):
        completed = mock.Mock(returncode=0)
        with mock.patch.object(m.subprocess, "run", return_value=completed) as run:
            self.assertEqual(m._service(["stop", "moonshiner-trace-example"]), 0)
        run.assert_called_once_with(
            ["systemctl", "--user", "stop", "moonshiner-trace-example.service"])

    def test_service_stop_rejects_non_moonshiner_unit(self):
        with self.assertRaises(SystemExit):
            m._service(["stop", "ssh"])

    def test_update_uses_official_installer_and_reports_version(self):
        pipe = mock.Mock()
        pipe.stdout = mock.Mock()
        pipe.wait.return_value = 0
        installed = mock.Mock(returncode=0)
        version = mock.Mock(returncode=0)
        with mock.patch.object(m.shutil, "which", side_effect=[
                 "/usr/bin/curl", "/bin/bash", "/installed/bin/moonshiner"]), \
             mock.patch.object(m.subprocess, "Popen", return_value=pipe) as popen, \
             mock.patch.object(m.subprocess, "run", side_effect=[installed, version]) as run:
            self.assertEqual(m._update([]), 0)
        self.assertIn("greghavens/moonshiner/main/install.sh", popen.call_args.args[0][-1])
        self.assertEqual(run.call_args_list[0].args[0], ["/bin/bash"])
        self.assertEqual(run.call_args_list[1].args[0][-1], "--version")


class Registry(unittest.TestCase):
    def test_keys_are_unique_and_indexed(self):
        keys = [phase.key for phase in m.PHASES]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(m.BY_KEY), set(keys))

    def test_orders_are_strictly_increasing(self):
        orders = [phase.order for phase in m.PHASES]
        self.assertEqual(orders, sorted(orders))
        self.assertEqual(len(orders), len(set(orders)))

    def test_full_run_excludes_optional_phases(self):
        self.assertTrue(all(not phase.optional for phase in m.FULL))
        self.assertEqual({p.key for p in m.PHASES} - {p.key for p in m.FULL},
                         {p.key for p in m.PHASES if p.optional})

    def test_argless_phases_declare_no_run_argv(self):
        for phase in m.PHASES:
            if not phase.takes_argv:
                self.assertEqual(phase.run_argv, ())


class Plan(unittest.TestCase):
    def keys(self, *args):
        return [phase.key for phase in m._plan(*args)]

    def test_default_plan_is_the_full_ordered_pipeline(self):
        self.assertEqual(self.keys(None, None, [], [], False),
                         [phase.key for phase in m.FULL])

    def test_offline_drops_every_metered_phase(self):
        plan = m._plan(None, None, [], [], True)
        self.assertTrue(all(not phase.metered for phase in plan))
        self.assertNotIn("generate", [phase.key for phase in plan])
        self.assertNotIn("screen", [phase.key for phase in plan])

    def test_from_to_is_an_inclusive_slice(self):
        self.assertEqual(self.keys("generate", "screen", [], [], False),
                         ["generate", "screen"])

    def test_with_folds_optional_phase_in_at_its_order(self):
        keys = self.keys(None, None, ["validate"], [], False)
        self.assertIn("validate", keys)
        # validate (2.5) sits right after audit (2), before generate (3).
        self.assertEqual(keys.index("audit") + 1, keys.index("validate"))

    def test_skip_removes_a_phase(self):
        self.assertNotIn("import", self.keys(None, None, [], ["import"], False))

    def test_offline_can_yield_an_empty_plan(self):
        # A single metered phase, requested offline, plans to nothing.
        self.assertEqual(m._plan("generate", "generate", [], [], True), [])


if __name__ == "__main__":
    unittest.main()
