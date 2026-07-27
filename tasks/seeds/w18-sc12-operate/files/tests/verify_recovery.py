#!/usr/bin/env python3
"""Protected acceptance verifier for the fixture-authorized operation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "lab" / "gateway_bench.py"


def run(*arguments: str) -> dict[str, object]:
    process = subprocess.run(
        [sys.executable, "-B", str(BENCH), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=5,
    )
    if process.returncode != 0:
        raise AssertionError(
            f"bench {' '.join(arguments)} failed ({process.returncode}): "
            f"{process.stderr.strip()}"
        )
    return json.loads(process.stdout)


def require(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def main() -> int:
    selector = (ROOT / "config" / "active-profile.env").read_text(encoding="utf-8")
    require(
        selector,
        "ACTIVE_PROFILE=flood-containment.env\n",
        "the authorized operation must be the single profile-selector change",
    )

    profile = run("profile")
    require(profile["name"], "flood-containment.env", "active profile")
    require(profile["malformed_decode_attempts"], 1, "one-pass malformed handling")
    require(profile["reject_streak_limit"], 4, "degradation threshold")
    require(profile["quarantine_ms"], 5000, "quarantine duration")
    require(profile["flash_policy"], "quarantine-summary", "flash policy")
    require(profile["watchdog_policy"], "delivered", "watchdog policy")
    require(profile["ack_retry_policy"], "scheduled", "ACK retry policy")
    require(profile["ack_max_attempts"], 4, "ACK attempt ceiling")
    require(profile["ack_retry_delays_ms"], [100, 200, 400], "ACK retry schedule")

    flood = run("replay", "replay/malformed-flood.tsv")
    flood_expected = {
        "frames_received": 128,
        "decoder_calls": 4,
        "malformed_processed": 4,
        "quarantine_suppressed": 124,
        "quarantine_transitions": 1,
        "delivered": 0,
        "ack_attempts": 0,
        "flash_commits": 1,
        "watchdog_kicks": 0,
        "degraded_sources": 1,
    }
    for key, expected in flood_expected.items():
        require(flood[key], expected, f"flood {key}")
    if int(flood["energy_units"]) > 150:
        raise AssertionError(
            f"flood energy budget exceeded: {flood['energy_units']} > 150"
        )

    recovery = run("replay", "replay/interleaved-recovery.tsv")
    recovery_expected = {
        "frames_received": 8,
        "decoder_calls": 6,
        "malformed_processed": 4,
        "quarantine_suppressed": 2,
        "quarantine_transitions": 1,
        "recovered_sources": 1,
        "delivered": 2,
        "ack_attempts": 2,
        "acknowledged": 2,
        "flash_commits": 1,
        "watchdog_kicks": 2,
        "pending_acks": 0,
        "degraded_sources": 0,
    }
    for key, expected in recovery_expected.items():
        require(recovery[key], expected, f"recovery {key}")

    reset = run("replay", "replay/streak-reset.tsv")
    reset_expected = {
        "frames_received": 7,
        "decoder_calls": 7,
        "malformed_processed": 6,
        "quarantine_suppressed": 0,
        "quarantine_transitions": 0,
        "delivered": 1,
        "ack_attempts": 1,
        "flash_commits": 0,
        "watchdog_kicks": 1,
        "degraded_sources": 0,
    }
    for key, expected in reset_expected.items():
        require(reset[key], expected, f"streak reset {key}")

    outage = run("replay", "replay/ack-outage.tsv")
    outage_expected = {
        "decoder_calls": 1,
        "delivered": 1,
        "ack_attempts": 4,
        "acknowledged": 0,
        "pending_acks": 0,
        "flash_commits": 0,
        "watchdog_kicks": 1,
        "ack_attempt_times_ms": [0, 1000, 1200, 2000],
    }
    for key, expected in outage_expected.items():
        require(outage[key], expected, f"ACK outage {key}")

    print(
        "recovery verified: "
        f"flood decode={flood['decoder_calls']} "
        f"tx={flood['ack_attempts']} "
        f"flash={flood['flash_commits']} "
        f"watchdog={flood['watchdog_kicks']} "
        f"energy={flood['energy_units']}; "
        f"delivered={recovery['delivered']} "
        f"recovered_sources={recovery['recovered_sources']}; "
        f"ack_attempts={outage['ack_attempts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
