#!/usr/bin/env python3
"""Deterministic radio-ingress bench used by the protected recovery replay."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path


PROFILE_KEYS = {
    "PROFILE_FORMAT",
    "MALFORMED_DECODE_ATTEMPTS",
    "REJECT_ACK",
    "REJECT_STREAK_LIMIT",
    "QUARANTINE_MS",
    "FLASH_POLICY",
    "WATCHDOG_POLICY",
    "ACK_RETRY_POLICY",
    "ACK_MAX_ATTEMPTS",
    "ACK_RETRY_DELAYS_MS",
}


def read_assignments(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{number}: expected KEY=value")
        key, value = line.split("=", 1)
        if not key or key in values:
            raise ValueError(f"{path}:{number}: invalid or duplicate key")
        values[key] = value
    return values


@dataclass(frozen=True)
class Profile:
    name: str
    malformed_decode_attempts: int
    reject_ack: bool
    reject_streak_limit: int
    quarantine_ms: int
    flash_policy: str
    watchdog_policy: str
    ack_retry_policy: str
    ack_max_attempts: int
    ack_retry_delays_ms: tuple[int, ...]


def load_active_profile(root: Path) -> Profile:
    selector_path = root / "config" / "active-profile.env"
    selector = read_assignments(selector_path)
    if set(selector) != {"ACTIVE_PROFILE"}:
        raise ValueError("active-profile.env must contain only ACTIVE_PROFILE")
    name = selector["ACTIVE_PROFILE"]
    if not name or Path(name).name != name:
        raise ValueError("ACTIVE_PROFILE must be a profile filename")
    profile_path = root / "config" / "profiles" / name
    if not profile_path.is_file():
        raise ValueError(f"unknown profile: {name}")
    values = read_assignments(profile_path)
    if set(values) != PROFILE_KEYS or values["PROFILE_FORMAT"] != "1":
        raise ValueError(f"invalid profile schema: {name}")
    delays = tuple(int(value) for value in values["ACK_RETRY_DELAYS_MS"].split(","))
    profile = Profile(
        name=name,
        malformed_decode_attempts=int(values["MALFORMED_DECODE_ATTEMPTS"]),
        reject_ack=values["REJECT_ACK"] == "1",
        reject_streak_limit=int(values["REJECT_STREAK_LIMIT"]),
        quarantine_ms=int(values["QUARANTINE_MS"]),
        flash_policy=values["FLASH_POLICY"],
        watchdog_policy=values["WATCHDOG_POLICY"],
        ack_retry_policy=values["ACK_RETRY_POLICY"],
        ack_max_attempts=int(values["ACK_MAX_ATTEMPTS"]),
        ack_retry_delays_ms=delays,
    )
    if profile.malformed_decode_attempts < 1 or profile.ack_max_attempts < 1:
        raise ValueError(f"invalid nonpositive work limit: {name}")
    if len(delays) != profile.ack_max_attempts - 1 or any(delay < 0 for delay in delays):
        raise ValueError(f"invalid ACK retry schedule: {name}")
    if profile.flash_policy not in {"per-reject", "quarantine-summary"}:
        raise ValueError(f"invalid flash policy: {name}")
    if profile.watchdog_policy not in {"all-received", "delivered"}:
        raise ValueError(f"invalid watchdog policy: {name}")
    if profile.ack_retry_policy not in {"inline", "scheduled"}:
        raise ValueError(f"invalid ACK retry policy: {name}")
    return profile


@dataclass
class SourceState:
    reject_streak: int = 0
    quarantine_until: int | None = None
    awaiting_recovery: bool = False


@dataclass
class PendingAck:
    source: int
    message_id: str
    failures_left: int
    attempts: int
    next_due: int


def load_events(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    required = {"at_ms", "source", "kind", "message_id", "ack_failures"}
    if not rows or set(rows[0]) != required:
        raise ValueError(f"invalid replay schema: {path}")
    previous = -1
    for row in rows:
        at_ms = int(row["at_ms"])
        if at_ms < previous or row["kind"] not in {"malformed", "valid", "poll"}:
            raise ValueError(f"invalid replay event: {row}")
        previous = at_ms
    return rows


def replay(root: Path, scenario: Path) -> dict[str, object]:
    profile = load_active_profile(root)
    events = load_events(scenario)
    sources: dict[int, SourceState] = {}
    pending: list[PendingAck] = []
    result: dict[str, object] = {
        "profile": profile.name,
        "frames_received": 0,
        "decoder_calls": 0,
        "malformed_processed": 0,
        "quarantine_suppressed": 0,
        "quarantine_transitions": 0,
        "recovered_sources": 0,
        "delivered": 0,
        "ack_attempts": 0,
        "acknowledged": 0,
        "flash_commits": 0,
        "watchdog_kicks": 0,
        "energy_units": 0,
        "ack_attempt_times_ms": [],
    }

    def add(name: str, amount: int = 1) -> None:
        result[name] = int(result[name]) + amount

    def ack_attempt(at_ms: int, failures_left: int) -> tuple[bool, int]:
        add("ack_attempts")
        add("energy_units", 5)
        attempt_times = result["ack_attempt_times_ms"]
        assert isinstance(attempt_times, list)
        attempt_times.append(at_ms)
        if failures_left > 0:
            return False, failures_left - 1
        add("acknowledged")
        return True, 0

    def poll_pending(at_ms: int) -> None:
        retained: list[PendingAck] = []
        for record in pending:
            if at_ms < record.next_due:
                retained.append(record)
                continue
            succeeded, record.failures_left = ack_attempt(at_ms, record.failures_left)
            record.attempts += 1
            if succeeded or record.attempts >= profile.ack_max_attempts:
                continue
            delay_index = record.attempts - 1
            record.next_due = at_ms + profile.ack_retry_delays_ms[delay_index]
            retained.append(record)
        pending[:] = retained

    for event in events:
        at_ms = int(event["at_ms"])
        if event["kind"] == "poll":
            poll_pending(at_ms)
            continue

        source = int(event["source"])
        state = sources.setdefault(source, SourceState())
        add("frames_received")
        add("energy_units")

        if state.quarantine_until is not None:
            if at_ms < state.quarantine_until:
                add("quarantine_suppressed")
                continue
            state.quarantine_until = None
            state.reject_streak = 0
            state.awaiting_recovery = True

        if event["kind"] == "malformed":
            add("decoder_calls", profile.malformed_decode_attempts)
            add("energy_units", 2 * profile.malformed_decode_attempts)
            add("malformed_processed")
            add("energy_units")
            if profile.reject_ack:
                add("ack_attempts")
                add("energy_units", 5)
                attempt_times = result["ack_attempt_times_ms"]
                assert isinstance(attempt_times, list)
                attempt_times.append(at_ms)
            if profile.flash_policy == "per-reject":
                add("flash_commits")
                add("energy_units", 8)
            if profile.watchdog_policy == "all-received":
                add("watchdog_kicks")
                add("energy_units")
            state.reject_streak += 1
            if (profile.reject_streak_limit > 0
                    and state.reject_streak == profile.reject_streak_limit):
                state.quarantine_until = at_ms + profile.quarantine_ms
                add("quarantine_transitions")
                if profile.flash_policy == "quarantine-summary":
                    add("flash_commits")
                    add("energy_units", 8)
            continue

        add("decoder_calls")
        add("energy_units", 2)
        add("delivered")
        add("energy_units", 3)
        state.reject_streak = 0
        if state.awaiting_recovery:
            add("recovered_sources")
            state.awaiting_recovery = False
        if profile.watchdog_policy in {"all-received", "delivered"}:
            add("watchdog_kicks")
            add("energy_units")

        failures_left = int(event["ack_failures"])
        if profile.ack_retry_policy == "inline":
            for _ in range(profile.ack_max_attempts):
                succeeded, failures_left = ack_attempt(at_ms, failures_left)
                if succeeded:
                    break
        else:
            succeeded, failures_left = ack_attempt(at_ms, failures_left)
            if not succeeded and profile.ack_max_attempts > 1:
                pending.append(PendingAck(
                    source=source,
                    message_id=event["message_id"],
                    failures_left=failures_left,
                    attempts=1,
                    next_due=at_ms + profile.ack_retry_delays_ms[0],
                ))

    result["pending_acks"] = len(pending)
    result["degraded_sources"] = sum(
        state.quarantine_until is not None for state in sources.values()
    )
    return result


def profile_as_dict(profile: Profile) -> dict[str, object]:
    return {
        "name": profile.name,
        "malformed_decode_attempts": profile.malformed_decode_attempts,
        "reject_ack": profile.reject_ack,
        "reject_streak_limit": profile.reject_streak_limit,
        "quarantine_ms": profile.quarantine_ms,
        "flash_policy": profile.flash_policy,
        "watchdog_policy": profile.watchdog_policy,
        "ack_retry_policy": profile.ack_retry_policy,
        "ack_max_attempts": profile.ack_max_attempts,
        "ack_retry_delays_ms": list(profile.ack_retry_delays_ms),
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("profile", help="show the active immutable profile")
    replay_parser = subcommands.add_parser("replay", help="run one TSV scenario")
    replay_parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "profile":
            output = profile_as_dict(load_active_profile(root))
        else:
            scenario = args.scenario
            if not scenario.is_absolute():
                scenario = root / scenario
            output = replay(root, scenario)
    except (OSError, ValueError) as error:
        parser.exit(2, f"gateway-bench: {error}\n")
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
