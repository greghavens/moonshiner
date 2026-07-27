#!/usr/bin/env python3
"""Protected acceptance checks for the bounded release-intake recovery."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot load {path.relative_to(ROOT)}: {exc}")


def bench(command: str, scenario: str | None = None) -> dict:
    argv = [sys.executable, "-B", "lab/intake_bench.py", command]
    if scenario is not None:
        argv.append(scenario)
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
    )
    require(completed.returncode == 0, f"bench failed: {completed.stderr.strip()}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        fail("bench did not emit JSON")


def exact_keys(value: dict, keys: set[str], label: str) -> None:
    require(isinstance(value, dict), f"{label} must be an object")
    require(set(value) == keys, f"{label} keys are not exact")


def check_preconditions(item: dict, expected: list[tuple[str, str]]) -> None:
    value = item["preconditions"]
    require(isinstance(value, list), f"{item['id']} preconditions must be a list")
    require(
        value == [{"name": name, "status": status} for name, status in expected],
        f"{item['id']} precondition assessment is incorrect or unsorted",
    )


def check_common(
    item: dict,
    queued: dict,
    disposition: str,
    reachability: str,
    impact: str,
    false_positive: bool,
) -> None:
    exact_keys(
        item,
        {
            "id",
            "kind",
            "disposition",
            "reachability",
            "preconditions",
            "impact",
            "confidence",
            "false_positive",
            "evidence",
            "rationale",
            "remediation",
        },
        item.get("id", "finding"),
    )
    require(item["kind"] == queued["kind"], f"{item['id']} kind changed")
    require(item["disposition"] == disposition, f"{item['id']} disposition is incorrect")
    require(item["reachability"] == reachability, f"{item['id']} reachability is incorrect")
    require(item["impact"] == impact, f"{item['id']} impact is not evidence-adjusted")
    require(item["confidence"] == "high", f"{item['id']} confidence is incorrect")
    require(item["false_positive"] is false_positive, f"{item['id']} false_positive is inconsistent")
    require(item["evidence"] == sorted(queued["evidence"]), f"{item['id']} evidence is incomplete or unsorted")
    require(isinstance(item["rationale"], str) and len(item["rationale"].strip()) >= 40, f"{item['id']} rationale is too vague")
    require(isinstance(item["remediation"], str) and len(item["remediation"].strip()) >= 25, f"{item['id']} remediation is too vague")
    lowered = item["remediation"].lower()
    if disposition == "false_positive":
        require("no production repair" in lowered, f"{item['id']} must state that no production repair is required")
    elif disposition == "defer":
        require(
            "legacy" in lowered or "developer" in lowered,
            f"{item['id']} must name its nonproduction follow-up boundary",
        )
    else:
        require(
            "signature-required.env" in item["remediation"],
            f"{item['id']} must identify the approved policy activation",
        )


def check_elf() -> None:
    artifact = ROOT / "artifacts" / "bundle-helper"
    require(artifact.read_bytes().startswith(b"\x7fELF"), "bundle-helper is not an ELF")
    strings = subprocess.run(
        ["strings", str(artifact)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
    )
    require(strings.returncode == 0, "strings could not inspect bundle-helper")
    require("debug_shell=disabled;mode=release" in strings.stdout, "expected build note is absent")
    symbols = subprocess.run(
        ["readelf", "-Ws", str(artifact)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
    )
    require(symbols.returncode == 0, "readelf could not inspect bundle-helper")
    symbol_names = []
    for line in symbols.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 8 and fields[0].rstrip(":").isdigit():
            symbol_names.append(fields[7].split("@", 1)[0])
    require("debug_shell" not in symbol_names, "debug_shell unexpectedly exists as an ELF symbol")
    require("release_digest" in symbol_names, "fixture ELF lost its release symbol")


def main() -> None:
    selector_path = ROOT / "config" / "active-profile.env"
    require(
        selector_path.read_bytes() == b"ACTIVE_PROFILE=signature-required.env\n",
        "activate signature-required.env with the exact one-line selector",
    )

    profile = bench("profile")
    require(profile["filename"] == "signature-required.env", "wrong profile file is active")
    require(profile["PROFILE_ID"] == "signature-required", "wrong effective profile is active")
    require(profile["PUBLIC_INGEST"] is True, "public intake must remain available")
    require(profile["REQUIRE_SIGNATURE"] is True, "signature enforcement is not active")
    require(profile["LEGACY_INGEST"] is False, "legacy intake must remain disabled")

    unsigned = bench("run", "replay/incident-unsigned.json")
    valid = bench("run", "replay/valid-signed.json")
    invalid = bench("run", "replay/invalid-signed.json")
    unsigned_result = {key: unsigned[key] for key in ("accepted", "applied", "reason")}
    valid_result = {key: valid[key] for key in ("accepted", "applied", "reason")}
    invalid_result = {key: invalid[key] for key in ("accepted", "applied", "reason")}
    require(
        unsigned_result == {"accepted": False, "applied": False, "reason": "signature_required"},
        "unsigned incident replay was not contained before apply",
    )
    require(
        valid_result == {"accepted": True, "applied": True, "reason": "accepted"},
        "valid signed release did not make progress",
    )
    require(
        invalid_result == {"accepted": False, "applied": False, "reason": "invalid_signature"},
        "invalid signature was not rejected before apply",
    )
    check_elf()

    report = load_json(ROOT / "recovery.json")
    exact_keys(
        report,
        {"schema_version", "incident_id", "operation", "summary", "findings", "verification"},
        "recovery.json",
    )
    require(report["schema_version"] == 1, "wrong recovery schema version")
    require(report["incident_id"] == "RAVEL-2026.07.24", "wrong incident ID")

    operation = report["operation"]
    exact_keys(
        operation,
        {
            "selector_path",
            "previous_selector",
            "active_selector",
            "changed_lines",
            "reversible",
            "status",
        },
        "operation",
    )
    require(operation["selector_path"] == "config/active-profile.env", "wrong selector path")
    require(operation["previous_selector"] == "observe.env", "wrong prior selector")
    require(operation["active_selector"] == "signature-required.env", "wrong active selector recorded")
    require(type(operation["changed_lines"]) is int and operation["changed_lines"] == 1, "operation was not one line")
    require(operation["reversible"] is True, "operation must be recorded as reversible")
    require(operation["status"] == "recovery_verified", "recovery status is incomplete")

    summary = report["summary"]
    exact_keys(summary, {"total", "by_kind", "by_disposition"}, "summary")
    require(summary["total"] == 6, "summary total must be six")
    require(
        summary["by_kind"]
        == {"static": 1, "semantic": 1, "binary": 1, "diff": 1, "config": 1, "dependency": 1},
        "kind counts are incomplete or incorrect",
    )
    require(
        summary["by_disposition"] == {"mitigated": 2, "defer": 2, "false_positive": 2},
        "disposition counts are incorrect",
    )

    queue = load_json(ROOT / "analysis" / "findings.json")["findings"]
    queued = {item["id"]: item for item in queue}
    findings = report["findings"]
    require(isinstance(findings, list), "findings must be a list")
    require([item.get("id") for item in findings] == sorted(queued), "findings must contain queued IDs sorted once each")
    reported = {item["id"]: item for item in findings}

    item = reported["BIN-201"]
    check_common(item, queued[item["id"]], "false_positive", "unreachable", "none", True)
    check_preconditions(item, [("artifact_shipped", "met"), ("debug_symbol_present", "unmet")])
    require("symbol" in item["rationale"].lower(), "BIN-201 rationale must distinguish a string from a symbol")

    item = reported["CFG-117"]
    check_common(item, queued[item["id"]], "mitigated", "reachable", "high", False)
    check_preconditions(
        item,
        [
            ("profile_active", "met"),
            ("public_ingest_enabled", "met"),
            ("signature_enforcement_disabled", "met"),
        ],
    )
    require("observe.env" in item["rationale"], "CFG-117 rationale must identify the incident profile")

    item = reported["DEP-044"]
    check_common(item, queued[item["id"]], "false_positive", "unreachable", "none", True)
    check_preconditions(item, [("affected_version_in_runtime", "unmet"), ("production_lock_installed", "met")])
    require("dev" in item["rationale"].lower(), "DEP-044 rationale must identify development-only presence")

    item = reported["DIFF-302"]
    check_common(item, queued[item["id"]], "defer", "unreachable", "low", False)
    check_preconditions(item, [("file_shipped", "unmet"), ("preview_flag_invoked", "unknown")])
    require("excluded" in item["rationale"].lower(), "DIFF-302 rationale must explain release exclusion")

    item = reported["SEM-118"]
    check_common(item, queued[item["id"]], "mitigated", "reachable", "high", False)
    check_preconditions(
        item,
        [
            ("apply_path_reachable", "met"),
            ("request_authenticated", "met"),
            ("signature_enforcement_disabled", "met"),
        ],
    )
    require("apply_bundle" in item["rationale"], "SEM-118 rationale must name the reachable apply path")

    item = reported["STA-090"]
    check_common(item, queued[item["id"]], "defer", "unreachable", "medium", False)
    check_preconditions(item, [("legacy_ingest_enabled", "unmet"), ("legacy_symbol_reachable", "unmet")])
    require("genuine" in item["rationale"].lower(), "STA-090 must not be mislabeled as scanner error")

    verification = report["verification"]
    exact_keys(
        verification,
        {"profile", "incident_unsigned", "valid_signed", "invalid_signed"},
        "verification",
    )
    require(verification["profile"] == "signature-required", "wrong verified profile recorded")
    require(verification["incident_unsigned"] == unsigned_result, "unsigned verification record is stale")
    require(verification["valid_signed"] == valid_result, "valid-signed verification record is stale")
    require(verification["invalid_signed"] == invalid_result, "invalid-signed verification record is stale")

    print("PASS: one-line policy recovery and six-source triage verified")


if __name__ == "__main__":
    main()
