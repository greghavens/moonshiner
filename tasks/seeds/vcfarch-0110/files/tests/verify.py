#!/usr/bin/env python3
"""Offline verifier for vcfarch-0110."""

from __future__ import annotations

import ast
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
OPENAPI_PATH = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
PLAN_SCHEMA_PATH = ROOT / "specifications" / "migration-plan.schema.json"
INVENTORY_PATH = ROOT / "fixtures" / "estate.json"
SNAPSHOT_PATH = ROOT / "fixtures" / "compatibility-snapshot.json"
ARTIFACT_DIR = ROOT / "architecture"

EXPECTED_DIGESTS = {
    OPENAPI_PATH: "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    PLAN_SCHEMA_PATH: "e934a6714b2082bedd278ddd165f85b39694e1b500dfc328a6ee73e1f0fb55c7",
    INVENTORY_PATH: "2528208afa60c6ee54d871e3921eba27d708c8a611031dd24f142a5c121702c2",
    SNAPSHOT_PATH: "cccfd463a9103919de85d0f9bdd5dafa781a3108d844b3e753ca5b760309131d",
}


class VerificationError(Exception):
    """A deterministic verification failure."""


def fail(message: str) -> None:
    raise VerificationError(message)


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"missing required file: {display_path(path)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {display_path(path)}: {exc}")


def json_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        fail(f"only local JSON references are supported, got {pointer!r}")
    value = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[part]
        except (KeyError, TypeError):
            fail(f"unresolvable JSON reference {pointer!r}")
    return value


def matches_type(instance: Any, expected: str) -> bool:
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
    fail(f"unsupported JSON Schema type {expected!r}")


def schema_validate(instance: Any, schema: Any, root_schema: Any, path: str = "$") -> None:
    if isinstance(schema, bool):
        if not schema:
            fail(f"{path}: value is forbidden by schema")
        return
    if not isinstance(schema, dict):
        fail(f"{path}: malformed schema")

    if "$ref" in schema:
        schema_validate(instance, json_pointer(root_schema, schema["$ref"]), root_schema, path)
        return

    if "allOf" in schema:
        for child in schema["allOf"]:
            schema_validate(instance, child, root_schema, path)
    if "anyOf" in schema:
        successes = 0
        for child in schema["anyOf"]:
            try:
                schema_validate(instance, child, root_schema, path)
                successes += 1
            except VerificationError:
                pass
        if not successes:
            fail(f"{path}: value does not satisfy anyOf")
    if "oneOf" in schema:
        successes = 0
        for child in schema["oneOf"]:
            try:
                schema_validate(instance, child, root_schema, path)
                successes += 1
            except VerificationError:
                pass
        if successes != 1:
            fail(f"{path}: value must satisfy exactly one oneOf branch")
    if "not" in schema:
        try:
            schema_validate(instance, schema["not"], root_schema, path)
        except VerificationError:
            pass
        else:
            fail(f"{path}: value satisfies forbidden schema")

    if instance is None and schema.get("nullable") is True:
        return
    if "const" in schema and instance != schema["const"]:
        fail(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        fail(f"{path}: {instance!r} is not in the allowed values")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(matches_type(instance, item) for item in expected_type):
            fail(f"{path}: wrong type")
    elif expected_type and not matches_type(instance, expected_type):
        fail(f"{path}: expected {expected_type}, got {type(instance).__name__}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in instance]
        if missing:
            fail(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                schema_validate(value, properties[key], root_schema, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                fail(f"{path}: additional property {key!r} is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                schema_validate(
                    value,
                    schema["additionalProperties"],
                    root_schema,
                    f"{path}.{key}",
                )
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            fail(f"{path}: too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            fail(f"{path}: too many properties")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            fail(f"{path}: too few array items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            fail(f"{path}: too many array items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(value, sort_keys=True, separators=(",", ":")) for value in instance]
            if len(encoded) != len(set(encoded)):
                fail(f"{path}: array items must be unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, value in enumerate(instance):
                schema_validate(value, item_schema, root_schema, f"{path}[{index}]")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            fail(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            fail(f"{path}: string is longer than maxLength")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], instance)
            except re.error as exc:
                fail(f"{path}: invalid schema pattern: {exc}")
            if matched is None:
                fail(f"{path}: string does not match required pattern")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            fail(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            fail(f"{path}: number is above maximum")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            fail(f"{path}: number is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            fail(f"{path}: number is not below exclusiveMaximum")


def validate_sddc_before_any_other_check() -> tuple[dict[str, Any], dict[str, Any]]:
    """This is deliberately the first verification operation in main()."""
    sddc = load_json(ARTIFACT_DIR / "greenfield-sddc.json")
    openapi = load_json(OPENAPI_PATH)
    try:
        sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
    except (KeyError, TypeError):
        fail("installer OpenAPI does not contain components.schemas.SddcSpec")
    schema_validate(sddc, sddc_schema, openapi, "$.greenfieldSddc")
    return sddc, openapi


def verify_input_integrity() -> None:
    for path, expected in EXPECTED_DIGESTS.items():
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            fail(f"missing pinned input: {path.relative_to(ROOT)}")
        if actual != expected:
            fail(f"pinned input was modified: {path.relative_to(ROOT)}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def verify_sddc_architecture(sddc: dict[str, Any], snapshot: dict[str, Any]) -> set[str]:
    target = snapshot["targetFleet"]["version"]
    require(sddc.get("workflowType") == "VCF", "SddcSpec workflowType must be VCF")
    require(sddc.get("version") == target, "SddcSpec version must equal the pinned fleet version")
    require(
        sddc.get("vcenterSpec", {}).get("useExistingDeployment") is False,
        "greenfield vCenter must not reuse an existing deployment",
    )

    hosts = sddc.get("hostSpecs", [])
    hostnames = [host.get("hostname") for host in hosts]
    require(len(hostnames) == 8, "SddcSpec must contain exactly eight management hosts")
    require(len(set(hostnames)) == 8 and all(hostnames), "management hostnames must be unique")

    required_networks = {"MANAGEMENT", "VMOTION", "VSAN", "VM_MANAGEMENT", "FLEET_MANAGEMENT"}
    network_types = {network.get("networkType") for network in sddc.get("networkSpecs", [])}
    require(required_networks <= network_types, "SddcSpec is missing one or more required networks")

    dvs_specs = sddc.get("dvsSpecs", [])
    require(bool(dvs_specs), "SddcSpec must define a distributed switch")
    covered = set()
    for dvs in dvs_specs:
        covered.update(dvs.get("networks", []))
    require(required_networks <= covered, "distributed switch does not carry every required network")

    vsan_spec = sddc.get("datastoreSpec", {}).get("vsanSpec", {})
    require(
        vsan_spec.get("failuresToTolerate") == snapshot["topology"]["failuresToTolerate"],
        "SddcSpec vSAN failuresToTolerate does not match the pinned stretched design",
    )
    return set(hostnames)


def verify_topology(
    topology: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    sddc_hostnames: set[str],
) -> None:
    authority = snapshot["topology"]
    require(topology.get("clusterType") == authority["clusterType"], "wrong cluster type")
    require(topology.get("failuresToTolerate") == authority["failuresToTolerate"], "wrong topology FTT")

    inventory_sites = {site["siteId"]: site for site in inventory["sites"]}
    data_sites = topology.get("dataSites")
    require(isinstance(data_sites, list) and len(data_sites) == 2, "topology needs exactly two data sites")
    require(
        {site.get("siteId") for site in data_sites} == set(authority["dataSiteIds"]),
        "topology data sites do not match the pinned design",
    )
    placed_hosts: set[str] = set()
    for placement in data_sites:
        site_id = placement.get("siteId")
        expected_site = inventory_sites[site_id]
        require(placement.get("failureDomain") == expected_site["failureDomain"], "wrong data failure domain")
        hostnames = placement.get("hostnames")
        require(
            isinstance(hostnames, list) and len(hostnames) == authority["hostsPerDataSite"],
            f"{site_id} must contain exactly {authority['hostsPerDataSite']} management hosts",
        )
        require(len(hostnames) == len(set(hostnames)), f"{site_id} contains duplicate hosts")
        require(not (placed_hosts & set(hostnames)), "a management host is placed in both data sites")
        placed_hosts.update(hostnames)
    require(placed_hosts == sddc_hostnames, "topology host placement must exactly cover SddcSpec hosts")

    witness = topology.get("witness")
    require(isinstance(witness, dict), "topology must define a witness")
    require(witness.get("inventoryId") == authority["witnessInventoryId"], "wrong witness appliance")
    require(witness.get("siteId") == authority["witnessSiteId"], "witness is not in the third site")
    require(
        witness.get("failureDomain") == authority["witnessFailureDomain"],
        "witness is not in the third failure domain",
    )
    require(witness.get("runsWorkloads") is False, "witness must not run workloads")
    require(witness.get("dataClusterMember") is False, "witness must not be a data-cluster member")
    require(witness.get("hostname") not in sddc_hostnames, "witness must not be an SddcSpec data host")
    require(
        witness.get("siteId") not in authority["dataSiteIds"]
        and witness.get("failureDomain") not in authority["dataFailureDomains"],
        "witness must be outside both data sites and fault domains",
    )


def verify_research(research: Any) -> None:
    if isinstance(research, list):
        sources = research
    elif isinstance(research, dict):
        sources = research.get("sources")
    else:
        fail("architecture/research.json must contain a source list")

    require(isinstance(sources, list) and bool(sources), "research must contain at least one source")
    seen_urls: set[str] = set()
    combined_notes: list[str] = []
    time_fields = ("retrievedAt", "retrievalTime", "accessedAt", "accessedAtUtc")
    note_fields = ("finding", "note", "factUsed", "decision")

    for index, source in enumerate(sources):
        label = f"research source {index + 1}"
        require(isinstance(source, dict), f"{label} must be an object")

        title = source.get("title")
        require(isinstance(title, str) and title.strip(), f"{label} needs a title")

        raw_url = source.get("url")
        require(isinstance(raw_url, str) and raw_url.strip(), f"{label} needs a URL")
        parsed = urlparse(raw_url)
        host = (parsed.hostname or "").lower()
        require(
            parsed.scheme == "https" and (host == "broadcom.com" or host.endswith(".broadcom.com")),
            f"{label} must use a real Broadcom-published HTTPS URL",
        )
        require(raw_url not in seen_urls, f"duplicate research URL {raw_url}")
        seen_urls.add(raw_url)

        retrieved = next(
            (source.get(field) for field in time_fields if isinstance(source.get(field), str)),
            None,
        )
        require(retrieved is not None and retrieved.strip(), f"{label} needs a retrieval time")
        try:
            datetime.fromisoformat(retrieved.replace("Z", "+00:00"))
        except ValueError:
            fail(f"{label} retrieval time is not ISO 8601")

        note = next(
            (source.get(field) for field in note_fields if isinstance(source.get(field), str)),
            None,
        )
        require(isinstance(note, str) and note.strip(), f"{label} needs a compatibility fact note")
        combined_notes.append(f"{title} {note}".lower())

    findings = " ".join(combined_notes)
    topic_terms = {
        "vCenter": ("vcenter",),
        "ESXi/vSAN": ("esxi", "vsan"),
        "NSX": ("nsx",),
        "Live Site Recovery/vSphere Replication": (
            "live site recovery",
            "site recovery manager",
            "vsphere replication",
            "recovery",
        ),
    }
    for topic, alternatives in topic_terms.items():
        require(any(term in findings for term in alternatives), f"research does not cover {topic}")


def transition_gates(snapshot: dict[str, Any], kind: str, to_version: str) -> set[str]:
    required: set[str] = set()
    for rule in snapshot["transitionGateRules"]:
        if kind in rule["kinds"]:
            required.update(rule["gates"])
    for rule in snapshot["conditionalTransitionGates"]:
        if kind == rule["kind"] and to_version == rule["toVersion"]:
            required.update(rule["gates"])
    return required


def component_gates(
    snapshot: dict[str, Any], component: dict[str, Any], imported_ids: set[str]
) -> set[str]:
    path = snapshot["upgradePaths"][component["id"]]
    result: set[str] = set()
    for to_version in path[1:]:
        result.update(transition_gates(snapshot, component["kind"], to_version))
    if component["id"] in imported_ids:
        result.update({"vcf-import-precheck", "stretched-network-readiness"})
    return result


def verify_plan(
    plan: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    plan_schema: dict[str, Any],
) -> None:
    schema_validate(plan, plan_schema, plan_schema, "$.migrationPlan")
    require(plan["estateId"] == inventory["estateId"], "migration plan estateId mismatch")
    require(
        plan["targetFleetVersion"] == snapshot["targetFleet"]["version"],
        "migration plan target fleet mismatch",
    )

    expected_gate_catalog = {gate["id"]: gate["condition"] for gate in snapshot["gates"]}
    actual_gate_catalog = {gate["id"]: gate["condition"] for gate in plan["gateCatalog"]}
    require(len(actual_gate_catalog) == len(plan["gateCatalog"]), "duplicate gate IDs")
    allowed_gates = set(expected_gate_catalog)
    require(set(actual_gate_catalog) <= allowed_gates, "gate catalog contains an undefined gate ID")

    inventory_by_id = {component["id"]: component for component in inventory["components"]}
    require(len(inventory_by_id) == len(inventory["components"]), "fixture has duplicate component IDs")
    import_baselines = snapshot["importBaselines"]
    imported_ids = {item for baseline in import_baselines.values() for item in baseline}

    register_by_id: dict[str, dict[str, Any]] = {}
    for item in plan["components"]:
        component_id = item["inventoryId"]
        require(component_id not in register_by_id, f"duplicate component register entry {component_id}")
        register_by_id[component_id] = item
    require(set(register_by_id) == set(inventory_by_id), "component register must exactly cover inventory")

    for component_id, source in inventory_by_id.items():
        item = register_by_id[component_id]
        require(item["kind"] == source["kind"], f"{component_id}: kind mismatch")
        require(item["currentVersion"] == source["version"], f"{component_id}: current version mismatch")
        require(
            item["targetVersion"] == snapshot["targetVersions"][source["kind"]],
            f"{component_id}: target version mismatch",
        )
        require(
            item["targetState"] == snapshot["targetFleet"]["state"],
            f"{component_id}: target state mismatch",
        )
        expected = component_gates(snapshot, source, imported_ids)
        require(set(item["gatedBy"]) == expected, f"{component_id}: incomplete or extraneous component gates")

    cataloged_gates = {
        gate
        for item in plan["components"]
        for gate in item["gatedBy"]
    } | {
        gate
        for step in plan["steps"]
        for gate in step["gates"]
    }
    require(
        set(actual_gate_catalog) == cataloged_gates,
        "gate catalog must cover exactly the gates used by components and steps",
    )

    steps = plan["steps"]
    require([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "step order must be contiguous")
    step_ids = [step["id"] for step in steps]
    require(len(step_ids) == len(set(step_ids)), "step IDs must be unique")

    state = {component_id: component["version"] for component_id, component in inventory_by_id.items()}
    path_index = {component_id: 0 for component_id in inventory_by_id}
    imported_sites: set[str] = set()
    witness_placed = False
    recovery_paired = False
    recovery = snapshot["recovery"]
    witness_id = snapshot["topology"]["witnessInventoryId"]

    for step in steps:
        step_components = step["components"]
        require(set(step_components) <= set(inventory_by_id), f"{step['id']}: unknown component")
        require(set(step["gates"]) <= allowed_gates, f"{step['id']}: undefined gate")
        transitions = step["transitions"]

        if step["operation"] == "UPGRADE":
            require(bool(transitions), f"{step['id']}: UPGRADE needs transitions")
            transition_ids = [transition["inventoryId"] for transition in transitions]
            require(
                len(transition_ids) == len(set(transition_ids)),
                f"{step['id']}: component transitions more than once in a step",
            )
            require(set(transition_ids) == set(step_components), f"{step['id']}: transition/component mismatch")
            expected_step_gates: set[str] = set()
            for transition in transitions:
                component_id = transition["inventoryId"]
                source = inventory_by_id[component_id]
                path = snapshot["upgradePaths"][component_id]
                index = path_index[component_id]
                require(index + 1 < len(path), f"{step['id']}: transition exceeds pinned path for {component_id}")
                require(
                    transition["fromVersion"] == state[component_id] == path[index],
                    f"{step['id']}: wrong fromVersion for {component_id}",
                )
                require(
                    transition["toVersion"] == path[index + 1],
                    f"{step['id']}: skipped or invented version hop for {component_id}",
                )
                expected_step_gates.update(
                    transition_gates(snapshot, source["kind"], transition["toVersion"])
                )

                site_id = source["siteId"]
                if source["kind"] == "NSX" and transition["toVersion"] == snapshot["targetVersions"]["NSX"]:
                    require(site_id in imported_sites, f"{step['id']}: NSX 9.1 transition precedes VCF import")
                if source["kind"] == "VCENTER" and transition["toVersion"] == snapshot["targetVersions"]["VCENTER"]:
                    require(
                        state["nsx-ord" if site_id == "ord-dc1" else "nsx-dfw"]
                        == snapshot["targetVersions"]["NSX"],
                        f"{step['id']}: vCenter target transition must follow its NSX target transition",
                    )
                    require(
                        all(state[item] == recovery["bridgeVersion"] for item in recovery["componentIds"]),
                        f"{step['id']}: recovery components must all be at the bridge version first",
                    )
                if source["kind"] in {"ESXI", "VSAN"} and transition["toVersion"] == snapshot["targetVersions"][source["kind"]]:
                    vc_id = "vc-ord" if site_id == "ord-dc1" else "vc-dfw"
                    nsx_id = "nsx-ord" if site_id == "ord-dc1" else "nsx-dfw"
                    require(state[vc_id] == snapshot["targetVersions"]["VCENTER"], f"{step['id']}: data target precedes vCenter")
                    require(state[nsx_id] == snapshot["targetVersions"]["NSX"], f"{step['id']}: data target precedes NSX")
                    require(witness_placed, f"{step['id']}: witness must be placed before data target transitions")
                    require(
                        state[witness_id] == snapshot["targetVersions"]["VSAN_WITNESS"],
                        f"{step['id']}: witness must reach its target before data target transitions",
                    )

            require(set(step["gates"]) == expected_step_gates, f"{step['id']}: incorrect transition gates")
            for transition in transitions:
                component_id = transition["inventoryId"]
                state[component_id] = transition["toVersion"]
                path_index[component_id] += 1

        elif step["operation"] == "IMPORT":
            require(not transitions, f"{step['id']}: IMPORT must not contain version transitions")
            represented_sites = [
                site_id
                for site_id, baseline in import_baselines.items()
                if set(baseline) <= set(step_components)
            ]
            require(represented_sites, f"{step['id']}: IMPORT does not contain a complete site baseline")
            expected_components = {item for site_id in represented_sites for item in import_baselines[site_id]}
            require(set(step_components) == expected_components, f"{step['id']}: partial or extraneous import inventory")
            require(
                set(step["gates"]) == {"vcf-import-precheck", "stretched-network-readiness"},
                f"{step['id']}: incorrect import gates",
            )
            for site_id in represented_sites:
                require(site_id not in imported_sites, f"{step['id']}: site imported more than once")
                for component_id, version in import_baselines[site_id].items():
                    require(state[component_id] == version, f"{step['id']}: {component_id} is not at its import baseline")
                imported_sites.add(site_id)

        elif step["operation"] == "CONFIGURE":
            require(not transitions, f"{step['id']}: CONFIGURE must not contain version transitions")
            if set(step_components) == {witness_id}:
                require(not witness_placed, "witness placement appears more than once")
                require(
                    set(step["gates"]) == {"witness-failure-domain", "witness-connectivity"},
                    f"{step['id']}: incorrect witness placement gates",
                )
                witness_placed = True
            elif set(step_components) == set(recovery["componentIds"]):
                require(not recovery_paired, "recovery pairing appears more than once")
                require(
                    all(
                        state[item] == snapshot["targetVersions"][inventory_by_id[item]["kind"]]
                        for item in recovery["componentIds"]
                    ),
                    f"{step['id']}: recovery sites paired before final transitions",
                )
                require(
                    set(step["gates"]) == {"recovery-interoperability", "recovery-pair-quiesced"},
                    f"{step['id']}: incorrect recovery pairing gates",
                )
                recovery_paired = True
            else:
                fail(f"{step['id']}: unsupported CONFIGURE component set")
        else:
            fail(f"{step['id']}: unsupported operation")

    require(imported_sites == set(import_baselines), "both data sites must be imported")
    require(witness_placed, "plan never places the witness")
    require(recovery_paired, "plan never pairs the final recovery components")
    for component_id, source in inventory_by_id.items():
        path = snapshot["upgradePaths"][component_id]
        require(path_index[component_id] == len(path) - 1, f"{component_id}: incomplete upgrade path")
        require(state[component_id] == snapshot["targetVersions"][source["kind"]], f"{component_id}: wrong final state")


def verify_stdlib_package() -> None:
    package = ROOT / "vcf_arch"
    require((package / "__init__.py").is_file(), "vcf_arch is not a Python package")
    require((package / "__main__.py").is_file(), "vcf_arch has no module entry point")
    python_files = sorted(package.rglob("*.py"))
    require(bool(python_files), "vcf_arch contains no Python files")
    stdlib = set(sys.stdlib_module_names) | {"__future__", "vcf_arch"}
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            fail(f"cannot parse {path.relative_to(ROOT)}: {exc}")
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".", 1)[0]
                    require(top in stdlib, f"third-party import {alias.name!r} in {path.relative_to(ROOT)}")
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                module = node.module.split(".", 1)[0]
            if module:
                require(module in stdlib, f"third-party import {node.module!r} in {path.relative_to(ROOT)}")


def verify_generator_matches_committed_artifacts() -> None:
    expected_names = ["greenfield-sddc.json", "topology.json", "migration-plan.json"]
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_name:
        output_dir = Path(temp_name) / "architecture"
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        command = [
            sys.executable,
            "-m",
            "vcf_arch",
            "--inventory",
            str(INVENTORY_PATH),
            "--compatibility",
            str(SNAPSHOT_PATH),
            "--output-dir",
            str(output_dir),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            fail("architecture generator timed out")
        require(
            completed.returncode == 0,
            f"architecture generator failed ({completed.returncode}): {completed.stderr.strip()}",
        )
        for name in expected_names:
            committed = load_json(ARTIFACT_DIR / name)
            generated = load_json(output_dir / name)
            require(generated == committed, f"generator output differs from committed architecture/{name}")


def main() -> int:
    try:
        # Required ordering: validate the submitted SddcSpec with the installer schema first.
        sddc, _openapi = validate_sddc_before_any_other_check()

        verify_input_integrity()
        inventory = load_json(INVENTORY_PATH)
        snapshot = load_json(SNAPSHOT_PATH)
        plan_schema = load_json(PLAN_SCHEMA_PATH)
        topology = load_json(ARTIFACT_DIR / "topology.json")
        plan = load_json(ARTIFACT_DIR / "migration-plan.json")
        research = load_json(ARTIFACT_DIR / "research.json")

        hostnames = verify_sddc_architecture(sddc, snapshot)
        verify_topology(topology, inventory, snapshot, hostnames)
        verify_research(research)
        verify_plan(plan, inventory, snapshot, plan_schema)
        verify_stdlib_package()
        verify_generator_matches_committed_artifacts()
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: SddcSpec, stretched topology, research, migration plan, and stdlib generator verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
