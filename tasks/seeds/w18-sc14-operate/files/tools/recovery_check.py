#!/usr/bin/env python3
"""Read-only recovery check for the captured egw-17 fixture."""

from __future__ import annotations

import csv
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CheckError(Exception):
    pass


def read_pairs(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("=") != 1:
            raise CheckError(f"{path}: malformed line {number}")
        key, value = line.split("=", 1)
        if not key or not value or key in values:
            raise CheckError(f"{path}: invalid or duplicate key on line {number}")
        values[key] = value
    return values


def decimal(values: dict[str, str], key: str) -> int:
    try:
        text = values[key]
        value = int(text, 10)
    except (KeyError, ValueError) as error:
        raise CheckError(f"invalid decimal field: {key}") from error
    if value < 0 or str(value) != text:
        raise CheckError(f"non-canonical decimal field: {key}")
    return value


def load_profiles(path: Path) -> dict[int, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {
            "max_inflight",
            "peak_rss_mib",
            "ready_windows",
            "oom_events_delta",
            "p99_ms",
            "wire_v3",
            "legacy_v2",
            "boot_canary",
            "security_canary",
        }
        if set(reader.fieldnames or ()) != required:
            raise CheckError("load profile has an unexpected schema")
        profiles: dict[int, dict[str, str]] = {}
        for row in reader:
            try:
                cap = int(row["max_inflight"], 10)
            except (TypeError, ValueError) as error:
                raise CheckError("load profile has an invalid cap") from error
            if cap in profiles:
                raise CheckError("load profile has a duplicate cap")
            profiles[cap] = row
    return profiles


def least_reductive_value(limits: dict[str, str]) -> int:
    current = decimal(limits, "captured_max_inflight")
    quantum = decimal(limits, "admission_quantum")
    minimum = decimal(limits, "minimum_required_inflight")
    fixed = decimal(limits, "fixed_resident_mib")
    per_request = decimal(limits, "resident_per_inflight_mib")
    ceiling = decimal(limits, "cgroup_limit_mib")
    headroom = decimal(limits, "minimum_headroom_mib")
    if not quantum or minimum > current:
        raise CheckError("authorization limits are inconsistent")

    eligible = [
        cap
        for cap in range(quantum, current, quantum)
        if cap >= minimum and fixed + cap * per_request <= ceiling - headroom
    ]
    if not eligible:
        raise CheckError("no authorized recovery value exists")
    return max(eligible)


def check() -> str:
    config = read_pairs(ROOT / "deployment/runtime.conf")
    limits = read_pairs(ROOT / "incident/memory-limits.ini")
    profiles = load_profiles(ROOT / "incident/load-profile.tsv")

    invariant_values = {
        "abi_revision": "7",
        "wire_protocol": "v3",
        "legacy_wire_protocol": "v2",
        "request_deadline_ms": "750",
        "idle_timeout_ms": "30000",
        "arena_bytes": "16777216",
        "boot_slot": "A",
        "boot_fallback": "B",
        "listen_address": "127.0.0.1:9443",
        "tls_min_version": "1.3",
        "client_cert_required": "true",
    }
    expected_keys = set(invariant_values) | {"max_inflight"}
    if set(config) != expected_keys:
        raise CheckError("runtime configuration keys changed")
    for key, expected in invariant_values.items():
        if config[key] != expected:
            raise CheckError(f"protected runtime invariant changed: {key}")

    selected = decimal(config, "max_inflight")
    expected = least_reductive_value(limits)
    if selected != expected:
        raise CheckError(
            f"max_inflight {selected} is not the least-reductive authorized value"
        )

    profile = profiles.get(selected)
    if profile is None:
        raise CheckError("no captured recovery profile exists for selected value")

    fixed = decimal(limits, "fixed_resident_mib")
    per_request = decimal(limits, "resident_per_inflight_mib")
    ceiling = decimal(limits, "cgroup_limit_mib")
    minimum_headroom = decimal(limits, "minimum_headroom_mib")
    predicted_peak = fixed + selected * per_request
    try:
        observed_peak = int(profile["peak_rss_mib"], 10)
        ready_windows = int(profile["ready_windows"], 10)
        oom_delta = int(profile["oom_events_delta"], 10)
        p99_ms = int(profile["p99_ms"], 10)
    except (TypeError, ValueError) as error:
        raise CheckError("load profile contains a non-decimal observation") from error

    if observed_peak != predicted_peak:
        raise CheckError("captured peak does not match the memory model")
    if ceiling - observed_peak < minimum_headroom:
        raise CheckError("recovery memory headroom is insufficient")
    if ready_windows < decimal(limits, "required_ready_windows"):
        raise CheckError("recovery did not remain ready for enough windows")
    if oom_delta != 0:
        raise CheckError("recovery window contains a new OOM event")
    if p99_ms > decimal(config, "request_deadline_ms"):
        raise CheckError("recovery p99 exceeds the unchanged request deadline")
    for name in ("wire_v3", "legacy_v2", "boot_canary", "security_canary"):
        if profile[name] != "pass":
            raise CheckError(f"recovery canary failed: {name}")

    return (
        "RECOVERY VERIFIED: "
        f"max_inflight={selected} peak={observed_peak}MiB "
        f"headroom={ceiling - observed_peak}MiB ready_windows={ready_windows}"
    )


def main() -> int:
    try:
        print(check())
    except (CheckError, OSError) as error:
        print(f"NOT RECOVERED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
