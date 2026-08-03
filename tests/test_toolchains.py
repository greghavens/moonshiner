from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import configuration  # noqa: E402
import toolchains  # noqa: E402


class DeclaredPowerShellRequirements(unittest.TestCase):
    def test_standalone_environment_assignments_are_not_executables(self):
        seed = {"prerequisites": {"commands": [
            "PYTHONDONTWRITEBYTECODE=1", " LC_ALL=C ", "python3"]}}
        self.assertEqual(toolchains.declared_commands(seed), ["python3"])

    def test_unknown_executable_names_remain_fail_closed(self):
        seed = {"prerequisites": {"commands": [
            "PYTHONDONTWRITEBYTECODE=1", "unknown-seed-tool"]}}
        commands = toolchains.declared_commands(seed)
        self.assertEqual(commands, ["unknown-seed-tool"])
        with mock.patch.object(toolchains.shutil, "which", return_value=None):
            ready, detail = toolchains.provision(commands)
        self.assertFalse(ready)
        self.assertIn("no Moonshiner toolchain package mapping", detail)
        self.assertIn("unknown-seed-tool", detail)

    def test_normalizes_all_seed_module_shapes_to_exact_versions(self):
        seeds = [
            {"prerequisites": {
                "commands": ["python3", "pwsh"],
                "powershell_modules": [{
                    "name": "VMware.Sdk.Vcf.SddcManager",
                    "minimum_version": "13.5.0.25380678"}]}},
            {"prerequisites": {
                "commands": ["pwsh"], "powershell": "7.2 or later",
                "modules": [{"name": "VMware.Sdk.Vcf.SddcManager",
                             "version": "13.5.0.25380678"}]}},
            {"prerequisites": [{"kind": "powershell-module",
                                "name": "VMware.Sdk.Vcf.SddcManager",
                                "version": "13.5.0.25380678",
                                "provided_by_environment": True}]},
            {"prerequisites": [
                "PowerShell 7",
                "VMware.Sdk.Vcf.SddcManager 13.5.0.25380678 or later"]},
        ]
        for seed in seeds:
            with self.subTest(seed=seed):
                self.assertEqual(toolchains.declared_powershell_modules(seed), [
                    ("VMware.Sdk.Vcf.SddcManager", "13.5.0.25380678")])
                self.assertIn("pwsh", toolchains.declared_commands(seed))


class PowerShellModuleDeployment(unittest.TestCase):
    def test_save_module_uses_exact_version_and_project_path(self):
        missing = subprocess.CompletedProcess([], 1, "", "")
        saved = subprocess.CompletedProcess([], 0, "", "")
        available = subprocess.CompletedProcess([], 0, "", "")
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(configuration, "PROJECT_STATE",
                               pathlib.Path(tmp)), \
             mock.patch.object(toolchains, "powershell_runtime",
                               return_value=pathlib.Path("/opt/pwsh/pwsh")), \
             mock.patch.object(toolchains.subprocess, "run",
                               side_effect=[missing, saved, available]) as run:
            ready, detail = toolchains.provision_powershell_modules([
                ("VMware.Sdk.Vcf.SddcManager", "13.5.0.25380678")])
        self.assertTrue(ready, detail)
        save_call = run.call_args_list[1]
        self.assertIn("Save-Module", save_call.args[0][-1])
        self.assertIn("-RequiredVersion", save_call.args[0][-1])
        self.assertEqual(save_call.kwargs["env"]["MOONSHINER_MODULE_NAME"],
                         "VMware.Sdk.Vcf.SddcManager")
        self.assertEqual(save_call.kwargs["env"]["MOONSHINER_MODULE_VERSION"],
                         "13.5.0.25380678")
        self.assertEqual(pathlib.Path(
            save_call.kwargs["env"]["MOONSHINER_MODULE_PATH"]),
            pathlib.Path(tmp) / "toolchains" / "powershell" / "Modules")


if __name__ == "__main__":
    unittest.main()
