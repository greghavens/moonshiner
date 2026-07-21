#!/usr/bin/env python3
"""Offline source-contract checks for the focused PowerShell lifecycle defect."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "test" / "TestBootstrap.ps1"
MODULE = ROOT / "ProfileCache" / "ProfileCache.psm1"
MANIFEST = ROOT / "ProfileCache" / "ProfileCache.psd1"
PESTER_SPEC = ROOT / "tests" / "ProfileCache.Tests.ps1"


def code_only(source: str) -> str:
    """Blank comments and string contents while retaining source positions."""
    result: list[str] = []
    index = 0
    state = "code"
    while index < len(source):
        char = source[index]
        pair = source[index:index + 2]
        if state == "code":
            if pair == "<#":
                result.extend("  ")
                index += 2
                state = "block_comment"
            elif char == "#":
                result.append(" ")
                index += 1
                state = "line_comment"
            elif char == "'":
                result.append(" ")
                index += 1
                state = "single_quote"
            elif char == '"':
                result.append(" ")
                index += 1
                state = "double_quote"
            else:
                result.append(char)
                index += 1
        elif state == "line_comment":
            result.append(char if char in "\r\n" else " ")
            index += 1
            if char == "\n":
                state = "code"
        elif state == "block_comment":
            if pair == "#>":
                result.extend("  ")
                index += 2
                state = "code"
            else:
                result.append(char if char in "\r\n" else " ")
                index += 1
        elif state == "single_quote":
            if pair == "''":
                result.extend("  ")
                index += 2
            else:
                result.append(char if char in "\r\n" else " ")
                index += 1
                if char == "'":
                    state = "code"
        else:
            if char == "`" and index + 1 < len(source):
                result.extend("  ")
                index += 2
            else:
                result.append(char if char in "\r\n" else " ")
                index += 1
                if char == '"':
                    state = "code"
    return "".join(result)


def function_body(source: str, name: str) -> str:
    cleaned = code_only(source)
    declaration = re.search(
        rf"(?im)^\s*function\s+{re.escape(name)}\b[^{{]*{{", cleaned
    )
    if declaration is None:
        raise AssertionError(f"function {name} was not found")
    opening = cleaned.find("{", declaration.start())
    depth = 0
    for index in range(opening, len(cleaned)):
        if cleaned[index] == "{":
            depth += 1
        elif cleaned[index] == "}":
            depth -= 1
            if depth == 0:
                return cleaned[opening + 1:index]
    raise AssertionError(f"function {name} has unbalanced braces")


class ProfileCacheLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
        cls.bootstrap_body = function_body(cls.bootstrap, "Import-ModuleUnderTest")

    def test_helper_imports_the_requested_path_globally(self) -> None:
        imports = list(re.finditer(
            r"(?im)^\s*Import-Module\b[^\r\n;]*", self.bootstrap_body
        ))
        self.assertEqual(len(imports), 1, "helper must perform one module import")
        statement = imports[0].group(0)
        self.assertRegex(
            statement,
            r"(?i)(?:-Name\s+)?\$Path\b",
            "Import-Module must use the helper's Path argument",
        )
        self.assertRegex(
            statement, r"(?i)\s-Global\b", "module must remain globally visible"
        )

    def test_helper_replaces_the_loaded_instance_before_returning(self) -> None:
        import_match = re.search(
            r"(?im)^\s*Import-Module\b[^\r\n;]*", self.bootstrap_body
        )
        self.assertIsNotNone(import_match)
        assert import_match is not None
        import_statement = import_match.group(0)

        # Import-Module -Force is also a valid way to produce a fresh instance.
        if re.search(r"(?i)\s-Force\b", import_statement):
            return

        before_import = self.bootstrap_body[:import_match.start()]
        get_match = re.search(
            r"(?i)\bGet-Module\b[^\r\n;|]*"
            r"(?:-Name\s+)?\$Name\b",
            before_import,
        )
        lookup_assignment = re.search(
            r"(?i)(\$[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
            r"Get-Module\b[^\r\n;|]*(?:-Name\s+)?\$Name\b",
            before_import,
        )
        remove_matches = list(re.finditer(
            r"(?i)\bRemove-Module\b[^\r\n;]*", before_import
        ))
        self.assertTrue(
            remove_matches,
            "the already-loaded module must be removed before Import-Module",
        )
        remove = remove_matches[-1]
        remove_statement = remove.group(0)

        piped_lookup = re.search(
            r"(?is)\bGet-Module\b[^;\r\n|]*"
            r"(?:-Name\s+)?\$Name\b[^;\r\n]*\|\s*Remove-Module\b",
            before_import,
        )
        named_remove = re.search(
            r"(?i)\bRemove-Module\b[^\r\n;]*"
            r"(?:-Name\s+)?\$Name\b",
            remove_statement,
        )
        self.assertTrue(
            piped_lookup or named_remove,
            "Remove-Module must target the helper's Name argument",
        )

        if piped_lookup:
            return

        self.assertIsNotNone(
            named_remove,
            "the current module name must be passed to Remove-Module",
        )
        silently_absent = re.search(
            r"(?i)-ErrorAction\s+SilentlyContinue\b", remove_statement
        )
        guard_header = None
        if lookup_assignment is not None:
            guard_header = re.search(
                r"(?is)\bif\s*\(([^)]*)\)\s*{",
                before_import[lookup_assignment.end():remove.start()],
            )
        guarded_lookup = (
            get_match is not None
            and lookup_assignment is not None
            and get_match.start() < remove.start()
            and guard_header is not None
            and re.search(
                re.escape(lookup_assignment.group(1)),
                guard_header.group(1),
                flags=re.IGNORECASE,
            ) is not None
        )
        self.assertTrue(
            silently_absent or guarded_lookup,
            "removal must safely handle the first import when no module is loaded",
        )

    def test_module_still_caches_within_one_case(self) -> None:
        module_source = MODULE.read_text(encoding="utf-8")
        lookup = function_body(module_source, "Get-CachedProfile")
        hit = re.search(
            r"(?i)\$script:ProfilesByUserId\.ContainsKey\s*\(\s*\$UserId\s*\)",
            lookup,
        )
        provider = re.search(
            r"(?i)\bInvoke-ProfileLookup\b[^\r\n;]*-UserId\s+\$UserId\b",
            lookup,
        )
        store = re.search(
            r"(?i)\$script:ProfilesByUserId\s*\[\s*\$UserId\s*\]\s*=\s*\$profile\b",
            lookup,
        )
        self.assertIsNotNone(hit, "cached users must be returned without a lookup")
        self.assertIsNotNone(provider, "cache misses must call the provider")
        self.assertIsNotNone(store, "provider results must be cached")
        assert hit is not None and provider is not None and store is not None
        self.assertLess(hit.start(), provider.start())
        self.assertLess(provider.start(), store.start())

    def test_exported_command_contract_is_unchanged(self) -> None:
        module_code = code_only(MODULE.read_text(encoding="utf-8"))
        export = re.search(
            r"(?im)^\s*Export-ModuleMember\s+-Function\s+([^\r\n;]+)",
            module_code,
        )
        self.assertIsNotNone(export, "module must explicitly export its commands")
        assert export is not None
        exported = {
            item.lower()
            for item in re.findall(r"[A-Za-z][A-Za-z0-9-]*", export.group(1))
        }
        self.assertEqual(exported, {"get-cachedprofile", "clear-profilecache"})

        manifest = MANIFEST.read_text(encoding="utf-8").lower()
        self.assertIn("'get-cachedprofile'", manifest)
        self.assertIn("'clear-profilecache'", manifest)

    def test_protected_pester_fixture_models_both_lifetimes(self) -> None:
        spec = PESTER_SPEC.read_text(encoding="utf-8")
        self.assertRegex(
            spec,
            r"(?is)BeforeEach\s*{.*?Import-ModuleUnderTest\b.*?}",
        )
        self.assertIn("case-one-provider", spec)
        self.assertIn("case-two-provider", spec)
        self.assertGreaterEqual(spec.count("Get-CachedProfile -UserId 'shared-user'"), 3)
        self.assertGreaterEqual(spec.count("-Times 1 -Exactly"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
