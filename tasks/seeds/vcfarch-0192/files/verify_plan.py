#!/usr/bin/env python3
"""Offline protected verifier for the generated VCF migration plan."""

import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit


class VerificationError(Exception):
    pass


def fail(message):
    raise VerificationError(message)


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing required file: {path}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path}: {exc}")


def resolve_ref(root, ref):
    if not ref.startswith("#/"):
        fail(f"unsupported schema reference {ref!r}")
    value = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            fail(f"broken schema reference {ref!r}")
        value = value[part]
    return value


def schema_type_ok(value, expected):
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return False


def validate_schema(value, schema, root, path="$"):
    if "$ref" in schema:
        validate_schema(value, resolve_ref(root, schema["$ref"]), root, path)
        return

    if "const" in schema and value != schema["const"]:
        fail(f"schema: {path} must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        fail(f"schema: {path} has unsupported value {value!r}")

    expected_type = schema.get("type")
    if expected_type is not None and not schema_type_ok(value, expected_type):
        fail(f"schema: {path} must be {expected_type}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                fail(f"schema: {path} is missing required property {name!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                fail(f"schema: {path} has unexpected properties {extras}")
        for name, child in value.items():
            if name in properties:
                validate_schema(child, properties[name], root, f"{path}.{name}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            fail(f"schema: {path} has too few items")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True) for item in value]
            if len(serialized) != len(set(serialized)):
                fail(f"schema: {path} must contain unique items")
        unique_by = schema.get("uniqueBy")
        if unique_by:
            keys = []
            for index, item in enumerate(value):
                if not isinstance(item, dict) or unique_by not in item:
                    fail(f"schema: {path}[{index}] lacks unique key {unique_by!r}")
                keys.append(item[unique_by])
            if len(keys) != len(set(keys)):
                fail(f"schema: {path} must be unique by {unique_by!r}")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                validate_schema(item, item_schema, root, f"{path}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            fail(f"schema: {path} is too short")
        pattern = schema.get("pattern")
        if pattern and re.search(pattern, value) is None:
            fail(f"schema: {path} does not match {pattern!r}")

    if isinstance(value, int) and not isinstance(value, bool):
        if value < schema.get("minimum", value):
            fail(f"schema: {path} is below its minimum")


def keyed(items, key, label):
    result = {}
    for item in items:
        value = item[key]
        if value in result:
            fail(f"duplicate {label}: {value}")
        result[value] = item
    return result


def require_equal(actual, expected, label):
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def verify_research(records):
    required_topics = {
        "upgrade-sequence",
        "source-target-compatibility",
        "content-configuration-behavior",
        "support-boundary",
        "sizing",
    }
    covered_topics = set()

    for index, record in enumerate(records):
        parsed = urlsplit(record["url"])
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        ):
            fail(f"research record {index} is not an HTTPS Broadcom source")
        if not parsed.path or parsed.path == "/":
            fail(f"research record {index} must identify a Broadcom page")
        try:
            accessed = date.fromisoformat(record["accessedOn"])
        except ValueError:
            fail(f"research record {index} has an invalid accessedOn date")
        if accessed.isoformat() != record["accessedOn"]:
            fail(f"research record {index} has a non-canonical accessedOn date")
        covered_topics.update(record["topics"])

    require_equal(covered_topics, required_topics, "research topic coverage")


def verify_semantics(plan, inventory, snapshot):
    require_equal(plan["estateId"], inventory["estateId"], "estateId")
    require_equal(plan["targetPlatformVersion"], inventory["targetPlatformVersion"], "target platform")
    require_equal(snapshot["targetPlatformVersion"], inventory["targetPlatformVersion"], "snapshot target platform")

    source_products = keyed(inventory["sourceProducts"], "key", "inventory source key")
    expected_migrations = keyed(snapshot["migrations"], "sourceKey", "snapshot migration")
    migrations = keyed(plan["migrations"], "sourceKey", "plan migration")
    require_equal(set(migrations), set(source_products), "migration source coverage")
    require_equal(set(expected_migrations), set(source_products), "snapshot source coverage")

    for source_key, source in source_products.items():
        migration = migrations[source_key]
        expected = expected_migrations[source_key]
        require_equal(migration["source"], {"product": source["product"], "version": source["version"]}, f"{source_key} source identity")
        require_equal(migration["target"], {"component": expected["targetComponent"], "version": expected["targetVersion"]}, f"{source_key} target identity")
        require_equal(migration["path"], expected["path"], f"{source_key} supported path")

        inventory_ids = {item["id"] for item in source["content"]}
        seen = {}
        for bucket in ("carried", "manual", "abandoned"):
            for item in migration["content"][bucket]:
                if item["id"] in seen:
                    fail(f"{source_key} content {item['id']} is classified more than once")
                seen[item["id"]] = (bucket, item)
        require_equal(set(seen), inventory_ids, f"{source_key} content classification coverage")
        require_equal(set(expected["contentDisposition"]), inventory_ids, f"{source_key} snapshot content coverage")
        for content_id, authority in expected["contentDisposition"].items():
            bucket, item = seen[content_id]
            require_equal(bucket, authority["bucket"], f"{content_id} disposition")
            require_equal(item["method"], authority["method"], f"{content_id} method")
            if bucket == "abandoned":
                require_equal(item["replacement"], authority["replacement"], f"{content_id} replacement")

    design = keyed(snapshot["targetDesign"], "component", "snapshot target component")
    architecture = keyed(plan["architecture"], "component", "plan target component")
    require_equal(set(architecture), set(design), "target architecture coverage")

    management = inventory["managementDomain"]
    hosts = {host["id"]: host for host in management["hosts"]}
    host_allocations = {host_id: {"vCpu": 0, "memoryGb": 0} for host_id in hosts}
    for component, expected in design.items():
        actual = architecture[component]
        for field in ("version", "deploymentMethod", "formFactor", "nodeCount", "vCpuPerNode", "memoryGbPerNode"):
            require_equal(actual[field], expected[field], f"{component} {field}")
        require_equal(actual["sizingBasis"], expected["capacityChecks"], f"{component} sizing basis")
        for check in actual["sizingBasis"]:
            if check["capacity"] < check["required"]:
                fail(f"{component} capacity does not cover {check['metric']}")

        placement = actual["placement"]
        require_equal(placement["domain"], management["id"], f"{component} placement domain")
        require_equal(placement["datastore"], management["datastore"], f"{component} datastore")
        require_equal(len(placement["nodes"]), expected["nodeCount"], f"{component} placed node count")
        placed_hosts = [node["host"] for node in placement["nodes"]]
        placed_faults = [node["faultDomain"] for node in placement["nodes"]]
        if len(set(placed_hosts)) != expected["nodeCount"]:
            fail(f"{component} nodes must be placed on distinct hosts")
        if len(set(placed_faults)) != expected["nodeCount"]:
            fail(f"{component} nodes must span distinct fault domains")
        for node in placement["nodes"]:
            if node["host"] not in hosts:
                fail(f"{component} uses unknown host {node['host']}")
            require_equal(node["faultDomain"], hosts[node["host"]]["faultDomain"], f"{component} fault domain for {node['host']}")
            host_allocations[node["host"]]["vCpu"] += actual["vCpuPerNode"]
            host_allocations[node["host"]]["memoryGb"] += actual["memoryGbPerNode"]

    for host_id, allocated in host_allocations.items():
        if allocated["vCpu"] > hosts[host_id]["availableVCpu"]:
            fail(f"target placement exceeds available vCPU on {host_id}")
        if allocated["memoryGb"] > hosts[host_id]["availableMemoryGb"]:
            fail(f"target placement exceeds available memory on {host_id}")

    boundaries = keyed(plan["supportBoundaries"], "itemId", "plan support boundary")
    expected_boundaries = keyed(snapshot["supportBoundaries"], "itemId", "snapshot support boundary")
    require_equal(set(boundaries), set(expected_boundaries), "support boundary coverage")
    for item_id, expected in expected_boundaries.items():
        require_equal(boundaries[item_id]["boundaryType"], expected["boundaryType"], f"{item_id} boundary type")
        require_equal(boundaries[item_id]["boundary"], expected["boundary"], f"{item_id} boundary")

    expected_steps = snapshot["orderedSteps"]
    require_equal(len(plan["steps"]), len(expected_steps), "ordered step count")
    require_equal([step["order"] for step in plan["steps"]], list(range(1, len(expected_steps) + 1)), "step order numbers")
    for actual, expected in zip(plan["steps"], expected_steps):
        require_equal(actual["id"], expected["id"], f"step {actual['order']} id")
        require_equal(actual["action"], expected["action"], f"step {actual['order']} action")
        require_equal(actual["products"], expected["products"], f"step {actual['order']} products")
        require_equal(actual["gates"], expected["requiredGates"], f"step {actual['order']} gates")


def main(argv):
    if len(argv) != 5:
        fail("usage: verify_plan.py PLAN INVENTORY SNAPSHOT INSTALLER_SPEC")

    plan_path, inventory_path, snapshot_path, spec_path = argv[1:]

    # Contract requirement: schema validation is complete before fixtures are
    # loaded and before any architecture/content compatibility assertion runs.
    spec = load_json(spec_path)
    plan = load_json(plan_path)
    schema = spec.get("artifactSchema")
    if not isinstance(schema, dict):
        fail("installer specification has no artifactSchema")
    validate_schema(plan, schema, schema)

    inventory = load_json(inventory_path)
    snapshot = load_json(snapshot_path)
    verify_semantics(plan, inventory, snapshot)
    verify_research(plan["researchConsulted"])

    # Source selection remains open-ended. The verifier checks the resulting
    # records and topic coverage offline, without making grading network-dependent.
    print("VCF migration architecture verified")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except VerificationError as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        sys.exit(1)
