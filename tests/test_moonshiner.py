"""The single-process orchestrator: phase registry integrity and run planning."""
import pathlib
import sys
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

import moonshiner as m  # noqa: E402


class FrontDoor(unittest.TestCase):
    def test_help_leads_with_normal_jobs_not_phases(self):
        text = m._help()
        self.assertIn("moonshiner setup", text)
        self.assertIn("AUTHOR SEEDS", text)
        self.assertIn("moonshiner dataset build", text)
        self.assertNotIn("sec-generate", text)

    def test_no_arguments_sets_up_then_runs_one_trace(self):
        trace = mock.Mock(return_value=0)
        fake_module = mock.Mock(main=trace)
        with mock.patch.object(m, "_configured", return_value=False), \
             mock.patch.object(m, "_setup", return_value=0), \
             mock.patch.dict(sys.modules, {"trace_pipeline": fake_module}):
            self.assertEqual(m.main([]), 0)
        trace.assert_called_once_with([])

    def test_provider_presets_include_endpoint_protocol_and_key(self):
        for provider in ("openrouter", "openai", "anthropic"):
            preset = m.PROVIDER_PRESETS[provider]
            self.assertIn("base_url", preset)
            self.assertIn("api", preset)
            self.assertIn("key_env", preset)


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
