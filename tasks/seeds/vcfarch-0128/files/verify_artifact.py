#!/usr/bin/env python3
"""Protected deterministic verifier for the VCF architecture artifact."""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent


class VerificationFailure(Exception):
    pass


class SchemaFailure(VerificationFailure):
    pass


def schema_fail(path: str, message: str) -> None:
    raise SchemaFailure(f"SddcSpec schema violation at {path}: {message}")


def resolve_pointer(document: Any, ref: str) -> Any:
    if not ref.startswith("#/"):
        raise SchemaFailure(f"unsupported non-local schema reference: {ref}")
    node = document
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            node = node[part]
        except (KeyError, TypeError) as exc:
            raise SchemaFailure(f"unresolvable schema reference: {ref}") from exc
    return node


def json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isfinite(float(left)) and math.isfinite(float(right)) and left == right
    return left == right


def validate_schema(instance: Any, schema: Any, document: Any, path: str = "$.sddcSpec") -> None:
    """Validate the OpenAPI 3.0 schema keywords used by the pinned installer spec."""
    if isinstance(schema, bool):
        if not schema:
            schema_fail(path, "schema is false")
        return
    if not isinstance(schema, dict):
        schema_fail(path, "invalid schema node")

    if "$ref" in schema:
        validate_schema(instance, resolve_pointer(document, schema["$ref"]), document, path)
        return

    if instance is None and schema.get("nullable") is True:
        return

    for child in schema.get("allOf", []):
        validate_schema(instance, child, document, path)

    if "anyOf" in schema:
        matches = 0
        for child in schema["anyOf"]:
            try:
                validate_schema(instance, child, document, path)
                matches += 1
            except SchemaFailure:
                pass
        if matches == 0:
            schema_fail(path, "does not match any anyOf branch")

    if "oneOf" in schema:
        matches = 0
        for child in schema["oneOf"]:
            try:
                validate_schema(instance, child, document, path)
                matches += 1
            except SchemaFailure:
                pass
        if matches != 1:
            schema_fail(path, f"matches {matches} oneOf branches, expected exactly one")

    if "not" in schema:
        try:
            validate_schema(instance, schema["not"], document, path)
        except SchemaFailure:
            pass
        else:
            schema_fail(path, "matches a forbidden schema")

    if "enum" in schema and not any(json_equal(instance, item) for item in schema["enum"]):
        schema_fail(path, f"value is not in enum {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(instance, dict):
            schema_fail(path, "expected object")
        required = schema.get("required", [])
        missing = [name for name in required if name not in instance]
        if missing:
            schema_fail(path, f"missing required properties {missing!r}")
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            schema_fail(path, f"has fewer than {schema['minProperties']} properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            schema_fail(path, f"has more than {schema['maxProperties']} properties")
        properties = schema.get("properties", {})
        for name, child in properties.items():
            if name in instance:
                validate_schema(instance[name], child, document, f"{path}.{name}")
        additional = schema.get("additionalProperties", True)
        extras = set(instance) - set(properties)
        if additional is False and extras:
            schema_fail(path, f"unexpected properties {sorted(extras)!r}")
        if isinstance(additional, dict):
            for name in extras:
                validate_schema(instance[name], additional, document, f"{path}.{name}")

    elif expected_type == "array":
        if not isinstance(instance, list):
            schema_fail(path, "expected array")
        if "minItems" in schema and len(instance) < schema["minItems"]:
            schema_fail(path, f"has fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            schema_fail(path, f"has more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                schema_fail(path, "items are not unique")
        if "items" in schema:
            for index, item in enumerate(instance):
                validate_schema(item, schema["items"], document, f"{path}[{index}]")

    elif expected_type == "string":
        if not isinstance(instance, str):
            schema_fail(path, "expected string")
        if "minLength" in schema and len(instance) < schema["minLength"]:
            schema_fail(path, f"is shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            schema_fail(path, f"is longer than {schema['maxLength']} characters")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance) is not None
            except re.error as exc:
                raise SchemaFailure(f"invalid pattern in pinned schema at {path}: {exc}") from exc
            if not matched:
                schema_fail(path, f"does not match pattern {schema['pattern']!r}")

    elif expected_type == "integer":
        if isinstance(instance, bool) or not isinstance(instance, int):
            schema_fail(path, "expected integer")
        validate_number(instance, schema, path)

    elif expected_type == "number":
        if isinstance(instance, bool) or not isinstance(instance, (int, float)):
            schema_fail(path, "expected number")
        if not math.isfinite(float(instance)):
            schema_fail(path, "number must be finite")
        validate_number(instance, schema, path)

    elif expected_type == "boolean":
        if not isinstance(instance, bool):
            schema_fail(path, "expected boolean")

    elif expected_type is not None:
        schema_fail(path, f"unsupported schema type {expected_type!r}")


def validate_number(value: int | float, schema: dict[str, Any], path: str) -> None:
    if "minimum" in schema:
        minimum = schema["minimum"]
        exclusive = schema.get("exclusiveMinimum", False)
        if value < minimum or (exclusive is True and value == minimum):
            schema_fail(path, f"is below minimum {minimum!r}")
    if "maximum" in schema:
        maximum = schema["maximum"]
        exclusive = schema.get("exclusiveMaximum", False)
        if value > maximum or (exclusive is True and value == maximum):
            schema_fail(path, f"is above maximum {maximum!r}")
    if isinstance(schema.get("exclusiveMinimum"), (int, float)) and not isinstance(
        schema.get("exclusiveMinimum"), bool
    ):
        if value <= schema["exclusiveMinimum"]:
            schema_fail(path, f"must be greater than {schema['exclusiveMinimum']!r}")
    if isinstance(schema.get("exclusiveMaximum"), (int, float)) and not isinstance(
        schema.get("exclusiveMaximum"), bool
    ):
        if value >= schema["exclusiveMaximum"]:
            schema_fail(path, f"must be less than {schema['exclusiveMaximum']!r}")
    if "multipleOf" in schema:
        quotient = value / schema["multipleOf"]
        if not math.isclose(quotient, round(quotient), rel_tol=0.0, abs_tol=1e-12):
            schema_fail(path, f"is not a multiple of {schema['multipleOf']!r}")


def expect_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VerificationFailure(f"{path} must be an object")
    return value


def expect_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise VerificationFailure(f"{path} must be an array")
    return value


def expect_equal(actual: Any, expected: Any, path: str) -> None:
    if actual != expected:
        raise VerificationFailure(f"{path}: expected {expected!r}, got {actual!r}")


def expect_nonempty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VerificationFailure(f"{path} must be a non-empty string")
    return value


def verify_research(value: Any) -> None:
    research = expect_object(value, "$.research")
    expect_equal(set(research), {"consulted"}, "$.research fields")
    consulted = expect_list(research.get("consulted"), "$.research.consulted")
    if not consulted:
        raise VerificationFailure("$.research.consulted must contain at least one consulted source")

    required_fields = {"title", "url", "consultedAt", "claims"}
    for index, item in enumerate(consulted):
        item_path = f"$.research.consulted[{index}]"
        source = expect_object(item, item_path)
        expect_equal(set(source), required_fields, f"{item_path} fields")
        expect_nonempty_string(source["title"], f"{item_path}.title")
        url = expect_nonempty_string(source["url"], f"{item_path}.url")
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not hostname:
            raise VerificationFailure(f"{item_path}.url must be an absolute HTTP(S) source URL")
        if hostname == "localhost" or hostname.endswith((".localhost", ".invalid")):
            raise VerificationFailure(f"{item_path}.url must identify a real network source")
        expect_nonempty_string(source["consultedAt"], f"{item_path}.consultedAt")

        claims = expect_list(source["claims"], f"{item_path}.claims")
        if not claims:
            raise VerificationFailure(f"{item_path}.claims must contain at least one claim")
        for claim_index, claim in enumerate(claims):
            expect_nonempty_string(claim, f"{item_path}.claims[{claim_index}]")


def verify_semantics(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expect_equal(
        set(artifact),
        {"schemaVersion", "fleet", "sddcSpec", "topology", "migrationPlan", "research"},
        "artifact fields",
    )
    expect_equal(artifact.get("schemaVersion"), "1.0", "$.schemaVersion")

    authority_fleet = snapshot["targetFleet"]
    fleet = expect_object(artifact.get("fleet"), "$.fleet")
    expect_equal(fleet, authority_fleet, "$.fleet")

    sddc = expect_object(artifact.get("sddcSpec"), "$.sddcSpec")
    greenfield = snapshot["greenfield"]
    for field in ("sddcId", "workflowType", "version"):
        expect_equal(sddc.get(field), greenfield[field], f"$.sddcSpec.{field}")

    host_specs = expect_list(sddc.get("hostSpecs"), "$.sddcSpec.hostSpecs")
    hostnames = [expect_object(item, f"$.sddcSpec.hostSpecs[{index}]").get("hostname")
                 for index, item in enumerate(host_specs)]
    expect_equal(hostnames, greenfield["hostnames"], "$.sddcSpec.hostSpecs host order")

    network_specs = expect_list(sddc.get("networkSpecs"), "$.sddcSpec.networkSpecs")
    networks: dict[str, Any] = {}
    for index, item in enumerate(network_specs):
        network = expect_object(item, f"$.sddcSpec.networkSpecs[{index}]")
        network_type = network.get("networkType")
        if network_type in networks:
            raise VerificationFailure(f"duplicate networkType {network_type!r}")
        networks[network_type] = network.get("vlanId")
    expect_equal(networks, greenfield["networkVlans"], "$.sddcSpec.networkSpecs VLAN mapping")

    datastore = expect_object(sddc.get("datastoreSpec"), "$.sddcSpec.datastoreSpec")
    vsan = expect_object(datastore.get("vsanSpec"), "$.sddcSpec.datastoreSpec.vsanSpec")
    expect_equal(
        vsan.get("failuresToTolerate"),
        greenfield["vsanFailuresToTolerate"],
        "$.sddcSpec.datastoreSpec.vsanSpec.failuresToTolerate",
    )

    topology = expect_object(artifact.get("topology"), "$.topology")
    expect_equal(topology, snapshot["topology"], "$.topology")
    data_sites = topology["dataSites"]
    data_site_ids = {site["siteId"] for site in data_sites}
    data_fault_domains = {site["faultDomain"] for site in data_sites}
    witness = topology["witness"]
    if witness["siteId"] in data_site_ids or witness["faultDomain"] in data_fault_domains:
        raise VerificationFailure("$.topology.witness must be in an independent third site and fault domain")
    if witness["hostsManagementWorkloads"] is not False:
        raise VerificationFailure("$.topology.witness must not host management workloads")

    components = {
        component["id"]: component for component in expect_list(inventory.get("components"), "inventory.components")
    }
    ordered = snapshot["orderedComponents"]
    expect_equal(set(components), set(ordered), "protected inventory/snapshot component set")

    plan = expect_object(artifact.get("migrationPlan"), "$.migrationPlan")
    expect_equal(
        set(plan),
        {"targetFleet", "targetVcfVersion", "steps"},
        "$.migrationPlan fields",
    )
    expect_equal(plan.get("targetFleet"), authority_fleet["name"], "$.migrationPlan.targetFleet")
    expect_equal(
        plan.get("targetVcfVersion"),
        authority_fleet["targetVcfVersion"],
        "$.migrationPlan.targetVcfVersion",
    )
    steps = expect_list(plan.get("steps"), "$.migrationPlan.steps")
    expect_equal(len(steps), len(ordered), "$.migrationPlan.steps length")

    required_step_fields = {
        "order", "componentId", "siteId", "product", "fromVersion",
        "viaVersions", "targetVersion", "action", "gates",
    }
    seen: set[str] = set()
    for index, component_id in enumerate(ordered):
        step_path = f"$.migrationPlan.steps[{index}]"
        step = expect_object(steps[index], step_path)
        expect_equal(set(step), required_step_fields, f"{step_path} fields")
        expect_equal(step["order"], index + 1, f"{step_path}.order")
        expect_equal(step["componentId"], component_id, f"{step_path}.componentId")
        if component_id in seen:
            raise VerificationFailure(f"duplicate migration component {component_id!r}")
        seen.add(component_id)
        component = components[component_id]
        expect_equal(step["siteId"], component["siteId"], f"{step_path}.siteId")
        expect_equal(step["product"], component["product"], f"{step_path}.product")
        expect_equal(step["fromVersion"], component["version"], f"{step_path}.fromVersion")
        path_authority = snapshot["paths"][component_id]
        for field in ("viaVersions", "targetVersion", "action", "gates"):
            expect_equal(step[field], path_authority[field], f"{step_path}.{field}")

    expect_equal(seen, set(components), "$.migrationPlan component coverage")
    verify_research(artifact.get("research"))


def main() -> int:
    process = subprocess.run(
        ["java", "TestMain.java"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
        check=False,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "no diagnostic output"
        raise VerificationFailure(f"TestMain failed with exit {process.returncode}: {detail}")
    try:
        artifact_value = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationFailure(f"TestMain output is not one valid JSON document: {exc}") from exc
    # This is deliberately the first artifact check. It uses SddcSpec directly
    # from the protected upstream OpenAPI document and resolves that document's refs.
    with (ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json").open(
        encoding="utf-8"
    ) as handle:
        installer_openapi = json.load(handle)
    sddc_candidate = artifact_value.get("sddcSpec") if isinstance(artifact_value, dict) else None
    sddc_schema = installer_openapi["components"]["schemas"]["SddcSpec"]
    validate_schema(sddc_candidate, sddc_schema, installer_openapi)

    # Only after schema validation succeeds do fixture-derived architecture checks run.
    artifact = expect_object(artifact_value, "artifact")
    with (ROOT / "fixtures" / "estate-inventory.json").open(encoding="utf-8") as handle:
        inventory = json.load(handle)
    with (ROOT / "fixtures" / "compatibility-snapshot.json").open(encoding="utf-8") as handle:
        snapshot = json.load(handle)
    verify_semantics(artifact, inventory, snapshot)
    print("PASS: installer SddcSpec, stretched topology, and 17-component migration plan")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (VerificationFailure, KeyError, TypeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
