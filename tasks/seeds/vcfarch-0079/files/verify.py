#!/usr/bin/env python3
"""Offline, deterministic verifier for the VCF migration architecture."""

from __future__ import annotations

import ast
import copy
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
OPENAPI_PATH = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
PLAN_SCHEMA_PATH = ROOT / "migration-plan.schema.json"
INVENTORY_PATH = ROOT / "estate_inventory.json"
SNAPSHOT_PATH = ROOT / "compatibility_snapshot.json"
RESEARCH_PATH = ROOT / "research-sources.md"
PACKAGE_PATH = ROOT / "vcf_architecture"

# These protect the grading inputs. They are filled from the shipped files.
PROTECTED_SHA256 = {
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    "migration-plan.schema.json": "0ebc9d388d914c4d35d07d5d3d1531686b716a254bd90f93b4e5384686c656db",
    "estate_inventory.json": "5f2a1bf3379ad6c0cc09a872febaaea2bc7175ab9591a6225fe05e1148faa1e4",
    "compatibility_snapshot.json": "4ca23b03eed7bd9710e333dc6923a46b667a563f2788389791a679d6acd929b6",
}


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        # A missing submission artifact is an expected baseline test failure,
        # not evidence that the verifier's executable toolchain is absent.
        raise VerificationError(f"required JSON artifact is missing: {path.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read JSON {path.name}: {exc}") from exc


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(131072), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_research_record() -> None:
    try:
        text = RESEARCH_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise VerificationError(f"cannot read research-sources.md: {exc}") from exc

    access_dates = re.findall(r"\bAccessed\s+(\d{4}-\d{2}-\d{2})\b", text, flags=re.IGNORECASE)
    require(access_dates, "research-sources.md must record an Accessed YYYY-MM-DD date")
    for value in access_dates:
        try:
            dt.date.fromisoformat(value)
        except ValueError as exc:
            raise VerificationError(f"invalid research access date: {value}") from exc

    bullets = [line.strip() for line in text.splitlines() if line.lstrip().startswith("- ")]
    require(len(bullets) >= 2, "research-sources.md must contain at least two live-source entries")
    has_global_date = any(
        not line.lstrip().startswith("- ") and re.search(r"\bAccessed\s+\d{4}-\d{2}-\d{2}\b", line, re.IGNORECASE)
        for line in text.splitlines()
    )
    for index, bullet in enumerate(bullets, start=1):
        urls = re.findall(r"https://[^\s)>]+", bullet)
        require(len(urls) == 1, f"research source {index} must contain exactly one HTTPS URL")
        url = urls[0].rstrip(".,;:")
        url_position = bullet.index(urls[0])
        require(":" in bullet[2:url_position], f"research source {index} must name the source before its URL")
        suffix = bullet[url_position + len(urls[0]) :]
        require(suffix.startswith(" — "), f"research source {index} must put its informed decision after an em dash")
        if not has_global_date:
            require(
                re.search(r"\bAccessed\s+\d{4}-\d{2}-\d{2}\b", bullet, re.IGNORECASE) is not None,
                f"research source {index} has no access date",
            )
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        require(
            hostname == "broadcom.com"
            or hostname.endswith(".broadcom.com")
            or hostname == "vmware.com"
            or hostname.endswith(".vmware.com"),
            f"research source {index} is not an official Broadcom or VMware source",
        )
        require(
            hostname not in {"localhost", "127.0.0.1"}
            and not hostname.endswith(".invalid"),
            f"research source {index} is not a genuine live-source URL",
        )
        decision = suffix.removeprefix(" — ").strip()
        require(len(decision) >= 20, f"research source {index} must state the decision it informed")

    lowered = text.lower()
    for topic in ("compatib", "interop", "upgrad", "document"):
        require(topic in lowered, f"research-sources.md does not cover required {topic} research")
    require("snapshot" in lowered, "research-sources.md must compare the live evidence with the frozen snapshot")
    require(
        "conflict" in lowered or "discrep" in lowered,
        "research-sources.md must state whether live evidence conflicted with the frozen snapshot",
    )


def verify_package_sources() -> None:
    require(PACKAGE_PATH.is_dir() and not PACKAGE_PATH.is_symlink(), "vcf_architecture must be a package directory")
    required = {PACKAGE_PATH / "__init__.py", PACKAGE_PATH / "__main__.py"}
    require(all(path.is_file() for path in required), "vcf_architecture must contain __init__.py and __main__.py")
    sources = sorted(PACKAGE_PATH.rglob("*.py"))
    require(sources, "vcf_architecture does not contain Python source files")
    allowed_roots = set(sys.stdlib_module_names) | {"vcf_architecture"}
    for source in sources:
        require(not source.is_symlink(), f"package source must not be a symlink: {source.relative_to(ROOT)}")
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source.relative_to(ROOT)))
        except (OSError, SyntaxError) as exc:
            raise VerificationError(f"cannot parse package source {source.relative_to(ROOT)}: {exc}") from exc
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = [node.module.split(".", 1)[0]]
            for root_name in imported:
                require(
                    root_name in allowed_roots,
                    f"non-standard-library import {root_name!r} in {source.relative_to(ROOT)}",
                )


def run_generator(inventory_path: Path, snapshot_path: Path, output_path: Path) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    command = [
        sys.executable,
        "-B",
        "-m",
        "vcf_architecture",
        "--inventory",
        str(inventory_path),
        "--compatibility",
        str(snapshot_path),
        "--output",
        str(output_path),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerificationError(f"cannot execute vcf_architecture: {exc}") from exc
    detail = (completed.stderr or completed.stdout).strip()
    require(
        completed.returncode == 0,
        f"vcf_architecture exited with {completed.returncode}: {detail[:500]}",
    )
    require(output_path.is_file(), "vcf_architecture did not create the requested output file")
    return load_json(output_path)


def json_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        raise VerificationError(f"unsupported non-local schema reference: {pointer}")
    value = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise VerificationError(f"unresolvable schema reference: {pointer}") from exc
    return value


class SchemaValidator:
    """The deterministic JSON Schema/OpenAPI subset used by the shipped schemas."""

    def __init__(self, document: dict[str, Any]):
        self.document = document

    def validate(self, instance: Any, schema: Any, path: str = "$") -> list[str]:
        errors: list[str] = []
        if isinstance(schema, bool):
            return [] if schema else [f"{path}: schema rejects all values"]
        if not isinstance(schema, dict):
            return [f"{path}: invalid schema node"]

        if "$ref" in schema:
            return self.validate(instance, json_pointer(self.document, schema["$ref"]), path)

        if "allOf" in schema:
            for child in schema["allOf"]:
                errors.extend(self.validate(instance, child, path))
        if "anyOf" in schema:
            branches = [self.validate(instance, child, path) for child in schema["anyOf"]]
            if all(branch for branch in branches):
                errors.append(f"{path}: does not match any allowed schema")
        if "oneOf" in schema:
            matches = sum(not self.validate(instance, child, path) for child in schema["oneOf"])
            if matches != 1:
                errors.append(f"{path}: must match exactly one allowed schema (matched {matches})")
        if "not" in schema and not self.validate(instance, schema["not"], path):
            errors.append(f"{path}: matches a forbidden schema")

        if instance is None and schema.get("nullable") is True:
            return errors

        expected_type = schema.get("type")
        if expected_type is not None:
            allowed = expected_type if isinstance(expected_type, list) else [expected_type]
            if not any(self._is_type(instance, candidate) for candidate in allowed):
                errors.append(f"{path}: expected type {expected_type}, got {type(instance).__name__}")
                return errors

        if "const" in schema and instance != schema["const"]:
            errors.append(f"{path}: expected constant {schema['const']!r}")
        if "enum" in schema and instance not in schema["enum"]:
            errors.append(f"{path}: {instance!r} is not in the allowed values")

        if isinstance(instance, str):
            if len(instance) < schema.get("minLength", 0):
                errors.append(f"{path}: string is shorter than minLength")
            if "maxLength" in schema and len(instance) > schema["maxLength"]:
                errors.append(f"{path}: string is longer than maxLength")
            if "pattern" in schema:
                try:
                    matched = re.search(schema["pattern"], instance)
                except re.error as exc:
                    raise VerificationError(f"invalid schema regex at {path}: {exc}") from exc
                if matched is None:
                    errors.append(f"{path}: string does not match {schema['pattern']!r}")

        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                errors.append(f"{path}: number is below minimum")
            if "maximum" in schema and instance > schema["maximum"]:
                errors.append(f"{path}: number is above maximum")
            if "exclusiveMinimum" in schema:
                limit = schema["exclusiveMinimum"]
                if isinstance(limit, (int, float)) and not isinstance(limit, bool) and instance <= limit:
                    errors.append(f"{path}: number is not above exclusiveMinimum")
            if "exclusiveMaximum" in schema:
                limit = schema["exclusiveMaximum"]
                if isinstance(limit, (int, float)) and not isinstance(limit, bool) and instance >= limit:
                    errors.append(f"{path}: number is not below exclusiveMaximum")

        if isinstance(instance, list):
            if len(instance) < schema.get("minItems", 0):
                errors.append(f"{path}: array has fewer than minItems")
            if "maxItems" in schema and len(instance) > schema["maxItems"]:
                errors.append(f"{path}: array has more than maxItems")
            if schema.get("uniqueItems"):
                canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
                if len(canonical) != len(set(canonical)):
                    errors.append(f"{path}: array items are not unique")
            if "items" in schema:
                for index, value in enumerate(instance):
                    errors.extend(self.validate(value, schema["items"], f"{path}[{index}]"))

        if isinstance(instance, dict):
            required = schema.get("required", [])
            for name in required:
                if name not in instance:
                    errors.append(f"{path}: missing required property {name!r}")
            properties = schema.get("properties", {})
            for name, value in instance.items():
                if name in properties:
                    errors.extend(self.validate(value, properties[name], f"{path}.{name}"))
                elif schema.get("additionalProperties") is False:
                    errors.append(f"{path}: unexpected property {name!r}")
                elif isinstance(schema.get("additionalProperties"), dict):
                    errors.extend(
                        self.validate(value, schema["additionalProperties"], f"{path}.{name}")
                    )
            if len(instance) < schema.get("minProperties", 0):
                errors.append(f"{path}: object has fewer than minProperties")
            if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
                errors.append(f"{path}: object has more than maxProperties")

        return errors

    @staticmethod
    def _is_type(value: Any, expected: str) -> bool:
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
        return False


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def require_schema(instance: Any, document: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    errors = SchemaValidator(document).validate(instance, schema)
    if errors:
        detail = "\n  - ".join(errors[:30])
        raise VerificationError(f"{label} validation failed:\n  - {detail}")


def step_matches(step: dict[str, Any], selector: dict[str, str], components: dict[str, dict[str, Any]]) -> bool:
    if "componentId" in selector and step["componentId"] != selector["componentId"]:
        return False
    if "componentKind" in selector:
        if components[step["componentId"]]["kind"] != selector["componentKind"]:
            return False
    if "toVersion" in selector and step["toVersion"] != selector["toVersion"]:
        return False
    return True


def verify_target_spec(spec: dict[str, Any], inventory: dict[str, Any]) -> None:
    require(spec.get("sddcId") == inventory["site"]["sddcId"], "targetSddcSpec.sddcId mismatch")
    require(spec.get("version") == inventory["targetVcfVersion"], "targetSddcSpec.version mismatch")
    require(spec.get("workflowType") == "VCF", "targetSddcSpec.workflowType must be VCF")
    require(spec.get("dnsSpec", {}).get("subdomain") == inventory["site"]["dnsSubdomain"], "DNS subdomain mismatch")
    require(
        spec.get("dnsSpec", {}).get("nameservers") == inventory["site"]["dnsServers"],
        "DNS server order/content mismatch",
    )
    require(spec.get("ntpServers") == inventory["site"]["ntpServers"], "NTP server mismatch")
    require(spec.get("vcenterSpec", {}).get("useExistingDeployment") is True, "existing vCenter must be retained")
    require(spec.get("nsxtSpec", {}).get("useExistingDeployment") is True, "existing NSX must be retained")
    require(spec.get("nsxtSpec", {}).get("version") == inventory["targetVcfVersion"], "NSX target version mismatch")

    expected_hosts = {
        component["id"] for component in inventory["components"] if component["kind"] == "ESX_HOST"
    }
    actual_hosts = {host.get("hostname") for host in spec.get("hostSpecs", [])}
    require(actual_hosts == expected_hosts, "targetSddcSpec.hostSpecs must name every inventoried ESX host")

    expected_networks = {
        network["networkType"]: (
            network["vlanId"],
            network["subnet"],
            network["gateway"],
            network["mtu"],
        )
        for network in inventory["networks"]
    }
    actual_networks = {
        network.get("networkType"): (
            network.get("vlanId"),
            network.get("subnet"),
            network.get("gateway"),
            network.get("mtu"),
        )
        for network in spec.get("networkSpecs", [])
    }
    require(actual_networks == expected_networks, "targetSddcSpec.networkSpecs do not match the inventory")

    host_vds = inventory["managementCluster"]["hostVds"]
    matching_vds = [item for item in spec.get("dvsSpecs", []) if item.get("dvsName") == host_vds["name"]]
    require(len(matching_vds) == 1, "targetSddcSpec must contain the inventoried management VDS exactly once")
    actual_mapping = {item.get("id"): item.get("uplink") for item in matching_vds[0]["vmnicsToUplinks"]}
    require(actual_mapping == host_vds["vmnicToUplink"], "management VDS vmnic mapping mismatch")


def verify_architecture(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    architecture = plan["architecture"]
    for key, expected in snapshot["targetArchitecture"].items():
        require(architecture.get(key) == expected, f"architecture.{key} must be {expected!r}")

    edge = architecture["edgeCluster"]
    sizing = snapshot["edgeSizing"]
    requirement = inventory["edgeRequirement"]
    form_factor = edge["formFactor"]
    expected_capacity = sizing["perActiveNodeCapacityGbps"][form_factor]
    require(form_factor == sizing["requiredFormFactor"], "Edge form factor does not meet the pinned sizing decision")
    require(edge["nodeCount"] == sizing["nodeCount"], "Edge node count mismatch")
    require(edge["haMode"] == sizing["haMode"], "Edge HA mode mismatch")
    require(edge["tier0Mode"] == sizing["tier0Mode"], "Tier-0 mode mismatch")
    require(edge["northboundRouting"] == sizing["northboundRouting"], "northbound routing mismatch")
    require(edge["perActiveNodeCapacityGbps"] == expected_capacity, "per-node Edge capacity mismatch")
    require(
        edge["normalCapacityGbps"] == expected_capacity * edge["nodeCount"],
        "normal Edge capacity arithmetic is incorrect",
    )
    surviving = expected_capacity * (edge["nodeCount"] - 1)
    require(edge["capacityAfterSingleNodeFailureGbps"] == surviving, "single-failure capacity arithmetic is incorrect")
    require(surviving >= requirement["requiredNorthSouthGbps"], "Edge cluster misses required throughput after one node fails")

    uplinks = edge["uplinks"]
    expected_uplinks = {
        (
            node["componentId"],
            link["edgeInterface"],
            link["hostPnic"],
            link["tor"],
            link["speedGbps"],
            link["vlanId"],
        )
        for node in inventory["edgeNodes"]
        for link in node["availableUplinks"]
    }
    actual_uplinks = {
        (
            link["edgeNode"],
            link["edgeInterface"],
            link["hostPnic"],
            link["tor"],
            link["speedGbps"],
            link["vlanId"],
        )
        for link in uplinks
    }
    require(actual_uplinks == expected_uplinks, "Edge uplink layout must use exactly the available independent paths")
    for node in inventory["edgeNodes"]:
        links = [link for link in uplinks if link["edgeNode"] == node["componentId"]]
        require(len(links) == sizing["requiredUplinksPerNode"], f"wrong uplink count for {node['componentId']}")
        require(
            len({link["tor"] for link in links}) == sizing["requiredDistinctTorsPerNode"],
            f"uplinks for {node['componentId']} do not span independent ToRs",
        )
        require(
            len({link["hostPnic"] for link in links}) == sizing["requiredUplinksPerNode"],
            f"uplinks for {node['componentId']} do not use independent pNICs",
        )
        require(
            all(link["speedGbps"] >= sizing["requiredUplinkSpeedGbps"] for link in links),
            f"uplink speed is insufficient for {node['componentId']}",
        )


def verify_plan(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    require(plan["estateId"] == inventory["estateId"], "estateId mismatch")
    require(plan["currentVcfVersion"] == inventory["currentVcfVersion"], "current VCF version mismatch")
    require(plan["targetVcfVersion"] == inventory["targetVcfVersion"], "target VCF version mismatch")

    components = {component["id"]: component for component in inventory["components"]}
    require(set(snapshot["componentPaths"]) == set(components), "snapshot component coverage mismatch")

    targets = plan["componentTargets"]
    require(len(targets) == len(components), "componentTargets must contain every component exactly once")
    target_map = {target["componentId"]: target for target in targets}
    require(len(target_map) == len(targets), "duplicate componentTargets entry")
    require(set(target_map) == set(components), "componentTargets coverage mismatch")
    for component_id, component in components.items():
        target = target_map[component_id]
        require(target["componentName"] == component["name"], f"component name mismatch for {component_id}")
        require(target["currentVersion"] == component["currentVersion"], f"current version mismatch for {component_id}")
        require(target["targetVersion"] == component["targetVersion"], f"target version mismatch for {component_id}")

    gates = plan["gates"]
    gate_ids = [gate["id"] for gate in gates]
    require(len(gate_ids) == len(set(gate_ids)), "duplicate gate id")
    require(set(gate_ids) == set(snapshot["requiredGateIds"]), "gate set differs from the pinned required gates")

    steps = plan["steps"]
    orders = [step["order"] for step in steps]
    require(orders == list(range(1, len(steps) + 1)), "steps must be listed in contiguous execution order")
    step_ids = [step["id"] for step in steps]
    require(len(step_ids) == len(set(step_ids)), "duplicate step id")
    require(step_ids == [f"STEP-{order:03d}" for order in orders], "step ids must correspond to execution order")
    require(all(step["componentId"] in components for step in steps), "step refers to a non-inventoried component")
    for step in steps:
        component = components[step["componentId"]]
        require(step["componentName"] == component["name"], f"step component name mismatch for {step['id']}")
        require(set(step["gates"]).issubset(set(gate_ids)), f"unknown gate reference in {step['id']}")

    by_component: dict[str, list[dict[str, Any]]] = {component_id: [] for component_id in components}
    for step in steps:
        by_component[step["componentId"]].append(step)

    for component_id, component_steps in by_component.items():
        component = components[component_id]
        path = snapshot["componentPaths"][component_id]
        version_steps = [step for step in component_steps if step["fromVersion"] != step["toVersion"]]
        actual_pairs = [(step["fromVersion"], step["toVersion"]) for step in version_steps]
        expected_pairs = list(zip(path, path[1:]))
        require(actual_pairs == expected_pairs, f"unsupported or incomplete version chain for {component_id}: {actual_pairs!r}")
        require(path[0] == components[component_id]["currentVersion"], f"snapshot start mismatch for {component_id}")
        require(path[-1] == components[component_id]["targetVersion"], f"snapshot target mismatch for {component_id}")
        require(target_map[component_id]["finalGates"] == version_steps[-1]["gates"], f"final gates mismatch for {component_id}")
        for index, step in enumerate(version_steps, start=1):
            expected_milestone = "5.2.1.0" if index == 1 else "9.1.0.0"
            require(step["milestone"] == expected_milestone, f"wrong milestone for {step['id']}")
            expected_action = "DECOMMISSION" if step["toVersion"] == "DECOMMISSIONED" else "UPGRADE"
            require(step["action"] == expected_action, f"wrong action for {step['id']}")

            required_gates: set[str] = set()
            for rule in snapshot["transitionGateRules"]:
                if component["kind"] in rule["componentKinds"] and step["milestone"] == rule["milestone"]:
                    required_gates.update(rule["requiredGates"])
            require(required_gates.issubset(set(step["gates"])), f"missing required gate on {step['id']}")

    for edge_node in inventory["edgeNodes"]:
        resize_steps = [
            step
            for step in by_component[edge_node["componentId"]]
            if step["action"] == "RESIZE"
        ]
        require(len(resize_steps) == 1, f"{edge_node['componentId']} requires exactly one resize action")
        resize = resize_steps[0]
        require(resize["fromVersion"] == resize["toVersion"] == "4.2.1.0", "Edge resize must occur on the 5.2.1 NSX level")
        require(resize["milestone"] == "9.1.0.0", "Edge resize belongs to 9.1 preparation")
        require(
            resize.get("changes") == {
                "formFactor": {
                    "from": edge_node["currentFormFactor"],
                    "to": snapshot["edgeSizing"]["requiredFormFactor"],
                }
            },
            f"incorrect form-factor change for {edge_node['componentId']}",
        )
        require(
            {"GATE-VCF52-HEALTHY", "GATE-EDGE-CAPACITY"}.issubset(set(resize["gates"])),
            f"missing resize gates for {edge_node['componentId']}",
        )

    expected_vcf_pairs = list(zip(snapshot["vcfPath"], snapshot["vcfPath"][1:]))
    transitions = plan["vcfTransitions"]
    require(
        [(item["fromVersion"], item["toVersion"]) for item in transitions] == expected_vcf_pairs,
        "VCF platform path contains an unsupported or missing hop",
    )
    require([item["order"] for item in transitions] == list(range(1, len(transitions) + 1)), "VCF transition order is invalid")
    step_by_id = {step["id"]: step for step in steps}
    for transition in transitions:
        require(transition["completesAfterStepId"] in step_by_id, "VCF transition completion step does not exist")
        milestone_steps = [step for step in steps if step["milestone"] == transition["toVersion"]]
        require(milestone_steps, f"no component steps for VCF milestone {transition['toVersion']}")
        require(
            transition["completesAfterStepId"] == max(milestone_steps, key=lambda item: item["order"])["id"],
            f"VCF transition {transition['toVersion']} completes before its component work",
        )
        matching_rules = [
            rule
            for rule in snapshot["vcfTransitionGateRules"]
            if rule["fromVersion"] == transition["fromVersion"] and rule["toVersion"] == transition["toVersion"]
        ]
        require(len(matching_rules) == 1, "missing frozen VCF gate rule")
        require(
            set(matching_rules[0]["requiredGates"]).issubset(set(transition["gates"])),
            f"VCF transition {transition['toVersion']} is missing a required gate",
        )

    for constraint in snapshot["orderConstraints"]:
        before = [step for step in steps if step_matches(step, constraint["before"], components)]
        after = [step for step in steps if step_matches(step, constraint["after"], components)]
        require(before and after, f"order constraint selector did not resolve: {constraint!r}")
        require(
            max(step["order"] for step in before) < min(step["order"] for step in after),
            f"component ordering constraint violated: {constraint!r}",
        )

    verify_target_spec(plan["targetSddcSpec"], inventory)
    verify_architecture(plan, inventory, snapshot)


def verify_generator(
    submitted_plan: dict[str, Any],
    inventory: dict[str, Any],
    snapshot: dict[str, Any],
    openapi: dict[str, Any],
    plan_schema: dict[str, Any],
) -> None:
    verify_package_sources()
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as raw_temp:
        temp = Path(raw_temp)
        generated_path = temp / "generated-plan.json"
        generated = run_generator(INVENTORY_PATH, SNAPSHOT_PATH, generated_path)
        require(generated == submitted_plan, "migration-plan.json is not the plan produced by vcf_architecture")

        variant_inventory = copy.deepcopy(inventory)
        variant_inventory["estateId"] = "chi-retail-verifier-variant"
        variant_inventory["site"]["sddcId"] = "chi02-m01"
        variant_inventory["site"]["dnsSubdomain"] = "variant.corp.example"
        variant_inventory["site"]["dnsServers"] = ["10.30.0.10", "10.30.0.11"]
        variant_inventory["site"]["ntpServers"] = ["10.30.0.20", "10.30.0.21"]
        variant_inventory["managementCluster"]["name"] = "chi-m02-cl01"
        variant_inventory["managementCluster"]["datacenterName"] = "chi-m02-dc"
        variant_inventory["managementCluster"]["hostVds"]["name"] = "chi-m02-vds01"
        for component in variant_inventory["components"]:
            component["name"] = f"Variant {component['name']}"

        variant_snapshot = copy.deepcopy(snapshot)
        variant_snapshot["edgeSizing"]["perActiveNodeCapacityGbps"]["XLARGE"] = 21
        variant_inventory_path = temp / "estate-inventory-variant.json"
        variant_snapshot_path = temp / "compatibility-variant.json"
        variant_output_path = temp / "migration-plan-variant.json"
        variant_inventory_path.write_text(json.dumps(variant_inventory), encoding="utf-8")
        variant_snapshot_path.write_text(json.dumps(variant_snapshot), encoding="utf-8")

        variant = run_generator(variant_inventory_path, variant_snapshot_path, variant_output_path)
        sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
        require_schema(variant.get("targetSddcSpec"), openapi, sddc_schema, "variant installer SddcSpec")
        require_schema(variant, plan_schema, plan_schema, "variant migration plan schema")
        verify_plan(variant, variant_inventory, variant_snapshot)


def main(argv: list[str]) -> int:
    plan_path = Path(argv[1]) if len(argv) == 2 else ROOT / "migration-plan.json"
    if not plan_path.is_absolute():
        plan_path = ROOT / plan_path

    try:
        # Required first check: validate the embedded target architecture against
        # the installer specification's own SddcSpec schema before any other
        # artifact, fixture, compatibility, ordering, or research-related check.
        plan = load_json(plan_path)
        openapi = load_json(OPENAPI_PATH)
        target_spec = plan.get("targetSddcSpec") if isinstance(plan, dict) else None
        sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
        require_schema(target_spec, openapi, sddc_schema, "installer SddcSpec")

        plan_schema = load_json(PLAN_SCHEMA_PATH)
        require_schema(plan, plan_schema, plan_schema, "migration plan schema")

        for relative, expected in PROTECTED_SHA256.items():
            actual = sha256(ROOT / relative)
            require(actual == expected, f"protected grading input changed: {relative}")

        inventory = load_json(INVENTORY_PATH)
        snapshot = load_json(SNAPSHOT_PATH)
        verify_plan(plan, inventory, snapshot)
        verify_research_record()
        verify_generator(plan, inventory, snapshot, openapi, plan_schema)
    except (KeyError, TypeError, VerificationError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("PASS: migration-plan.json is schema-valid and satisfies the frozen VCF architecture constraints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
