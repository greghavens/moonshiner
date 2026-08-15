#!/usr/bin/env python3
"""Deterministic verifier for vcfarch-0115."""

from __future__ import annotations

import ast
import copy
import datetime
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


EXPECTED_HASHES = {
    "estate_inventory.json": "4842ed5ca0601083b6247b4ca8ff25ecb162b758292084540c83a4c43426973d",
    "compatibility_snapshot.json": "89cb6157e8b79c4e029d10b68051e224b75eac88d86c9ba170f5e4624bca827f",
    "migration-plan-schema.json": "8ad5fb8c0d55e396a8e1bb8514255e061de67ff69fbd30a0b794187cba51500c",
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
}


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"missing required file: {path}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path}: {exc}")


def resolve_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        fail(f"unsupported non-local schema reference: {pointer}")
    value = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[part]
        except (KeyError, TypeError):
            fail(f"unresolvable schema reference: {pointer}")
    return value


def is_json_type(value: Any, expected: str) -> bool:
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
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_json_schema(
    value: Any,
    schema: Any,
    document: Any,
    path: str = "$",
) -> None:
    if isinstance(schema, bool):
        if not schema:
            fail(f"{path}: rejected by false schema")
        return
    if not isinstance(schema, dict):
        fail(f"{path}: malformed schema")
    if "$ref" in schema:
        validate_json_schema(value, resolve_pointer(document, schema["$ref"]), document, path)
        return
    if value is None and schema.get("nullable") is True:
        return

    for branch in schema.get("allOf", []):
        validate_json_schema(value, branch, document, path)
    if "anyOf" in schema:
        errors = []
        for branch in schema["anyOf"]:
            try:
                validate_json_schema(value, branch, document, path)
                break
            except VerificationError as exc:
                errors.append(str(exc))
        else:
            fail(f"{path}: does not satisfy anyOf ({'; '.join(errors)})")
    if "oneOf" in schema:
        matches = 0
        for branch in schema["oneOf"]:
            try:
                validate_json_schema(value, branch, document, path)
                matches += 1
            except VerificationError:
                pass
        if matches != 1:
            fail(f"{path}: satisfies {matches} oneOf branches, expected exactly one")
    if "not" in schema:
        try:
            validate_json_schema(value, schema["not"], document, path)
        except VerificationError:
            pass
        else:
            fail(f"{path}: matches forbidden schema")

    expected_type = schema.get("type")
    if expected_type is not None:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(is_json_type(value, item) for item in types):
            fail(f"{path}: expected type {expected_type}, got {type(value).__name__}")
    if "const" in schema and value != schema["const"]:
        fail(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        fail(f"{path}: value {value!r} is not in enum")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            fail(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            fail(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value)
            except re.error as exc:
                fail(f"{path}: invalid schema pattern: {exc}")
            if matched is None:
                fail(f"{path}: string does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            fail(f"{path}: number is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            fail(f"{path}: number is above maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            fail(f"{path}: number is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            fail(f"{path}: number is not below exclusiveMaximum")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            fail(f"{path}: array has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            fail(f"{path}: array has too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                fail(f"{path}: array items are not unique")
        if "items" in schema:
            for index, item in enumerate(value):
                validate_json_schema(item, schema["items"], document, f"{path}[{index}]")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                fail(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, child_schema in properties.items():
            if key in value:
                validate_json_schema(value[key], child_schema, document, f"{path}.{key}")
        additional = schema.get("additionalProperties", True)
        extras = set(value) - set(properties)
        if additional is False and extras:
            fail(f"{path}: unexpected properties {sorted(extras)}")
        if isinstance(additional, dict):
            for key in extras:
                validate_json_schema(value[key], additional, document, f"{path}.{key}")
        if len(value) < schema.get("minProperties", 0):
            fail(f"{path}: object has too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            fail(f"{path}: object has too many properties")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(131072), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_research(research: Any) -> None:
    if not isinstance(research, dict):
        fail("research.json must contain a JSON object")
    consulted = research.get("consulted")
    if not isinstance(consulted, list) or not consulted:
        fail("research.json must contain a non-empty consulted array")
    required = ("title", "url", "accessedOn", "conclusion")
    for index, entry in enumerate(consulted):
        if not isinstance(entry, dict):
            fail(f"research consulted entry {index} must be an object")
        for field in required:
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                fail(f"research consulted entry {index} has no non-empty {field}")
        try:
            parsed_date = datetime.date.fromisoformat(entry["accessedOn"])
        except ValueError:
            fail(f"research consulted entry {index} accessedOn is not YYYY-MM-DD")
        if parsed_date.isoformat() != entry["accessedOn"]:
            fail(f"research consulted entry {index} accessedOn is not canonical YYYY-MM-DD")
        parsed_url = urlsplit(entry["url"])
        hostname = (parsed_url.hostname or "").lower()
        if parsed_url.scheme not in {"http", "https"} or not hostname:
            fail(f"research consulted entry {index} does not have an HTTP(S) URL")
        if hostname != "broadcom.com" and not hostname.endswith(".broadcom.com"):
            fail(f"research consulted entry {index} is not a Broadcom-published source")


def expected_sddc_spec(inventory: dict[str, Any]) -> dict[str, Any]:
    inputs = inventory["sddcInputs"]
    network = inputs["managementNetwork"]
    return {
        "sddcId": inputs["sddcId"],
        "workflowType": "VCF_EXTEND",
        "version": inventory["targetVcfVersion"],
        "vcenterSpec": {
            "vcenterHostname": inputs["vcenterHostname"],
            "rootVcenterPassword": inputs["rootVcenterPassword"],
            "version": inventory["targetVcfVersion"],
            "useExistingDeployment": True,
            "sslThumbprint": inputs["vcenterSslThumbprint"],
        },
        "networkSpecs": [
            {
                "networkType": "MANAGEMENT",
                "vlanId": network["vlanId"],
                "subnet": network["subnet"],
                "gateway": network["gateway"],
                "subnetMask": network["subnetMask"],
                "mtu": network["mtu"],
            }
        ],
        "dnsSpec": {
            "subdomain": inputs["dnsSubdomain"],
            "nameservers": inputs["nameservers"],
        },
    }


def check_sddc_mapping(actual: dict[str, Any], inventory: dict[str, Any]) -> None:
    expected = expected_sddc_spec(inventory)
    for key in ("sddcId", "workflowType", "version"):
        if actual.get(key) != expected[key]:
            fail(f"targetSddcSpec.{key} does not map the inventory SDDC inputs")
    for section in ("vcenterSpec", "dnsSpec"):
        actual_section = actual.get(section)
        if not isinstance(actual_section, dict):
            fail(f"targetSddcSpec.{section} is missing")
        for key, value in expected[section].items():
            if actual_section.get(key) != value:
                fail(f"targetSddcSpec.{section}.{key} does not map the inventory")
    networks = actual.get("networkSpecs")
    required_network = expected["networkSpecs"][0]
    if not isinstance(networks, list) or not any(
        isinstance(network, dict)
        and all(network.get(key) == value for key, value in required_network.items())
        for network in networks
    ):
        fail("targetSddcSpec does not contain the inventoried management network")


def compatibility_gate(fact: dict[str, Any]) -> dict[str, str]:
    left = fact["left"]
    right = fact["right"]
    return {
        "id": fact["id"],
        "kind": "compatibility",
        "condition": (
            f"{left['product']} {left['version']} with "
            f"{right['product']} {right['version']}: {fact['status']}."
        ),
    }


def upgrade_gate(edge: dict[str, Any]) -> dict[str, str]:
    condition = edge.get("condition")
    if condition is None:
        condition = (
            f"{edge['fromProduct']} {edge['fromVersion']} to "
            f"{edge['toProduct']} {edge['toVersion']}: {edge['status']}."
        )
    return {"id": edge["id"], "kind": "upgrade-path", "condition": condition}


def sequencing_gate(sequence: dict[str, Any]) -> dict[str, str]:
    return {
        "id": sequence["id"],
        "kind": "sequencing",
        "condition": sequence["condition"],
    }


def check_artifact(
    artifact: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    if artifact["estateId"] != inventory["estateId"]:
        fail("estateId does not match inventory")
    if artifact["targetFleetId"] != inventory["targetFleetId"]:
        fail("targetFleetId does not match inventory")
    if artifact["targetVcfVersion"] != inventory["targetVcfVersion"]:
        fail("targetVcfVersion does not match inventory")
    check_sddc_mapping(artifact["targetSddcSpec"], inventory)

    components = inventory["components"]
    plans = artifact["componentPlans"]
    expected_ids = {item["id"] for item in components}
    plan_ids = [item["componentId"] for item in plans]
    if len(plan_ids) != len(set(plan_ids)):
        fail("a component appears more than once in componentPlans")
    if set(plan_ids) != expected_ids:
        fail("componentPlans must cover exactly every inventory component")
    orders = sorted(item["order"] for item in plans)
    if orders != list(range(1, len(plans) + 1)):
        fail("component plan order values must be unique and contiguous from 1")

    facts = {item["id"]: item for item in snapshot["compatibilityFacts"]}
    edges = {item["id"]: item for item in snapshot["upgradeEdges"]}
    sequences = snapshot["sequencing"]
    plan_by_id = {item["componentId"]: item for item in plans}
    component_by_kind = {item["kind"]: item for item in components}

    for component in components:
        plan = plan_by_id[component["id"]]
        target = snapshot["targets"][component["kind"]]
        if plan["componentName"] != component["name"]:
            fail(f"{component['id']}: componentName does not match inventory")
        if plan["currentVersion"] != component["version"]:
            fail(f"{component['id']}: currentVersion does not match inventory")
        if plan["targetProduct"] != target["product"]:
            fail(f"{component['id']}: targetProduct does not match pinned target")
        if plan["targetVersion"] != target["version"]:
            fail(f"{component['id']}: targetVersion does not match pinned target")
        if plan["targetFleetId"] != inventory["targetFleetId"]:
            fail(f"{component['id']}: targetFleetId does not name the single fleet")

        required_gates: dict[str, dict[str, str]] = {}
        support_ids = [target["supportEvidence"], *target.get("additionalEvidence", [])]
        for evidence_id in support_ids:
            fact = facts.get(evidence_id)
            if fact is None or fact["status"] == "not-supported":
                fail(f"pinned target evidence is not a supported combination: {evidence_id}")
            required_gates[evidence_id] = compatibility_gate(fact)

        version = component["version"]
        product = component.get("compatibilityProduct", component["name"])
        for transition in plan["transitions"]:
            if transition["fromVersion"] != version:
                fail(f"{component['id']}: transitions are not a continuous ordered path")
            edge = edges.get(transition["evidenceId"])
            if edge is None:
                fail(f"{component['id']}: transition cites an unknown upgrade edge")
            expected_edge = (
                edge["kind"],
                edge["fromProduct"],
                edge["fromVersion"],
                edge["toProduct"],
                edge["toVersion"],
            )
            actual_edge = (
                component["kind"],
                product,
                transition["fromVersion"],
                edge["toProduct"],
                transition["toVersion"],
            )
            if actual_edge != expected_edge:
                fail(f"{component['id']}: transition is not allowed by the pinned upgrade graph")
            required_gates[edge["id"]] = upgrade_gate(edge)
            version = edge["toVersion"]
            product = edge["toProduct"]
        if version != target["version"] or product != target["product"]:
            fail(f"{component['id']}: transitions do not reach the pinned target")

        for sequence in sequences:
            if sequence["afterKind"] == component["kind"]:
                required_gates[sequence["id"]] = sequencing_gate(sequence)

        actual_gates = {gate["id"]: gate for gate in plan["gates"]}
        if len(actual_gates) != len(plan["gates"]):
            fail(f"{component['id']}: duplicate gate id")
        if actual_gates != required_gates:
            fail(f"{component['id']}: gates do not exactly describe the pinned constraints")

    order_by_kind = {
        component["kind"]: plan_by_id[component["id"]]["order"] for component in components
    }
    for sequence in sequences:
        before = sequence["beforeKind"]
        after = sequence["afterKind"]
        if before not in component_by_kind or after not in component_by_kind:
            fail(f"pinned sequence names a missing fixture component: {sequence['id']}")
        if order_by_kind[before] >= order_by_kind[after]:
            fail(f"migration order violates {sequence['id']}")


def check_stdlib_package(root: Path) -> None:
    package = root / "vcf_arch"
    init_file = package / "__init__.py"
    main_file = package / "__main__.py"
    if not init_file.is_file() or not main_file.is_file():
        fail("vcf_arch must be an importable package with __init__.py and __main__.py")
    allowed = set(getattr(sys, "stdlib_module_names", ())) | {"__future__", "vcf_arch"}
    for path in sorted(package.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            fail(f"invalid Python syntax in {path}: {exc}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                names = [node.module.split(".", 1)[0]] if node.module else []
            else:
                continue
            for name in names:
                if name not in allowed:
                    fail(f"non-stdlib import {name!r} in {path}")


def run_cli(
    root: Path,
    inventory_path: Path,
    compatibility_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-S",
        "-m",
        "vcf_arch",
        "--inventory",
        str(inventory_path),
        "--compatibility",
        str(compatibility_path),
        "--output",
        str(output_path),
    ]
    result = subprocess.run(
        command,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        fail(f"package CLI failed ({result.returncode}): {result.stderr.strip()}")
    return load_json(output_path)


def check_cli(
    root: Path,
    artifact: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_dir:
        temp_root = Path(temp_dir)
        regenerated = run_cli(
            root,
            root / "estate_inventory.json",
            root / "compatibility_snapshot.json",
            temp_root / "architecture.json",
        )
        if regenerated != artifact:
            fail("architecture.json is not the deterministic output of the package CLI")

        variant = copy.deepcopy(inventory)
        variant["estateId"] = f"{variant['estateId']}-variant"
        variant["targetFleetId"] = f"{variant['targetFleetId']}-variant"
        variant["components"][0]["id"] = f"{variant['components'][0]['id']}-variant"
        variant["sddcInputs"]["sddcId"] = "chi01-m02"
        variant["sddcInputs"]["vcenterHostname"] = "vc02.chi.example.com"
        variant_path = temp_root / "variant-inventory.json"
        with variant_path.open("w", encoding="utf-8") as handle:
            json.dump(variant, handle)
        variant_artifact = run_cli(
            root,
            variant_path,
            root / "compatibility_snapshot.json",
            temp_root / "variant-architecture.json",
        )
        if variant_artifact == artifact:
            fail("package CLI ignores the supplied inventory")
        check_artifact(variant_artifact, variant, snapshot)

        snapshot_variant = copy.deepcopy(snapshot)
        snapshot_variant["upgradeEdges"][0]["condition"] = (
            "Variant pinned vCenter transition condition."
        )
        snapshot_variant_path = temp_root / "variant-compatibility.json"
        with snapshot_variant_path.open("w", encoding="utf-8") as handle:
            json.dump(snapshot_variant, handle)
        snapshot_variant_artifact = run_cli(
            root,
            root / "estate_inventory.json",
            snapshot_variant_path,
            temp_root / "variant-compatibility-architecture.json",
        )
        if snapshot_variant_artifact == artifact:
            fail("package CLI ignores the supplied compatibility snapshot")
        check_artifact(snapshot_variant_artifact, inventory, snapshot_variant)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    artifact = load_json(root / "architecture.json")
    installer = load_json(root / "specifications/vcf-installer/vcf-installer-openapi.json")

    # This is deliberately the first validation performed on the deliverable.
    try:
        target_sddc_spec = artifact["targetSddcSpec"]
        sddc_schema = installer["components"]["schemas"]["SddcSpec"]
    except (KeyError, TypeError):
        fail("artifact does not expose a target SddcSpec for installer-schema validation")
    validate_json_schema(target_sddc_spec, sddc_schema, installer, "$.targetSddcSpec")
    print("PASS: targetSddcSpec validates against the pinned installer SddcSpec")

    for relative, expected in EXPECTED_HASHES.items():
        path = root / relative
        if not path.is_file() or sha256(path) != expected:
            fail(f"protected fixture or authority file changed: {relative}")

    plan_schema = load_json(root / "migration-plan-schema.json")
    validate_json_schema(artifact, plan_schema, plan_schema)
    inventory = load_json(root / "estate_inventory.json")
    snapshot = load_json(root / "compatibility_snapshot.json")
    check_research(load_json(root / "research.json"))
    check_artifact(artifact, inventory, snapshot)
    check_stdlib_package(root)
    check_cli(root, artifact, inventory, snapshot)
    print("PASS: research, architecture, pinned compatibility plan, and stdlib package are valid")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
