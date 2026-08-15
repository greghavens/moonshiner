#!/usr/bin/env python3
"""Deterministic artifact verifier. It never opens or inspects research/."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OPENAPI = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
PLAN_SCHEMA = ROOT / "schemas/migration-plan.schema.json"
INVENTORY = ROOT / "testdata/estate.json"
SNAPSHOT = ROOT / "testdata/compatibility-snapshot.json"


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read JSON {path.relative_to(ROOT)}: {error}")


def pointer(document: Any, reference: str) -> Any:
    if not reference.startswith("#/"):
        fail(f"unsupported non-local schema reference {reference!r}")
    value = document
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or token not in value:
            fail(f"unresolvable schema reference {reference!r}")
        value = value[token]
    return value


def json_type_matches(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def validate_schema(value: Any, schema: dict[str, Any], document: dict[str, Any], path: str) -> None:
    """Validate the JSON-Schema/OpenAPI keywords used by the pinned documents."""
    if "$ref" in schema:
        validate_schema(value, pointer(document, schema["$ref"]), document, path)
        return

    for member in schema.get("allOf", []):
        validate_schema(value, member, document, path)

    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            matches = 0
            for member in schema[keyword]:
                try:
                    validate_schema(value, member, document, path)
                    matches += 1
                except VerificationError:
                    pass
            wanted = matches >= 1 if keyword == "anyOf" else matches == 1
            if not wanted:
                fail(f"{path}: does not satisfy {keyword}")

    if "const" in schema and value != schema["const"]:
        fail(f"{path}: got {value!r}, want constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        fail(f"{path}: {value!r} is not in {schema['enum']!r}")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(json_type_matches(value, item) for item in expected_type):
            fail(f"{path}: wrong JSON type")
    elif isinstance(expected_type, str) and not json_type_matches(value, expected_type):
        fail(f"{path}: got {type(value).__name__}, want {expected_type}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            fail(f"{path}: missing required properties {missing}")
        if len(value) < schema.get("minProperties", 0):
            fail(f"{path}: has too few properties")
        properties = schema.get("properties", {})
        for name, child in value.items():
            if name in properties:
                validate_schema(child, properties[name], document, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                fail(f"{path}: unexpected property {name!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(child, schema["additionalProperties"], document, f"{path}.{name}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            fail(f"{path}: has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            fail(f"{path}: has too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                fail(f"{path}: items are not unique")
        if isinstance(schema.get("items"), dict):
            for index, child in enumerate(value):
                validate_schema(child, schema["items"], document, f"{path}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            fail(f"{path}: string is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            fail(f"{path}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            fail(f"{path}: does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            fail(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            fail(f"{path}: above maximum {schema['maximum']}")


def combined_version(target: dict[str, str]) -> str:
    return f"{target['version']}.{target['build']}"


def short_hostname(hostname: str) -> str:
    return hostname.split(".", 1)[0]


def validate_installer_first(plan: dict[str, Any]) -> None:
    """This is intentionally the first validation phase."""
    openapi = load_json(OPENAPI)
    if openapi.get("info", {}).get("version") != "9.1.0.0":
        fail("vendored installer OpenAPI is not version 9.1.0.0")
    if "targetSddcSpec" not in plan:
        fail("$.targetSddcSpec: missing before installer-schema validation")
    schema = openapi.get("components", {}).get("schemas", {}).get("SddcSpec")
    if not isinstance(schema, dict):
        fail("vendored installer OpenAPI has no SddcSpec schema")
    validate_schema(plan["targetSddcSpec"], schema, openapi, "$.targetSddcSpec")
    print("PASS installer SddcSpec schema (vcf-api-specs 9.1.0.0)")


def validate_fixed_plan_schema(plan: dict[str, Any]) -> None:
    schema = load_json(PLAN_SCHEMA)
    validate_schema(plan, schema, schema, "$")
    print("PASS migration-plan schema")


def expect_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: got {actual!r}, want {expected!r}")


def validate_installer_projection(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    spec = plan["targetSddcSpec"]
    target_by_kind = {item["kind"]: item for item in snapshot["targets"]}
    topology = inventory["topology"]

    expect_equal(spec.get("sddcId"), inventory["desiredDomainId"], "targetSddcSpec.sddcId")
    expect_equal(spec.get("workflowType"), "VCF", "targetSddcSpec.workflowType")
    expect_equal(spec.get("version"), snapshot["targetVcf"], "targetSddcSpec.version")
    expect_equal(spec.get("skipEsxThumbprintValidation"), False, "targetSddcSpec.skipEsxThumbprintValidation")
    expect_equal(spec.get("skipGatewayPingValidation"), False, "targetSddcSpec.skipGatewayPingValidation")
    expect_equal(spec.get("dnsSpec"), {
        "subdomain": topology["dnsDomain"],
        "nameservers": topology["nameServers"],
    }, "targetSddcSpec.dnsSpec")
    expect_equal(spec.get("ntpServers"), topology["ntpServers"], "targetSddcSpec.ntpServers")
    expect_equal(spec.get("clusterSpec"), {
        "datacenterName": topology["datacenterName"],
        "clusterName": topology["clusterName"],
    }, "targetSddcSpec.clusterSpec")
    expect_equal(spec.get("datastoreSpec"), {
        "existingDatastoreName": topology["datastoreName"],
    }, "targetSddcSpec.datastoreSpec")

    vc = spec.get("vcenterSpec", {})
    expect_equal(vc.get("vcenterHostname"), topology["vcenterHostname"], "targetSddcSpec.vcenterSpec.vcenterHostname")
    expect_equal(vc.get("useExistingDeployment"), True, "targetSddcSpec.vcenterSpec.useExistingDeployment")
    expect_equal(vc.get("sslThumbprint"), topology["vcenterSslThumbprint"], "targetSddcSpec.vcenterSpec.sslThumbprint")
    expect_equal(vc.get("version"), combined_version(target_by_kind["vcenter"]), "targetSddcSpec.vcenterSpec.version")
    if vc.get("rootVcenterPassword") != "${VcRoot9!Pass}":
        fail("targetSddcSpec.vcenterSpec.rootVcenterPassword must be the documented non-secret placeholder")

    expected_hosts = []
    for item in sorted(inventory["components"], key=lambda component: component["id"]):
        if item["kind"] == "esxi":
            expected_hosts.append({
                "hostname": short_hostname(item["hostname"]),
                "sslThumbprint": topology["esxiSslThumbprints"][item["hostname"]],
            })
    expect_equal(spec.get("hostSpecs"), expected_hosts, "targetSddcSpec.hostSpecs")

    expected_networks = []
    for network in topology["networks"]:
        projected = {
            "networkType": network["type"],
            "vlanId": network["vlan"],
            "subnet": network["subnet"],
            "gateway": network["gateway"],
            "subnetMask": network["mask"],
            "mtu": network["mtu"],
        }
        if network.get("ipRanges"):
            projected["includeIpAddressRanges"] = [
                {"startIpAddress": item["start"], "endIpAddress": item["end"]}
                for item in network["ipRanges"]
            ]
        expected_networks.append(projected)
    expect_equal(spec.get("networkSpecs"), expected_networks, "targetSddcSpec.networkSpecs")

    nsx = spec.get("nsxtSpec", {})
    expect_equal(nsx.get("vipFqdn"), topology["nsxVipFqdn"], "targetSddcSpec.nsxtSpec.vipFqdn")
    expect_equal(nsx.get("useExistingDeployment"), True, "targetSddcSpec.nsxtSpec.useExistingDeployment")
    expect_equal(nsx.get("sslThumbprint"), topology["nsxSslThumbprint"], "targetSddcSpec.nsxtSpec.sslThumbprint")
    expect_equal(nsx.get("version"), combined_version(target_by_kind["nsx"]), "targetSddcSpec.nsxtSpec.version")
    expect_equal(nsx.get("nsxtManagers"), [
        {"hostname": hostname} for hostname in topology["nsxManagers"]
    ], "targetSddcSpec.nsxtSpec.nsxtManagers")


def validate_compatibility(plan: dict[str, Any]) -> None:
    inventory = load_json(INVENTORY)
    snapshot = load_json(SNAPSHOT)

    expect_equal(plan["estateId"], inventory["estateId"], "estateId")
    expect_equal(plan["designType"], "brownfield", "designType")
    expect_equal(plan["fleet"], {
        "id": inventory["fleet"]["id"],
        "currentVersion": inventory["fleet"]["version"],
        "targetVersion": inventory["fleet"]["version"],
    }, "fleet")
    expect_equal(plan["managementDomainImpact"], {
        "change": "none",
        "managementDomainId": inventory["fleet"]["managementDomain"]["id"],
    }, "managementDomainImpact")
    if not inventory["fleet"]["managementDomain"]["immutable"]:
        fail("fixture does not protect the management domain")

    inventory_by_id = {item["id"]: item for item in inventory["components"]}
    planned_by_id = {item["id"]: item for item in plan["components"]}
    if len(planned_by_id) != len(plan["components"]):
        fail("components contains duplicate ids")
    expect_equal(set(planned_by_id), set(inventory_by_id), "planned component ids")

    targets = {item["kind"]: item for item in snapshot["targets"]}
    transitions = {
        (item["kind"], item["fromVersion"], item["fromBuild"]): item
        for item in snapshot["transitions"]
    }
    for component_id, source in inventory_by_id.items():
        planned = planned_by_id[component_id]
        expect_equal(planned["kind"], source["kind"], f"components[{component_id}].kind")
        expect_equal(planned["name"], source["name"], f"components[{component_id}].name")
        expect_equal(planned["source"], {
            "version": source["version"], "build": source["build"]
        }, f"components[{component_id}].source")
        target = targets.get(source["kind"])
        if target is None:
            fail(f"snapshot has no target for {source['kind']}")
        expect_equal(planned["target"], {
            "product": target["product"],
            "version": target["version"],
            "build": target["build"],
        }, f"components[{component_id}].target")
        transition = transitions.get((source["kind"], source["version"], source["build"]))
        if transition is None:
            fail(f"snapshot has no transition for {component_id}")
        expect_equal(planned["disposition"], transition["disposition"], f"components[{component_id}].disposition")
        expect_equal(planned["via"], transition["via"], f"components[{component_id}].via")
        expect_equal(set(planned["gates"]), set(transition["requiredGates"]), f"components[{component_id}].gates")

    expected_gates = {item["id"]: item for item in snapshot["gates"]}
    planned_gates = {item["id"]: item for item in plan["gates"]}
    if len(planned_gates) != len(plan["gates"]):
        fail("gates contains duplicate ids")
    expect_equal(planned_gates, expected_gates, "gate definitions")

    expected_operations = {item["operation"]: item for item in snapshot["operations"]}
    planned_operations = {item["operation"]: item for item in plan["steps"]}
    if len(planned_operations) != len(plan["steps"]):
        fail("steps contains duplicate operations")
    expect_equal(set(planned_operations), set(expected_operations), "planned operations")
    orders = [item["order"] for item in plan["steps"]]
    if orders != sorted(orders) or len(orders) != len(set(orders)):
        fail("steps are not in strictly increasing order")

    step_order_by_id = {item["id"]: item["order"] for item in plan["steps"]}
    referenced_components: set[str] = set()
    for operation, rule in expected_operations.items():
        step = planned_operations[operation]
        expect_equal(step["id"], rule["id"], f"steps[{operation}].id")
        expect_equal(step["order"], rule["order"], f"steps[{operation}].order")
        expect_equal(step["scope"], rule["scope"], f"steps[{operation}].scope")
        expect_equal(step["dependsOn"], rule["dependsOn"], f"steps[{operation}].dependsOn")
        expect_equal(set(step["gates"]), set(rule["requiredGates"]), f"steps[{operation}].gates")
        kinds = set(rule["componentKinds"])
        expected_ids = sorted(item["id"] for item in inventory["components"] if item["kind"] in kinds)
        expect_equal(step["componentIds"], expected_ids, f"steps[{operation}].componentIds")
        referenced_components.update(expected_ids)
        for dependency in step["dependsOn"]:
            if dependency not in step_order_by_id or step_order_by_id[dependency] >= step["order"]:
                fail(f"step {step['id']} has a missing or non-prior dependency {dependency}")
        for gate in step["gates"]:
            if gate not in expected_gates:
                fail(f"step {step['id']} refers to unknown gate {gate}")
    expect_equal(referenced_components, set(inventory_by_id), "components referenced by steps")

    order_by_operation = {item["operation"]: item["order"] for item in plan["steps"]}
    for rule in snapshot["orderRules"]:
        if order_by_operation[rule["before"]] >= order_by_operation[rule["after"]]:
            fail(f"order rule violated: {rule['before']} must precede {rule['after']}")

    validate_installer_projection(plan, inventory, snapshot)
    print("PASS fixture coverage, pinned compatibility, gates, order, and management-domain isolation")


def main() -> int:
    plan_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "architecture/plan.json"
    if not plan_path.is_absolute():
        plan_path = (Path.cwd() / plan_path).resolve()
    plan = load_json(plan_path)
    if not isinstance(plan, dict):
        fail("artifact root must be an object")

    # Do not reorder these phases: installer validation is the first check.
    validate_installer_first(plan)
    validate_fixed_plan_schema(plan)
    validate_compatibility(plan)
    print("PASS architecture artifact")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        raise SystemExit(1)
