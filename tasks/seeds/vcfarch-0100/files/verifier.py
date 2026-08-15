#!/usr/bin/env python3
"""Protected, offline verification for the VCF migration artifact."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def resolve_pointer(document: Any, pointer: str) -> Any:
    require(pointer.startswith("#/"), f"only local schema references are supported: {pointer}")
    value = document
    for token in pointer[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        require(isinstance(value, dict) and token in value, f"unresolved schema reference {pointer}")
        value = value[token]
    return value


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
    raise VerificationError(f"unsupported schema type {expected}")


def validate_schema(value: Any, schema: dict[str, Any], document: dict[str, Any], path: str) -> None:
    """Validate the JSON Schema/OpenAPI keywords used by the two shipped schemas."""
    if "$ref" in schema:
        validate_schema(value, resolve_pointer(document, schema["$ref"]), document, path)
        return

    if value is None and schema.get("nullable") is True:
        return

    for branch in schema.get("allOf", []):
        validate_schema(value, branch, document, path)

    if "anyOf" in schema:
        successes = 0
        for branch in schema["anyOf"]:
            try:
                validate_schema(value, branch, document, path)
                successes += 1
            except VerificationError:
                pass
        require(successes >= 1, f"{path} does not satisfy any allowed schema")

    if "oneOf" in schema:
        successes = 0
        for branch in schema["oneOf"]:
            try:
                validate_schema(value, branch, document, path)
                successes += 1
            except VerificationError:
                pass
        require(successes == 1, f"{path} must satisfy exactly one allowed schema")

    if "const" in schema:
        require(value == schema["const"], f"{path} must equal {schema['const']!r}")
    if "enum" in schema:
        require(value in schema["enum"], f"{path} is not an allowed value")

    expected_type = schema.get("type")
    if expected_type is not None:
        alternatives = expected_type if isinstance(expected_type, list) else [expected_type]
        require(any(type_matches(value, item) for item in alternatives),
                f"{path} must have type {expected_type}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        require(not missing, f"{path} is missing required properties {missing}")
        properties = schema.get("properties", {})
        for name, child in value.items():
            if name in properties:
                validate_schema(child, properties[name], document, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                raise VerificationError(f"{path} has unexpected property {name}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(child, schema["additionalProperties"], document,
                                f"{path}.{name}")
        if "minProperties" in schema:
            require(len(value) >= schema["minProperties"], f"{path} has too few properties")
        if "maxProperties" in schema:
            require(len(value) <= schema["maxProperties"], f"{path} has too many properties")

    if isinstance(value, list):
        if "minItems" in schema:
            require(len(value) >= schema["minItems"], f"{path} has too few items")
        if "maxItems" in schema:
            require(len(value) <= schema["maxItems"], f"{path} has too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            require(len(encoded) == len(set(encoded)), f"{path} items must be unique")
        if isinstance(schema.get("items"), dict):
            for index, child in enumerate(value):
                validate_schema(child, schema["items"], document, f"{path}[{index}]")

    if isinstance(value, str):
        if "minLength" in schema:
            require(len(value) >= schema["minLength"], f"{path} is too short")
        if "maxLength" in schema:
            require(len(value) <= schema["maxLength"], f"{path} is too long")
        if "pattern" in schema:
            require(re.search(schema["pattern"], value) is not None,
                    f"{path} does not match its schema pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema:
            require(value >= schema["minimum"], f"{path} is below its minimum")
        if "maximum" in schema:
            require(value <= schema["maximum"], f"{path} is above its maximum")


def run_client() -> Any:
    build = ROOT / ".sandbox-home" / "build"
    build.mkdir(parents=True, exist_ok=True)
    compiled = subprocess.run(
        ["javac", "-d", str(build), "MigrationPlanClient.java", "TestMain.java"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    require(compiled.returncode == 0, "Java compilation failed:\n" + compiled.stderr[-2000:])
    executed = subprocess.run(
        ["java", "-cp", str(build), "TestMain"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    require(executed.returncode == 0, "TestMain failed:\n" + executed.stderr[-2000:])
    try:
        artifact = json.loads(executed.stdout)
    except json.JSONDecodeError as error:
        raise VerificationError(f"client output is not one JSON value: {error}") from error
    return artifact


def version_matches(value: str, pattern: str) -> bool:
    expression = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
    return re.fullmatch(expression, value) is not None


def verify_research_log() -> None:
    research = (ROOT / "research-consulted.md").read_text()
    require("replace this placeholder" not in research.lower(),
            "research-consulted.md still contains its placeholder")
    consulted_dates = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", research))
    require(consulted_dates, "research-consulted.md is missing a consultation date")
    for consulted in consulted_dates:
        try:
            date.fromisoformat(consulted)
        except ValueError as error:
            raise VerificationError(
                f"invalid research consultation date {consulted}"
            ) from error

    url_pattern = re.compile(r"https://[^\s)>|]+")
    entries: dict[str, str] = {}
    paragraphs = re.split(r"\n\s*\n", research)
    for match in url_pattern.finditer(research):
        url = match.group(0).rstrip(".,;:")
        context = next((paragraph for paragraph in paragraphs if url in paragraph), "")
        entries[url] = context

    require(len(entries) >= 3,
            "research-consulted.md must contain at least three live source URLs")
    hosts: set[str] = set()
    has_interop_or_upgrade_path = False
    has_vcf_91_guidance = False
    for url, context in entries.items():
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        require(parsed.scheme == "https" and "." in host and
                host != "localhost" and not host.endswith(".invalid"),
                "research sources must use plausible reachable HTTPS URLs")
        hosts.add(host)
        description = url_pattern.sub("", context)
        description = re.sub(r"[#*_|`—-]+", " ", description)
        words = re.findall(r"[A-Za-z0-9.]+", description)
        require(len(words) >= 10,
                f"research source {url} is missing its title or fact used")
        combined = context.lower()
        if (host == "interopmatrix.broadcom.com" or "interoperab" in combined or
                "upgrade path" in combined or "upgrade-path" in combined):
            has_interop_or_upgrade_path = True
        if "9.1" in combined and ("vcf" in combined or "cloud foundation" in combined):
            has_vcf_91_guidance = True

    require("compatibilityguide.broadcom.com" in hosts,
            "research is missing the published Broadcom Compatibility Guide")
    require(has_interop_or_upgrade_path,
            "research is missing Broadcom interoperability/upgrade-path material")
    require(has_vcf_91_guidance,
            "research is missing relevant Broadcom VCF 9.1 upgrade guidance")


def assert_target_sddc_values(artifact: dict[str, Any], inventory: dict[str, Any],
                              snapshot: dict[str, Any]) -> None:
    spec = artifact["targetSddcSpec"]
    identity = inventory["managementIdentity"]
    target = snapshot["targetVcfVersion"]
    require(spec.get("sddcId") == identity["sddcId"], "targetSddcSpec.sddcId changed")
    require(spec.get("workflowType") == "VCF", "existing-estate workflowType must be VCF")
    require(spec.get("version") == target, "targetSddcSpec version is not the pinned target")
    require(spec.get("vcfInstanceName") == identity["vcfInstanceName"],
            "targetSddcSpec.vcfInstanceName changed")
    dns = spec.get("dnsSpec", {})
    require(dns.get("subdomain") == identity["dnsSubdomain"] and
            dns.get("nameservers") == identity["nameservers"],
            "targetSddcSpec DNS values differ from inventory")
    require(identity["managementNetwork"] in spec.get("networkSpecs", []),
            "targetSddcSpec does not preserve the inventory management network")
    vcenter = spec.get("vcenterSpec", {})
    require(vcenter.get("vcenterHostname") == identity["vcenterHostname"],
            "targetSddcSpec vCenter hostname changed")
    require(vcenter.get("rootVcenterPassword") == identity["rootVcenterPassword"],
            "targetSddcSpec vCenter fixture credential changed")
    require(vcenter.get("sslThumbprint") == identity["sslThumbprint"],
            "targetSddcSpec vCenter thumbprint changed")
    require(vcenter.get("useExistingDeployment") is True,
            "targetSddcSpec must convert the existing vCenter")
    transitions = {item["componentType"]: item for item in snapshot["componentTransitions"]}
    require(vcenter.get("version") == transitions["VCENTER"]["targetVersion"],
            "targetSddcSpec vCenter version is not the pinned target")
    license_spec = spec.get("licenseServerSpec", {})
    require(license_spec.get("hostname") == inventory["entitlement"]["licenseServerFqdn"] and
            license_spec.get("version") == transitions["LICENSE_SERVER"]["targetVersion"] and
            license_spec.get("useExistingDeployment") is False,
            "targetSddcSpec does not deploy the mandatory license server")


def verify_plan(artifact: dict[str, Any], inventory: dict[str, Any],
                snapshot: dict[str, Any]) -> None:
    require(artifact["estateId"] == inventory["estateId"], "estateId changed")
    require(artifact["sourceVcfVersion"] == inventory["currentVcfVersion"],
            "source VCF version changed")
    require(artifact["targetVcfVersion"] == inventory["requestedTargetVcfVersion"],
            "requested target VCF version changed")
    require(artifact["targetVcfVersion"] == snapshot["targetVcfVersion"],
            "target is not the pinned compatibility target")
    supported_edge = any(
        edge == {
            "from": artifact["sourceVcfVersion"],
            "to": artifact["targetVcfVersion"],
            "direct": True,
        }
        for edge in snapshot["supportedVcfUpgradeEdges"]
    )
    require(supported_edge, "the plan does not use a supported direct VCF edge")

    entitlement = inventory["entitlement"]["entitledCores"]
    eligible = [
        option for option in inventory["topologyOptions"]
        if option["supportedByTarget"]
        and option["evacuationFeasible"]
        and option["physicalCores"] <= entitlement
    ]
    require(len(eligible) == 1, "fixture does not yield one deterministic entitled topology")
    selected = eligible[0]
    architecture = artifact["architecture"]
    require(architecture["selectedTopology"] == selected["id"] and
            architecture["licensedPhysicalCores"] == selected["physicalCores"] and
            set(architecture["retainedClusters"]) == set(selected["retainClusterIds"]) and
            set(architecture["retiredClusters"]) == set(selected["retireClusterIds"]) and
            architecture["licenseServer"] == inventory["entitlement"]["licenseServerFqdn"],
            "architecture does not select the sole compatible, entitled topology")

    steps = artifact["steps"]
    require([step["order"] for step in steps] == list(range(1, len(steps) + 1)),
            "step order must be consecutive and array ordered")
    by_id = {step["componentId"]: step for step in steps}
    require(len(by_id) == len(steps), "componentId must be unique")
    components = {item["id"]: item for item in inventory["components"]}
    require(set(by_id) == set(components), "steps must cover every inventory component exactly once")
    order = {component_id: step["order"] for component_id, step in by_id.items()}
    known_gates = {gate["id"] for gate in snapshot["gateDefinitions"]}
    transitions = {item["componentType"]: item for item in snapshot["componentTransitions"]}

    required_dependencies: dict[str, set[str]] = {component_id: set() for component_id in by_id}
    required_gates: dict[str, set[str]] = {component_id: set() for component_id in by_id}
    retired_cluster_ids = set(selected["retireClusterIds"])

    for component_id, component in components.items():
        step = by_id[component_id]
        require(step["componentType"] == component["type"], f"{component_id} type changed")
        require(step["scope"] == component["scope"], f"{component_id} scope changed")
        require(step["fromVersion"] == component["currentVersion"],
                f"{component_id} current version changed")
        if component.get("clusterId") in retired_cluster_ids:
            expected_target = "DECOMMISSIONED"
            expected_action = "RETIRE"
            required_gates[component_id].update({"entitlement-core-limit", "evacuation-feasible"})
        else:
            require(component["type"] in transitions,
                    f"no pinned transition for {component['type']}")
            transition = transitions[component["type"]]
            require(version_matches(component["currentVersion"], transition["sourcePattern"]),
                    f"{component_id} source is outside its pinned transition")
            expected_target = transition["targetVersion"]
            expected_action = transition["action"]
            required_gates[component_id].add(transition["gateId"])
        require(step["targetVersion"] == expected_target,
                f"{component_id} target is not pinned")
        require(step["action"] == expected_action, f"{component_id} action is incorrect")
        require(set(step["gates"]) <= known_gates, f"{component_id} names an unknown gate")

    retired_component_ids = [
        component_id for component_id, component in components.items()
        if component.get("clusterId") in retired_cluster_ids
    ]
    require(retired_component_ids, "selected topology retires no inventory component")

    by_type: dict[str, list[str]] = {}
    for component_id, component in components.items():
        by_type.setdefault(component["type"], []).append(component_id)

    for rule in snapshot["sequenceRules"]:
        for successor in by_type.get(rule["successorType"], []):
            for predecessor in by_type.get(rule["predecessorType"], []):
                required_dependencies[successor].add(predecessor)
                required_gates[successor].add(rule["gateId"])

    for rule in snapshot["sameScopeRules"]:
        for successor in by_type.get(rule["successorType"], []):
            if by_id[successor]["action"] == "RETIRE":
                continue
            scope = components[successor]["scope"]
            for predecessor in by_type.get(rule["predecessorType"], []):
                if components[predecessor]["scope"] == scope:
                    required_dependencies[successor].add(predecessor)
                    required_gates[successor].add(rule["gateId"])

    for rule in snapshot["scopeRules"]:
        predecessors = [
            component_id for component_id, component in components.items()
            if component["scope"] == rule["predecessorScope"]
            and component["type"] in rule["componentTypes"]
            and by_id[component_id]["action"] != "RETIRE"
        ]
        successors = [
            component_id for component_id, component in components.items()
            if component["scope"] == rule["successorScope"]
            and component["type"] in rule["componentTypes"]
            and by_id[component_id]["action"] != "RETIRE"
        ]
        for successor in successors:
            required_dependencies[successor].update(predecessors)
            required_gates[successor].add(rule["gateId"])

    def dependency_ancestors(component_id: str) -> set[str]:
        ancestors: set[str] = set()
        pending = list(by_id[component_id]["dependsOn"])
        while pending:
            dependency = pending.pop()
            if dependency not in ancestors:
                ancestors.add(dependency)
                pending.extend(by_id[dependency]["dependsOn"])
        return ancestors

    for component_id, step in by_id.items():
        dependencies = set(step["dependsOn"])
        require(dependencies <= set(by_id), f"{component_id} has an unknown dependency")
        require(all(order[dependency] < order[component_id] for dependency in dependencies),
                f"{component_id} has a dependency that is not earlier")
        require(required_dependencies[component_id] <= dependency_ancestors(component_id),
                f"{component_id} does not depend on all pinned ordering predecessors")
        require(set(step["gates"]) == required_gates[component_id],
                f"{component_id} does not name exactly its applicable pinned gates")


def main() -> int:
    try:
        artifact = run_client()

        # Required first artifact check: validate the embedded target against the
        # installer specification's own SddcSpec before loading grading fixtures.
        openapi = json.loads((ROOT / "specifications" / "vcf-installer" /
                              "vcf-installer-openapi.json").read_text())
        sddc_schema = resolve_pointer(openapi, "#/components/schemas/SddcSpec")
        target_sddc = artifact.get("targetSddcSpec") if isinstance(artifact, dict) else None
        validate_schema(target_sddc, sddc_schema, openapi,
                        "$.targetSddcSpec")

        plan_schema = json.loads((ROOT / "schemas" / "migration-plan.schema.json").read_text())
        validate_schema(artifact, plan_schema, plan_schema, "$")
        inventory = json.loads((ROOT / "fixtures" / "estate-inventory.json").read_text())
        snapshot = json.loads((ROOT / "fixtures" / "compatibility-snapshot.json").read_text())
        assert_target_sddc_values(artifact, inventory, snapshot)
        verify_plan(artifact, inventory, snapshot)
        verify_research_log()
    except (OSError, subprocess.TimeoutExpired, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: official SddcSpec, migration schema, inventory, compatibility, ordering, entitlement, and research log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
