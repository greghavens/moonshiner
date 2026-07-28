#!/usr/bin/env python3
"""Adversarial acceptance tests for the generated-artifact workflow."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
GENERATED = (
    Path("internal/eventwire/types.gen.go"),
    Path("docs/events.md"),
)


def copy_source(destination: Path) -> None:
    ignored = shutil.ignore_patterns(".git", ".sandbox-home", "__pycache__", "*.pyc")
    shutil.copytree(ROOT, destination, ignore=ignored)


def offline_environment(base: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(base / "home"),
            "GOCACHE": str(base / "go-build"),
            "GOMODCACHE": str(base / "go-mod"),
            "GOPATH": str(base / "gopath"),
            "TMPDIR": str(base / "temporary"),
            "GOENV": "off",
            "GOPROXY": "off",
            "GOSUMDB": "off",
            "GOTOOLCHAIN": "local",
        }
    )
    for name in ("HOME", "GOCACHE", "GOMODCACHE", "GOPATH", "TMPDIR"):
        Path(environment[name]).mkdir(parents=True, exist_ok=True)
    return environment


def run_make(
    source: Path,
    target: str,
    environment: dict[str, str],
    *,
    expect_success: bool,
) -> str:
    completed = subprocess.run(
        ["make", "--no-print-directory", target],
        cwd=source,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=25,
    )
    if expect_success and completed.returncode != 0:
        raise AssertionError(
            f"make {target} failed with {completed.returncode}:\n{completed.stdout}"
        )
    if not expect_success and completed.returncode == 0:
        raise AssertionError(f"make {target} unexpectedly succeeded")
    return completed.stdout


def snapshot(root: Path) -> dict[str, tuple[str, int, str, int, int]]:
    root_status = root.stat()
    result: dict[str, tuple[str, int, str, int, int]] = {
        ".": (
            "directory",
            stat.S_IMODE(root_status.st_mode),
            "",
            root_status.st_mtime_ns,
            root_status.st_ino,
        )
    }
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        status = path.lstat()
        if path.is_symlink():
            result[relative] = (
                "symlink",
                stat.S_IMODE(status.st_mode),
                os.readlink(path),
                status.st_mtime_ns,
                status.st_ino,
            )
        elif path.is_dir():
            result[relative] = (
                "directory",
                stat.S_IMODE(status.st_mode),
                "",
                status.st_mtime_ns,
                status.st_ino,
            )
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result[relative] = (
                "file",
                stat.S_IMODE(status.st_mode),
                digest,
                status.st_mtime_ns,
                status.st_ino,
            )
    return result


def make_source_read_only(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            path.chmod(mode & ~0o222 | 0o555)
        else:
            path.chmod(mode & ~0o222)
    root.chmod(0o555)


def restore_owner_writes(root: Path) -> None:
    root.chmod(0o755)
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            path.chmod(mode | 0o700)
        else:
            path.chmod(mode | 0o600)


class DriftGateTests(unittest.TestCase):
    def isolated_source(self) -> tuple[tempfile.TemporaryDirectory[str], Path, dict[str, str]]:
        temporary = tempfile.TemporaryDirectory(prefix="eventwire-gate-")
        base = Path(temporary.name)
        source = base / "source"
        copy_source(source)
        return temporary, source, offline_environment(base / "runtime")

    def assert_temporary_work_cleaned(self, environment: dict[str, str]) -> None:
        temporary = Path(environment["TMPDIR"])
        self.assertEqual(
            list(temporary.iterdir()),
            [],
            f"temporary generation work leaked beneath {temporary}",
        )

    def test_clean_check_is_read_only_offline_and_git_independent(self) -> None:
        temporary, source, environment = self.isolated_source()
        try:
            self.assertFalse((source / ".git").exists())
            before = snapshot(source)
            make_source_read_only(source)
            read_only = snapshot(source)
            run_make(source, "check-generated", environment, expect_success=True)
            self.assertEqual(snapshot(source), read_only)
            self.assert_temporary_work_cleaned(environment)
            restore_owner_writes(source)
            self.assertEqual(
                {key: value[2] for key, value in snapshot(source).items()},
                {key: value[2] for key, value in before.items()},
            )
        finally:
            restore_owner_writes(source)
            temporary.cleanup()

    def test_each_stale_artifact_is_reported_without_repair(self) -> None:
        for artifact in GENERATED:
            with self.subTest(artifact=artifact.as_posix()):
                temporary, source, environment = self.isolated_source()
                try:
                    target = source / artifact
                    target.write_bytes(target.read_bytes() + b"\nmanual drift\n")
                    make_source_read_only(source)
                    before = snapshot(source)
                    output = run_make(
                        source,
                        "check-generated",
                        environment,
                        expect_success=False,
                    )
                    self.assertEqual(snapshot(source), before)
                    self.assert_temporary_work_cleaned(environment)
                    self.assertIn("stale", output.lower())
                    self.assertIn(artifact.as_posix(), output)
                finally:
                    restore_owner_writes(source)
                    temporary.cleanup()

    def test_schema_drift_reports_all_outputs_without_repair(self) -> None:
        temporary, source, environment = self.isolated_source()
        try:
            schema = source / "api/events.json"
            data = schema.read_text(encoding="utf-8")
            schema.write_text(
                data.replace(
                    '"description": "A build artifact was released."',
                    '"description": "A build artifact completed release."',
                ),
                encoding="utf-8",
            )
            make_source_read_only(source)
            before = snapshot(source)
            output = run_make(
                source,
                "check-generated",
                environment,
                expect_success=False,
            )
            self.assertEqual(snapshot(source), before)
            self.assert_temporary_work_cleaned(environment)
            self.assertIn("stale", output.lower())
            for artifact in GENERATED:
                self.assertIn(artifact.as_posix(), output)
        finally:
            restore_owner_writes(source)
            temporary.cleanup()

    def test_generate_recovers_exact_outputs_and_leaves_a_clean_gate(self) -> None:
        temporary, source, environment = self.isolated_source()
        try:
            for artifact in GENERATED:
                (source / artifact).write_text("corrupt\n", encoding="utf-8")
            run_make(source, "generate", environment, expect_success=True)

            candidate_dir = Path(temporary.name) / "independent"
            candidate_dir.mkdir()
            completed = subprocess.run(
                [
                    "go",
                    "run",
                    "./cmd/eventgen",
                    "-schema",
                    "api/events.json",
                    "-go-out",
                    str(candidate_dir / "types.gen.go"),
                    "-doc-out",
                    str(candidate_dir / "events.md"),
                ],
                cwd=source,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=25,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertEqual(
                (source / GENERATED[0]).read_bytes(),
                (candidate_dir / "types.gen.go").read_bytes(),
            )
            self.assertEqual(
                (source / GENERATED[1]).read_bytes(),
                (candidate_dir / "events.md").read_bytes(),
            )
            run_make(source, "check-generated", environment, expect_success=True)
            self.assert_temporary_work_cleaned(environment)
        finally:
            restore_owner_writes(source)
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
