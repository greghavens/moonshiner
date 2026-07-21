#!/usr/bin/env python3
"""Offline checks for the intended Release packing configuration."""

from __future__ import annotations

import re
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
PROPS = ROOT / "Directory.Build.props"
PROJECT = ROOT / "src/Deterministic.Text/Deterministic.Text.csproj"
SOURCE = ROOT / "src/Deterministic.Text/TextFingerprint.cs"

RELEASE_CONTRACT = {
    "PackageId": "Moonshiner.Deterministic.Text",
    "Authors": "Moonshiner Maintainers",
    "Description": "Small deterministic text fingerprint primitives.",
    "PackageTags": "text;fingerprint;deterministic",
    "PackageLicenseExpression": "MIT",
    "RepositoryType": "git",
    "RepositoryUrl": "https://example.invalid/moonshiner/deterministic-text.git",
    "RepositoryBranch": "main",
    "RepositoryCommit": "0123456789abcdef0123456789abcdef01234567",
    "PublishRepositoryUrl": "true",
    "IncludeSymbols": "true",
    "SymbolPackageFormat": "snupkg",
    "EmbedAllSources": "true",
    "PackProjectSources": "true",
}

BUILD_POLICY = {
    "TargetFramework": "net8.0",
    "VersionPrefix": "3.4.2",
    "AssemblyVersion": "3.0.0.0",
    "FileVersion": "3.4.2.0",
    "InformationalVersion": "3.4.2",
    "ContinuousIntegrationBuild": "true",
    "Deterministic": "true",
    "DeterministicSourcePaths": "true",
    "PathMap": "$(MSBuildProjectDirectory)=/_/src",
}


def load_project(path: Path) -> ET.Element:
    try:
        return ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise AssertionError(f"cannot read valid MSBuild XML from {path}: {error}") from error


def expand(value: str, properties: dict[str, str]) -> str:
    return re.sub(
        r"\$\(([A-Za-z_][A-Za-z0-9_.-]*)\)",
        lambda match: properties.get(match.group(1), ""),
        value,
    )


def condition_is_true(condition: str | None, properties: dict[str, str]) -> bool:
    if condition is None or not condition.strip():
        return True

    expanded = expand(condition, properties).strip()
    comparison = re.fullmatch(r"'([^']*)'\s*(==|!=)\s*'([^']*)'", expanded)
    if comparison is None:
        raise AssertionError(f"unsupported MSBuild condition in packing contract: {condition}")

    left, operator, right = comparison.groups()
    return (left == right) if operator == "==" else (left != right)


def apply_property_group(group: ET.Element, properties: dict[str, str]) -> None:
    if not condition_is_true(group.get("Condition"), properties):
        return
    for element in group:
        properties[element.tag] = (element.text or "").strip()


def evaluate_file(path: Path, properties: dict[str, str]) -> None:
    root = load_project(path)
    for element in root:
        if element.tag == "PropertyGroup":
            apply_property_group(element, properties)
        elif element.tag == "Choose":
            selected: ET.Element | None = None
            otherwise: ET.Element | None = None
            for branch in element:
                if branch.tag == "When" and selected is None:
                    if condition_is_true(branch.get("Condition"), properties):
                        selected = branch
                elif branch.tag == "Otherwise":
                    otherwise = branch
            if selected is None:
                selected = otherwise
            if selected is not None:
                for group in selected.findall("PropertyGroup"):
                    apply_property_group(group, properties)


def evaluated_properties(configuration: str) -> dict[str, str]:
    properties = {"Configuration": configuration}
    evaluate_file(PROPS, properties)
    evaluate_file(PROJECT, properties)
    return properties


class ReleasePackageContractTests(unittest.TestCase):
    def test_release_has_all_package_symbol_and_repository_properties(self) -> None:
        properties = evaluated_properties("Release")
        for name, expected in RELEASE_CONTRACT.items():
            self.assertEqual(
                properties.get(name),
                expected,
                f"Release must set {name} to {expected!r}",
            )

    def test_build_and_version_policy_remains_intact(self) -> None:
        properties = evaluated_properties("Release")
        for name, expected in BUILD_POLICY.items():
            self.assertEqual(
                properties.get(name),
                expected,
                f"build policy must keep {name} at {expected!r}",
            )

    def test_project_remains_packable_and_includes_source(self) -> None:
        root = load_project(PROJECT)
        self.assertEqual(root.get("Sdk"), "Microsoft.NET.Sdk")

        properties = evaluated_properties("Release")
        self.assertEqual(properties.get("GenerateDocumentationFile"), "true")
        self.assertEqual(properties.get("IsPackable"), "true")

        packed_sources: list[ET.Element] = []
        for item_group in root.findall("ItemGroup"):
            if condition_is_true(item_group.get("Condition"), properties):
                packed_sources.extend(item_group.findall("None"))

        source_items = [
            item for item in packed_sources
            if item.get("Include") == "TextFingerprint.cs"
        ]
        self.assertEqual(len(source_items), 1, "Release must pack TextFingerprint.cs once")
        item = source_items[0]
        self.assertEqual(item.get("Pack"), "true")
        self.assertEqual(item.get("PackagePath"), "src/")

    def test_public_source_contract_remains_intact(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        required_fragments = (
            "namespace Moonshiner.Deterministic.Text;",
            "public static class TextFingerprint",
            "public static string Compute(string value)",
            "const ulong offsetBasis = 14695981039346656037UL;",
            "const ulong prime = 1099511628211UL;",
            'return hash.ToString("x16");',
        )
        for fragment in required_fragments:
            self.assertIn(fragment, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
