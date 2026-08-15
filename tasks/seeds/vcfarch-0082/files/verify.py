#!/usr/bin/env python3
"""Offline acceptance verifier for the pinned VCF architecture task.

The research record is deliberately neither opened nor inspected here.
"""

from __future__ import annotations

import ipaddress
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
ARTIFACT = ROOT / "architecture.json"
INSTALLER_SCHEMA = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise VerificationError(f"{path.name}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=no_duplicates)
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path.name}: {exc}") from exc


def json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "#":
        return document
    if not pointer.startswith("#/"):
        raise VerificationError(f"unsupported external schema reference: {pointer}")
    current = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            current = current[part]
        except (KeyError, TypeError) as exc:
            raise VerificationError(f"unresolvable schema reference: {pointer}") from exc
    return current


def type_matches(value: Any, expected: str) -> bool:
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
    raise VerificationError(f"unsupported JSON Schema type in protected schema: {expected}")


def validate_schema(instance: Any, schema: Any, root_schema: Any, path: str = "$") -> list[str]:
    """Validate the JSON Schema keywords used by the pinned OpenAPI and plan schema."""
    errors: list[str] = []
    if not isinstance(schema, dict):
        return errors

    if "$ref" in schema:
        return validate_schema(instance, json_pointer(root_schema, schema["$ref"]), root_schema, path)

    for branch in schema.get("allOf", []):
        errors.extend(validate_schema(instance, branch, root_schema, path))

    if "anyOf" in schema:
        branch_errors = [validate_schema(instance, branch, root_schema, path) for branch in schema["anyOf"]]
        if all(branch_errors):
            errors.append(f"{path}: does not match any allowed schema")
            return errors

    if "oneOf" in schema:
        matches = sum(not validate_schema(instance, branch, root_schema, path) for branch in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: must match exactly one allowed schema")
            return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} is not in the enum")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(type_matches(instance, item) for item in expected_type):
            errors.append(f"{path}: expected one of types {expected_type}")
            return errors
    elif isinstance(expected_type, str) and not type_matches(instance, expected_type):
        errors.append(f"{path}: expected {expected_type}")
        return errors

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                errors.extend(validate_schema(value, properties[key], root_schema, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: additional property {key!r} is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(
                    validate_schema(value, schema["additionalProperties"], root_schema, f"{path}.{key}")
                )

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            normalized = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(normalized) != len(set(normalized)):
                errors.append(f"{path}: items must be unique")
        if isinstance(schema.get("items"), dict):
            for index, value in enumerate(instance):
                errors.extend(validate_schema(value, schema["items"], root_schema, f"{path}[{index}]"))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance)
            except re.error as exc:
                raise VerificationError(f"invalid pattern in protected schema at {path}: {exc}") from exc
            if matched is None:
                errors.append(f"{path}: does not match required pattern")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: less than minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: greater than maximum {schema['maximum']}")

    return errors


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise VerificationError(f"{label} does not match the protected fixture/snapshot")


def require_contains(actual: Any, expected: Any, label: str) -> None:
    """Require fixture-derived values while allowing other schema-valid design choices."""
    if isinstance(expected, dict):
        require(isinstance(actual, dict), f"{label} must be an object")
        for key, value in expected.items():
            require(key in actual, f"{label} is missing required architecture property {key!r}")
            require_contains(actual[key], value, f"{label}.{key}")
        return
    if isinstance(expected, list):
        require(isinstance(actual, list), f"{label} must be an array")
        require(len(actual) == len(expected), f"{label} has the wrong number of items")
        for index, value in enumerate(expected):
            require_contains(actual[index], value, f"{label}[{index}]")
        return
    require_equal(actual, expected, label)


def verify_sddc_spec_first() -> tuple[dict[str, Any], dict[str, Any]]:
    """The first acceptance operation: validate the artifact as the tagged SddcSpec."""
    artifact = load_json(ARTIFACT)
    installer = load_json(INSTALLER_SCHEMA)
    require(isinstance(artifact, dict), "architecture.json must contain a JSON object")
    sddc_ref = {"$ref": "#/components/schemas/SddcSpec"}
    errors = validate_schema(artifact, sddc_ref, installer)
    if errors:
        preview = "; ".join(errors[:12])
        raise VerificationError(f"architecture.json is not a valid installer SddcSpec: {preview}")
    return artifact, installer


def expected_target_spec(inventory: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    components = {item["id"]: item for item in inventory["components"]}
    versions = snapshot["targetSpecVersions"]
    management_version = versions["managementServices"]
    # The pinned authority fixes target service versions, but it does not fix
    # optional installer sizing or security/telemetry choices. Those choices
    # remain acceptable when they validate against SddcSpec.
    service = {"version": management_version}
    expected = {
        "sddcId": inventory["estate"]["sddcId"],
        "version": snapshot["targetVcfVersion"],
        "vcenterSpec": {
            "vcenterHostname": components["mgmt-vcenter"]["hostname"],
            "rootVcenterPassword": inventory["credentialPlaceholders"]["vcenterRoot"],
            "version": versions["vcenter"],
            "useExistingDeployment": True,
        },
        "clusterSpec": inventory["cluster"],
        "hostSpecs": [{"hostname": hostname} for hostname in components["mgmt-esxi"]["hostnames"]],
        "nsxtSpec": {
            "nsxtManagers": [
                {"hostname": hostname} for hostname in components["shared-nsx"]["managerHostnames"]
            ],
            "vipFqdn": components["shared-nsx"]["vipFqdn"],
            "version": versions["nsx"],
            "useExistingDeployment": True,
        },
        "networkSpecs": inventory["networks"],
        "dnsSpec": inventory["dns"],
        "ntpServers": inventory["ntpServers"],
        "sddcManagerSpec": {
            "hostname": components["sddc-manager"]["hostname"],
            "version": versions["sddcManager"],
            "useExistingDeployment": True,
        },
        "managementPoolName": inventory["targetNames"]["managementPoolName"],
        "vcfOperationsSpec": {
            "nodes": [
                {
                    "hostname": components["vcf-operations"]["hostname"],
                    "type": "master",
                }
            ],
            "useExistingDeployment": True,
            "version": versions["vcfOperations"],
        },
        "vcfManagementComponentsInfrastructureSpec": {
            "localRegionNetwork": {
                "networkName": inventory["managementServices"]["networkName"],
                "subnetMask": inventory["managementServices"]["subnetMask"],
                "gateway": inventory["managementServices"]["gateway"],
            }
        },
        "licenseServerSpec": {
            "hostname": inventory["targetNames"]["licenseServerHostname"],
            "version": versions["licenseServer"],
            "useExistingDeployment": False,
        },
        "vcfInstanceName": inventory["estate"]["vcfInstanceName"],
    }
    allowed_service_specs = {
        "fleetLcmSpec",
        "sddcLcmSpec",
        "fleetDepotSpec",
        "telemetryAcceptorSpec",
        "saltSpec",
        "saltRaasSpec",
    }
    required_service_specs = snapshot["requiredTargetServiceSpecs"]
    require(
        isinstance(required_service_specs, list)
        and len(required_service_specs) == len(set(required_service_specs))
        and set(required_service_specs).issubset(allowed_service_specs),
        "compatibility snapshot contains invalid required target service specs",
    )
    for property_name in required_service_specs:
        expected[property_name] = service
    return expected


def verify_target_spec(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expected = expected_target_spec(inventory, snapshot)
    require_contains(artifact, expected, "SddcSpec")


def verify_upgrade_path(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    source = inventory["estate"]["vcfVersion"]
    target = inventory["entitlement"]["targetVersion"]
    path = plan["upgradePath"]
    require(path[0] == source, "upgradePath must begin at the inventoried VCF version")
    require(path[-1] == target, "upgradePath must end at the entitled target version")
    edges = {(item["source"], item["target"]) for item in snapshot["supportedUpgradeEdges"]}
    for hop in zip(path, path[1:]):
        require(hop in edges, f"unsupported upgrade hop in architecture: {hop[0]} -> {hop[1]}")
    require(len(path) == 2, "the pinned authority requires the supported direct edge without an extra hop")


def verify_topology(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    topology = plan["topology"]
    options = {item["id"]: item for item in snapshot["topologyOptions"] if item["supported"]}
    selected = topology["selected"]
    require(selected in options, "selected topology is not supported by the pinned snapshot")
    entitlement_max = inventory["entitlement"]["maxActiveVcfInstances"]
    require_equal(topology["entitlementMaxActiveVcfInstances"], entitlement_max, "topology entitlement")
    require_equal(
        topology["activeVcfInstancesDuringMigration"],
        options[selected]["requiredActiveVcfInstances"],
        "selected topology active-instance requirement",
    )
    require(
        options[selected]["requiredActiveVcfInstances"] <= entitlement_max,
        "selected topology exceeds the licensed active-instance entitlement",
    )
    excluded_expected = {
        option_id
        for option_id, option in options.items()
        if option["requiredActiveVcfInstances"] > entitlement_max
    }
    excluded_actual = {item["id"] for item in topology["excludedTopologies"]}
    require_equal(excluded_actual, excluded_expected, "entitlement-excluded topology set")
    require(selected not in excluded_actual, "selected topology cannot also be excluded")


def verify_management_network(inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    constraints = snapshot["managementServicesConstraints"]
    service_network = inventory["managementServices"]
    start = ipaddress.ip_address(service_network["availableIpRange"]["start"])
    end = ipaddress.ip_address(service_network["availableIpRange"]["end"])
    require(int(end) >= int(start), "management services IP range is reversed")
    require(
        int(end) - int(start) + 1 >= constraints["minimumAvailableIps"],
        "management services IP range does not meet the pinned minimum",
    )
    matching_networks = [
        item for item in inventory["networks"] if item["networkType"] == constraints["allowedNetworkType"]
    ]
    require(len(matching_networks) == 1, "required management services network is missing or ambiguous")
    internal = ipaddress.ip_network(service_network["internalCidr"])
    if constraints["internalCidrMustNotOverlapEstateNetworks"]:
        for network in inventory["networks"]:
            require(
                not internal.overlaps(ipaddress.ip_network(network["subnet"])),
                "management services internal CIDR overlaps an estate network",
            )


def verify_steps(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    steps = plan["steps"]
    targets = snapshot["targetComponents"]
    require_equal([item["sequence"] for item in steps], list(range(1, len(targets) + 1)), "step sequence")
    require_equal([item["componentId"] for item in steps], [item["id"] for item in targets], "component order")
    require(len({item["componentId"] for item in steps}) == len(steps), "component steps must be unique")

    inventory_components = {item["id"]: item for item in inventory["components"]}
    target_ids = {item["id"] for item in targets}
    require(set(inventory_components).issubset(target_ids), "an inventoried component is absent from the plan")
    known_gates = set(snapshot["gates"])
    sequence_by_component = {item["componentId"]: item["sequence"] for item in steps}

    for step, target in zip(steps, targets):
        expected_step = {
            "sequence": step["sequence"],
            "componentId": target["id"],
            "componentName": target["name"],
            "scope": target["scope"],
            "sourceVersion": target["sourceVersion"],
            "targetVersion": target["targetVersion"],
            "action": target["action"],
            "dependsOn": target["dependsOn"],
            "gateIds": target["gateIds"],
        }
        require_equal(step, expected_step, f"step for {target['id']}")
        require(set(step["gateIds"]).issubset(known_gates), f"unknown gate on {target['id']}")
        for dependency in step["dependsOn"]:
            require(dependency in sequence_by_component, f"unknown dependency {dependency!r}")
            require(
                sequence_by_component[dependency] < step["sequence"],
                f"dependency {dependency!r} is not ordered before {target['id']!r}",
            )
        if target["id"] in inventory_components:
            require_equal(
                step["sourceVersion"],
                inventory_components[target["id"]]["version"],
                f"inventoried source version for {target['id']}",
            )
        else:
            require(step["sourceVersion"] == "not-installed", f"new component {target['id']} has a fake source")


def run_package_and_compare(artifact: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_dir:
        first = Path(temp_dir) / "first.json"
        second = Path(temp_dir) / "second.json"
        base = [
            sys.executable,
            "-S",
            "-m",
            "vcf_architecture",
            "--inventory",
            str(ROOT / "estate_inventory.json"),
            "--compatibility",
            str(ROOT / "compatibility_snapshot.json"),
        ]
        for output in (first, second):
            completed = subprocess.run(
                [*base, "--output", str(output)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )
            require(
                completed.returncode == 0,
                f"package CLI failed under stdlib-only Python (-S): {completed.stderr.strip()}",
            )
        require_equal(first.read_bytes(), second.read_bytes(), "deterministic CLI bytes")
        generated = load_json(first)
        require_equal(generated, artifact, "package-generated architecture")


def main() -> int:
    try:
        # Binding ordering requirement: no fixture/snapshot/plan checks occur before this call.
        artifact, installer = verify_sddc_spec_first()

        require_equal(installer.get("info", {}).get("version"), "9.1.0.0", "installer schema version")
        plan_schema = load_json(ROOT / "migration-plan.schema.json")
        require("migrationPlan" in artifact, "SddcSpec is missing migrationPlan")
        plan_errors = validate_schema(artifact["migrationPlan"], plan_schema, plan_schema)
        if plan_errors:
            raise VerificationError("migrationPlan schema validation failed: " + "; ".join(plan_errors[:12]))

        inventory = load_json(ROOT / "estate_inventory.json")
        snapshot = load_json(ROOT / "compatibility_snapshot.json")
        plan = artifact["migrationPlan"]
        require_equal(plan["inventoryRevision"], inventory["revision"], "inventory revision")
        require_equal(plan["compatibilitySnapshot"], snapshot["snapshotId"], "compatibility snapshot")
        require_equal(plan["sourceVersion"], inventory["estate"]["vcfVersion"], "plan source version")
        require_equal(plan["targetVersion"], snapshot["targetVcfVersion"], "plan target version")
        require_equal(
            inventory["entitlement"]["targetVersion"], snapshot["targetVcfVersion"], "entitled target"
        )

        verify_target_spec(artifact, inventory, snapshot)
        verify_upgrade_path(plan, inventory, snapshot)
        verify_topology(plan, inventory, snapshot)
        verify_management_network(inventory, snapshot)
        verify_steps(plan, inventory, snapshot)
        run_package_and_compare(artifact)
    except (VerificationError, KeyError, TypeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: installer-valid target architecture and pinned brownfield migration plan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
