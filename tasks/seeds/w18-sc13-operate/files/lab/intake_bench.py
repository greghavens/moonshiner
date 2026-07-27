#!/usr/bin/env python3
"""Finite offline model for the release-intake policy boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / "config" / "active-profile.env"
PROFILES = ROOT / "config" / "profiles"
PROFILE_KEYS = {
    "PROFILE_ID",
    "PUBLIC_INGEST",
    "REQUIRE_SIGNATURE",
    "LEGACY_INGEST",
}


class BenchError(ValueError):
    pass


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BenchError(f"cannot read {path.relative_to(ROOT)}: {exc.strerror}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line or line.startswith("#") or "=" not in line:
            raise BenchError(f"{path.relative_to(ROOT)}:{line_number}: invalid environment line")
        key, value = line.split("=", 1)
        if not key or not value or key in values:
            raise BenchError(f"{path.relative_to(ROOT)}:{line_number}: invalid assignment")
        values[key] = value
    return values


def load_policy() -> dict[str, object]:
    selector = parse_env(SELECTOR)
    if set(selector) != {"ACTIVE_PROFILE"}:
        raise BenchError("selector must contain only ACTIVE_PROFILE")
    filename = selector["ACTIVE_PROFILE"]
    if Path(filename).name != filename:
        raise BenchError("profile selector must be a filename")
    profile_path = PROFILES / filename
    profile = parse_env(profile_path)
    if set(profile) != PROFILE_KEYS:
        raise BenchError("selected profile has unexpected keys")
    for key in ("PUBLIC_INGEST", "REQUIRE_SIGNATURE", "LEGACY_INGEST"):
        if profile[key] not in {"0", "1"}:
            raise BenchError(f"{key} must be 0 or 1")
    return {
        "filename": filename,
        "PROFILE_ID": profile["PROFILE_ID"],
        "PUBLIC_INGEST": profile["PUBLIC_INGEST"] == "1",
        "REQUIRE_SIGNATURE": profile["REQUIRE_SIGNATURE"] == "1",
        "LEGACY_INGEST": profile["LEGACY_INGEST"] == "1",
    }


def run_scenario(policy: dict[str, object], scenario: dict[str, object]) -> dict[str, object]:
    if set(scenario) != {"scenario", "authenticated", "source", "manifest"}:
        raise BenchError("scenario has unexpected keys")
    if scenario["source"] != "public":
        raise BenchError("only the benign public replay class is modeled")
    if scenario["authenticated"] is not True:
        result = {"accepted": False, "applied": False, "reason": "authentication_required"}
    elif policy["PUBLIC_INGEST"] is not True:
        result = {"accepted": False, "applied": False, "reason": "public_ingest_disabled"}
    else:
        manifest = scenario["manifest"]
        if not isinstance(manifest, dict):
            raise BenchError("manifest must be an object")
        signature = manifest.get("signature")
        if policy["REQUIRE_SIGNATURE"] and signature is None:
            result = {"accepted": False, "applied": False, "reason": "signature_required"}
        elif policy["REQUIRE_SIGNATURE"] and signature != "valid":
            result = {"accepted": False, "applied": False, "reason": "invalid_signature"}
        else:
            result = {"accepted": True, "applied": True, "reason": "accepted"}
    return {
        "profile": policy["PROFILE_ID"],
        "scenario": scenario["scenario"],
        **result,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("profile")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("scenario")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        policy = load_policy()
        if args.command == "profile":
            output = policy
        else:
            scenario_path = Path(args.scenario)
            with scenario_path.open(encoding="utf-8") as handle:
                scenario = json.load(handle)
            output = run_scenario(policy, scenario)
    except (BenchError, OSError, json.JSONDecodeError) as exc:
        print(f"bench: {exc}", file=__import__("sys").stderr)
        return 2
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
