from __future__ import annotations

import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402


class EnvironmentPreflightTests(unittest.TestCase):
    def test_reference_setup_is_never_run_against_broken_baseline(self):
        seed = {"id": "reference-creates-helper",
                "reference_setup": "python3 .reference_solution.py"}
        with mock.patch.object(common, "materialize", return_value=ROOT), \
             mock.patch.object(common, "run_setup") as setup, \
             mock.patch.object(common, "run_verify",
                               return_value=(False, "expected baseline failure")):
            ready, detail = common.preflight_seed_environment(seed)
        self.assertTrue(ready)
        self.assertEqual(detail, "expected baseline failure")
        setup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
