"""The single-process orchestrator: phase registry integrity and run planning."""
import pathlib
import json
import tempfile
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
    def test_first_run_wizard_uses_trace_only_defaults_and_resumes_dataset(self):
        config = json.loads((_ROOT / "config.json").read_text())
        updates = {}
        with tempfile.TemporaryDirectory() as directory:
            key_path = pathlib.Path(directory) / "key"
            with mock.patch.object(configuration, "load_config", return_value=config), \
                 mock.patch.object(configuration, "update_local",
                                   side_effect=lambda key, value: updates.__setitem__(key, value)), \
                 mock.patch.object(m, "_ask",
                                   side_effect=["openrouter", "acme/orion-7b",
                                                "owner/orion-data"]) as ask, \
                 mock.patch.object(m.getpass, "getpass", return_value="secret"), \
                 mock.patch.object(m.shutil, "which", return_value="/usr/bin/pi"), \
                 mock.patch("common.key_env_name", return_value="OPENROUTER_API_KEY"), \
                 mock.patch("common.key_persist_path", return_value=key_path), \
                 mock.patch("import_existing.main", return_value=0) as resume:
                self.assertEqual(m._setup([]), 0)
        self.assertEqual(ask.call_count, 3)
        self.assertFalse(updates["pipeline.queues.seed_authoring"])
        self.assertTrue(updates["pipeline.queues.tracing"])
        self.assertEqual(updates["pipeline.trace.workers"], 2)
        self.assertEqual(updates["pipeline.trace.max_attempts"], 2)
        self.assertEqual(updates["publish.batch_size"], 10)
        self.assertEqual(updates["judge.runtime"], "codex")
        self.assertEqual(updates["teacher.model"], "acme/orion-7b")
        self.assertEqual(updates["model_profile"]["display_name"], "Orion 7B")
        self.assertNotIn("Kimi", json.dumps(updates))
        self.assertNotIn("Fable", json.dumps(updates))
        resume.assert_called_once_with(["--hf", "owner/orion-data"])

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

    def test_queue_liveness_is_scoped_to_this_project(self):
        config = {"pipeline": {"queues": {"seed_authoring": False,
                                             "tracing": True}}}
        inactive = mock.Mock(returncode=3)
        started = mock.Mock(returncode=0)
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch("common.CONFIG", config), \
             mock.patch("common.RUNS", pathlib.Path(directory)), \
             mock.patch.object(m, "_ensure_configured_pi"), \
             mock.patch.object(m.subprocess, "run",
                               side_effect=[inactive, started]) as run:
            self.assertEqual(m._start_default_queues(), 0)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any(command[0] == "pgrep" for command in commands))
        project_key = m.hashlib.sha256(
            str(configuration.PROJECT_ROOT).encode()).hexdigest()[:12]
        self.assertEqual(commands[0], ["systemctl", "--user", "is-active",
                                       "--quiet",
                                       f"moonshiner-trace-continuous-{project_key}.service"])
        self.assertIn(f"--unit=moonshiner-trace-continuous-{project_key}",
                      commands[1])

    def test_enabled_synthetic_correction_queue_starts_with_moonshiner(self):
        config = {"pipeline": {"queues": {"seed_authoring": False,
                                             "tracing": False}},
                  "synthetic_corrections": {"enabled": True}}
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch("common.CONFIG", config), \
             mock.patch("common.RUNS", pathlib.Path(directory)), \
             mock.patch.object(m, "_ensure_configured_pi"), \
             mock.patch.object(m.subprocess, "run",
                               side_effect=[mock.Mock(returncode=3),
                                            mock.Mock(returncode=0)]) as run:
            self.assertEqual(m._start_default_queues(), 0)
        command = run.call_args_list[1].args[0]
        self.assertIn("synthetic-corrections", command)
        self.assertEqual(command[-3:], ["synthetic-corrections", "run", "--yes"])

    def test_normal_start_provisions_missing_pi_in_stable_user_toolchain(self):
        config = {"teacher": {"runtime": "pi-openrouter"},
                  "runtimes": {"pi-openrouter": {"cli": "pi",
                                                   "managed_runtime_version": "1.2.3"}}}
        completed = mock.Mock(returncode=0)
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(configuration, "load_config", return_value=config), \
             mock.patch.object(configuration, "PROJECT_ROOT",
                               pathlib.Path(directory) / "project"), \
             mock.patch.dict("os.environ", {"XDG_DATA_HOME": directory}), \
             mock.patch.object(m.shutil, "which",
                               side_effect=lambda name: "/usr/bin/npm" if name == "npm" else None), \
             mock.patch.object(m.subprocess, "run", return_value=completed) as run:
            m._ensure_configured_pi()
        command = run.call_args.args[0]
        self.assertIn(str(pathlib.Path(directory) / "moonshiner" /
                          "toolchains" / "pi"), command)
        self.assertIn("@earendil-works/pi-coding-agent@1.2.3", command)

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

    def test_publication_format_config_fails_closed(self):
        with self.assertRaises(SystemExit):
            m._config(["set", "publish.format", "model-specific-format"])

    def test_status_reports_project_even_before_first_ledger_run(self):
        db = mock.Mock()
        service_result = mock.Mock(stdout="", returncode=0)
        seed_status = {"total": 0, "sol_authored": 0, "remaining": 0,
                       "model": None}
        with mock.patch.object(run_state, "connect", return_value=db), \
             mock.patch.object(run_state, "summaries", return_value=[]) as summaries, \
             mock.patch("seed_inventory.inventory_sets",
                        return_value=(set(), set(), set())), \
             mock.patch("seed_inventory.planned_ids", return_value=set()), \
             mock.patch("seed_inventory.accepted_ids", return_value=set()) as accepted, \
             mock.patch("seed_inventory.retired_seed_ids", return_value=set()), \
             mock.patch("seed_inventory.trace_state", return_value={
                 "target": set(), "accepted": set(), "active": set(),
                 "waiting": set(), "exhausted": set(),
                 "needs_reauthoring": set()}), \
             mock.patch("run_state.live_trace_run_ids", return_value=set()), \
             mock.patch("publish_queue.accepted_tasks", return_value=[]), \
             mock.patch.object(m.subprocess, "run", return_value=service_result), \
             mock.patch("builtins.print") as output:
            self.assertEqual(m._status([]), 0)
        summaries.assert_called_once_with(db, None, running_only=True)
        accepted.assert_called_once_with(db, include_review_files=False)
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

    def test_service_drain_pauses_only_the_coordinator_process(self):
        with mock.patch.object(m.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertEqual(m._service(["drain", "moonshiner-trace-example"]), 0)
        run.assert_called_once_with([
            "systemctl", "--user", "kill", "--kill-whom=main", "--signal=SIGSTOP",
            "moonshiner-trace-example.service",
        ])

    def test_service_restart_resolves_publisher_for_current_project(self):
        completed = mock.Mock(returncode=0)
        expected = m.hashlib.sha256(
            str(configuration.PROJECT_ROOT).encode()).hexdigest()[:12]
        with mock.patch.object(m.subprocess, "run", return_value=completed) as run, \
             mock.patch("trace_pipeline.ensure_publish_queue") as ensure:
            self.assertEqual(m._service(["restart", "publisher"]), 0)
        self.assertEqual(run.call_args_list, [mock.call([
            "systemctl", "--user", "stop",
            f"moonshiner-publish-{expected}.service",
        ]), mock.call([
            "systemctl", "--user", "reset-failed",
            f"moonshiner-publish-{expected}.service",
        ])])
        ensure.assert_called_once_with()

    def test_trace_coordinator_restart_recreates_from_current_release(self):
        completed = mock.Mock(returncode=0)
        name = "moonshiner-trace-continuous-example"
        with mock.patch.object(m.subprocess, "run", return_value=completed) as run:
            self.assertEqual(m._service(["restart", name]), 0)
        calls = run.call_args_list
        self.assertEqual(calls[0], mock.call(
            ["systemctl", "--user", "stop", f"{name}.service"]))
        recreated = calls[2].args[0]
        self.assertEqual(recreated[:5],
                         ["systemd-run", "--user", "--collect", f"--unit={name}",
                          f"--property=WorkingDirectory={configuration.PROJECT_ROOT}"])
        self.assertIn("--setenv=MOONSHINER_SUPERVISED=1", recreated)
        self.assertEqual(recreated[-3:], ["run", "--all", "--yes"])
        self.assertEqual(pathlib.Path(recreated[-4]).parent,
                         pathlib.Path(sys.executable).parent)

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
