#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROTECTED_SHA256 = {
    "ContractMockServer.java": "3ddf0d7443c587eb0af3bd405f85232f05b77e9b0bdb74171dcf55b97a38fd16",
    "TestMain.java": "e0a4cf45ca9c8b571e6bac0a4f95a098642e000bbd5203b7f4841bf7d6b695e8",
    "docs/contract.json": "b61b5c9d42ec773f9e6376edbce575d4939dc8d3be52733f6e46f830f5537725",
    "docs/official_sources.json": "4b3786dd53533ab8a5ed079f91946e23217fe3620c2d86f97e5903a6bfa9fe86",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str], cwd: Path) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        actual = sha256(ROOT / relative)
        if actual != expected:
            print(f"protected fixture changed: {relative}", file=sys.stderr)
            raise SystemExit(1)

    if not (ROOT / "AutomationClient.java").is_file():
        print("missing AutomationClient.java", file=sys.stderr)
        raise SystemExit(1)

    with tempfile.TemporaryDirectory(prefix="vcf90-0165-") as temporary:
        classes = Path(temporary) / "classes"
        classes.mkdir()
        run(
            [
                "javac",
                "-encoding",
                "UTF-8",
                "-d",
                str(classes),
                "AutomationClient.java",
                "ContractMockServer.java",
                "TestMain.java",
            ],
            ROOT,
        )
        run(["java", "-cp", str(classes), "TestMain"], ROOT)


if __name__ == "__main__":
    main()
