#!/usr/bin/env python3
"""Run the dependency-free ParcelFlow acceptance executable offline."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "tests" / "ParcelFlow.Tests" / "ParcelFlow.Tests.csproj"


def main() -> int:
    environment = os.environ.copy()
    with tempfile.TemporaryDirectory(
        prefix="csharp-notification-dispatch-",
    ) as temporary_directory:
        temporary_path = Path(temporary_directory)
        environment.update(
            {
                "DOTNET_CLI_HOME": temporary_directory,
                "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
                "DOTNET_NOLOGO": "1",
                "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
                "HOME": temporary_directory,
                "NUGET_PACKAGES": str(temporary_path / "packages"),
                "XDG_CACHE_HOME": str(temporary_path / "cache"),
                "XDG_DATA_HOME": str(temporary_path / "data"),
            }
        )

        try:
            completed = subprocess.run(
                [
                    "dotnet",
                    "run",
                    "--project",
                    str(PROJECT),
                    "--configuration",
                    "Release",
                    "--artifacts-path",
                    str(temporary_path / "artifacts"),
                    "--nologo",
                    "--verbosity",
                    "quiet",
                ],
                cwd=ROOT,
                env=environment,
                check=False,
                timeout=90,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f"FAIL unable to run offline acceptance tests: {error}", file=sys.stderr)
            return 1

        return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
