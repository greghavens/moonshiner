#!/usr/bin/env python3
"""Offline acceptance verifier for the VCF architecture artifact.

The first acceptance operation is validation of artifact.greenfield against
the pinned installer's own SddcSpec schema. This program checks the research
log structurally but performs no network access.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]


class VerificationError(Exception):
    pass


def read_json(relative: str) -> Any:
    path = ROOT / relative
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise VerificationError(f"missing {relative}") from error
    except json.JSONDecodeError as error:
        raise VerificationError(f"invalid JSON in {relative}: {error}") from error


def read_text(relative: str) -> str:
    path = ROOT / relative
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise VerificationError(f"missing {relative}") from error
    except UnicodeDecodeError as error:
        raise VerificationError(f"invalid UTF-8 in {relative}") from error


def resolve_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("#/"):
        raise VerificationError(f"only local schema references are supported: {pointer}")
    value = document
    for raw in pointer[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            value = value[token]
        except (KeyError, TypeError) as error:
            raise VerificationError(f"unresolved schema reference {pointer}") from error
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
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise VerificationError(f"unsupported schema type {expected!r}")


def validate_schema(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "$") -> None:
    if "$ref" in schema:
        validate_schema(value, resolve_pointer(root, schema["$ref"]), root, path)
        return

    if value is None and schema.get("nullable") is True:
        return
    if "const" in schema and value != schema["const"]:
        raise VerificationError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise VerificationError(f"{path}: {value!r} is not in {schema['enum']!r}")

    for branch in schema.get("allOf", []):
        validate_schema(value, branch, root, path)
    if "anyOf" in schema:
        matches = 0
        for branch in schema["anyOf"]:
            try:
                validate_schema(value, branch, root, path)
                matches += 1
            except VerificationError:
                pass
        if matches == 0:
            raise VerificationError(f"{path}: does not match any anyOf branch")
    if "oneOf" in schema:
        matches = 0
        for branch in schema["oneOf"]:
            try:
                validate_schema(value, branch, root, path)
                matches += 1
            except VerificationError:
                pass
        if matches != 1:
            raise VerificationError(f"{path}: matches {matches} oneOf branches, expected one")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(type_matches(value, item) for item in expected_type):
            raise VerificationError(f"{path}: expected one of types {expected_type!r}")
    elif expected_type and not type_matches(value, expected_type):
        raise VerificationError(f"{path}: expected {expected_type}, got {type(value).__name__}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise VerificationError(f"{path}: missing required properties {missing!r}")
        if len(value) < schema.get("minProperties", 0):
            raise VerificationError(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise VerificationError(f"{path}: too many properties")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for name, item in value.items():
            child_path = f"{path}.{name}"
            if name in properties:
                validate_schema(item, properties[name], root, child_path)
            elif additional is False:
                raise VerificationError(f"{child_path}: additional property is not allowed")
            elif isinstance(additional, dict):
                validate_schema(item, additional, root, child_path)

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise VerificationError(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise VerificationError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                raise VerificationError(f"{path}: items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_schema(item, item_schema, root, f"{path}[{index}]")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise VerificationError(f"{path}: string is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise VerificationError(f"{path}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise VerificationError(f"{path}: does not match pattern {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise VerificationError(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise VerificationError(f"{path}: above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise VerificationError(f"{path}: not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            raise VerificationError(f"{path}: not below exclusiveMaximum")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def check_greenfield(artifact: dict[str, Any], estate: dict[str, Any], spec: dict[str, Any]) -> None:
    require(spec.get("info", {}).get("version") == "9.1.0.0", "installer specification version is not 9.1.0.0")
    require(set(artifact) == {"schemaVersion", "greenfield", "edgeDesign", "migrationPlan"}, "artifact top-level keys differ from the fixed contract")
    require(artifact["schemaVersion"] == "1.0", "artifact schemaVersion must be 1.0")

    actual = artifact["greenfield"]
    wanted = estate["greenfield"]
    require(actual.get("sddcId") == wanted["sddcId"], "greenfield sddcId does not match fixture")
    require(actual.get("workflowType") == wanted["workflowType"], "greenfield workflowType does not match fixture")
    require(actual.get("version") == wanted["version"], "greenfield version does not match fixture")
    require(actual.get("managementPoolName") == wanted["managementPoolName"], "management pool does not match fixture")
    require([item.get("hostname") for item in actual.get("hostSpecs", [])] == wanted["hosts"], "greenfield hosts do not exactly match fixture")

    vcenter = actual.get("vcenterSpec", {})
    expected_vcenter = wanted["vcenter"]
    require(vcenter == {
        "vcenterHostname": expected_vcenter["hostname"],
        "rootVcenterPassword": expected_vcenter["rootPassword"],
        "vmSize": expected_vcenter["vmSize"],
        "storageSize": expected_vcenter["storageSize"],
        "ssoDomain": expected_vcenter["ssoDomain"],
    }, "greenfield vCenter spec does not match fixture")
    require(actual.get("clusterSpec") == wanted["cluster"], "greenfield cluster spec does not match fixture")
    require(actual.get("dnsSpec") == wanted["dns"], "greenfield DNS spec does not match fixture")
    require(actual.get("ntpServers") == wanted["ntpServers"], "greenfield NTP servers do not match fixture")

    expected_nsx = wanted["nsx"]
    require(actual.get("nsxtSpec") == {
        "vipFqdn": expected_nsx["vipFqdn"],
        "nsxtManagerSize": expected_nsx["managerSize"],
        "transportVlanId": expected_nsx["transportVlanId"],
        "nsxtManagers": [{"hostname": hostname} for hostname in expected_nsx["managers"]],
    }, "greenfield NSX spec does not match fixture")

    expected_dvs = wanted["dvs"]
    expected_network_types = [network["networkType"] for network in wanted["networks"]]
    require(actual.get("dvsSpecs") == [{
        "dvsName": expected_dvs["name"],
        "mtu": expected_dvs["mtu"],
        "networks": expected_network_types,
        "vmnicsToUplinks": expected_dvs["vmnicsToUplinks"],
    }], "greenfield DVS/uplink spec does not match fixture")
    require(actual.get("networkSpecs") == wanted["networks"], "greenfield network specs do not exactly match fixture")


def check_migration(artifact: dict[str, Any], estate: dict[str, Any], snapshot: dict[str, Any], plan_schema: dict[str, Any]) -> None:
    plan = artifact["migrationPlan"]
    validate_schema(plan, plan_schema, plan_schema, "$.migrationPlan")
    require(plan["estateId"] == estate["estateId"], "migration estateId does not match inventory")
    require(plan["fleetId"] == estate["fleetId"], "migration fleetId does not match inventory")
    require(plan["fleetTarget"] == estate["desiredFleetVersion"] == snapshot["fleetTarget"], "migration fleet target does not match fixture and snapshot")

    components = {item["id"]: item for item in estate["components"]}
    steps = plan["steps"]
    require(len(steps) == len(components), "migration plan must contain exactly one step per inventory component")
    require([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "migration orders must be contiguous and array-ordered")
    require(len({step["componentId"] for step in steps}) == len(steps), "migration contains duplicate component IDs")
    require({step["componentId"] for step in steps} == set(components), "migration component IDs do not exactly cover inventory")

    declared_gates = {gate["id"] for gate in snapshot["gates"]}
    positions: dict[str, int] = {}
    for step in steps:
        component = components[step["componentId"]]
        positions[step["componentId"]] = step["order"]
        matches = [path for path in snapshot["supportedPaths"]
                   if path["componentType"] == component["type"]
                   and path["fromVersion"] == component["version"]]
        require(len(matches) == 1, f"no unique pinned compatibility path for {component['id']}")
        supported = matches[0]
        expected = {
            "componentId": component["id"],
            "componentType": component["type"],
            "fromVersion": component["version"],
            "targetVersion": supported["targetVersion"],
            "path": supported["path"],
            "action": supported["action"],
            "targetControl": supported["targetControl"],
        }
        for field, value in expected.items():
            require(step[field] == value, f"{component['id']} has wrong {field}")
        require(set(step["gateIds"]) == set(supported["requiredGates"]), f"{component['id']} gates differ from pinned snapshot")
        require(set(step["gateIds"]) <= declared_gates, f"{component['id']} names an undeclared gate")

    for relation in snapshot["precedence"]:
        require(positions[relation["before"]] < positions[relation["after"]],
                f"ordering gate violated: {relation['before']} must precede {relation['after']}")


def check_edge(artifact: dict[str, Any], estate: dict[str, Any], snapshot: dict[str, Any]) -> None:
    requirement = estate["edgeRequirement"]
    edge_snapshot = snapshot["edge"]
    throughput = requirement["requiredNorthSouthGbps"]
    fitting = [profile for profile in edge_snapshot["profiles"]
               if profile["maxNorthSouthGbpsPerNode"] >= throughput]
    require(bool(fitting), "pinned Edge profiles cannot satisfy required throughput")
    selected = min(fitting, key=lambda profile: profile["maxNorthSouthGbpsPerNode"])
    required_design = edge_snapshot["requiredDesign"]
    expected_uplinks = [dict(uplink, teamingPolicy=required_design["uplinkTeamingPolicy"])
                        for uplink in requirement["availableUplinks"]]
    expected = {
        "requiredNorthSouthGbps": throughput,
        "failureCapacityGbpsPerNode": selected["maxNorthSouthGbpsPerNode"],
        "formFactor": selected["formFactor"],
        "nodeCount": required_design["nodeCount"],
        "tier0Mode": required_design["tier0Mode"],
        "routing": required_design["routing"],
        "ecmp": required_design["ecmp"],
        "overlayTeaming": required_design["overlayTeaming"],
        "uplinks": expected_uplinks,
    }
    require(artifact["edgeDesign"] == expected, "Edge design does not match throughput-derived pinned design")
    require(requirement["surviveSingleNodeFailure"] and requirement["surviveSingleUplinkFailure"], "fixture failure requirements were dropped")
    require(len(expected_uplinks) == required_design["uplinksPerNode"], "wrong number of Edge uplinks")
    require(all(item["linkGbps"] == required_design["linkGbps"] and item["linkGbps"] >= throughput for item in expected_uplinks), "an Edge uplink cannot carry the failure load")
    require(len({item["tor"] for item in expected_uplinks}) == len(expected_uplinks), "Edge uplinks do not reach distinct ToRs")
    require(len({item["vlanId"] for item in expected_uplinks}) == len(expected_uplinks), "Edge uplinks do not use distinct VLANs")


def check_research() -> None:
    text = read_text("research.md")
    lowered = text.lower()
    require("http://" not in lowered and ".invalid" not in lowered and "localhost" not in lowered,
            "research.md contains a non-public or non-HTTPS source URL")
    source_lines = [line.strip() for line in text.splitlines() if line.strip().startswith("- ")]
    require(len(source_lines) >= 3, "research.md must record at least three official sources")

    urls: list[str] = []
    hosts: set[str] = set()
    url_pattern = re.compile(r"https://[^\s)>]+")
    date_pattern = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
    for line in source_lines:
        url_match = url_pattern.search(line)
        require(url_match is not None, "research source is missing an HTTPS URL")
        assert url_match is not None
        url = url_match.group(0).rstrip(".,;")
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        require(host == "broadcom.com" or host.endswith(".broadcom.com"),
                f"research URL is not an official Broadcom page: {url}")
        require(host not in {"localhost", "127.0.0.1"} and not host.endswith(".invalid"),
                f"research URL is not publicly reachable: {url}")

        title = line[2:url_match.start()].strip(" *#[]()—-")
        require(len(title) >= 3, f"research source is missing a title: {url}")
        date_match = date_pattern.search(line)
        require(date_match is not None, f"research source is missing an ISO retrieval date: {url}")
        assert date_match is not None
        try:
            date.fromisoformat(date_match.group(0))
        except ValueError as error:
            raise VerificationError(f"research source has an invalid retrieval date: {url}") from error
        decision = line[date_match.end():].strip(" .—-:")
        require(len(decision.split()) >= 4, f"research source is missing the decision it informed: {url}")
        urls.append(url)
        hosts.add(host)

    require(len(urls) == len(set(urls)), "research.md contains duplicate source URLs")
    require("compatibilityguide.broadcom.com" in hosts,
            "research.md does not include the Broadcom Hardware Compatibility Guide")
    require("interopmatrix.broadcom.com" in hosts,
            "research.md does not include the Broadcom Product Interoperability Matrix")
    require(any(host not in {"compatibilityguide.broadcom.com", "interopmatrix.broadcom.com"} for host in hosts),
            "research.md does not include relevant Broadcom product or upgrade documentation")


def main() -> int:
    try:
        # Installer-schema validation is deliberately the first acceptance check.
        artifact = read_json("architecture.json")
        installer = read_json("specifications/vcf-installer/vcf-installer-openapi.json")
        greenfield = artifact.get("greenfield") if isinstance(artifact, dict) else None
        validate_schema(greenfield, {"$ref": "#/components/schemas/SddcSpec"}, installer, "$.greenfield")
        print("installer SddcSpec schema: PASS")

        estate = read_json("fixtures/estate.json")
        snapshot = read_json("snapshots/compatibility-2026-05-12.json")
        plan_schema = read_json("schemas/migration-plan.schema.json")
        check_greenfield(artifact, estate, installer)
        check_migration(artifact, estate, snapshot, plan_schema)
        check_edge(artifact, estate, snapshot)
        check_research()
        print("artifact, fixture, pinned snapshot, and research log: PASS")

        result = subprocess.run(
            ["go", "test", "-race", "-timeout", "30s", "./..."],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        require(result.returncode == 0, "go test -race failed")
        print("verification: PASS")
        return 0
    except (VerificationError, subprocess.TimeoutExpired) as error:
        print(f"verification: FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
