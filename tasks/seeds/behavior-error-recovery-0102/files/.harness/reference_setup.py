#!/usr/bin/env python3
"""Exercise the reference flow through the genuine availability executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "f59e799146595a23026a6bc0b3568fc59de870139aacc897cb703d3eb5f34604"
TARGETS = (
    ("Halifax conference trip", "Halifax", "2026-10-15"),
    ("Santa Fe field visit", "Santa Fe", "2026-10-15"),
)


def reset_generated_state() -> None:
    shutil.rmtree(ROOT / ".travel" / "runtime", ignore_errors=True)
    shutil.rmtree(ROOT / ".harness" / "runtime", ignore_errors=True)


def command_for(target: tuple[str, str, str]) -> list[str]:
    name, location, trip_date = target
    return [
        "./trip-availability",
        "check",
        "--name",
        name,
        "--location",
        location,
        "--date",
        trip_date,
    ]


def decode_result(process: subprocess.Popen[bytes]) -> tuple[int, dict]:
    stdout, stderr = process.communicate(timeout=15)
    raw = stdout if process.returncode == 0 else stderr
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("availability executable returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("availability executable returned a non-object")
    return process.returncode, payload


def concurrent_initial_checks() -> list[tuple[int, dict]]:
    processes = [
        subprocess.Popen(
            command_for(target),
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        for target in TARGETS
    ]
    return [decode_result(process) for process in processes]


def run_help() -> None:
    completed = subprocess.run(
        ["./trip-availability", "--help"],
        cwd=ROOT,
        capture_output=True,
        start_new_session=True,
        timeout=15,
    )
    if completed.returncode != 0 or b"usage: trip-availability" not in completed.stdout:
        raise RuntimeError("availability executable help failed")


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    run_help()
    initial = concurrent_initial_checks()
    successful: dict[str, bool] = {}
    transient_indices: list[int] = []
    for index, (return_code, payload) in enumerate(initial):
        expected_name = TARGETS[index][0]
        if return_code == 0:
            if payload.get("name") != expected_name or not isinstance(
                payload.get("available"), bool
            ):
                raise RuntimeError("successful initial branch was not grounded")
            successful[expected_name] = payload["available"]
        elif payload.get("error") == "temporary_unavailable":
            transient_indices.append(index)
        else:
            raise RuntimeError("initial branch returned an unexpected error")

    if len(successful) != 1 or len(transient_indices) != 1:
        raise RuntimeError("reference flow did not receive one partial failure")

    failed_index = transient_indices[0]
    retry = subprocess.run(
        command_for(TARGETS[failed_index]),
        cwd=ROOT,
        capture_output=True,
        start_new_session=True,
        timeout=15,
    )
    if retry.returncode == 0:
        retry_payload = json.loads(retry.stdout)
        if retry_payload.get("name") != TARGETS[failed_index][0] or not isinstance(
            retry_payload.get("available"), bool
        ):
            raise RuntimeError("retry result was not grounded")
        successful[TARGETS[failed_index][0]] = retry_payload["available"]
    else:
        retry_payload = json.loads(retry.stderr)
        if retry_payload.get("error") != "temporary_unavailable":
            raise RuntimeError("retry returned an unexpected error")

    lines = []
    for name, _, _ in TARGETS:
        value = str(successful[name]).lower() if name in successful else "unknown"
        lines.append(f"{name}: {value}")
    (ROOT / "availability.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
