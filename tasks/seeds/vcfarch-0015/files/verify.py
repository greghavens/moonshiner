#!/usr/bin/env python3
"""Offline acceptance verifier for the VCF architecture artifacts."""

from __future__ import annotations

import ast
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
OPENAPI_PATH = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
OPENAPI_SHA256 = "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"


class ValidationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise ValidationError(f"missing artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON in {path}: {exc}") from exc


def json_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ValidationError(f"unsupported schema type {expected!r}")


def resolve_pointer(document: Any, pointer: str) -> Any:
    require(pointer.startswith("#/"), f"only local JSON pointers are supported: {pointer}")
    current = document
    for raw in pointer[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        require(isinstance(current, dict) and token in current, f"unresolved $ref {pointer}")
        current = current[token]
    return current


def validate_json(instance: Any, schema: Any, document: Any, path: str = "$") -> None:
    """Validate the JSON Schema/OpenAPI keywords used by the protected contracts."""
    require(isinstance(schema, dict), f"{path}: schema is not an object")

    if "$ref" in schema:
        validate_json(instance, resolve_pointer(document, schema["$ref"]), document, path)
        return

    for child in schema.get("allOf", []):
        validate_json(instance, child, document, path)

    if "anyOf" in schema:
        matches = 0
        for child in schema["anyOf"]:
            try:
                validate_json(instance, child, document, path)
                matches += 1
            except ValidationError:
                pass
        require(matches >= 1, f"{path}: does not match anyOf")

    if "oneOf" in schema:
        matches = 0
        for child in schema["oneOf"]:
            try:
                validate_json(instance, child, document, path)
                matches += 1
            except ValidationError:
                pass
        require(matches == 1, f"{path}: must match exactly one oneOf branch, got {matches}")

    if instance is None and schema.get("nullable") is True:
        return

    expected_type = schema.get("type")
    if expected_type is not None:
        alternatives = expected_type if isinstance(expected_type, list) else [expected_type]
        require(
            any(json_type_matches(instance, item) for item in alternatives),
            f"{path}: expected type {expected_type!r}, got {type(instance).__name__}",
        )

    if "enum" in schema:
        require(instance in schema["enum"], f"{path}: {instance!r} is not in enum")
    if "const" in schema:
        require(instance == schema["const"], f"{path}: expected constant {schema['const']!r}")

    if isinstance(instance, dict):
        for name in schema.get("required", []):
            require(name in instance, f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, value in instance.items():
            child_path = f"{path}.{name}"
            if name in properties:
                validate_json(value, properties[name], document, child_path)
            elif schema.get("additionalProperties") is False:
                raise ValidationError(f"{child_path}: additional property is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_json(value, schema["additionalProperties"], document, child_path)
        if "minProperties" in schema:
            require(len(instance) >= schema["minProperties"], f"{path}: too few properties")
        if "maxProperties" in schema:
            require(len(instance) <= schema["maxProperties"], f"{path}: too many properties")

    if isinstance(instance, list):
        if "minItems" in schema:
            require(len(instance) >= schema["minItems"], f"{path}: too few items")
        if "maxItems" in schema:
            require(len(instance) <= schema["maxItems"], f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            require(len(encoded) == len(set(encoded)), f"{path}: items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                validate_json(value, item_schema, document, f"{path}[{index}]")

    if isinstance(instance, str):
        if "minLength" in schema:
            require(len(instance) >= schema["minLength"], f"{path}: string is too short")
        if "maxLength" in schema:
            require(len(instance) <= schema["maxLength"], f"{path}: string is too long")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance)
            except re.error as exc:
                raise ValidationError(f"{path}: invalid protected schema pattern: {exc}") from exc
            require(matched is not None, f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema:
            require(instance >= schema["minimum"], f"{path}: below minimum")
        if "maximum" in schema:
            require(instance <= schema["maximum"], f"{path}: above maximum")
        if "exclusiveMinimum" in schema:
            require(instance > schema["exclusiveMinimum"], f"{path}: below exclusive minimum")
        if "exclusiveMaximum" in schema:
            require(instance < schema["exclusiveMaximum"], f"{path}: above exclusive maximum")


def run_generator(output_dir: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "vcf_architecture",
        "--requirements",
        "fixtures/requirements.json",
        "--inventory",
        "fixtures/estate-inventory.json",
        "--compatibility",
        "fixtures/compatibility-snapshot.json",
        "--output-dir",
        str(output_dir),
    ]
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    require(
        completed.returncode == 0,
        "generator failed\nstdout:\n"
        + completed.stdout[-2000:]
        + "\nstderr:\n"
        + completed.stderr[-2000:],
    )


def verify_research_record() -> None:
    record = load_json(ROOT / "research-consulted.json")
    require(isinstance(record, list) and record, "research-consulted.json must be a non-empty JSON array")
    for index, source in enumerate(record):
        path = f"research-consulted.json[{index}]"
        require(isinstance(source, dict), f"{path} must be an object")
        for field in ("title", "url", "retrievalDate", "note"):
            require(
                isinstance(source.get(field), str) and bool(source[field].strip()),
                f"{path}.{field} must be a non-empty string",
            )
        parsed = urlsplit(source["url"])
        require(
            parsed.scheme == "https" and bool(parsed.netloc),
            f"{path}.url must be an absolute https URL",
        )
        hostname = (parsed.hostname or "").lower()
        require(
            hostname != "localhost"
            and not hostname.endswith((".invalid", ".test", ".example")),
            f"{path}.url must identify a real web source",
        )
        try:
            parsed_date = date.fromisoformat(source["retrievalDate"])
        except ValueError as exc:
            raise ValidationError(f"{path}.retrievalDate must be a valid ISO date") from exc
        require(
            source["retrievalDate"] == parsed_date.isoformat(),
            f"{path}.retrievalDate must use YYYY-MM-DD form",
        )


def verify_deterministic_artifacts(first: Path, second: Path) -> None:
    for name in ("sddc-spec.json", "migration-plan.json"):
        first_path = first / name
        second_path = second / name
        require(first_path.is_file(), f"missing artifact: {first_path}")
        require(second_path.is_file(), f"missing artifact on repeated generation: {second_path}")
        require(
            first_path.read_bytes() == second_path.read_bytes(),
            f"{name} is not byte-for-byte deterministic",
        )


def balanced_distribution(total: int, sites: list[str]) -> dict[str, int]:
    quotient, remainder = divmod(total, len(sites))
    return {site: quotient + (1 if index < remainder else 0) for index, site in enumerate(sites)}


def required_capacity(requirements: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    demand = requirements["capacity"]["workloadDemand"]
    host = requirements["capacity"]["workloadHostProfile"]
    policy = snapshot["capacityPolicy"]
    reserve = requirements["availability"]["workloadHostFailures"]
    by_resource = {
        "cpu": math.ceil(demand["vCpu"] / (host["physicalCores"] * policy["cpuUtilizationLimit"]))
        + reserve,
        "memory": math.ceil(demand["memoryGiB"] / (host["memoryGiB"] * policy["memoryUtilizationLimit"]))
        + reserve,
        "storage": math.ceil(
            demand["usableStorageTiB"]
            / (
                host["rawStorageTiB"]
                * policy["storageUtilizationLimit"]
                * policy["storageProtectionCapacityFactor"]
            )
        )
        + reserve,
    }
    return {
        "workloadDemand": demand,
        "workloadHostProfile": host,
        "policy": policy,
        "failureReserveHosts": reserve,
        "requiredHostsByResource": by_resource,
        "selectedWorkloadHostCount": max(by_resource.values()),
    }


def verify_sddc_semantics(
    sddc: dict[str, Any], requirements: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    installer = requirements["installer"]
    require(sddc.get("sddcId") == installer["sddcId"], "SddcSpec sddcId does not match requirements")
    require(sddc.get("workflowType") == requirements["workflowType"], "wrong installer workflowType")
    require(sddc.get("version") == requirements["targetVcfVersion"], "wrong SddcSpec version")
    require(sddc.get("vcfInstanceName") == requirements["vcfInstanceName"], "wrong VCF instance name")
    require(sddc.get("dnsSpec") == installer["dnsSpec"], "dnsSpec must preserve the installer input")
    require(sddc.get("ntpServers") == installer["ntpServers"], "NTP servers must preserve input order")
    require(sddc.get("vcenterSpec") == installer["vcenterSpec"], "vcenterSpec must preserve installer values")
    require(
        sddc.get("managementPoolName") == installer["managementPoolName"],
        "wrong management network pool name",
    )
    expected_hosts = [{"hostname": item["hostname"]} for item in installer["managementHosts"]]
    require(sddc.get("hostSpecs") == expected_hosts, "management hostSpecs do not match the fixture")
    require(sddc.get("networkSpecs") == installer["networks"], "networkSpecs must preserve every requested network")
    expected_dvs = dict(installer["dvs"])
    expected_dvs["networks"] = [item["networkType"] for item in installer["networks"]]
    require(sddc.get("dvsSpecs") == [expected_dvs], "DVS and vmnic/uplink mapping do not match requirements")

    architecture = sddc.get("x-vcfArchitecture")
    require(isinstance(architecture, dict), "SddcSpec needs an x-vcfArchitecture object")
    require(
        architecture.get("architectureId") == requirements["architectureId"],
        "wrong x-vcfArchitecture architectureId",
    )
    require(architecture.get("sites") == requirements["sites"], "site topology does not match requirements")
    require(
        architecture.get("availability") == requirements["availability"],
        "availability decisions do not match requirements",
    )

    capacity = required_capacity(requirements, snapshot)
    require(architecture.get("capacity") == capacity, "capacity result or governing inputs are wrong")
    host_count = capacity["selectedWorkloadHostCount"]
    azs = requirements["availability"]["workloadAvailabilityZones"]
    expected_workload_distribution = balanced_distribution(host_count, azs)
    management_distribution = {site: 0 for site in requirements["availability"]["managementAvailabilityZones"]}
    for host in installer["managementHosts"]:
        require(host["site"] in management_distribution, f"management host uses unknown site {host['site']}")
        management_distribution[host["site"]] += 1

    clusters = architecture.get("clusters")
    require(isinstance(clusters, list) and len(clusters) == 2, "architecture must contain management and workload clusters")
    by_kind = {item.get("kind"): item for item in clusters if isinstance(item, dict)}
    require(set(by_kind) == {"MANAGEMENT", "VI_WORKLOAD"}, "cluster kinds must be MANAGEMENT and VI_WORKLOAD")
    management = by_kind["MANAGEMENT"]
    require(management.get("id") == installer["sddcId"], "wrong management cluster id")
    require(management.get("hostCount") == len(installer["managementHosts"]), "wrong management host count")
    require(management.get("siteDistribution") == management_distribution, "wrong management AZ distribution")
    require(management.get("witnessSite") == requirements["availability"]["witnessSite"], "wrong witness site")
    workload = by_kind["VI_WORKLOAD"]
    require(workload.get("id") == requirements["capacity"]["workloadClusterId"], "wrong workload cluster id")
    require(workload.get("hostCount") == host_count, "workload cluster is not sized from capacity")
    require(workload.get("siteDistribution") == expected_workload_distribution, "wrong workload AZ distribution")
    require(workload.get("witnessSite") == requirements["availability"]["witnessSite"], "wrong workload witness")

    peak = requirements["edge"]["peakNorthSouthGbps"]
    candidates = sorted(
        (item for item in snapshot["edge"]["formFactors"] if item["validatedThroughputGbps"] >= peak),
        key=lambda item: (item["validatedThroughputGbps"], item["name"]),
    )
    require(candidates, "pinned snapshot has no Edge form factor capable of the required peak")
    selected = candidates[0]
    layout_name = selected["uplinkLayout"]
    expected_uplinks = snapshot["edge"]["uplinkLayouts"][layout_name]
    edge = architecture.get("edge")
    require(isinstance(edge, dict), "architecture is missing Edge design")
    require(edge.get("peakNorthSouthGbps") == peak, "Edge peak throughput input is missing or wrong")
    require(edge.get("formFactor") == selected["name"], "Edge form factor is not the smallest supported survivor")
    require(
        edge.get("perNodeValidatedThroughputGbps") == selected["validatedThroughputGbps"],
        "wrong Edge per-node throughput",
    )
    require(edge.get("uplinkLayout") == layout_name, "Edge uplink layout does not match its form factor")
    require(edge.get("uplinks") == expected_uplinks, "Edge uplink interfaces, pNICs, speeds, or fabrics are wrong")
    require(edge.get("haMode") == requirements["edge"]["haMode"], "wrong Edge HA mode")
    require(edge.get("nodeCount") == requirements["edge"]["nodeCount"], "wrong Edge node count")
    survivors = requirements["edge"]["nodeCount"] - requirements["availability"]["edgeNodeFailures"]
    require(edge.get("survivingNodesAfterFailure") == survivors, "wrong surviving Edge node count")
    require(
        edge.get("postFailureCapacityGbps") == survivors * selected["validatedThroughputGbps"],
        "wrong post-failure Edge capacity",
    )
    require(edge.get("headroomGbps") == selected["validatedThroughputGbps"] - peak, "wrong per-survivor headroom")
    require(edge["postFailureCapacityGbps"] >= peak, "Edge design cannot carry peak after the required failure")
    nodes = edge.get("nodes")
    require(isinstance(nodes, list) and len(nodes) == requirements["edge"]["nodeCount"], "wrong Edge node inventory")
    require(len({node.get("name") for node in nodes if isinstance(node, dict)}) == len(nodes), "Edge node names must be unique")
    require(
        sorted(node.get("site") for node in nodes) == sorted(requirements["edge"]["placementSites"]),
        "Edge nodes must span the requested availability zones",
    )
    require(all(node.get("formFactor") == selected["name"] for node in nodes), "all Edge nodes need one form factor")


def verify_migration(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any], schema: dict[str, Any]
) -> None:
    validate_json(plan, schema, schema)
    require(plan["estateId"] == inventory["estateId"], "migration plan has the wrong estateId")
    require(plan["targetVcfVersion"] == inventory["targetVcfVersion"], "migration plan has the wrong target version")

    inventory_by_id = {item["id"]: item for item in inventory["components"]}
    require(len(inventory_by_id) == len(inventory["components"]), "protected inventory contains duplicate ids")
    steps = plan["steps"]
    require([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "step order must be contiguous from 1")
    require(len(steps) == len(inventory_by_id), "migration plan must contain exactly one step per estate component")
    require(
        {step["componentId"] for step in steps} == set(inventory_by_id),
        "migration plan component coverage differs from the estate inventory",
    )

    external = {
        gate
        for gate in snapshot["externalGates"]
        if inventory.get("externalGates", {}).get(gate) is True
    }
    available = set(external)
    for step in steps:
        component = inventory_by_id[step["componentId"]]
        require(step["componentName"] == component["name"], f"{component['id']}: current component name changed")
        require(step["currentVersion"] == component["version"], f"{component['id']}: current version changed")
        transitions = snapshot["transitions"].get(component["id"], [])
        matches = [
            transition
            for transition in transitions
            if transition["from"] == component["version"]
            and transition["action"] == step["action"]
            and transition["target"] == step["target"]
        ]
        require(len(matches) == 1, f"{component['id']}: action/target is not a pinned supported transition")
        transition = matches[0]
        require(set(step["gates"]) == set(transition["requires"]), f"{component['id']}: gates do not match transition")
        missing = set(transition["requires"]) - available
        require(not missing, f"{component['id']}: gates are not satisfied before this step: {sorted(missing)}")
        available.update(transition["provides"])


def verify_stdlib_package() -> None:
    package = ROOT / "vcf_architecture"
    require((package / "__init__.py").is_file(), "vcf_architecture/__init__.py is required")
    require((package / "__main__.py").is_file(), "vcf_architecture must be runnable with -m")
    python_files = sorted(package.rglob("*.py"))
    require(python_files, "vcf_architecture has no Python modules")
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise ValidationError(f"invalid Python in {path}: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            else:
                continue
            for root in roots:
                require(root in stdlib or root == "vcf_architecture", f"non-stdlib import {root!r} in {path}")


def main() -> int:
    try:
        with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
            output_dir = Path(temporary) / "out-first"
            repeated_output_dir = Path(temporary) / "out-second"
            run_generator(output_dir)
            run_generator(repeated_output_dir)

            # Mandatory phase 1: validate the produced SddcSpec against the exact
            # installer OpenAPI component before loading or checking other artifacts.
            sddc = load_json(output_dir / "sddc-spec.json")
            openapi = load_json(OPENAPI_PATH)
            validate_json(sddc, openapi["components"]["schemas"]["SddcSpec"], openapi)
            require(
                hashlib.sha256(OPENAPI_PATH.read_bytes()).hexdigest() == OPENAPI_SHA256,
                "protected installer OpenAPI document is not the unmodified 9.1.0.0 copy",
            )
            require(openapi.get("info", {}).get("version") == "9.1.0.0", "wrong installer OpenAPI version")
            print("PASS phase 1: sddc-spec.json validates against installer SddcSpec")

            verify_deterministic_artifacts(output_dir, repeated_output_dir)
            verify_research_record()

            # Only after the installer schema passes may scenario, compatibility,
            # package, or migration assertions run.
            requirements = load_json(ROOT / "fixtures/requirements.json")
            inventory = load_json(ROOT / "fixtures/estate-inventory.json")
            snapshot = load_json(ROOT / "fixtures/compatibility-snapshot.json")
            migration_schema = load_json(ROOT / "schemas/migration-plan.schema.json")
            migration = load_json(output_dir / "migration-plan.json")
            verify_sddc_semantics(sddc, requirements, snapshot)
            verify_migration(migration, inventory, snapshot, migration_schema)
            verify_stdlib_package()
            print("PASS phase 2: architecture, capacity, Edge, and migration contracts")
            return 0
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
