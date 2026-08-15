#!/usr/bin/env python3
"""Deterministic, offline verifier for vcfarch-0093.

The generated JSON is validated against the pinned upstream SddcSpec before
the task-specific schema, inventory, or compatibility authority are examined.
Research activity is deliberately not queried or replayed here. The artifact
records are checked for public-source shape; the harness trace establishes
genuine network use.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read JSON {path.name}: {exc}")


def json_pointer(document: Any, ref: str) -> Any:
    if not ref.startswith("#/"):
        fail(f"unsupported non-local schema reference: {ref}")
    value = document
    for raw in ref[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(token)] if isinstance(value, list) else value[token]
        except (KeyError, IndexError, ValueError, TypeError):
            fail(f"unresolvable schema reference: {ref}")
    return value


def type_matches(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    fail(f"unsupported schema type: {expected}")


def validate_schema(instance: Any, schema: Any, root: Any, at: str = "$") -> None:
    """Validate the JSON Schema/OpenAPI keywords used by the pinned schemas."""
    if isinstance(schema, bool):
        if not schema:
            fail(f"{at}: rejected by false schema")
        return
    if not isinstance(schema, dict):
        fail(f"{at}: malformed schema")

    if "$ref" in schema:
        validate_schema(instance, json_pointer(root, schema["$ref"]), root, at)

    for sub in schema.get("allOf", []):
        validate_schema(instance, sub, root, at)

    if "anyOf" in schema:
        matches = 0
        for sub in schema["anyOf"]:
            try:
                validate_schema(instance, sub, root, at)
                matches += 1
            except VerificationError:
                pass
        if matches == 0:
            fail(f"{at}: does not match anyOf")

    if "oneOf" in schema:
        matches = 0
        for sub in schema["oneOf"]:
            try:
                validate_schema(instance, sub, root, at)
                matches += 1
            except VerificationError:
                pass
        if matches != 1:
            fail(f"{at}: must match exactly one oneOf branch, matched {matches}")

    if "not" in schema:
        try:
            validate_schema(instance, schema["not"], root, at)
        except VerificationError:
            pass
        else:
            fail(f"{at}: matches a forbidden schema")

    if "const" in schema and instance != schema["const"]:
        fail(f"{at}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        fail(f"{at}: {instance!r} is not one of {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(type_matches(instance, item) for item in expected_types):
            fail(f"{at}: expected type {expected_type!r}, got {type(instance).__name__}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in instance]
        if missing:
            fail(f"{at}: missing required properties {missing}")
        properties = schema.get("properties", {})
        for name, value in instance.items():
            if name in properties:
                validate_schema(value, properties[name], root, f"{at}.{name}")
            elif schema.get("additionalProperties") is False:
                fail(f"{at}: additional property {name!r} is forbidden")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(value, schema["additionalProperties"], root, f"{at}.{name}")
        if len(instance) < schema.get("minProperties", 0):
            fail(f"{at}: too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            fail(f"{at}: too many properties")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            fail(f"{at}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            fail(f"{at}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                fail(f"{at}: items must be unique")
        if isinstance(schema.get("items"), dict):
            for index, value in enumerate(instance):
                validate_schema(value, schema["items"], root, f"{at}[{index}]")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            fail(f"{at}: string is shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            fail(f"{at}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance)
            except re.error as exc:
                fail(f"{at}: unsupported regex in pinned schema: {exc}")
            if matched is None:
                fail(f"{at}: string does not match {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            fail(f"{at}: value is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            fail(f"{at}: value is above maximum")


def keyed(items: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier:
            fail(f"{label}: every item must have a non-empty id")
        if identifier in result:
            fail(f"{label}: duplicate id {identifier}")
        result[identifier] = item
    return result


def verify_research_log(entries: list[dict[str, Any]]) -> None:
    locators: set[str] = set()
    matrix_recorded = False
    for entry in entries:
        locator = entry["locator"]
        try:
            parsed = urlsplit(locator)
            hostname = (parsed.hostname or "").lower()
        except ValueError:
            fail("researchLog locators must be valid HTTPS URLs")
        if parsed.scheme != "https" or not hostname or parsed.username is not None or parsed.password is not None:
            fail("researchLog locators must be credential-free HTTPS URLs")
        try:
            port = parsed.port
        except ValueError:
            fail("researchLog locators must contain a valid HTTPS port")
        if port not in (None, 443):
            fail("researchLog locators must use the standard HTTPS port")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            fail("researchLog locators must name published sources, not IP literals")
        if hostname == "localhost" or hostname.endswith((".localhost", ".invalid", ".test", ".example")):
            fail("researchLog contains a reserved or local-only source")
        if hostname == "interopmatrix.broadcom.com":
            matrix_recorded = True
        if locator in locators:
            fail("researchLog locators must be unique")
        locators.add(locator)
        if "consultedOn" in entry:
            try:
                date.fromisoformat(entry["consultedOn"])
            except ValueError:
                fail("researchLog consultedOn must be a real ISO calendar date")
    if not matrix_recorded:
        fail("researchLog must include the published Broadcom Product Interoperability Matrix")


def verify_semantics(artifact: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any], spec_path: Path) -> None:
    verify_research_log(artifact["researchLog"])

    expected_digest = snapshot["installerSpec"]["sha256"]
    actual_digest = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    if actual_digest != expected_digest:
        fail("pinned installer specification digest does not match compatibility authority")
    if artifact["installerSpec"] != snapshot["installerSpec"]:
        fail("artifact installerSpec provenance must exactly match the pinned authority")

    if artifact["estateId"] != inventory["estateId"]:
        fail("artifact estateId does not match inventory")
    if artifact["sddcId"] != inventory["managementDomainId"]:
        fail("sddcId does not identify the inventoried management domain")
    if artifact["vcfInstanceName"] != inventory["vcfInstanceName"]:
        fail("vcfInstanceName does not match inventory")
    if artifact["targetVcfVersion"] != snapshot["targetVcfVersion"] or artifact["version"] != snapshot["targetVcfVersion"]:
        fail("artifact does not target the pinned VCF version")
    if artifact["workflowType"] != "VCF":
        fail("workflowType must describe a VCF architecture")

    if artifact["dnsSpec"] != inventory["dns"] or artifact["ntpServers"] != inventory["ntpServers"]:
        fail("DNS/NTP target state must preserve the inventoried infrastructure services")
    expected_networks = sorted(inventory["networks"], key=lambda item: item["networkType"])
    actual_networks = sorted(artifact["networkSpecs"], key=lambda item: item["networkType"])
    if actual_networks != expected_networks:
        fail("networkSpecs must carry the fixture's exact network architecture")
    actual_types = {item["networkType"] for item in actual_networks}
    if actual_types != set(snapshot["requiredNetworkTypes"]):
        fail("target network types do not match the pinned requirements")

    inventory_sites = keyed(inventory["sites"], "inventory sites")
    artifact_sites = keyed(artifact["sites"], "artifact sites")
    if artifact_sites != inventory_sites:
        fail("site and host affinity definitions must exactly preserve the estate fixture")

    stretch = artifact["stretchedManagementDomain"]
    data_sites = [site["id"] for site in inventory["sites"] if site["role"] == "DATA"]
    witness_sites = [site["id"] for site in inventory["sites"] if site["role"] == "WITNESS"]
    if len(data_sites) != snapshot["witnessRules"]["dataSiteCount"] or len(witness_sites) != 1:
        fail("inventory does not contain the pinned two-data-site/one-witness topology")
    if stretch["dataSiteIds"] != data_sites:
        fail("stretched domain must name the two data sites in fixture order")
    fixture_stretch = inventory["stretchedManagementDomain"]
    if stretch["preferredSiteId"] != fixture_stretch["preferredSiteId"] or stretch["secondarySiteId"] != fixture_stretch["secondarySiteId"]:
        fail("preferred and secondary sites do not match the fixture")
    witness = stretch["witness"]
    witness_site_id = witness_sites[0]
    if witness["siteId"] != fixture_stretch["witnessSiteId"] or witness["siteId"] != witness_site_id:
        fail("witness must be placed at the fixture's independent witness site")
    if witness["siteId"] in stretch["dataSiteIds"]:
        fail("witness cannot be placed in either management data site")
    data_domains = {inventory_sites[site_id]["failureDomain"] for site_id in data_sites}
    if witness["failureDomain"] != inventory_sites[witness_site_id]["failureDomain"] or witness["failureDomain"] in data_domains:
        fail("witness must use the independent third failure domain")
    if witness["componentId"] != fixture_stretch["witnessComponentId"]:
        fail("stretched domain names the wrong witness component")
    if witness["quorumOnly"] is not snapshot["witnessRules"]["quorumOnly"]:
        fail("witness must remain quorum-only")
    if witness["runsManagementWorkloads"] is not snapshot["witnessRules"]["runsManagementWorkloads"]:
        fail("witness must not host management workloads")

    inventory_components = keyed(inventory["components"], "inventory components")
    authority_components = keyed(snapshot["components"], "snapshot components")
    plan_components = keyed(artifact["components"], "plan components")
    if set(plan_components) != set(inventory_components) or set(plan_components) != set(authority_components):
        fail("plan must name every and only inventoried component")

    gate_ids = [gate["id"] for gate in artifact["gates"]]
    if len(gate_ids) != len(set(gate_ids)):
        fail("gate ids must be unique")
    if set(gate_ids) != set(snapshot["requiredGates"]):
        fail("plan gates do not match the pinned compatibility authority")

    steps = artifact["steps"]
    step_ids = [step["id"] for step in steps]
    if step_ids != snapshot["requiredStepOrder"]:
        fail("migration step order does not match the pinned upgrade sequence")
    if [step["order"] for step in steps] != list(range(1, len(steps) + 1)):
        fail("migration step order numbers must be contiguous and one-based")
    step_map = keyed(steps, "migration steps")
    used_gates: set[str] = set()
    for index, step in enumerate(steps):
        unknown_gates = set(step["gateIds"]) - set(gate_ids)
        if unknown_gates:
            fail(f"step {step['id']} references unknown gates {sorted(unknown_gates)}")
        used_gates.update(step["gateIds"])
        unknown_components = set(step["componentIds"]) - set(plan_components)
        if unknown_components:
            fail(f"step {step['id']} references unknown components {sorted(unknown_components)}")
        required_predecessors = step["requiresStepIds"]
        if index == 0:
            if required_predecessors:
                fail("first migration step cannot require another step")
        elif step_ids[index - 1] not in required_predecessors:
            fail(f"step {step['id']} must require its immediate predecessor")
        if any(step_ids.index(dep) >= index for dep in required_predecessors if dep in step_ids):
            fail(f"step {step['id']} has a non-prior dependency")
        unknown_dependencies = set(required_predecessors) - set(step_ids)
        if unknown_dependencies:
            fail(f"step {step['id']} has unknown dependencies {sorted(unknown_dependencies)}")
    if used_gates != set(gate_ids):
        fail("every pinned gate must control at least one migration step")

    for component_id, source in inventory_components.items():
        authority = authority_components[component_id]
        planned = plan_components[component_id]
        for field in ("name", "kind", "siteIds"):
            if planned[field] != source[field]:
                fail(f"component {component_id} does not preserve inventory field {field}")
        if planned["currentVersion"] != source["version"]:
            fail(f"component {component_id} has the wrong current version")
        for field in ("targetProduct", "targetVersion", "supportedRoute", "finalStepId", "action"):
            if planned[field] != authority[field]:
                fail(f"component {component_id} disagrees with pinned {field}")
        if planned["gatedBy"] != authority["requiredGateIds"]:
            fail(f"component {component_id} does not name its exact compatibility gates")
        final_step = step_map[planned["finalStepId"]]
        if component_id not in final_step["componentIds"]:
            fail(f"component {component_id} is absent from its final migration step")
        if not set(planned["gatedBy"]).issubset(set(final_step["gateIds"])):
            fail(f"component {component_id}'s final step does not enforce all of its gates")

    expected_hosts = [item["id"] for item in inventory["components"] if item["kind"] == "ESXI_HOST"]
    actual_hosts = [item["hostname"] for item in artifact["hostSpecs"]]
    if actual_hosts != expected_hosts:
        fail("SddcSpec host list must preserve all management hosts in fixture order")
    if artifact["clusterSpec"].get("clusterName") != "cluster-mgmt-stretched":
        fail("target SddcSpec must retain the stretched management cluster")
    if artifact["datastoreSpec"].get("vsanSpec", {}).get("datastoreName") != "vsan-mgmt-stretched":
        fail("target SddcSpec must retain the stretched vSAN datastore")
    if "REDACTED" not in artifact["vcenterSpec"]["rootVcenterPassword"]:
        fail("required vCenter credential must be an explicit non-secret placeholder")

    hops = artifact["migrationHops"]
    authority_hops = snapshot["supportedVcfHops"]
    if len(hops) != len(authority_hops):
        fail("migration must contain the complete supported VCF hop chain")
    for hop, authority in zip(hops, authority_hops):
        if (hop["order"], hop["fromVcf"], hop["toVcf"], hop["mechanism"]) != (
            authority["order"], authority["from"], authority["to"], authority["mechanism"]
        ):
            fail("migration contains an unsupported or out-of-order VCF hop")
        if not set(hop["gateIds"]).issubset(set(gate_ids)):
            fail("migration hop references an unknown gate")
    if hops[0]["fromVcf"] != inventory["vcfVersion"] or hops[-1]["toVcf"] != snapshot["targetVcfVersion"]:
        fail("VCF hop chain does not span the inventoried source and pinned target")
    for prior, following in zip(hops, hops[1:]):
        if prior["toVcf"] != following["fromVcf"]:
            fail("VCF hop chain is discontinuous")
    actual_pairs = {(hop["fromVcf"], hop["toVcf"]) for hop in hops}
    for forbidden in snapshot["forbiddenDirectHops"]:
        if (forbidden["from"], forbidden["to"]) in actual_pairs:
            fail("migration includes a pinned unsupported direct hop")


def run() -> None:
    root = Path(__file__).resolve().parent
    source = root / "VcfMigrationPlanner.java"
    harness = root / "TestMain.java"
    inventory_path = root / "estate-inventory.json"
    snapshot_path = root / "compatibility-snapshot.json"
    task_schema_path = root / "migration-plan-schema.json"
    spec_path = root / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"

    with tempfile.TemporaryDirectory(prefix="vcfarch-0093-") as temp_name:
        temp = Path(temp_name)
        compile_result = subprocess.run(
            ["javac", "-encoding", "UTF-8", "-d", str(temp), str(source), str(harness)],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        if compile_result.returncode != 0:
            fail("Java compilation failed:\n" + compile_result.stdout)

        artifact_path = temp / "migration-plan.json"
        execute_result = subprocess.run(
            ["java", "-cp", str(temp), "TestMain", str(inventory_path), str(snapshot_path), str(artifact_path)],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        if execute_result.returncode != 0:
            fail("TestMain failed:\n" + execute_result.stdout)

        # Phase 1: the artifact and pinned OpenAPI document are the only inputs
        # examined here. SddcSpec validation intentionally precedes all task checks.
        artifact = load_json(artifact_path)
        installer_spec = load_json(spec_path)
        if not isinstance(artifact, dict):
            fail("installer SddcSpec validation failed: artifact must be an object")
        try:
            sddc_schema = installer_spec["components"]["schemas"]["SddcSpec"]
            validate_schema(artifact, sddc_schema, installer_spec)
        except (KeyError, TypeError) as exc:
            fail(f"pinned installer specification is malformed: {exc}")

        # Phase 2: only after upstream schema success, apply the fixed extension
        # contract and deterministic fixture/snapshot semantics. No network access.
        task_schema = load_json(task_schema_path)
        validate_schema(artifact, task_schema, task_schema)
        inventory = load_json(inventory_path)
        snapshot = load_json(snapshot_path)
        verify_semantics(artifact, inventory, snapshot, spec_path)

    print("PASS: SddcSpec, migration schema, fixture, and pinned compatibility checks succeeded")


if __name__ == "__main__":
    try:
        run()
    except (VerificationError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
