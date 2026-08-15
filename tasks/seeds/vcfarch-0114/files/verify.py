#!/usr/bin/env python3
"""Offline, deterministic verifier for the VCF architecture artifact."""

from __future__ import annotations

import ast
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent


class VerificationError(Exception):
    pass


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise VerificationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_no_duplicates,
            parse_constant=lambda token: (_ for _ in ()).throw(
                VerificationError(f"non-finite JSON number: {token}")
            ),
        )
    except FileNotFoundError as error:
        raise VerificationError(f"missing required artifact: {path.name}") from error
    except json.JSONDecodeError as error:
        raise VerificationError(f"invalid JSON in {path.name}: {error}") from error


def json_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    if expected == "null":
        return value is None
    return True


def resolve_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise VerificationError(f"unsupported non-local schema reference: {reference}")
    node: Any = root_schema
    for raw in reference[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or token not in node:
            raise VerificationError(f"unresolvable schema reference: {reference}")
        node = node[token]
    if not isinstance(node, dict):
        raise VerificationError(f"schema reference is not an object: {reference}")
    return node


def validate_json_schema(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> None:
    if "$ref" in schema:
        validate_json_schema(value, resolve_ref(root_schema, schema["$ref"]), root_schema, path)
        return

    for child in schema.get("allOf", []):
        validate_json_schema(value, child, root_schema, path)
    if "anyOf" in schema:
        if not any(_schema_accepts(value, child, root_schema, path) for child in schema["anyOf"]):
            raise VerificationError(f"{path}: does not satisfy anyOf")
    if "oneOf" in schema:
        accepted = sum(
            _schema_accepts(value, child, root_schema, path) for child in schema["oneOf"]
        )
        if accepted != 1:
            raise VerificationError(f"{path}: must satisfy exactly one oneOf branch")

    expected_type = schema.get("type")
    if expected_type:
        alternatives = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(json_type_matches(value, item) for item in alternatives):
            raise VerificationError(f"{path}: expected {expected_type}, got {type(value).__name__}")
    if value is None and schema.get("nullable"):
        return
    if "enum" in schema and value not in schema["enum"]:
        raise VerificationError(f"{path}: value is outside enum")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise VerificationError(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in properties:
                validate_json_schema(item, properties[key], root_schema, child_path)
            elif schema.get("additionalProperties") is False:
                raise VerificationError(f"{child_path}: additional property is forbidden")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_json_schema(
                    item, schema["additionalProperties"], root_schema, child_path
                )
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            raise VerificationError(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise VerificationError(f"{path}: too many properties")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise VerificationError(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise VerificationError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(canonical) != len(set(canonical)):
                raise VerificationError(f"{path}: items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_json_schema(item, item_schema, root_schema, f"{path}[{index}]")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise VerificationError(f"{path}: string shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise VerificationError(f"{path}: string longer than maxLength")
        if "pattern" in schema:
            try:
                matches = re.search(schema["pattern"], value)
            except re.error as error:
                raise VerificationError(f"invalid pattern in pinned schema: {error}") from error
            if matches is None:
                raise VerificationError(f"{path}: string does not match schema pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise VerificationError(f"{path}: number below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise VerificationError(f"{path}: number above maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise VerificationError(f"{path}: number below exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            raise VerificationError(f"{path}: number above exclusiveMaximum")


def _schema_accepts(
    value: Any, schema: dict[str, Any], root_schema: dict[str, Any], path: str
) -> bool:
    try:
        validate_json_schema(value, schema, root_schema, path)
        return True
    except VerificationError:
        return False


def extract_sddc_spec(artifact: Any) -> dict[str, Any]:
    try:
        sddc_spec = artifact["greenfield"]["sddcSpec"]
    except (KeyError, TypeError) as error:
        raise VerificationError("architecture.json lacks greenfield.sddcSpec") from error
    if not isinstance(sddc_spec, dict):
        raise VerificationError("greenfield.sddcSpec must be an object")
    return sddc_spec


def validate_installer_sddc_first(artifact: Any, openapi: dict[str, Any]) -> None:
    """This is deliberately the first artifact validation phase."""
    try:
        schema = openapi["components"]["schemas"]["SddcSpec"]
    except (KeyError, TypeError) as error:
        raise VerificationError("pinned installer specification lacks SddcSpec") from error
    validate_json_schema(extract_sddc_spec(artifact), schema, openapi, "$.greenfield.sddcSpec")


def require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise VerificationError(f"{label} does not match the estate inventory")


def expected_sddc(inventory: dict[str, Any]) -> dict[str, Any]:
    greenfield = inventory["greenfield"]
    return {
        "sddcId": greenfield["sddcId"],
        "workflowType": greenfield["workflowType"],
        "version": greenfield["version"],
        "dnsSpec": greenfield["dns"],
        "ntpServers": greenfield["ntpServers"],
        "vcenterSpec": {
            "vcenterHostname": greenfield["vcenter"]["hostname"],
            "rootVcenterPassword": greenfield["vcenter"]["rootPasswordReference"],
            "vmSize": greenfield["vcenter"]["vmSize"],
            "storageSize": greenfield["vcenter"]["storageSize"],
            "useExistingDeployment": False,
        },
        "clusterSpec": greenfield["cluster"],
        "hostSpecs": [{"hostname": hostname} for hostname in greenfield["hosts"]],
        "networkSpecs": greenfield["networks"],
        "dvsSpecs": [
            {
                "dvsName": greenfield["distributedSwitch"]["name"],
                "mtu": greenfield["distributedSwitch"]["mtu"],
                "networks": greenfield["distributedSwitch"]["networks"],
                "vmnicsToUplinks": greenfield["distributedSwitch"]["vmnicsToUplinks"],
            }
        ],
        "nsxtSpec": {
            "nsxtManagers": [
                {"hostname": hostname}
                for hostname in greenfield["nsx"]["managerHostnames"]
            ],
            "nsxtManagerSize": greenfield["nsx"]["managerSize"],
            "vipFqdn": greenfield["nsx"]["vipFqdn"],
            "rootNsxtManagerPassword": greenfield["nsx"]["rootPasswordReference"],
            "nsxtAdminPassword": greenfield["nsx"]["adminPasswordReference"],
            "nsxtAuditPassword": greenfield["nsx"]["auditPasswordReference"],
            "transportVlanId": greenfield["nsx"]["transportVlanId"],
            "useExistingDeployment": False,
        },
        "datastoreSpec": {
            "vsanSpec": {
                "datastoreName": greenfield["datastore"]["name"],
                "failuresToTolerate": greenfield["datastore"]["failuresToTolerate"],
            }
        },
    }


def verify_greenfield(
    artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    if artifact.get("artifactVersion") != 1:
        raise VerificationError("artifactVersion must be 1")
    if set(artifact) != {"artifactVersion", "greenfield", "existingEstate"}:
        raise VerificationError("architecture.json has an unexpected top-level shape")
    if not isinstance(artifact["greenfield"], dict) or set(artifact["greenfield"]) != {
        "sddcSpec",
        "edgeDesign",
    }:
        raise VerificationError("greenfield has an unexpected shape")
    if not isinstance(artifact["existingEstate"], dict) or set(
        artifact["existingEstate"]
    ) != {"migrationPlan"}:
        raise VerificationError("existingEstate has an unexpected shape")
    require_equal(
        artifact["greenfield"]["sddcSpec"], expected_sddc(inventory), "greenfield.sddcSpec"
    )

    requirement = inventory["greenfield"]["edgeRequirement"]
    design = artifact["greenfield"].get("edgeDesign")
    if not isinstance(design, dict):
        raise VerificationError("greenfield.edgeDesign must be an object")
    required_keys = {
        "requiredNorthSouthThroughputGbps",
        "formFactor",
        "validatedCapacityGbps",
        "nodeCount",
        "teamingPolicy",
        "routingProtocol",
        "tepMtu",
        "uplinks",
    }
    if set(design) != required_keys:
        raise VerificationError("greenfield.edgeDesign has an unexpected shape")
    throughput = requirement["requiredNorthSouthThroughputGbps"]
    eligible = [
        item
        for item in snapshot["edgeSizing"]
        if item["maxValidatedNorthSouthThroughputGbps"] >= throughput
    ]
    if not eligible:
        raise VerificationError("snapshot has no sufficient Edge form factor")
    chosen = min(eligible, key=lambda item: item["maxValidatedNorthSouthThroughputGbps"])
    require_equal(design["requiredNorthSouthThroughputGbps"], throughput, "Edge throughput")
    require_equal(design["formFactor"], chosen["formFactor"], "Edge form factor")
    require_equal(
        design["validatedCapacityGbps"],
        chosen["maxValidatedNorthSouthThroughputGbps"],
        "Edge validated capacity",
    )
    require_equal(design["nodeCount"], requirement["edgeNodeCount"], "Edge node count")

    profiles = [
        item
        for item in snapshot["edgeUplinkProfiles"]
        if throughput > item["minimumThroughputExclusiveGbps"]
    ]
    if not profiles:
        raise VerificationError("snapshot has no Edge uplink profile for throughput")
    profile = max(profiles, key=lambda item: item["minimumThroughputExclusiveGbps"])
    require_equal(design["teamingPolicy"], profile["teamingPolicy"], "Edge teaming")
    require_equal(design["routingProtocol"], profile["routingProtocol"], "Edge routing")
    require_equal(design["routingProtocol"], requirement["routingProtocol"], "inventory routing")
    require_equal(design["tepMtu"], profile["tepMtu"], "Edge TEP MTU")
    require_equal(design["tepMtu"], requirement["tepMtu"], "inventory TEP MTU")

    available = {item["linkId"]: item for item in requirement["availableUplinks"]}
    selected = design["uplinks"]
    if not isinstance(selected, list) or len(selected) != len(available):
        raise VerificationError("Edge design must use the supplied redundant uplink set")
    if {item.get("linkId") for item in selected} != set(available):
        raise VerificationError("Edge design uplink IDs do not match inventory")
    for item in selected:
        require_equal(item, available[item["linkId"]], f"uplink {item['linkId']}")
    nodes = {item["edgeNode"] for item in selected}
    if len(nodes) != design["nodeCount"]:
        raise VerificationError("Edge uplinks do not cover the declared node count")
    for node in nodes:
        links = [item for item in selected if item["edgeNode"] == node]
        if len(links) < profile["minimumUplinksPerNode"]:
            raise VerificationError(f"{node} has too few uplinks")
        if any(item["speedGbps"] < profile["minimumUplinkSpeedGbps"] for item in links):
            raise VerificationError(f"{node} has an underspeed uplink")
        if len({item["failureDomain"] for item in links}) < profile["minimumFailureDomainsPerNode"]:
            raise VerificationError(f"{node} lacks uplink failure-domain diversity")


def transition_lookup(snapshot: dict[str, Any], component_type: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {
        (item["from"], item["to"], item["action"]): item
        for item in snapshot["transitions"][component_type]
    }


def verify_migration(
    artifact: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    migration_schema: dict[str, Any],
) -> None:
    try:
        plan = artifact["existingEstate"]["migrationPlan"]
    except (KeyError, TypeError) as error:
        raise VerificationError("architecture.json lacks existingEstate.migrationPlan") from error
    validate_json_schema(plan, migration_schema, migration_schema, "$.existingEstate.migrationPlan")
    estate = inventory["existingEstate"]
    require_equal(plan["estateId"], inventory["estateId"], "migration estateId")
    require_equal(
        plan["targetFleetVersion"], inventory["targetFleet"]["version"], "fleet version"
    )

    inventory_components = {item["id"]: item for item in estate["components"]}
    summaries = plan["components"]
    if len(summaries) != len(inventory_components):
        raise VerificationError("migration components must name every inventory component once")
    summary_by_id = {item["id"]: item for item in summaries}
    if len(summary_by_id) != len(summaries) or set(summary_by_id) != set(inventory_components):
        raise VerificationError("migration component IDs are missing or duplicated")
    for component_id, component in inventory_components.items():
        summary = summary_by_id[component_id]
        require_equal(summary["type"], component["type"], f"{component_id} type")
        require_equal(summary["name"], component["name"], f"{component_id} name")
        require_equal(summary["sourceVersion"], component["version"], f"{component_id} source")
        require_equal(
            summary["targetVersion"],
            snapshot["targetVersions"][component["type"]],
            f"{component_id} target",
        )

    steps = plan["steps"]
    sequences = [item["sequence"] for item in steps]
    if sequences != list(range(1, len(steps) + 1)):
        raise VerificationError("migration step sequence must be contiguous and ordered")
    operation_ids = [item["operationId"] for item in steps]
    if len(operation_ids) != len(set(operation_ids)):
        raise VerificationError("migration operationId values must be unique")

    current = {key: item["version"] for key, item in inventory_components.items()}
    used_component_gates: dict[str, set[str]] = {key: set() for key in current}
    event_step: dict[tuple[str, str], int] = {}
    gate_catalog = snapshot["gateCatalog"]
    conditions = estate["technicalConditions"]
    recovery_ids = {
        item["type"]: item["id"]
        for item in estate["components"]
        if item["type"] in {"LIVE_SITE_RECOVERY", "VSPHERE_REPLICATION"}
    }

    for step in steps:
        changes = step["changes"]
        ids = [change["componentId"] for change in changes]
        if len(ids) != len(set(ids)):
            raise VerificationError(f"step {step['sequence']} changes a component twice")
        required_gates: set[str] = set()
        for change in changes:
            component_id = change["componentId"]
            if component_id not in inventory_components:
                raise VerificationError(f"step references unknown component {component_id}")
            if change["fromVersion"] != current[component_id]:
                raise VerificationError(f"step has discontinuous source for {component_id}")
            component_type = inventory_components[component_id]["type"]
            key = (change["fromVersion"], change["toVersion"], step["action"])
            transition = transition_lookup(snapshot, component_type).get(key)
            if transition is None:
                raise VerificationError(f"unsupported transition for {component_id}: {key}")
            required_gates.update(transition["requiredGates"])
        if set(step["gateIds"]) != required_gates:
            raise VerificationError(f"step {step['sequence']} does not name exactly its required gates")
        for gate_id in step["gateIds"]:
            if gate_id not in gate_catalog:
                raise VerificationError(f"unknown gate {gate_id}")
            for condition in gate_catalog[gate_id]["inventoryConditions"]:
                if conditions.get(condition) is not True:
                    raise VerificationError(f"gate {gate_id} fails inventory condition {condition}")
        for change in changes:
            component_id = change["componentId"]
            current[component_id] = change["toVersion"]
            used_component_gates[component_id].update(step["gateIds"])
            component_type = inventory_components[component_id]["type"]
            event_step[(component_type, change["toVersion"])] = step["sequence"]
        if len(recovery_ids) == 2:
            lsr_version = current[recovery_ids["LIVE_SITE_RECOVERY"]]
            vr_version = current[recovery_ids["VSPHERE_REPLICATION"]]
            if lsr_version != vr_version:
                raise VerificationError("paired recovery components leave a step at different versions")

    for component_id, component in inventory_components.items():
        target = snapshot["targetVersions"][component["type"]]
        if current[component_id] != target:
            raise VerificationError(f"{component_id} does not finish at {target}")
        if set(summary_by_id[component_id]["gateIds"]) != used_component_gates[component_id]:
            raise VerificationError(f"component summary does not name exact gates for {component_id}")

    for constraint in snapshot["orderingConstraints"]:
        before = constraint["before"]
        after = constraint["after"]
        before_key = (before["componentType"], before["toVersion"])
        after_key = (after["componentType"], after["toVersion"])
        if before_key not in event_step or after_key not in event_step:
            raise VerificationError("an ordering-constraint milestone is absent")
        if event_step[before_key] >= event_step[after_key]:
            raise VerificationError(f"ordering constraint violated: {before_key} before {after_key}")


def verify_stdlib_package() -> None:
    package = ROOT / "vcf_architecture"
    required = [package / "__init__.py", package / "__main__.py"]
    if not all(path.is_file() for path in required):
        raise VerificationError("vcf_architecture must be an importable Python package")
    python_files = sorted(package.rglob("*.py"))
    if not python_files:
        raise VerificationError("vcf_architecture has no Python source")
    allowed = set(sys.stdlib_module_names) | {"vcf_architecture"}
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            raise VerificationError(f"invalid Python in {path.relative_to(ROOT)}: {error}") from error
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module not in allowed:
                        raise VerificationError(f"non-stdlib import {module} in {path.relative_to(ROOT)}")
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                module = node.module.split(".")[0]
                if module not in allowed:
                    raise VerificationError(f"non-stdlib import {module} in {path.relative_to(ROOT)}")


def verify_research_sources(research: Any) -> None:
    if not isinstance(research, dict):
        raise VerificationError("research-sources.json must contain an object")
    consulted = research.get("consulted")
    if not isinstance(consulted, list) or not consulted:
        raise VerificationError("research-sources.json must have a non-empty consulted array")
    required = {"title", "url", "accessedAt", "claimsChecked"}
    for index, entry in enumerate(consulted):
        label = f"research source {index + 1}"
        if not isinstance(entry, dict) or not required.issubset(entry):
            raise VerificationError(f"{label} lacks a required field")
        for field in ("title", "url", "accessedAt"):
            if not isinstance(entry[field], str) or not entry[field].strip():
                raise VerificationError(f"{label} has an invalid {field}")
        claims = entry["claimsChecked"]
        if not isinstance(claims, list) or not claims or any(
            not isinstance(claim, str) or not claim.strip() for claim in claims
        ):
            raise VerificationError(f"{label} has an invalid claimsChecked array")
        parsed = urlsplit(entry["url"])
        hostname = (parsed.hostname or "").lower()
        official_host = (
            hostname == "broadcom.com"
            or hostname.endswith(".broadcom.com")
            or hostname == "vmware.com"
            or hostname.endswith(".vmware.com")
            or (
                hostname == "github.com"
                and parsed.path.lower().startswith(("/vmware/", "/broadcom/"))
            )
        )
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or not official_host
        ):
            raise VerificationError(f"{label} is not a Broadcom-published web source")


def produce_with_package(output: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "vcf_architecture",
        "--inventory",
        "estate_inventory.json",
        "--compatibility",
        "compatibility_snapshot.json",
        "--output",
        str(output),
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    if result.returncode != 0:
        detail = (result.stdout + "\n" + result.stderr).strip()[-1000:]
        raise VerificationError(f"package command failed: {detail}")


def main() -> int:
    try:
        artifact = load_json(ROOT / "architecture.json")
        openapi = load_json(
            ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
        )

        # Required phase ordering: validate against the installer's own SddcSpec
        # before loading or checking the inventory, compatibility, package, or plan.
        validate_installer_sddc_first(artifact, openapi)
        print("PASS phase 1: installer SddcSpec schema")

        inventory = load_json(ROOT / "estate_inventory.json")
        snapshot = load_json(ROOT / "compatibility_snapshot.json")
        migration_schema = load_json(ROOT / "migration-plan.schema.json")
        research = load_json(ROOT / "research-sources.json")
        verify_greenfield(artifact, inventory, snapshot)
        verify_migration(artifact, inventory, snapshot, migration_schema)
        verify_stdlib_package()
        verify_research_sources(research)

        with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
            first_path = Path(temporary) / "first.json"
            second_path = Path(temporary) / "second.json"
            produce_with_package(first_path)
            produced = load_json(first_path)
            validate_installer_sddc_first(produced, openapi)
            if produced != artifact:
                raise VerificationError("package output differs from architecture.json")
            produce_with_package(second_path)
            produced_again = load_json(second_path)
            validate_installer_sddc_first(produced_again, openapi)
            if produced_again != produced:
                raise VerificationError("package output is not deterministic")

        print("PASS phase 2: architecture, migration, research, and stdlib package")
        return 0
    except (VerificationError, KeyError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
