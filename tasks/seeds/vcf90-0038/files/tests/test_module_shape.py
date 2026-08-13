"""PROTECTED FILE -- do not modify.

Checks that VcfEvcGuard is a real, loadable PowerShell module that declares its
dependency on the VMware.Sdk.Vcf PowerCLI modules instead of carrying a copy of
them.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import support  # noqa: E402

VENDORED_MARKERS = (
    "VMware.Sdk.Vcf.Installer.Cmdlets.dll",
    "VMware.Sdk.Vcf.SddcManager.Cmdlets.dll",
    "VMware.Binding.OpenApi.dll",
    "VMware.Sdk.OpenApi.dll",
)


class ModuleShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.require_pwsh()
        support.ensure_module_written()
        cls.report = support.run_driver(
            "inspect_module.ps1", {"ManifestPath": support.MANIFEST}
        )

    def test_the_manifest_is_valid_and_the_module_imports(self):
        if not self.report.get("ok"):
            self.fail(
                "VcfEvcGuard could not be imported from its manifest.\n%s"
                % (self.report.get("error") or self.report.get("_stdout"))
            )
        self.assertTrue(self.report.get("manifestValid"))

    def test_the_manifest_declares_a_root_module(self):
        self.assertEqual("VcfEvcGuard.psm1", self.report.get("rootModule"))

    def test_the_public_surface_is_the_two_documented_functions(self):
        self.assertEqual(
            ["Connect-VcfEvcGuardServer", "Invoke-VcfEvcModeGuardedSet"],
            sorted(self.report.get("exportedFunctions", [])),
        )

    def test_the_vcf_sdk_is_a_declared_dependency(self):
        required = self.report.get("requiredModules", [])
        sdk = [name for name in required if name.startswith("VMware.Sdk.Vcf.")]
        self.assertTrue(
            sdk,
            "the manifest must declare a VMware.Sdk.Vcf.* module in RequiredModules; "
            "it declares %r" % (required,),
        )

    def test_importing_the_module_brings_the_vcf_sdk_with_it(self):
        loaded = self.report.get("loadedSdkModules", [])
        self.assertTrue(
            loaded,
            "importing VcfEvcGuard must load the VMware.Sdk.Vcf PowerCLI module it depends on",
        )

    def test_the_sdk_is_not_vendored_into_the_workspace(self):
        found = []
        for base, directories, filenames in os.walk(support.ROOT):
            directories[:] = [d for d in directories if d not in (".git", "__pycache__")]
            for filename in filenames:
                if filename in VENDORED_MARKERS:
                    found.append(os.path.relpath(os.path.join(base, filename), support.ROOT))
        self.assertEqual(
            [],
            found,
            "the VMware.Sdk.Vcf modules are an environment prerequisite and must not be "
            "copied into the repository",
        )


if __name__ == "__main__":
    unittest.main()
