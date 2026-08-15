#!/usr/bin/env python3
"""Deterministic, offline verifier for the VCF migration architecture."""

from __future__ import annotations

import ast
import copy
from datetime import date
import hashlib
import importlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "fixtures" / "estate.json"
SNAPSHOT_PATH = ROOT / "fixtures" / "compatibility_snapshot.json"
PLAN_PATH = ROOT / "migration_plan.json"
PACKAGE_PATH = ROOT / "vcf_architecture"
RESEARCH_PATH = ROOT / "research.md"
SCHEMA_PATH = ROOT / "schema" / "migration-plan.schema.json"

# These hashes make the checked-in fixture and grading authority immutable.
INVENTORY_SHA256 = "2cf0b4155f76a467363e579e651e66b4338143a9b5d17e76137c31ef43d69c65"
SNAPSHOT_SHA256 = "bc5b5cc9650a2946105b7d17130cdb7f163beb0a2cbbc130ceb9a9a3f87d4160"
SCHEMA_SHA256 = "389ccb8b99d2fc2763506c229e0d54ac2d2c5df5de1d4518b8e4fb19b6a1960c"

EXPECTED_PRODUCT_STEPS = {
    "ops-01": ["protect-source", "prepare-lifecycle", "upgrade-operations"],
    "auto-01": ["protect-source", "prepare-lifecycle", "prepare-automation", "upgrade-automation"],
    "logs-01": [
        "protect-source",
        "deploy-logs",
        "move-logs-content-and-feeds",
        "transfer-log-data",
        "cutover-and-retire",
    ],
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_research() -> list[str]:
    """Validate the deterministic artifact record; live consultation is trace-reviewed."""

    if not RESEARCH_PATH.is_file():
        return ["research.md is missing"]
    try:
        text = RESEARCH_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"cannot load research.md: {exc}"]

    expected_header = ["Title", "Publisher", "Accessed", "URL", "Design decision"]
    rows: list[list[str]] = []
    header_seen = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if cells == expected_header:
            header_seen = True
            continue
        if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        if header_seen:
            rows.append(cells)

    errors: list[str] = []
    if not header_seen:
        return ["research.md must contain a Title/Publisher/Accessed/URL/Design decision table"]
    if not rows:
        errors.append("research.md must record at least one consulted Broadcom source")

    urls: set[str] = set()
    corpus: list[str] = []
    for index, cells in enumerate(rows, start=1):
        if len(cells) != len(expected_header) or not all(cells):
            errors.append(f"research source row {index} must populate all five fields")
            continue
        title, publisher, accessed, url, decision = cells
        corpus.extend((title.lower(), decision.lower()))
        if "broadcom" not in publisher.lower():
            errors.append(f"research source row {index} is not identified as Broadcom-published")
        try:
            date.fromisoformat(accessed)
        except ValueError:
            errors.append(f"research source row {index} has an invalid ISO access date")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (host == "broadcom.com" or host.endswith(".broadcom.com")):
            errors.append(f"research source row {index} must use a live HTTPS Broadcom URL")
        if url in urls:
            errors.append(f"research source row {index} repeats a URL")
        urls.add(url)

    joined = " ".join(corpus)
    coverage = {
        "Operations migration": "operations" in joined,
        "Automation migration": "automation" in joined,
        "Logs migration": "operations for logs" in joined,
        "content compatibility/exclusions": any(term in joined for term in ("content", "compatib", "unsupported", "exclusion")),
        "sizing": "siz" in joined,
        "placement/failure tolerance": any(term in joined for term in ("host", "placement", "vsan", "failure")),
        "support boundaries": "support" in joined,
    }
    for topic, present in coverage.items():
        if not present:
            errors.append(f"research.md does not document a source-backed decision for {topic}")
    return errors


def resource_totals(components: list[dict[str, Any]]) -> dict[str, int]:
    totals = {"vcpu": 0, "memory_gib": 0, "storage_gib": 0}
    for component in components:
        sizing = component["sizing"]
        count = sizing["node_count"]
        totals["vcpu"] += count * sizing["vcpu_per_node"]
        totals["memory_gib"] += count * sizing["memory_gib_per_node"]
        totals["storage_gib"] += count * sizing["disk_gib_per_node"]
    return totals


def expected_capacity(estate: dict[str, Any], components: list[dict[str, Any]]) -> dict[str, Any]:
    domain = estate["foundation"]["management_domain"]
    ftt = domain["storage"]["failures_to_tolerate"]
    surviving = domain["host_count"] - ftt
    per_host = domain["per_host_capacity"]
    available = {
        "vcpu": surviving * per_host["physical_cores"],
        "memory_gib": surviving * per_host["memory_gib"],
        "storage_gib": surviving * per_host["usable_storage_gib"],
    }
    targets = resource_totals(components)
    baseline = domain["baseline_committed"]
    combined = {
        "vcpu": baseline["vcpu"] + targets["vcpu"],
        "memory_gib": baseline["memory_gib"] + targets["memory_gib"],
        "storage_gib": baseline["storage_gib"] + targets["storage_gib"],
    }
    return {
        "surviving_hosts": surviving,
        "available_after_ftt": available,
        "target_components_total": targets,
        "baseline_plus_target": combined,
        "fits_after_ftt": all(combined[key] <= available[key] for key in available),
    }


def validate_plan(plan: Any, estate: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    if not isinstance(plan, dict):
        return ["migration_plan.json must contain a JSON object"]

    top_keys = {
        "schema_version",
        "estate_id",
        "snapshot_id",
        "architecture",
        "product_migrations",
        "support_boundaries",
        "steps",
    }
    check(set(plan) == top_keys, "plan top-level fields do not match schema 1.0")
    check(plan.get("schema_version") == "1.0", "schema_version must be 1.0")
    check(plan.get("estate_id") == estate["estate_id"], "estate_id does not match inventory")
    check(plan.get("snapshot_id") == snapshot["snapshot_id"], "snapshot_id does not match pinned authority")

    architecture = plan.get("architecture")
    if not isinstance(architecture, dict):
        errors.append("architecture must be an object")
        return errors

    expected_arch_keys = {
        "site_count",
        "site_id",
        "deployment_model",
        "target_foundation_version",
        "management_domain",
        "capacity",
        "target_components",
    }
    check(set(architecture) == expected_arch_keys, "architecture fields do not match schema")
    check(architecture.get("site_count") == 1, "architecture must be single-site")
    check(architecture.get("site_count") == estate["site"]["site_count"], "site count contradicts inventory")
    check(architecture.get("site_id") == estate["site"]["id"], "site placement contradicts inventory")
    check(
        architecture.get("deployment_model") == snapshot["host_requirements"]["deployment_model"],
        "deployment model contradicts pinned authority",
    )
    check(
        architecture.get("target_foundation_version") == snapshot["target_foundation_version"],
        "target foundation version contradicts pinned authority",
    )

    domain = architecture.get("management_domain")
    inventory_domain = estate["foundation"]["management_domain"]
    expected_domain = {
        "id": inventory_domain["id"],
        "cluster_id": inventory_domain["cluster_id"],
        "host_count": inventory_domain["host_count"],
        "storage_architecture": inventory_domain["storage"]["architecture"],
        "raid": inventory_domain["storage"]["raid"],
        "failures_to_tolerate": inventory_domain["storage"]["failures_to_tolerate"],
    }
    check(domain == expected_domain, "management-domain topology or storage policy contradicts inventory")

    if isinstance(domain, dict):
        hosts = domain.get("host_count")
        raid = domain.get("raid")
        ftt = domain.get("failures_to_tolerate")
        minimum = snapshot["host_requirements"]["management_domain_minimum_hosts"]
        check(isinstance(hosts, int), "management-domain host count must be an integer")
        policy_table = snapshot["host_requirements"]["storage_policy_minimum_hosts"].get(raid, {})
        required_for_ftt = policy_table.get(str(ftt))
        check(required_for_ftt is not None, "RAID/FTT combination is absent from the pinned supported table")
        if isinstance(hosts, int) and isinstance(required_for_ftt, int):
            required_minimum = max(minimum, required_for_ftt)
            check(
                hosts == required_minimum,
                f"host count {hosts} must remain at the supported minimum of {required_minimum} for {raid} failures-to-tolerate {ftt}",
            )

    target_components = architecture.get("target_components")
    check(target_components == snapshot["target_components"], "target placement or sizing differs from pinned design")
    if isinstance(target_components, list):
        expected = expected_capacity(estate, target_components)
        check(architecture.get("capacity") == expected, "capacity arithmetic or FTT headroom is incorrect")
        check(expected["fits_after_ftt"] is True, "target components do not fit after the stated host failure")

        targets = {item.get("component"): item for item in target_components if isinstance(item, dict)}
        profile = estate["workload_profile"]
        ops = targets.get("VCF Operations", {}).get("sizing", {})
        check(ops.get("maximum_objects", 0) >= profile["operations_objects"], "VCF Operations object sizing is too small")
        check(ops.get("maximum_metrics", 0) >= profile["operations_metrics"], "VCF Operations metric sizing is too small")
        automation = targets.get("VCF Automation", {}).get("sizing", {})
        check(
            automation.get("maximum_managed_deployments", 0) >= profile["automation_managed_deployments"],
            "VCF Automation sizing is too small",
        )
        logs = targets.get("VCF Operations for Logs", {}).get("sizing", {})
        log_nodes = logs.get("node_count", 0)
        check(log_nodes >= 3, "production Logs cluster must contain at least three nodes")
        check(
            log_nodes * logs.get("ingestion_gib_per_day_per_node", 0) >= profile["logs_gib_per_day"],
            "Logs daily-ingestion sizing is too small",
        )
        check(
            log_nodes * logs.get("events_per_second_per_node", 0) >= profile["logs_events_per_second"],
            "Logs events-per-second sizing is too small",
        )

    inventory_products = {item["id"]: item for item in estate["source_products"]}
    path_rules = {item["source_id"]: item for item in snapshot["migration_paths"]}
    compatibility = {item["content_id"]: item for item in snapshot["content_compatibility"]}
    migrations = plan.get("product_migrations")
    if not isinstance(migrations, list):
        errors.append("product_migrations must be an array")
        migrations = []
    check(len(migrations) == len(inventory_products), "every source product must have exactly one migration mapping")
    seen_sources: set[str] = set()
    seen_content: set[str] = set()

    for migration in migrations:
        if not isinstance(migration, dict):
            errors.append("each product migration must be an object")
            continue
        check(
            set(migration) == {"source", "target", "method", "carry_forward", "abandoned", "step_ids"},
            "product migration fields do not match schema",
        )
        source = migration.get("source", {})
        source_id = source.get("id") if isinstance(source, dict) else None
        check(source_id in inventory_products, f"unknown source product mapping: {source_id!r}")
        check(source_id not in seen_sources, f"duplicate source product mapping: {source_id!r}")
        if source_id not in inventory_products or source_id in seen_sources:
            continue
        seen_sources.add(source_id)
        inventoried = inventory_products[source_id]
        expected_source = {
            "id": inventoried["id"],
            "product": inventoried["product"],
            "former_name": inventoried["former_name"],
            "version": inventoried["version"],
        }
        check(source == expected_source, f"source identity/version is incorrect for {source_id}")
        rule = path_rules[source_id]
        check(inventoried["version"] in rule["supported_source_versions"], f"unsupported source version for {source_id}")
        check(
            migration.get("target") == {"component": rule["target_component"], "version": rule["target_version"]},
            f"target component/version is incorrect for {source_id}",
        )
        check(migration.get("method") == rule["method"], f"migration method is incorrect for {source_id}")
        step_ids = migration.get("step_ids")
        check(
            isinstance(step_ids, list) and step_ids and all(isinstance(item, str) and item for item in step_ids),
            f"step_ids must link {source_id} to at least one migration step",
        )
        check(step_ids == EXPECTED_PRODUCT_STEPS[source_id], f"step_ids do not map the executable path for {source_id}")

        content_by_id = {item["id"]: item for item in inventoried["content"]}
        for field, disposition in (("carry_forward", "carry"), ("abandoned", "abandon")):
            entries = migration.get(field)
            if not isinstance(entries, list):
                errors.append(f"{field} must be an array for {source_id}")
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    errors.append(f"{field} entries must be objects for {source_id}")
                    continue
                check(
                    set(entry) == {"content_id", "name", "mode", "reason_code"},
                    f"content disposition fields do not match schema for {source_id}",
                )
                content_id = entry.get("content_id")
                check(content_id in content_by_id, f"content {content_id!r} does not belong to {source_id}")
                check(content_id not in seen_content, f"content {content_id!r} is accounted more than once")
                if content_id not in content_by_id or content_id in seen_content:
                    continue
                seen_content.add(content_id)
                pinned = compatibility.get(content_id)
                check(pinned is not None, f"content {content_id!r} is absent from pinned compatibility")
                if pinned is not None:
                    check(pinned["disposition"] == disposition, f"wrong disposition for {content_id}")
                    check(entry.get("mode") == pinned["mode"], f"wrong transfer/disposition mode for {content_id}")
                    check(entry.get("reason_code") == pinned["reason_code"], f"wrong reason code for {content_id}")
                check(entry.get("name") == content_by_id[content_id]["name"], f"wrong content name for {content_id}")

    expected_content = {
        item["id"]
        for product in estate["source_products"]
        for item in product["content"]
    }
    check(seen_sources == set(inventory_products), "not all source products are mapped")
    check(seen_content == expected_content, "not all inventoried content is accounted exactly once")
    check(set(compatibility) == expected_content, "pinned content authority and inventory are inconsistent")

    check(plan.get("support_boundaries") == snapshot["support_boundaries"], "support boundaries differ from pinned authority")

    phases = snapshot["required_phases"]
    phase_ids = [item["id"] for item in phases]
    steps = plan.get("steps")
    if not isinstance(steps, list):
        errors.append("steps must be an array")
        steps = []
    check([item.get("id") for item in steps if isinstance(item, dict)] == phase_ids, "migration phases are missing or out of order")
    expected_sequences = list(range(1, len(phases) + 1))
    check(
        [item.get("sequence") for item in steps if isinstance(item, dict)] == expected_sequences,
        "step sequence numbers must be contiguous and ordered",
    )
    all_gate_ids: set[str] = set()
    phase_positions = {phase_id: index for index, phase_id in enumerate(phase_ids)}
    for index, phase in enumerate(phases):
        for predecessor in phase["after"]:
            check(
                predecessor in phase_positions and phase_positions[predecessor] < index,
                f"phase dependency {predecessor} -> {phase['id']} is not ordered",
            )
        if index >= len(steps) or not isinstance(steps[index], dict):
            continue
        step = steps[index]
        check(
            set(step) == {"sequence", "id", "objective", "actions", "gates"},
            f"step fields do not match schema for {phase['id']}",
        )
        check(
            isinstance(step.get("objective"), str) and len(step["objective"].strip()) >= 20,
            f"step {phase['id']} lacks a concrete objective",
        )
        actions = step.get("actions")
        check(
            isinstance(actions, list)
            and actions
            and all(isinstance(item, str) and len(item.strip()) >= 20 for item in actions),
            f"step {phase['id']} must contain concrete actions",
        )
        gates = step.get("gates")
        if not isinstance(gates, list):
            errors.append(f"step {phase['id']} gates must be an array")
            continue
        gate_ids = [gate.get("id") for gate in gates if isinstance(gate, dict)]
        check(set(gate_ids) == set(phase["required_gate_ids"]), f"step {phase['id']} has the wrong pass/fail gates")
        check(len(gate_ids) == len(set(gate_ids)), f"step {phase['id']} repeats a gate")
        for gate in gates:
            if not isinstance(gate, dict):
                errors.append(f"step {phase['id']} contains a non-object gate")
                continue
            check(
                set(gate) == {"id", "condition", "evidence", "on_failure"},
                f"gate fields do not match schema in step {phase['id']}",
            )
            check(gate.get("id") not in all_gate_ids, f"gate {gate.get('id')!r} is duplicated across steps")
            all_gate_ids.add(gate.get("id"))
            check(
                isinstance(gate.get("condition"), str) and len(gate["condition"].strip()) >= 20,
                f"gate {gate.get('id')!r} lacks a concrete condition",
            )
            check(
                isinstance(gate.get("evidence"), str) and len(gate["evidence"].strip()) >= 20,
                f"gate {gate.get('id')!r} lacks concrete evidence",
            )
            check(gate.get("on_failure") == "halt", f"gate {gate.get('id')!r} must halt its step on failure")

    valid_steps = set(phase_ids)
    for migration in migrations:
        if isinstance(migration, dict) and isinstance(migration.get("step_ids"), list):
            check(set(migration["step_ids"]) <= valid_steps, "product migration references an unknown step")

    return errors


def verify_stdlib_only() -> list[str]:
    errors: list[str] = []
    if not PACKAGE_PATH.is_dir():
        return ["vcf_architecture package is missing"]
    python_files = sorted(PACKAGE_PATH.rglob("*.py"))
    if not python_files:
        return ["vcf_architecture package contains no Python modules"]
    allowed = set(sys.stdlib_module_names) | {"vcf_architecture"}
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            errors.append(f"cannot parse {path.relative_to(ROOT)}: {exc}")
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                root_name = name.split(".", 1)[0]
                if root_name not in allowed:
                    errors.append(f"non-stdlib import {name!r} in {path.relative_to(ROOT)}")
    return errors


def main() -> int:
    failures: list[str] = []
    try:
        estate = load_json(INVENTORY_PATH)
        snapshot = load_json(SNAPSHOT_PATH)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot load protected fixtures: {exc}")
        return 1

    if canonical_hash(estate) != INVENTORY_SHA256:
        failures.append("estate fixture was modified")
    if canonical_hash(snapshot) != SNAPSHOT_SHA256:
        failures.append("compatibility snapshot was modified")
    try:
        schema = load_json(SCHEMA_PATH)
        if canonical_hash(schema) != SCHEMA_SHA256:
            failures.append("migration-plan schema was modified")
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"cannot load protected migration-plan schema: {exc}")

    if not PLAN_PATH.is_file():
        failures.append("migration_plan.json is missing")
        plan = {}
    else:
        try:
            plan = load_json(PLAN_PATH)
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"cannot load migration_plan.json: {exc}")
            plan = {}

    failures.extend(validate_plan(plan, estate, snapshot))
    failures.extend(verify_stdlib_only())
    failures.extend(validate_research())

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        module = importlib.import_module("vcf_architecture")
        builder = getattr(module, "build_plan")
        built = builder(copy.deepcopy(estate), copy.deepcopy(snapshot))
        if built != plan:
            failures.append("build_plan output differs from checked-in migration_plan.json")
        if builder(copy.deepcopy(estate), copy.deepcopy(snapshot)) != built:
            failures.append("build_plan is not deterministic")
        incompatible_cases = []
        bad_hosts = copy.deepcopy(estate)
        bad_hosts["foundation"]["management_domain"]["host_count"] = 3
        incompatible_cases.append(("host-count/FTT contradiction", bad_hosts, copy.deepcopy(snapshot)))
        excess_hosts = copy.deepcopy(estate)
        excess_hosts["foundation"]["management_domain"]["host_count"] = 5
        incompatible_cases.append(("management domain above the required minimum", excess_hosts, copy.deepcopy(snapshot)))
        bad_version = copy.deepcopy(estate)
        bad_version["source_products"][0]["version"] = "8.10.2"
        incompatible_cases.append(("unsupported source version", bad_version, copy.deepcopy(snapshot)))
        duplicate_product = copy.deepcopy(estate)
        duplicate_product["source_products"].append(copy.deepcopy(duplicate_product["source_products"][0]))
        incompatible_cases.append(("duplicate source product", duplicate_product, copy.deepcopy(snapshot)))
        duplicate_content = copy.deepcopy(estate)
        duplicate_content["source_products"][0]["content"].append(
            copy.deepcopy(duplicate_content["source_products"][0]["content"][0])
        )
        incompatible_cases.append(("duplicate inventory content", duplicate_content, copy.deepcopy(snapshot)))
        duplicate_path = copy.deepcopy(snapshot)
        duplicate_path["migration_paths"].append(copy.deepcopy(duplicate_path["migration_paths"][0]))
        incompatible_cases.append(("duplicate migration path", copy.deepcopy(estate), duplicate_path))
        duplicate_rule = copy.deepcopy(snapshot)
        duplicate_rule["content_compatibility"].append(copy.deepcopy(duplicate_rule["content_compatibility"][0]))
        incompatible_cases.append(("duplicate content disposition", copy.deepcopy(estate), duplicate_rule))
        invalid_disposition = copy.deepcopy(snapshot)
        invalid_disposition["content_compatibility"][0]["disposition"] = "maybe"
        incompatible_cases.append(("invalid content disposition", copy.deepcopy(estate), invalid_disposition))
        unknown_target = copy.deepcopy(snapshot)
        unknown_target["migration_paths"][0]["target_component"] = "Unknown Operations Target"
        incompatible_cases.append(("migration path without a target component", copy.deepcopy(estate), unknown_target))
        wrong_placement = copy.deepcopy(snapshot)
        wrong_placement["target_components"][0]["placement"]["cluster_id"] = "other-cluster"
        incompatible_cases.append(("target placement outside the inventory", copy.deepcopy(estate), wrong_placement))
        undersized = copy.deepcopy(snapshot)
        undersized["target_components"][0]["sizing"]["maximum_objects"] = 1
        incompatible_cases.append(("target sizing below workload", copy.deepcopy(estate), undersized))
        wrong_boundary = copy.deepcopy(snapshot)
        wrong_boundary["support_boundaries"][0]["version"] = "8.18.2"
        incompatible_cases.append(("support boundary identity mismatch", copy.deepcopy(estate), wrong_boundary))
        invalid_transfer_window = copy.deepcopy(snapshot)
        invalid_transfer_window["migration_paths"][2]["maximum_log_data_transfer_days"] = 30
        incompatible_cases.append(("log transfer window contradicts inventoried content", copy.deepcopy(estate), invalid_transfer_window))
        for label, candidate_estate, candidate_snapshot in incompatible_cases:
            try:
                builder(candidate_estate, candidate_snapshot)
            except Exception:  # noqa: BLE001 - any explicit rejection is acceptable here
                pass
            else:
                failures.append(f"build_plan accepted {label}")
    except Exception as exc:  # noqa: BLE001 - report candidate failure cleanly
        failures.append(f"build_plan failed: {type(exc).__name__}: {exc}")

    if PACKAGE_PATH.is_dir():
        with tempfile.TemporaryDirectory(prefix="vcf-architecture-verify-") as temp_dir:
            output_path = Path(temp_dir) / "plan.json"
            command = [
                sys.executable,
                "-B",
                "-m",
                "vcf_architecture",
                "--inventory",
                str(INVENTORY_PATH),
                "--compatibility",
                str(SNAPSHOT_PATH),
                "--output",
                str(output_path),
            ]
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
            if completed.returncode != 0:
                failures.append(f"package CLI failed: {completed.stderr.strip() or completed.stdout.strip()}")
            elif not output_path.is_file():
                failures.append("package CLI did not write its output")
            else:
                try:
                    generated = load_json(output_path)
                    if generated != plan:
                        failures.append("package CLI output differs from checked-in migration_plan.json")
                except (OSError, json.JSONDecodeError) as exc:
                    failures.append(f"package CLI wrote invalid JSON: {exc}")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("PASS: VCF migration architecture is complete, compatible, sized, ordered, and FTT-consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
