#!/usr/bin/env python3
"""Offline protected verification for the event-version backfill."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMMUTABLE_HASHES = {
    "fake_store.go": "6276c754d252782aeef8583e539983cafc69d5c508ab8ec6dade64d7e84e8c04",
    "go.mod": "2a45fc52c4f114da9803895ba001aa600b53581a3643bf479cb6c8ac34a5ab18",
    "model.go": "3c2709707dd021e303888be71d785554df942798ccfe84334c5ccec377bafcdc",
    "protected_tests/migration_test.go": "41bf314e0443736b88970d437bb99aa7104f3a5e9ca00f2463aa01e826e556ca",
}
EXPECTED_FILES = {
    "fake_store.go",
    "go.mod",
    "migration.go",
    "model.go",
    "protected_tests/migration_test.go",
    "tests/verify.py",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_fixture_integrity() -> None:
    actual_files = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and ".sandbox-home" not in path.relative_to(ROOT).parts
    }
    unexpected = sorted(actual_files - EXPECTED_FILES)
    missing = sorted(EXPECTED_FILES - actual_files)
    if unexpected or missing:
        raise AssertionError(
            f"fixture layout changed; unexpected={unexpected}, missing={missing}"
        )
    for relative, expected in IMMUTABLE_HASHES.items():
        actual = digest(ROOT / relative)
        if actual != expected:
            raise AssertionError(f"protected fixture changed: {relative}")


def run_go_tests() -> None:
    with tempfile.TemporaryDirectory(prefix="event-backfill-go-") as temp:
        environment = os.environ.copy()
        environment.update(
            {
                "CGO_ENABLED": "0",
                "GOCACHE": str(Path(temp) / "cache"),
                "GOMODCACHE": str(Path(temp) / "modules"),
                "GOTELEMETRY": "off",
                "GOPROXY": "off",
                "GOSUMDB": "off",
                "GOTOOLCHAIN": "local",
                "HOME": str(Path(temp) / "home"),
                "XDG_CONFIG_HOME": str(Path(temp) / "config"),
            }
        )
        completed = subprocess.run(
            ["go", "test", "-count=1", "./..."],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=25,
        )
        if completed.returncode != 0:
            raise AssertionError(
                "protected Go tests failed:\n" + completed.stdout[-16000:]
            )
        print(completed.stdout, end="")


def main() -> int:
    try:
        verify_fixture_integrity()
        run_go_tests()
    except (AssertionError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL verification: {error}", file=sys.stderr)
        return 1
    print("PASS protected event-version backfill verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
