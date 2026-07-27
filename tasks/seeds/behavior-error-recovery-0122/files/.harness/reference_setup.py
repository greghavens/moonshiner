#!/usr/bin/env python3
"""Exercise the reference flow through genuine tripctl executions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "3d50884f563a2f72abf909c2b5678383d85bbd29bf0fa97f789b7b4b1eb2f7ed"
TARGETS = (
    ("Kyoto visit 122", "Kyoto", "2026-09-23"),
    ("Montreal visit 122", "Montreal", "2026-09-23"),
)


def reset_generated_state() -> None:
    shutil.rmtree(ROOT / ".travel" / "runtime", ignore_errors=True)
    shutil.rmtree(ROOT / ".harness" / "runtime", ignore_errors=True)


def command_for(target: tuple[str, str, str]) -> list[str]:
    name, location, trip_date = target
    return [
        "./tripctl",
        "check-availability",
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
    except json.JSONDecodeError as error:
        raise RuntimeError("tripctl returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise RuntimeError("tripctl returned a non-object")
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


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    subprocess.run(
        ["./tripctl", "--help"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        start_new_session=True,
        timeout=15,
    )
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
