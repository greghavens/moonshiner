from __future__ import annotations

import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402
import toolchains  # noqa: E402


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

    def test_declared_tools_and_modules_are_provisioned_before_verification(self):
        seed = {"id": "powershell-seed", "prerequisites": {
            "commands": ["python3", "pwsh"],
            "powershell_modules": [{
                "name": "VMware.Sdk.Vcf.SddcManager",
                "minimum_version": "13.5.0.25380678"}]}}
        order: list[str] = []
        with mock.patch.object(common, "materialize", return_value=ROOT), \
             mock.patch.object(toolchains, "provision",
                               side_effect=lambda _commands: (
                                   order.append("commands") or (True, "ready"))), \
             mock.patch.object(toolchains, "provision_powershell_modules",
                               side_effect=lambda _modules: (
                                   order.append("modules") or (True, "ready"))), \
             mock.patch.object(common, "run_verify",
                               side_effect=lambda *_args: (
                                   order.append("verify") or
                                   (False, "expected baseline failure"))):
            ready, _ = common.preflight_seed_environment(seed)
        self.assertTrue(ready)
        self.assertEqual(order, ["commands", "modules", "verify"])

    def test_module_deployment_failure_is_infrastructure(self):
        seed = {"id": "powershell-seed", "prerequisites": [{
            "kind": "powershell-module", "name": "VMware.Module",
            "version": "1.2.3"}]}
        with mock.patch.object(toolchains, "provision", return_value=(True, "ready")), \
             mock.patch.object(toolchains, "provision_powershell_modules",
                               return_value=(False, "gallery unavailable")), \
             mock.patch.object(common, "materialize") as materialize:
            ready, detail = common.preflight_seed_environment(seed)
        self.assertFalse(ready)
        self.assertEqual(detail, "gallery unavailable")
        materialize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
