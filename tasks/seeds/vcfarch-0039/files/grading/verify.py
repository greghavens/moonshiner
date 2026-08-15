#!/usr/bin/env python3
"""Offline acceptance oracle for the VCF architecture artifact."""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def json_type_matches(value: Any, expected: str) -> bool:
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
    return True


def resolve_pointer(document: Any, pointer: str) -> Any:
    if pointer == "#":
        return document
    if not pointer.startswith("#/"):
        fail(f"unsupported non-local schema reference: {pointer}")
    node = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(part)]
        else:
            node = node[part]
    return node


def schema_errors(instance: Any, schema: Any, document: Any, path: str = "$") -> list[str]:
    """Validate the JSON Schema/OpenAPI keywords used by the bundled contracts."""
    errors: list[str] = []
    if schema is True:
        return errors
    if schema is False:
        return [f"{path}: rejected by false schema"]
    if not isinstance(schema, dict):
        return [f"{path}: invalid schema node"]

    if "$ref" in schema:
        errors.extend(schema_errors(instance, resolve_pointer(document, schema["$ref"]), document, path))
        remaining = {key: value for key, value in schema.items() if key != "$ref"}
        if remaining:
            errors.extend(schema_errors(instance, remaining, document, path))
        return errors

    if instance is None and schema.get("nullable") is True:
        return errors

    if "allOf" in schema:
        for branch in schema["allOf"]:
            errors.extend(schema_errors(instance, branch, document, path))
    if "anyOf" in schema:
        if not any(not schema_errors(instance, branch, document, path) for branch in schema["anyOf"]):
            errors.append(f"{path}: does not satisfy anyOf")
    if "oneOf" in schema:
        matches = sum(not schema_errors(instance, branch, document, path) for branch in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: satisfies {matches} oneOf branches, expected exactly one")
    if "not" in schema and not schema_errors(instance, schema["not"], document, path):
        errors.append(f"{path}: satisfies a forbidden schema")

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not in the allowed enumeration")

    expected_type = schema.get("type")
    if expected_type:
        choices = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(json_type_matches(instance, choice) for choice in choices):
            errors.append(f"{path}: expected type {expected_type}, got {type(instance).__name__}")
            return errors

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")

        properties = schema.get("properties", {})
        pattern_properties = schema.get("patternProperties", {})
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            matched = False
            if key in properties:
                matched = True
                errors.extend(schema_errors(value, properties[key], document, child_path))
            for pattern, child_schema in pattern_properties.items():
                if re.search(pattern, key):
                    matched = True
                    errors.extend(schema_errors(value, child_schema, document, child_path))
            if not matched and "additionalProperties" in schema:
                additional = schema["additionalProperties"]
                if additional is False:
                    errors.append(f"{path}: additional property {key!r} is not allowed")
                elif isinstance(additional, dict):
                    errors.extend(schema_errors(value, additional, document, child_path))

        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            errors.append(f"{path}: has fewer than {schema['minProperties']} properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            errors.append(f"{path}: has more than {schema['maxProperties']} properties")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: has fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: has more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            normalized = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(normalized) != len(set(normalized)):
                errors.append(f"{path}: array items are not unique")
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for index, value in enumerate(instance):
                errors.extend(schema_errors(value, items_schema, document, f"{path}[{index}]"))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], instance) is None:
                    errors.append(f"{path}: does not match required pattern")
            except re.error as exc:
                fail(f"invalid regular expression in bundled schema at {path}: {exc}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: number is less than minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: number is greater than maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema:
            boundary = schema["exclusiveMinimum"]
            if isinstance(boundary, bool):
                if boundary and "minimum" in schema and instance <= schema["minimum"]:
                    errors.append(f"{path}: number does not exceed exclusive minimum")
            elif instance <= boundary:
                errors.append(f"{path}: number does not exceed exclusive minimum {boundary}")
        if "exclusiveMaximum" in schema:
            boundary = schema["exclusiveMaximum"]
            if isinstance(boundary, bool):
                if boundary and "maximum" in schema and instance >= schema["maximum"]:
                    errors.append(f"{path}: number does not precede exclusive maximum")
            elif instance >= boundary:
                errors.append(f"{path}: number does not precede exclusive maximum {boundary}")

    return errors


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def require_object(parent: dict[str, Any], key: str, label: str) -> dict[str, Any]:
    value = parent.get(key)
    require(isinstance(value, dict), f"{label} must be an object")
    return value


def require_array(parent: dict[str, Any], key: str, label: str) -> list[Any]:
    value = parent.get(key)
    require(isinstance(value, list), f"{label} must be an array")
    return value


def index_unique(items: list[Any], key: str, label: str) -> dict[Any, dict[str, Any]]:
    result: dict[Any, dict[str, Any]] = {}
    for item in items:
        require(isinstance(item, dict), f"each {label} entry must be an object")
        value = item.get(key)
        require(value is not None, f"each {label} entry needs {key}")
        require(value not in result, f"duplicate {label} {key}: {value!r}")
        result[value] = item
    return result


def verify_research(artifact: dict[str, Any]) -> None:
    research = require_array(artifact, "xResearch", "xResearch")
    require(research, "xResearch must record at least one consulted source")
    for index, source in enumerate(research):
        require(isinstance(source, dict), f"xResearch[{index}] must be an object")
        for key in ("title", "url", "accessedOn", "finding"):
            value = source.get(key)
            require(
                isinstance(value, str) and bool(value.strip()),
                f"xResearch[{index}].{key} must be a non-empty string",
            )

        parsed_url = urlparse(source["url"])
        require(
            parsed_url.scheme in {"http", "https"} and bool(parsed_url.netloc),
            f"xResearch[{index}].url must be an absolute HTTP(S) URL",
        )
        hostname = (parsed_url.hostname or "").lower()
        require(
            hostname != "invalid" and not hostname.endswith(".invalid"),
            f"xResearch[{index}].url cannot use the reserved .invalid domain",
        )
        try:
            date.fromisoformat(source["accessedOn"])
        except ValueError:
            fail(f"xResearch[{index}].accessedOn must be an ISO 8601 calendar date")


def verify_networks(artifact: dict[str, Any], requirements: dict[str, Any]) -> None:
    expected = {
        "MANAGEMENT": requirements["networking"]["management"],
        "VSAN": requirements["networking"]["vsan"],
        "VMOTION": requirements["networking"]["vmotion"],
        "VM_MANAGEMENT": requirements["networking"]["vmManagement"],
        "FLEET_MANAGEMENT": requirements["networking"]["fleetManagement"],
    }
    networks = index_unique(require_array(artifact, "networkSpecs", "networkSpecs"), "networkType", "network")
    require(set(expected).issubset(networks), "installer network types are incomplete")
    for network_type, fixture in expected.items():
        actual = networks[network_type]
        require_equal(actual.get("vlanId"), fixture["vlan"], f"{network_type} VLAN")
        require_equal(actual.get("subnet"), fixture["cidr"], f"{network_type} subnet")
        require_equal(actual.get("gateway"), fixture["gateway"], f"{network_type} gateway")
        require_equal(actual.get("mtu"), fixture["mtu"], f"{network_type} MTU")


def verify_installer_semantics(
    artifact: dict[str, Any], requirements: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    target = snapshot["targetVcfVersion"]
    greenfield = snapshot["greenfield"]
    require_equal(artifact.get("version"), target, "SddcSpec version")
    require_equal(artifact.get("workflowType"), greenfield["workflowType"], "workflow type")
    require_equal(artifact.get("vcfInstanceName"), requirements["designId"], "VCF instance name")

    host_specs = require_array(artifact, "hostSpecs", "hostSpecs")
    management_rule = greenfield["managementDomain"]
    require_equal(len(host_specs), management_rule["hostCount"], "management hosts")
    host_names = [host.get("hostname") for host in host_specs if isinstance(host, dict)]
    require_equal(len(set(host_names)), management_rule["hostCount"], "unique management host names")
    for host in host_specs:
        credentials = require_object(host, "credentials", "management-host credentials")
        require(isinstance(credentials.get("password"), str) and credentials["password"], "each management host needs a password")

    vcenter = require_object(artifact, "vcenterSpec", "vcenterSpec")
    require(isinstance(vcenter.get("vcenterHostname"), str) and vcenter["vcenterHostname"], "vCenter hostname is required")
    require_equal(vcenter.get("vmSize"), management_rule["vcenterSize"], "vCenter sizing")
    require_equal(vcenter.get("version"), target, "vCenter target")
    require_equal(vcenter.get("useExistingDeployment"), False, "greenfield vCenter")

    manager = require_object(artifact, "sddcManagerSpec", "sddcManagerSpec")
    require(isinstance(manager.get("hostname"), str) and manager["hostname"], "SDDC Manager hostname is required")
    require_equal(manager.get("version"), target, "SDDC Manager target")
    require_equal(manager.get("useExistingDeployment"), False, "greenfield SDDC Manager")

    cluster = require_object(artifact, "clusterSpec", "clusterSpec")
    require(isinstance(cluster.get("clusterName"), str) and cluster["clusterName"], "management cluster name is required")
    datastore = require_object(artifact, "datastoreSpec", "datastoreSpec")
    vsan = require_object(datastore, "vsanSpec", "vSAN datastore spec")
    require(isinstance(vsan.get("datastoreName"), str) and vsan["datastoreName"], "vSAN datastore name is required")
    require_equal(
        vsan.get("failuresToTolerate"),
        requirements["availability"]["managementHostFailures"],
        "management vSAN failure tolerance",
    )
    verify_networks(artifact, requirements)

    dns = require_object(artifact, "dnsSpec", "dnsSpec")
    require_equal(dns.get("subdomain"), requirements["sites"][0]["dnsDomain"], "DNS subdomain")
    require_equal(dns.get("nameservers"), requirements["networking"]["dnsServers"], "DNS servers")
    require_equal(artifact.get("ntpServers"), requirements["networking"]["ntpServers"], "NTP servers")

    nsxt = require_object(artifact, "nsxtSpec", "nsxtSpec")
    require_equal(nsxt.get("version"), target, "NSX target")
    require_equal(nsxt.get("useExistingDeployment"), False, "greenfield NSX")
    require_equal(nsxt.get("nsxtManagerSize"), management_rule["nsxManagerSize"], "NSX manager size")
    managers = require_array(nsxt, "nsxtManagers", "NSX managers")
    require_equal(len(managers), management_rule["nsxManagerCount"], "NSX manager count")
    require_equal(len({manager.get("hostname") for manager in managers}), management_rule["nsxManagerCount"], "unique NSX managers")

    sizing = greenfield["sizing"]
    operations = require_object(artifact, "vcfOperationsSpec", "vcfOperationsSpec")
    ops_rule = sizing["VCF Operations"]
    require_equal(operations.get("version"), target, "VCF Operations target")
    require_equal(operations.get("applianceSize"), ops_rule["applianceSize"], "VCF Operations size")
    require_equal(operations.get("useExistingDeployment"), False, "greenfield VCF Operations")
    ops_nodes = require_array(operations, "nodes", "VCF Operations nodes")
    require_equal(len(ops_nodes), ops_rule["nodeCount"], "VCF Operations node count")
    require_equal({node.get("type") for node in ops_nodes}, {"master", "replica", "data"}, "VCF Operations node roles")

    automation = require_object(artifact, "vcfAutomationSpec", "vcfAutomationSpec")
    automation_rule = sizing["VCF Automation"]
    require_equal(automation.get("version"), target, "VCF Automation target")
    require_equal(automation.get("size"), automation_rule["size"], "VCF Automation size")
    require_equal(automation.get("useExistingDeployment"), False, "greenfield VCF Automation")
    require(isinstance(automation.get("hostname"), str) and automation["hostname"], "VCF Automation hostname is required")
    require(len(automation.get("ipPool", [])) >= 6, "VCF Automation requires at least six service IPs")

    logs_rule = sizing["VCF Operations for Logs"]
    vsp = require_object(artifact, "vspClusterSpec", "vspClusterSpec")
    require_equal(vsp.get("version"), target, "management-services platform target")
    require_equal(vsp.get("size"), logs_rule["platformSize"], "management-services platform size")
    require_equal(vsp.get("useExistingDeployment"), False, "greenfield management-services platform")
    require_equal(automation.get("platformFqdn"), vsp.get("platformFqdn"), "Automation platform binding")
    vidb = require_object(artifact, "vidbSpec", "vidbSpec")
    require_equal(vidb.get("version"), target, "VIDB target")
    require_equal(vidb.get("size"), logs_rule["vidbSize"], "VIDB size")

    infrastructure = require_object(
        artifact, "vcfManagementComponentsInfrastructureSpec", "management components infrastructure"
    )
    local_network = require_object(infrastructure, "localRegionNetwork", "local management-services network")
    cross_network = require_object(infrastructure, "xRegionNetwork", "cross-region management-services network")
    require(isinstance(local_network.get("networkName"), str) and local_network["networkName"], "local network binding is required")
    require_equal(local_network.get("gateway"), requirements["networking"]["vmManagement"]["gateway"], "local network gateway")
    require(isinstance(cross_network.get("networkName"), str) and cross_network["networkName"], "fleet network binding is required")
    require_equal(cross_network.get("gateway"), requirements["networking"]["fleetManagement"]["gateway"], "fleet network gateway")


def verify_architecture_extension(
    artifact: dict[str, Any], requirements: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    architecture = require_object(artifact, "xArchitecture", "xArchitecture")
    require_equal(architecture.get("designId"), requirements["designId"], "architecture design id")
    sites = index_unique(require_array(architecture, "sites", "architecture sites"), "siteId", "site")
    require_equal(set(sites), {"DAL", "AUS"}, "architecture sites")

    host_profile = requirements["hostProfile"]
    for site_fixture in requirements["sites"]:
        site_id = site_fixture["siteId"]
        site = sites[site_id]
        require_equal(site.get("role"), site_fixture["role"], f"{site_id} role")
        require_equal(site.get("dnsDomain"), site_fixture["dnsDomain"], f"{site_id} DNS domain")
        management = require_object(site, "managementDomain", f"{site_id} management domain")
        require_equal(management.get("hostCount"), site_fixture["managementDomainHosts"], f"{site_id} management hosts")
        workload = require_object(site, "workloadDomain", f"{site_id} workload domain")
        fixture_workload = site_fixture["workloadDomain"]
        require_equal(workload.get("name"), fixture_workload["name"], f"{site_id} workload name")
        require_equal(workload.get("hostCount"), fixture_workload["hosts"], f"{site_id} workload hosts")
        require_equal(
            workload.get("hostFailureTolerance"),
            requirements["availability"]["workloadHostFailures"],
            f"{site_id} workload failure tolerance",
        )
        survivors = fixture_workload["hosts"] - requirements["availability"]["workloadHostFailures"]
        expected_capacity = {
            "physicalCores": survivors * host_profile["physicalCoresPerHost"],
            "memoryTiB": survivors * host_profile["memoryGiBPerHost"] / 1024,
            "usableStorageTiB": survivors * host_profile["usableStorageTiBPerHost"],
        }
        capacity = require_object(workload, "capacityAfterFailures", f"{site_id} capacity evidence")
        for metric, expected in expected_capacity.items():
            actual = capacity.get(metric)
            require(isinstance(actual, (int, float)) and not isinstance(actual, bool), f"{site_id} {metric} must be numeric")
            require(math.isclose(actual, expected, rel_tol=0, abs_tol=1e-9), f"{site_id} {metric}: expected {expected}, got {actual}")
        require(capacity["physicalCores"] >= fixture_workload["requiredPhysicalCores"], f"{site_id} CPU capacity is insufficient")
        require(capacity["memoryTiB"] >= fixture_workload["requiredMemoryTiB"], f"{site_id} memory capacity is insufficient")
        require(capacity["usableStorageTiB"] >= fixture_workload["requiredUsableStorageTiB"], f"{site_id} storage capacity is insufficient")

    dal_management = require_object(sites["DAL"], "managementDomain", "DAL management domain")
    require_equal(
        dal_management.get("hostFailureTolerance"),
        requirements["availability"]["managementHostFailures"],
        "management failure tolerance",
    )
    require_equal(sites["AUS"]["managementDomain"].get("hostFailureTolerance"), 0, "AUS management failure tolerance")

    availability = require_object(architecture, "availability", "architecture availability")
    fixture_availability = requirements["availability"]
    require_equal(availability.get("siteLinkRttMs"), fixture_availability["siteLinkRttMs"], "site RTT")
    require_equal(availability.get("recoveryMode"), fixture_availability["recoveryMode"], "recovery mode")
    require_equal(availability.get("rpoMinutes"), fixture_availability["rpoMinutes"], "RPO")
    require_equal(availability.get("rtoMinutes"), fixture_availability["rtoMinutes"], "RTO")
    require_equal(availability.get("stretchedManagementDomain"), False, "management-domain topology")

    components = index_unique(require_array(architecture, "components", "component decisions"), "name", "component")
    required_components = {"VCF Operations", "VCF Automation", "VCF Operations for Logs"}
    require(
        required_components.issubset(components),
        "management component decisions must include VCF Operations, VCF Automation, and "
        "VCF Operations for Logs",
    )
    sizing = snapshot["greenfield"]["sizing"]
    demand_keys = {
        "VCF Operations": ("operationsDemand", "managedVms"),
        "VCF Automation": ("automationDemand", "concurrentDeployments"),
        "VCF Operations for Logs": ("loggingDemand", "ingestGiBPerDay"),
    }
    for name in required_components:
        decision = components[name]
        rule = sizing[name]
        require_equal(decision.get("placement"), rule["placement"], f"{name} placement")
        require_equal(decision.get("deploymentModel"), rule["deploymentModel"], f"{name} deployment model")
        require_equal(decision.get("nodeCount"), rule["nodeCount"], f"{name} node count")
        fixture_section, demand_key = demand_keys[name]
        require_equal(decision.get(demand_key), requirements[fixture_section][demand_key], f"{name} sizing demand")

    require_equal(components["VCF Operations"].get("size"), sizing["VCF Operations"]["applianceSize"], "VCF Operations decision size")
    require_equal(components["VCF Automation"].get("size"), sizing["VCF Automation"]["size"], "VCF Automation decision size")
    logs = components["VCF Operations for Logs"]
    require_equal(logs.get("targetName"), sizing["VCF Operations for Logs"]["targetName"], "Log Management target name")
    require_equal(logs.get("platformSize"), sizing["VCF Operations for Logs"]["platformSize"], "Log Management platform size")
    require_equal(logs.get("vidbSize"), sizing["VCF Operations for Logs"]["vidbSize"], "Log Management VIDB size")
    require_equal(logs.get("retentionDays"), requirements["loggingDemand"]["retentionDays"], "log retention")


def verify_migration(
    artifact: dict[str, Any], estate: dict[str, Any], snapshot: dict[str, Any], migration_schema: dict[str, Any]
) -> None:
    plan = artifact.get("xMigrationPlan")
    errors = schema_errors(plan, migration_schema, migration_schema)
    if errors:
        fail("migration-plan schema validation failed first error: " + errors[0])
    assert isinstance(plan, dict)
    require_equal(plan["schemaVersion"], "1.0", "migration schema version")
    require_equal(plan["estateId"], estate["estateId"], "migration estate id")
    require_equal(plan["targetVcfVersion"], snapshot["targetVcfVersion"], "migration target VCF")
    require(
        estate["vcfVersion"] in snapshot["supportedSourceVcfVersions"],
        "fixture source VCF is absent from compatibility snapshot",
    )

    inventory = index_unique(estate["components"], "component", "estate component")
    additions = index_unique(estate.get("requiredAdditions", []), "component", "required addition")
    steps = plan["steps"]
    path_rows = index_unique(
        snapshot["migration"]["upgradePaths"], "sourceComponent", "compatibility path"
    )
    expected_components = set(inventory) | set(additions)
    require_equal(set(path_rows), expected_components, "fixture/snapshot migration components")
    require_equal(len(steps), len(expected_components), "migration step count")
    require_equal([step["order"] for step in steps], list(range(1, len(steps) + 1)), "migration order values")

    step_by_component: dict[str, dict[str, Any]] = {}
    for step in steps:
        name = step["source"]["component"]
        require(name not in step_by_component, f"duplicate migration source component {name!r}")
        step_by_component[name] = step
    require_equal(set(step_by_component), expected_components, "migration source components")

    for name, step in step_by_component.items():
        path = path_rows[name]
        if name in inventory:
            expected_source = inventory[name]
            require_equal(path["sourceVersion"], expected_source["version"], f"snapshot source for {name}")
        else:
            expected_source = {"component": name, "version": "not-installed"}
            require_equal(path["sourceVersion"], "not-installed", f"snapshot source for {name}")
            require_equal(
                path["targetVersion"], additions[name]["version"], f"required target addition for {name}"
            )
        require_equal(step["source"], expected_source, f"migration source for {name}")
        require_equal(step["target"]["component"], path["targetComponent"], f"migration target component for {name}")
        require_equal(step["target"]["version"], path["targetVersion"], f"migration target version for {name}")
        require_equal(step["action"], path["action"], f"migration action for {name}")
        require_equal(set(step["gates"]), set(path["gates"]), f"migration gates for {name}")
        for prerequisite in path["afterComponents"]:
            require(
                step_by_component[prerequisite]["order"] < step["order"],
                f"migration component {name} must follow {prerequisite}",
            )


def main() -> int:
    if len(sys.argv) != 6:
        print(
            "usage: verify.py ARTIFACT OPENAPI REQUIREMENTS ESTATE COMPATIBILITY_SNAPSHOT",
            file=sys.stderr,
        )
        return 2

    artifact_path, openapi_path, requirements_path, estate_path, snapshot_path = map(Path, sys.argv[1:])
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        openapi = json.loads(openapi_path.read_text(encoding="utf-8"))

        # Binding acceptance order: validate the complete artifact as the upstream
        # installer SddcSpec before loading fixtures or running any semantic check.
        installer_errors = schema_errors(
            artifact, {"$ref": "#/components/schemas/SddcSpec"}, openapi
        )
        if installer_errors:
            fail("installer SddcSpec schema validation failed first error: " + installer_errors[0])
        assert isinstance(artifact, dict)
        verify_research(artifact)

        requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
        estate = json.loads(estate_path.read_text(encoding="utf-8"))
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        migration_schema_path = snapshot_path.parent.parent / "schemas" / "migration-plan.schema.json"
        migration_schema = json.loads(migration_schema_path.read_text(encoding="utf-8"))

        verify_installer_semantics(artifact, requirements, snapshot)
        verify_architecture_extension(artifact, requirements, snapshot)
        verify_migration(artifact, estate, snapshot, migration_schema)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, VerificationError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("PASS: VCF 9.1 SddcSpec, architecture, sizing, placement, and migration plan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
