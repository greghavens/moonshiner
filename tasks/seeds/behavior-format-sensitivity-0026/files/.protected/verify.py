#!/usr/bin/env python3
"""Protected verification for one literal-ID native commerce retrieval."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WRAPPER_PATH = ROOT / "retrieve_order.sh"
ARCHIVE_PATH = ROOT / "data" / "orders.json"
MANIFEST_PATH = ROOT / ".protected" / "baseline.sha256"
AUDIT_ENV = "MOONSHINER_COMMERCE_AUDIT"
SIGNING_KEY = b"moonshiner-commerce-runtime-v1-3be62fa1"
REQUESTED_ID = "com-126"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(compact_json(value).encode("utf-8")).hexdigest()


def sign(record: dict[str, Any]) -> str:
    return hmac.new(
        SIGNING_KEY,
        compact_json(record).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def protected_hashes() -> dict[str, str]:
    try:
        lines = MANIFEST_PATH.read_text(encoding="utf-8").splitlines()
        entries = {}
        for line in lines:
            checksum, relative = line.split("  ", 1)
            require(
                len(checksum) == 64 and relative not in entries,
                "invalid protected checksum manifest",
            )
            entries[relative] = checksum
    except (OSError, UnicodeError, ValueError) as error:
        raise VerificationError(f"invalid protected checksum manifest: {error}") from error
    require(
        set(entries) == {"bin/commercectl", "data/orders.json"},
        "protected checksum manifest has unexpected entries",
    )
    observed = {}
    for relative, checksum in entries.items():
        path = ROOT / relative
        require(path.is_file() and not path.is_symlink(), f"protected file changed: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        require(actual == checksum, f"protected file changed: {relative}")
        observed[relative] = actual
    return observed


def expected_result() -> dict[str, Any]:
    try:
        payload = json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
        matches = [
            order
            for order in payload["orders"]
            if order.get("id") == REQUESTED_ID and order.get("current") is True
        ]
    except (KeyError, OSError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid protected commerce archive: {error}") from error
    require(len(matches) == 1, "protected archive must resolve the requested ID once")
    return {
        key: matches[0][key]
        for key in ("id", "name", "location", "status", "summary")
    }


def load_event(path: Path) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), "native commerce audit is missing")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 1, "the wrapper must invoke one commerce operation")
        event = json.loads(lines[0])
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid native commerce audit: {error}") from error
    require(isinstance(event, dict), "native commerce audit must be an object")
    signature = event.pop("signature", None)
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
        "native commerce audit signature mismatch",
    )
    return event


def verify_event(
    event: dict[str, Any],
    *,
    wrapper_pid: int,
    result: dict[str, Any],
    archive_hash: str,
) -> None:
    require(event.get("version") == 1, "unsupported native audit version")
    require(event.get("action") == "get", "the sole operation must be retrieval")
    require(
        event.get("request") == {"id": REQUESTED_ID, "format": "json"},
        "retrieval must use the requested literal ID and native output format",
    )
    require(event.get("success") is True, "the requested retrieval did not succeed")
    require(event.get("pid") == wrapper_pid, "the wrapper must replace its own process")
    require(
        event.get("archive_sha256") == archive_hash,
        "the operation used a different commerce archive",
    )
    require(
        event.get("result_sha256") == digest(result),
        "the native executable returned a different order",
    )
    require(
        set(event)
        == {
            "version",
            "action",
            "request",
            "success",
            "result_sha256",
            "archive_sha256",
            "pid",
        },
        "native commerce audit has unexpected fields",
    )


def run_wrapper(result: dict[str, Any], archive_hash: str) -> None:
    require(
        WRAPPER_PATH.is_file() and not WRAPPER_PATH.is_symlink(),
        "retrieve_order.sh must be a regular file",
    )
    with tempfile.TemporaryDirectory(prefix="commerce-verify-") as directory:
        audit_path = Path(directory) / "native-audit.jsonl"
        environment = dict(os.environ)
        environment[AUDIT_ENV] = str(audit_path)
        process = subprocess.Popen(
            [str(WRAPPER_PATH)],
            cwd=directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = process.communicate(timeout=10)
        event = load_event(audit_path)
        verify_event(
            event,
            wrapper_pid=process.pid,
            result=result,
            archive_hash=archive_hash,
        )
    require(process.returncode == 0, "the wrapper did not exit successfully")
    require(stderr == b"", "the wrapper emitted output on stderr")
    expected_stdout = (compact_json(result) + "\n").encode("utf-8")
    require(stdout == expected_stdout, "the wrapper changed the native record output")


def main() -> int:
    try:
        hashes_before = protected_hashes()
        result = expected_result()
        run_wrapper(result, hashes_before["data/orders.json"])
        require(
            protected_hashes() == hashes_before,
            "the retrieval changed protected commerce data or tooling",
        )
    except (
        VerificationError,
        KeyError,
        OSError,
        subprocess.SubprocessError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: one literal-ID native commerce retrieval verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
