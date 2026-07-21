#!/usr/bin/env python3
"""Offline checks for the release asset pipeline.

The validation host intentionally need not provide a Go toolchain.  The tests
model Go's target-file and go:embed selection for this package, and verify the
deterministic inputs and release-script invariants directly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shlex
import unittest


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "internal" / "assets"
GENERATED = ASSETS / "generated"


class TagExpression:
    def __init__(self, expression: str, active: set[str]) -> None:
        self.tokens = re.findall(r"&&|\|\||!|\(|\)|[A-Za-z0-9_.]+", expression)
        self.index = 0
        self.active = active

    def evaluate(self) -> bool:
        value = self.parse_or()
        if self.index != len(self.tokens):
            raise ValueError(f"unsupported go:build expression: {self.tokens}")
        return value

    def parse_or(self) -> bool:
        value = self.parse_and()
        while self.peek() == "||":
            self.index += 1
            right = self.parse_and()
            value = value or right
        return value

    def parse_and(self) -> bool:
        value = self.parse_term()
        while self.peek() == "&&":
            self.index += 1
            right = self.parse_term()
            value = value and right
        return value

    def parse_term(self) -> bool:
        token = self.peek()
        if token is None:
            raise ValueError("incomplete go:build expression")
        if token == "!":
            self.index += 1
            return not self.parse_term()
        if token == "(":
            self.index += 1
            value = self.parse_or()
            if self.peek() != ")":
                raise ValueError("unclosed go:build expression")
            self.index += 1
            return value
        self.index += 1
        return token in self.active

    def peek(self) -> str | None:
        if self.index == len(self.tokens):
            return None
        return self.tokens[self.index]


def selected_for(source: Path, goos: str) -> bool:
    name = source.name.removesuffix(".go")
    suffixes = name.split("_")[1:]
    known_goos = {"aix", "android", "darwin", "dragonfly", "freebsd", "illumos",
                  "ios", "js", "linux", "netbsd", "openbsd", "plan9", "solaris",
                  "wasip1", "windows"}
    known_arch = {"386", "amd64", "arm", "arm64", "loong64", "mips", "mips64",
                  "mips64le", "mipsle", "ppc64", "ppc64le", "riscv64", "s390x", "wasm"}
    filename_goos = next((part for part in suffixes[-2:] if part in known_goos), None)
    filename_arch = next((part for part in suffixes[-2:] if part in known_arch), None)
    if filename_goos is not None and filename_goos != goos:
        return False
    if filename_arch is not None and filename_arch != "amd64":
        return False

    text = source.read_text(encoding="utf-8")
    match = re.search(r"^//go:build\s+(.+)$", text, re.MULTILINE)
    if not match:
        return True
    active = {goos, "amd64"}
    if goos != "windows":
        active.add("unix")
    return TagExpression(match.group(1), active).evaluate()


def embedded_bundle_files(goos: str) -> set[str]:
    patterns: list[str] = []
    declaration_count = 0
    for source in sorted(ASSETS.glob("*.go")):
        if source.name.endswith("_test.go") or not selected_for(source, goos):
            continue
        text = source.read_text(encoding="utf-8")
        self_contained = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        declarations = re.findall(r"\bvar\s+bundle\s+embed\.FS\b", self_contained)
        declaration_count += len(declarations)
        attached = re.search(
            r"(?P<directives>(?://go:embed[^\n]*\n)+)\s*"
            r"var\s+bundle\s+embed\.FS\b",
            self_contained,
            re.MULTILINE,
        )
        if attached:
            for directive in re.findall(
                r"^//go:embed\s+(.+)$", attached.group("directives"), re.MULTILINE
            ):
                patterns.extend(shlex.split(directive))

    if declaration_count != 1:
        raise AssertionError(
            f"{goos}/amd64 selects {declaration_count} embedded bundle declarations, want 1"
        )

    included: set[str] = set()
    for pattern in patterns:
        if pattern.startswith("all:"):
            pattern = pattern[4:]
        if pattern.startswith("/") or ".." in Path(pattern).parts:
            raise AssertionError(f"unsafe go:embed pattern {pattern!r}")
        for match in ASSETS.glob(pattern):
            if match.is_dir():
                files = (item for item in match.rglob("*") if item.is_file())
            else:
                files = (match,) if match.is_file() else ()
            included.update(item.relative_to(ASSETS).as_posix() for item in files)
    return included


class ReleaseAssetsTest(unittest.TestCase):
    def test_every_target_embeds_all_generated_assets(self) -> None:
        expected = {
            item.relative_to(ASSETS).as_posix()
            for item in GENERATED.rglob("*")
            if item.is_file()
        }
        self.assertIn("generated/index.html", expected)
        self.assertIn(b"moonshiner-runtime-asset-v1", (GENERATED / "index.html").read_bytes())
        for goos in ("linux", "windows"):
            actual = embedded_bundle_files(goos)
            self.assertTrue(
                expected <= actual,
                f"{goos}/amd64 embedded files {sorted(actual)} do not include "
                f"all generated files {sorted(expected)}",
            )

    def test_checked_in_generation_is_byte_exact(self) -> None:
        web = ROOT / "web"
        source_files = sorted(item for item in web.rglob("*") if item.is_file())
        self.assertFalse(any(item.is_symlink() for item in web.rglob("*")))

        entries = []
        expected_names = set()
        for source in source_files:
            name = source.relative_to(web).as_posix()
            data = source.read_bytes()
            expected_names.add(name)
            self.assertEqual(data, (GENERATED / name).read_bytes(), name)
            entries.append({
                "path": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            })

        expected_manifest = (json.dumps(entries, indent=2) + "\n").encode()
        self.assertEqual(expected_manifest, (GENERATED / "manifest.json").read_bytes())
        actual_names = {
            item.relative_to(GENERATED).as_posix()
            for item in GENERATED.rglob("*")
            if item.is_file()
        }
        self.assertEqual(expected_names | {"manifest.json"}, actual_names)

    def test_release_script_preserves_guards_and_reproducibility(self) -> None:
        script = (ROOT / "scripts" / "build-release.sh").read_text(encoding="utf-8")
        required = (
            "go generate ./internal/assets",
            "git diff --quiet HEAD -- internal/assets/generated",
            "git ls-files --others --exclude-standard -- internal/assets/generated",
            "for target in linux/amd64 windows/amd64",
            "export CGO_ENABLED=0",
            "SOURCE_DATE_EPOCH",
            "-trimpath",
            "-buildvcs=false",
            "-ldflags=-buildid=",
        )
        for fragment in required:
            self.assertIn(fragment, script)
        guard_end = script.index("dist_dir=")
        self.assertLess(script.index("go generate ./internal/assets"), guard_end)
        self.assertLess(script.index("git diff --quiet HEAD"), guard_end)
        self.assertLess(script.index("git ls-files --others"), guard_end)

    def test_runtime_reads_only_the_embedded_bundle(self) -> None:
        lookup = (ASSETS / "assets.go").read_text(encoding="utf-8")
        main = (ROOT / "cmd" / "distill" / "main.go").read_text(encoding="utf-8")
        self.assertIn('bundle.ReadFile(path.Join("generated", clean))', lookup)
        self.assertNotRegex(lookup, r"\bos\.(?:Open|ReadFile)\b")
        self.assertIn("assets.Lookup(*assetName)", main)


if __name__ == "__main__":
    unittest.main(verbosity=2)
