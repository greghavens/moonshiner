#!/usr/bin/env python3
"""Protected acceptance test for the fleet lookup wrapper."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "run_lookup.sh"
REGISTRY = ROOT / "data" / "vehicles.json"
TARGET_ID = "fle-120"


class CheckFailure(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_target() -> dict[str, Any]:
    with REGISTRY.open(encoding="utf-8") as source:
        records = json.load(source)["vehicles"]
    matches = [record for record in records if record.get("id") == TARGET_ID]
    require(len(matches) == 1, "protected registry must contain one target record")
    return matches[0]


def run_wrapper(
    audit_path: Path, caller_directory: Path
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["FLEET_REGISTRY_PATH"] = str(REGISTRY)
    environment["FLEET_AUDIT_PATH"] = str(audit_path)
    return subprocess.run(
        [str(WRAPPER)],
        cwd=caller_directory,
        env=environment,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )


def parse_audit(audit_path: Path) -> list[dict[str, Any]]:
    require(audit_path.is_file(), "wrapper did not invoke the native fleet executable")
    try:
        return [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
    except json.JSONDecodeError as error:
        raise CheckFailure(f"native audit is not valid JSON: {error}") from error


def verify() -> None:
    require(WRAPPER.is_file(), "run_lookup.sh is missing")
    require(os.access(WRAPPER, os.X_OK), "run_lookup.sh is not executable")

    target = load_target()
    registry_before = file_digest(REGISTRY)
    with tempfile.TemporaryDirectory(prefix="fleet-lookup-") as temporary:
        temporary_root = Path(temporary)
        caller_directory = temporary_root / "unrelated" / "working-directory"
        caller_directory.mkdir(parents=True)
        audit_path = temporary_root / "native-audit.jsonl"
        completed = run_wrapper(audit_path, caller_directory)
        events = parse_audit(audit_path)

    require(completed.returncode == 0, f"wrapper exited {completed.returncode}")
    require(completed.stderr == "", "wrapper emitted chat or diagnostics on stderr")
    require(len(events) == 1, "wrapper must perform exactly one fleet data operation")

    event = events[0]
    require(event.get("resource") == "vehicle", "operation targeted the wrong resource")
    require(event.get("operation") == "get", "wrapper must use direct retrieval")
    arguments = event.get("arguments")
    require(isinstance(arguments, dict), "native invocation arguments were not structured")
    require(set(arguments) == {"id"}, "native invocation must carry only the ID argument")
    require(
        type(arguments["id"]) is str and arguments["id"] == TARGET_ID,
        "vehicle ID must remain the requested literal string",
    )

    canonical_record = json.dumps(target, sort_keys=True, separators=(",", ":")) + "\n"
    require(completed.stdout == canonical_record, "wrapper altered or annotated the full record")
    require(file_digest(REGISTRY) == registry_before, "lookup modified the fleet registry")


if __name__ == "__main__":
    try:
        verify()
    except (CheckFailure, OSError, subprocess.SubprocessError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("PASS: one direct typed-ID lookup emitted the full record")
