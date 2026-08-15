#!/usr/bin/env python3
"""Deterministic verifier for the generated VCF architecture artifact."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read JSON from {path}: {exc}")


def json_pointer(root: Any, ref: str) -> Any:
    if not ref.startswith("#/"):
        fail(f"only local schema references are supported, got {ref!r}")
    value = root
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            fail(f"unresolvable schema reference {ref!r}")
        value = value[part]
    return value


def is_json_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
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
        return (isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value))
    return True


def validate_schema(value: Any, schema: Any, root: Any, path: str = "$") -> None:
    """Validate the JSON Schema/OpenAPI subset used by the shipped schemas."""
    if isinstance(schema, bool):
        if not schema:
            fail(f"{path}: value is rejected by schema")
        return
    if not isinstance(schema, dict):
        fail(f"{path}: malformed schema node")

    if "$ref" in schema:
        validate_schema(value, json_pointer(root, schema["$ref"]), root, path)
        return

    if value is None and schema.get("nullable") is True:
        return

    for subschema in schema.get("allOf", []):
        validate_schema(value, subschema, root, path)

    if "anyOf" in schema:
        errors = []
        for subschema in schema["anyOf"]:
            try:
                validate_schema(value, subschema, root, path)
                break
            except VerificationError as exc:
                errors.append(str(exc))
        else:
            fail(f"{path}: no anyOf branch matched: {'; '.join(errors)}")

    if "oneOf" in schema:
        matches = 0
        for subschema in schema["oneOf"]:
            try:
                validate_schema(value, subschema, root, path)
                matches += 1
            except VerificationError:
                pass
        if matches != 1:
            fail(f"{path}: expected exactly one oneOf match, got {matches}")

    if "not" in schema:
        try:
            validate_schema(value, schema["not"], root, path)
        except VerificationError:
            pass
        else:
            fail(f"{path}: value matched a forbidden schema")

    if "const" in schema and value != schema["const"]:
        fail(f"{path}: expected constant {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        fail(f"{path}: value {value!r} is not in the allowed enum")

    declared_type = schema.get("type")
    if declared_type is not None:
        candidates = declared_type if isinstance(declared_type, list) else [declared_type]
        if not any(is_json_type(value, item) for item in candidates):
            fail(f"{path}: expected type {declared_type!r}, got {type(value).__name__}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                fail(f"{path}: missing required property {key!r}")

        properties = schema.get("properties", {})
        for key, child in value.items():
            if key in properties:
                validate_schema(child, properties[key], root, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                fail(f"{path}: unexpected property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(child, schema["additionalProperties"], root,
                                f"{path}.{key}")

        if len(value) < schema.get("minProperties", 0):
            fail(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            fail(f"{path}: too many properties")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            fail(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            fail(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":"))
                       for item in value]
            if len(encoded) != len(set(encoded)):
                fail(f"{path}: array items must be unique")
        items_schema = schema.get("items")
        if items_schema is not None:
            for index, child in enumerate(value):
                validate_schema(child, items_schema, root, f"{path}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            fail(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            fail(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                matches = re.search(schema["pattern"], value) is not None
            except re.error as exc:
                fail(f"{path}: invalid pattern in shipped schema: {exc}")
            if not matches:
                fail(f"{path}: string does not match pattern {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            fail(f"{path}: number is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            fail(f"{path}: number is above maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            fail(f"{path}: number is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            fail(f"{path}: number is not below exclusiveMaximum")


def expect(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        fail(f"{label} must be an array")
    return value


def verify_greenfield(sddc: dict[str, Any], inventory: dict[str, Any]) -> None:
    req = require_dict(inventory["greenfieldRequirements"], "greenfieldRequirements")
    expect(sddc.get("sddcId"), req["sddcId"], "sddcSpec.sddcId")
    expect(sddc.get("workflowType"), req["workflowType"], "sddcSpec.workflowType")
    expect(sddc.get("version"), req["targetVersion"], "sddcSpec.version")
    expect(sddc.get("vcfInstanceName"), req["vcfInstanceName"],
           "sddcSpec.vcfInstanceName")

    availability = req["availability"]
    hosts = require_list(sddc.get("hostSpecs"), "sddcSpec.hostSpecs")
    expect(len(hosts), availability["managementHostCount"], "management host count")
    expect([require_dict(host, "hostSpec").get("hostname") for host in hosts],
           req["hostnames"], "management host names")

    expect(sddc.get("dnsSpec"), req["dns"], "sddcSpec.dnsSpec")
    expect(sddc.get("ntpServers"), req["ntpServers"], "sddcSpec.ntpServers")

    appliances = req["appliances"]
    vcenter = require_dict(sddc.get("vcenterSpec"), "sddcSpec.vcenterSpec")
    expect(vcenter.get("vcenterHostname"), appliances["vcenterHostname"],
           "vCenter hostname")
    expect(vcenter.get("useExistingDeployment"), False,
           "vCenter must be a greenfield deployment")
    expect(vcenter.get("version"), req["targetVersion"], "vCenter target version")
    if not isinstance(vcenter.get("rootVcenterPassword"), str):
        fail("vCenter password must be a non-secret placeholder string")

    manager = require_dict(sddc.get("sddcManagerSpec"), "sddcSpec.sddcManagerSpec")
    expect(manager.get("hostname"), appliances["sddcManagerHostname"],
           "SDDC Manager hostname")
    expect(manager.get("useExistingDeployment"), False,
           "SDDC Manager must be a greenfield deployment")
    expect(manager.get("version"), req["targetVersion"], "SDDC Manager version")

    nsx = require_dict(sddc.get("nsxtSpec"), "sddcSpec.nsxtSpec")
    expect([node.get("hostname") for node in require_list(nsx.get("nsxtManagers"),
                                                          "NSX managers")],
           appliances["nsxManagerHostnames"], "NSX Manager nodes")
    expect(nsx.get("vipFqdn"), appliances["nsxVipFqdn"], "NSX VIP")
    expect(nsx.get("useExistingDeployment"), False,
           "NSX must be a greenfield deployment")
    expect(nsx.get("version"), req["targetVersion"], "NSX target version")
    overlay = req["nsxHostOverlay"]
    expect(nsx.get("transportVlanId"), overlay["vlanId"], "NSX transport VLAN")
    expected_pool = {
        "name": overlay["poolName"],
        "description": "DFW01 NSX host TEP pool",
        "subnets": [{
            "cidr": overlay["cidr"],
            "gateway": overlay["gateway"],
            "ipAddressPoolRanges": [{
                "start": overlay["rangeStart"],
                "end": overlay["rangeEnd"],
            }],
        }],
    }
    expect(nsx.get("ipAddressPoolSpec"), expected_pool, "NSX host-overlay pool")

    cluster = req["cluster"]
    expect(sddc.get("clusterSpec", {}).get("datacenterName"), cluster["datacenterName"],
           "datacenter name")
    expect(sddc.get("clusterSpec", {}).get("clusterName"), cluster["clusterName"],
           "cluster name")
    vsan = require_dict(sddc.get("datastoreSpec", {}).get("vsanSpec"),
                        "sddcSpec.datastoreSpec.vsanSpec")
    expect(vsan.get("datastoreName"), cluster["datastoreName"], "vSAN datastore name")
    expect(vsan.get("failuresToTolerate"), availability["hostFailuresToTolerate"],
           "vSAN failures to tolerate")
    expect(vsan.get("esaConfig", {}).get("enabled"), True, "vSAN ESA")

    expected_networks = []
    for network in req["networks"]:
        expected_networks.append({
            "networkType": network["networkType"],
            "vlanId": network["vlanId"],
            "subnet": network["subnet"],
            "gateway": network["gateway"],
            "mtu": network["mtu"],
            "includeIpAddressRanges": [{
                "startIpAddress": network["rangeStart"],
                "endIpAddress": network["rangeEnd"],
            }],
        })
    expect(sddc.get("networkSpecs"), expected_networks, "management-domain networks")

    switch_req = req["distributedSwitch"]
    switches = require_list(sddc.get("dvsSpecs"), "sddcSpec.dvsSpecs")
    expect(len(switches), 1, "distributed switch count")
    switch = require_dict(switches[0], "distributed switch")
    expect(switch.get("dvsName"), switch_req["name"], "distributed switch name")
    expect(switch.get("mtu"), switch_req["mtu"], "distributed switch MTU")
    expect(switch.get("networks"), [n["networkType"] for n in req["networks"]],
           "distributed switch networks")
    expect(switch.get("vmnicsToUplinks"),
           [{"id": uplink["vmnic"], "uplink": uplink["uplink"]}
            for uplink in switch_req["uplinks"]],
           "distributed switch uplinks")


def verify_migration(plan: dict[str, Any], inventory: dict[str, Any],
                     snapshot: dict[str, Any]) -> None:
    estate = require_dict(inventory["estate"], "estate")
    target_bundle = require_dict(snapshot["targetBundle"], "targetBundle")
    expect(plan.get("sourceEstate"), estate["siteCode"], "migrationPlan.sourceEstate")
    expect(plan.get("targetBundle"), target_bundle["id"], "migrationPlan.targetBundle")

    components = {item["id"]: item for item in estate["components"]}
    transitions = {item["componentId"]: item for item in snapshot["transitions"]}
    expect(set(components), set(transitions), "snapshot transition coverage")

    steps = require_list(plan.get("steps"), "migrationPlan.steps")
    ids = [require_dict(step, "migration step").get("componentId") for step in steps]
    expect(len(ids), len(set(ids)), "migration plan component uniqueness")
    expect(set(ids), set(components), "migration plan inventory coverage")

    expected_ids = [item["componentId"]
                    for item in sorted(snapshot["transitions"], key=lambda item: item["order"])]
    expect(ids, expected_ids, "migration plan ordering")

    for step in steps:
        component_id = step["componentId"]
        component = components[component_id]
        transition = transitions[component_id]
        expect(step["order"], transition["order"], f"{component_id} order")
        expect(step["componentName"], component["name"], f"{component_id} name")
        expect(step["currentVersion"], component["version"], f"{component_id} current version")
        expect(step["currentBuild"], component["build"], f"{component_id} current build")
        expect(step["targetVersion"], transition["targetVersion"],
               f"{component_id} target version")
        expect(step["targetBuild"], transition["targetBuild"],
               f"{component_id} target build")
        expect(step["action"], transition["disposition"], f"{component_id} action")
        expect(set(step["gates"]), set(transition["requiredGateIds"]),
               f"{component_id} technical gates")

        target_build = transition["targetBuild"]
        if (transition["directUpgradeAllowed"] is False
                and isinstance(target_build, int)
                and component["build"] > target_build
                and step["action"] in {"UPGRADE", "ROLLING_UPGRADE"}):
            fail(f"{component_id}: newer installed build cannot be a direct upgrade "
                 "to the older target build")


def verify_research(value: Any) -> None:
    research = require_list(value, "research")
    if not research:
        fail("research must contain at least one consulted public source")

    for index, raw_source in enumerate(research):
        source = require_dict(raw_source, f"research[{index}]")
        for field in ("title", "url", "usedFor"):
            field_value = source.get(field)
            if not isinstance(field_value, str) or not field_value.strip():
                fail(f"research[{index}].{field} must be a non-empty string")

        parsed = urlparse(source["url"])
        if parsed.scheme != "https" or not parsed.netloc:
            fail(f"research[{index}].url must be an absolute HTTPS URL")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: verify.py <architecture.json> <project-dir>", file=sys.stderr)
        return 2

    artifact_path = Path(sys.argv[1])
    project = Path(sys.argv[2])
    artifact = load_json(artifact_path)

    # The installer specification's own SddcSpec schema is intentionally the
    # first validation performed on the generated artifact.
    openapi = load_json(project / "specifications/vcf-installer/vcf-installer-openapi.json")
    try:
        sddc = artifact["sddcSpec"]
    except (TypeError, KeyError):
        fail("artifact is missing sddcSpec")
    sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    validate_schema(sddc, sddc_schema, openapi, "$.sddcSpec")

    # All other schema and semantic checks occur only after SddcSpec succeeds.
    migration_schema = load_json(project / "schemas/migration-plan-schema.json")
    try:
        migration = artifact["migrationPlan"]
    except (TypeError, KeyError):
        fail("artifact is missing migrationPlan")
    validate_schema(migration, migration_schema, migration_schema, "$.migrationPlan")

    inventory = load_json(project / "fixtures/estate-inventory.json")
    snapshot = load_json(project / "fixtures/compatibility-snapshot.json")
    verify_greenfield(require_dict(sddc, "sddcSpec"), inventory)
    verify_migration(require_dict(migration, "migrationPlan"), inventory, snapshot)
    try:
        research = artifact["research"]
    except (TypeError, KeyError):
        fail("artifact is missing research")
    verify_research(research)
    print("VCF architecture artifact verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
