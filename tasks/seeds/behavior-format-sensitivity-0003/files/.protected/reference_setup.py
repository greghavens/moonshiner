#!/usr/bin/env python3
"""Exercise the reference path through genuine reservationctl executions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MARKER_PATH = ROOT / ".reference_solution"
MARKER_SHA256 = "7d6975a67800edefa029f4a07478937bd78d36e68df58f23029a2fc1a2ebb3a0"
RUNTIME_PATH = ROOT / ".protected" / "runtime"
REFERENCE_RESPONSE_PATH = RUNTIME_PATH / "reference-response.txt"


def run(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(arguments),
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def main() -> None:
    if (
        not MARKER_PATH.is_file()
        or hashlib.sha256(MARKER_PATH.read_bytes()).hexdigest() != MARKER_SHA256
    ):
        raise RuntimeError("reference patch marker is missing or invalid")

    shutil.rmtree(RUNTIME_PATH, ignore_errors=True)

    help_result = run("./reservationctl", "--help")
    match = re.search(
        rb"^Pending request: ([^\r\n]+)$",
        help_result.stdout,
        flags=re.MULTILINE,
    )
    if match is None:
        raise RuntimeError("reference help did not identify one pending request")
    request_path = match.group(1).decode("utf-8")

    retrieval = run(
        "./reservationctl",
        "retrieve",
        "--request",
        request_path,
    )
    try:
        payload = json.loads(retrieval.stdout)
        record = payload["record"]
        response_line = payload["response_line"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("reference retrieval output is incomplete") from error
    if (
        not isinstance(record, dict)
        or not isinstance(record.get("id"), str)
        or not isinstance(response_line, str)
        or response_line
        != f"hospitality_get(id={json.dumps(record['id'])})"
    ):
        raise RuntimeError("reference retrieval response line is not grounded")

    REFERENCE_RESPONSE_PATH.write_text(response_line + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
