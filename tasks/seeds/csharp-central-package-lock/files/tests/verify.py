#!/usr/bin/env python3
"""Protected verifier for the central-package lock-file repair seed."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
JSON_PACKAGE = "Moon.Security.Json"
JSON_VERSION = "4.2.1"
WINDOWS_PACKAGE = "Moon.Windows.CredentialStore"
WINDOWS_VERSION = "2.4.0"


def xml_root(relative_path: str) -> ET.Element:
    return ET.parse(ROOT / relative_path).getroot()


def property_values(relative_path: str) -> dict[str, str]:
    root = xml_root(relative_path)
    return {
        child.tag: (child.text or "").strip()
        for group in root.findall("PropertyGroup")
        for child in group
    }


def package_hash(package_id: str, version: str) -> str:
    path = ROOT / "packages" / f"{package_id}.{version}.nupkg"
    digest = hashlib.sha512(path.read_bytes()).digest()
    return base64.b64encode(digest).decode("ascii")


def assert_direct_entry(
    case: unittest.TestCase,
    dependencies: dict[str, object],
    package_id: str,
    version: str,
) -> None:
    case.assertIn(package_id, dependencies)
    entry = dependencies[package_id]
    case.assertEqual("Direct", entry.get("type"))
    case.assertEqual(f"[{version}, )", entry.get("requested"))
    case.assertEqual(version, entry.get("resolved"))
    case.assertEqual(package_hash(package_id, version), entry.get("contentHash"))


class CentralPackageContractTests(unittest.TestCase):
    def test_central_versions_are_the_only_version_authority(self) -> None:
        root = xml_root("Directory.Packages.props")
        props = property_values("Directory.Packages.props")
        self.assertEqual("true", props.get("ManagePackageVersionsCentrally"))
        self.assertEqual("true", props.get("CentralPackageTransitivePinningEnabled"))
        self.assertEqual("false", props.get("CentralPackageVersionOverrideEnabled"))

        versions = {
            item.attrib["Include"]: item.attrib["Version"]
            for item in root.findall("./ItemGroup/PackageVersion")
        }
        self.assertEqual(
            {JSON_PACKAGE: JSON_VERSION, WINDOWS_PACKAGE: WINDOWS_VERSION},
            versions,
        )

        for project in sorted((ROOT / "src").glob("*/*.csproj")):
            for reference in ET.parse(project).getroot().findall(
                "./ItemGroup/PackageReference"
            ):
                self.assertNotIn("Version", reference.attrib, project.as_posix())
                self.assertNotIn("VersionOverride", reference.attrib, project.as_posix())

    def test_windows_dependency_remains_intentionally_target_specific(self) -> None:
        project = xml_root("src/Desktop/Desktop.csproj")
        frameworks = project.findtext("./PropertyGroup/TargetFrameworks")
        self.assertEqual("net10.0;net10.0-windows", frameworks)

        placements: list[tuple[str, str]] = []
        for group in project.findall("ItemGroup"):
            condition = group.attrib.get("Condition", "")
            for reference in group.findall("PackageReference"):
                placements.append((reference.attrib["Include"], condition))

        self.assertIn((JSON_PACKAGE, ""), placements)
        windows_placements = [
            condition for name, condition in placements if name == WINDOWS_PACKAGE
        ]
        self.assertEqual(1, len(windows_placements))
        self.assertRegex(
            windows_placements[0],
            r"TargetFramework.*==.*net10\.0-windows",
        )

    def test_locked_restore_and_vulnerability_controls_stay_enabled(self) -> None:
        props = property_values("Directory.Build.props")
        expected = {
            "RestorePackagesWithLockFile": "true",
            "RestoreLockedMode": "true",
            "NuGetAudit": "true",
            "NuGetAuditMode": "all",
            "NuGetAuditLevel": "high",
        }
        for name, value in expected.items():
            self.assertEqual(value, props.get(name), name)
        warnings = set(filter(None, props.get("WarningsAsErrors", "").split(";")))
        self.assertTrue({"NU1903", "NU1904"}.issubset(warnings))

        policy = json.loads((ROOT / "eng/dependency-policy.json").read_text())
        package_policy = policy["packages"][JSON_PACKAGE]
        self.assertEqual(JSON_VERSION, package_policy["minimumVersion"])
        self.assertIn("4.2.0", package_policy["blockedVersions"])
        self.assertEqual(
            {
                "enabled": True,
                "mode": "all",
                "level": "high",
                "warningsAsErrors": ["NU1903", "NU1904"],
            },
            policy["nugetAudit"],
        )

        config = xml_root("NuGet.Config")
        sources = config.findall("./packageSources/add")
        self.assertIsNotNone(config.find("./packageSources/clear"))
        self.assertEqual([("seed-local", "./packages")], [
            (source.attrib["key"], source.attrib["value"]) for source in sources
        ])
        self.assertFalse(
            any(re.match(r"^[a-z]+://", source.attrib["value"], re.I) for source in sources)
        )


class LockSnapshotTests(unittest.TestCase):
    def test_core_lock_matches_the_approved_central_pin(self) -> None:
        lock = json.loads((ROOT / "src/Core/packages.lock.json").read_text())
        self.assertEqual(2, lock.get("version"))
        self.assertEqual({"net10.0"}, set(lock["dependencies"]))
        target = lock["dependencies"]["net10.0"]
        self.assertEqual({JSON_PACKAGE}, set(target))
        assert_direct_entry(self, target, JSON_PACKAGE, JSON_VERSION)

    def test_desktop_lock_preserves_target_specific_shape(self) -> None:
        lock = json.loads((ROOT / "src/Desktop/packages.lock.json").read_text())
        self.assertEqual(2, lock.get("version"))
        targets = lock["dependencies"]
        self.assertIn("net10.0", targets)
        windows_targets = [name for name in targets if name.startswith("net10.0-windows")]
        self.assertEqual(1, len(windows_targets))
        self.assertEqual(2, len(targets))

        portable = targets["net10.0"]
        windows = targets[windows_targets[0]]
        self.assertEqual({JSON_PACKAGE, "core"}, set(portable))
        self.assertEqual({JSON_PACKAGE, WINDOWS_PACKAGE, "core"}, set(windows))
        assert_direct_entry(self, portable, JSON_PACKAGE, JSON_VERSION)
        assert_direct_entry(self, windows, JSON_PACKAGE, JSON_VERSION)
        assert_direct_entry(self, windows, WINDOWS_PACKAGE, WINDOWS_VERSION)
        self.assertNotIn(WINDOWS_PACKAGE, portable)

        for target in (portable, windows):
            self.assertEqual("Project", target["core"].get("type"))
            self.assertEqual(
                {JSON_PACKAGE: f"[{JSON_VERSION}, )"},
                target["core"].get("dependencies"),
            )

    def test_blocked_version_is_absent_from_all_locks(self) -> None:
        for lock_path in sorted((ROOT / "src").glob("*/packages.lock.json")):
            self.assertNotIn("4.2.0", lock_path.read_text(), lock_path.as_posix())


class OfflineRestoreTests(unittest.TestCase):
    def test_locked_restore_and_all_target_builds_succeed_offline(self) -> None:
        dotnet = shutil.which("dotnet")
        self.assertIsNotNone(dotnet, "dotnet SDK is required for this C# seed")

        with tempfile.TemporaryDirectory(prefix="central-lock-") as temporary:
            temp = Path(temporary)
            checkout = temp / "checkout"
            shutil.copytree(
                ROOT,
                checkout,
                ignore=shutil.ignore_patterns(
                    ".artifacts", "bin", "obj", "__pycache__", "*.pyc"
                ),
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(temp / "home"),
                    "XDG_CACHE_HOME": str(temp / "xdg-cache"),
                    "XDG_CONFIG_HOME": str(temp / "xdg-config"),
                    "XDG_DATA_HOME": str(temp / "xdg-data"),
                    "DOTNET_CLI_HOME": str(temp / "dotnet-home"),
                    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
                    "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
                    "DOTNET_NOLOGO": "1",
                    "NUGET_PACKAGES": str(temp / "nuget-packages"),
                    "NUGET_HTTP_CACHE_PATH": str(temp / "nuget-http-cache"),
                    "NUGET_CERT_REVOCATION_MODE": "offline",
                }
            )

            commands = [
                [
                    dotnet,
                    "restore",
                    "src/Desktop/Desktop.csproj",
                    "--locked-mode",
                    "--configfile",
                    "NuGet.Config",
                    "--no-cache",
                    "--verbosity",
                    "minimal",
                ],
                [
                    dotnet,
                    "build",
                    "src/Desktop/Desktop.csproj",
                    "--framework",
                    "net10.0",
                    "--configuration",
                    "Release",
                    "--no-restore",
                    "--disable-build-servers",
                    "-m:1",
                    "--verbosity",
                    "minimal",
                ],
                [
                    dotnet,
                    "build",
                    "src/Desktop/Desktop.csproj",
                    "--framework",
                    "net10.0-windows",
                    "--configuration",
                    "Release",
                    "--no-restore",
                    "--disable-build-servers",
                    "-m:1",
                    "--verbosity",
                    "minimal",
                ],
            ]
            for command in commands:
                result = subprocess.run(
                    command,
                    cwd=checkout,
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=35,
                    check=False,
                )
                self.assertEqual(
                    0,
                    result.returncode,
                    f"command failed: {' '.join(command)}\n{result.stdout}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
