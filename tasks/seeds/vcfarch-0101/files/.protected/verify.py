#!/usr/bin/env python3
"""Offline protected verifier for vcfarch-0101."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "architecture"
SDDC_PATH = ARTIFACT_DIR / "greenfield-sddc-spec.json"
TOPOLOGY_PATH = ARTIFACT_DIR / "site-topology.json"
PLAN_PATH = ARTIFACT_DIR / "migration-plan.json"
RESEARCH_PATH = ROOT / "research.md"
OPENAPI_PATH = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
PROVENANCE_PATH = ROOT / "specifications/vcf-installer/provenance.json"
PLAN_SCHEMA_PATH = ROOT / "schemas/migration-plan.schema.json"
TOPOLOGY_SCHEMA_PATH = ROOT / "schemas/site-topology.schema.json"
ESTATE_PATH = ROOT / "fixtures/estate.json"
SNAPSHOT_PATH = ROOT / "compatibility/compatibility-snapshot.json"
MODULE_PATH = ROOT / "VcfFleetArchitecture/VcfFleetArchitecture.psm1"
MANIFEST_PATH = ROOT / "VcfFleetArchitecture/VcfFleetArchitecture.psd1"
INVOKER_PATH = ROOT / ".protected/invoke_module.ps1"
EXPECTED_SPEC_SHA256 = "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"
OUTPUT_NAMES = (
    "greenfield-sddc-spec.json",
    "site-topology.json",
    "migration-plan.json",
)
SDK_MODEL_COMMANDS = (
    "Initialize-VcfInstallerSddcSpec",
    "Initialize-VcfInstallerSddcHostSpec",
    "Initialize-VcfInstallerSddcVcenterSpec",
    "Initialize-VcfInstallerSddcNetworkSpec",
    "Initialize-VcfInstallerDnsSpec",
    "Initialize-VcfInstallerSddcClusterSpec",
    "Initialize-VcfInstallerSddcDatastoreSpec",
    "Initialize-VcfInstallerVsanSpec",
    "Initialize-VcfInstallerVsanEsaConfig",
    "Initialize-VcfInstallerSddcNsxtSpec",
    "Initialize-VcfInstallerNsxtManagerSpec",
)


class VerificationFailure(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise VerificationFailure(f"missing required file: {path.relative_to(ROOT)}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationFailure(f"invalid UTF-8 JSON in {path.relative_to(ROOT)}: {exc}") from exc


def resolve_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise VerificationFailure(f"unsupported non-local schema reference: {reference}")
    node: Any = root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise VerificationFailure(f"unresolved schema reference: {reference}")
        node = node[part]
    if not isinstance(node, dict):
        raise VerificationFailure(f"schema reference does not resolve to an object: {reference}")
    return node


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
        return isinstance(instance, (int, float)) and not isinstance(instance, bool) and math.isfinite(instance)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    raise VerificationFailure(f"unsupported schema type in protected fixture: {expected}")


def validate_schema(
    instance: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> list[str]:
    if "$ref" in schema:
        return validate_schema(instance, resolve_ref(root_schema, schema["$ref"]), root_schema, path)

    errors: list[str] = []
    if instance is None and schema.get("nullable") is True:
        return errors

    if "allOf" in schema:
        for child in schema["allOf"]:
            errors.extend(validate_schema(instance, child, root_schema, path))
    if "anyOf" in schema:
        alternatives = [validate_schema(instance, child, root_schema, path) for child in schema["anyOf"]]
        if not any(not alternative for alternative in alternatives):
            errors.append(f"{path}: does not match any allowed schema")
    if "oneOf" in schema:
        matches = sum(not validate_schema(instance, child, root_schema, path) for child in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: must match exactly one allowed schema, matched {matches}")

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(type_matches(instance, value) for value in expected_types):
            errors.append(f"{path}: expected {' or '.join(expected_types)}, got {type(instance).__name__}")
            return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} is not in the allowed enum")

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in instance:
                errors.append(f"{path}: missing required property {name!r}")
        for name, value in instance.items():
            child_path = f"{path}.{name}"
            if name in properties:
                errors.extend(validate_schema(value, properties[name], root_schema, child_path))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{child_path}: additional property is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(validate_schema(value, schema["additionalProperties"], root_schema, child_path))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: has fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: has more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(canonical) != len(set(canonical)):
                errors.append(f"{path}: items are not unique")
        if isinstance(schema.get("items"), dict):
            for index, value in enumerate(instance):
                errors.extend(validate_schema(value, schema["items"], root_schema, f"{path}[{index}]"))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema:
            try:
                if re.search(schema["pattern"], instance) is None:
                    errors.append(f"{path}: string does not match {schema['pattern']!r}")
            except re.error as exc:
                raise VerificationFailure(f"invalid protected schema pattern {schema['pattern']!r}: {exc}") from exc

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: value is below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: value is above maximum {schema['maximum']}")

    return errors


def assert_schema(instance: Any, schema: dict[str, Any], root_schema: dict[str, Any], label: str) -> None:
    errors = validate_schema(instance, schema, root_schema)
    if errors:
        detail = "\n  - ".join(errors[:30])
        raise VerificationFailure(f"{label} schema validation failed:\n  - {detail}")


def find_rule(component: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    matches = [
        rule
        for rule in snapshot["rules"]
        if rule["componentType"] == component["type"]
        and rule["sourceVersion"] == component["currentVersion"]
    ]
    if len(matches) != 1:
        raise VerificationFailure(
            f"pinned snapshot must contain exactly one rule for {component['type']} {component['currentVersion']}; found {len(matches)}"
        )
    return matches[0]


def expected_sddc(estate: dict[str, Any]) -> dict[str, Any]:
    greenfield = estate["greenfield"]
    hosts = sorted(
        (component for component in estate["components"] if component["type"] == "ESXI"),
        key=lambda component: (component["siteId"], component["id"]),
    )
    return {
        "sddcId": greenfield["sddcId"],
        "workflowType": greenfield["workflowType"],
        "hostSpecs": [{"hostname": component["hostname"]} for component in hosts],
        "version": estate["targetFleet"]["version"],
        "vcenterSpec": {
            "vcenterHostname": greenfield["vcenterHostname"],
            "rootVcenterPassword": greenfield["vcenterPasswordReference"],
            "vmSize": greenfield["vcenterVmSize"],
            "storageSize": greenfield["vcenterStorageSize"],
        },
        "clusterSpec": {
            "datacenterName": greenfield["datacenterName"],
            "clusterName": greenfield["clusterName"],
        },
        "nsxtSpec": {
            "nsxtManagers": [
                {"hostname": hostname} for hostname in greenfield["nsxManagerHostnames"]
            ],
            "nsxtManagerSize": greenfield["nsxManagerSize"],
            "vipFqdn": greenfield["nsxVipFqdn"],
            "transportVlanId": greenfield["nsxTransportVlanId"],
        },
        "networkSpecs": copy.deepcopy(greenfield["networks"]),
        "dnsSpec": {
            "subdomain": greenfield["dnsSubdomain"],
            "nameservers": list(greenfield["nameServers"]),
        },
        "ntpServers": list(greenfield["ntpServers"]),
        "datastoreSpec": {
            "vsanSpec": {
                "datastoreName": greenfield["vsanDatastoreName"],
                "esaConfig": {"enabled": greenfield["vsanEsaEnabled"]},
                "failuresToTolerate": greenfield["vsanFailuresToTolerate"],
            }
        },
    }


def expected_topology(estate: dict[str, Any]) -> dict[str, Any]:
    sites_by_id = {site["id"]: site for site in estate["sites"]}
    data_site_ids = estate["managementDomain"]["dataSiteIds"]
    hosts = sorted(
        (component for component in estate["components"] if component["type"] == "ESXI"),
        key=lambda component: (component["siteId"], component["id"]),
    )
    components_by_id = {component["id"]: component for component in estate["components"]}
    witness = components_by_id[estate["managementDomain"]["witnessComponentId"]]
    witness_site = sites_by_id[witness["siteId"]]
    return {
        "schemaVersion": "1.0.0",
        "designType": "TWO_SITE_STRETCHED_MANAGEMENT_DOMAIN",
        "targetFleet": copy.deepcopy(estate["targetFleet"]),
        "managementDomain": {
            "id": estate["managementDomain"]["id"],
            "stretched": True,
            "dataSites": [
                {
                    "siteId": site_id,
                    "role": sites_by_id[site_id]["role"],
                    "failureDomain": sites_by_id[site_id]["failureDomain"],
                }
                for site_id in data_site_ids
            ],
            "hostPlacement": [
                {
                    "componentId": component["id"],
                    "hostname": component["hostname"],
                    "siteId": component["siteId"],
                }
                for component in hosts
            ],
            "storage": {
                "type": "VSAN_STRETCHED_CLUSTER",
                "witness": {
                    "componentId": witness["id"],
                    "hostname": witness["hostname"],
                    "siteId": witness["siteId"],
                    "failureDomain": witness_site["failureDomain"],
                    "runsOnManagementDomain": False,
                },
            },
        },
        "protection": {
            "pairId": estate["recoveryPair"]["id"],
            "targetProduct": "VCF_PROTECTION_AND_RECOVERY",
            "targetVersion": estate["targetFleet"]["version"],
            "appliances": [
                {"resourceId": f"{site_id}-vpr", "siteId": site_id}
                for site_id in estate["recoveryPair"]["siteIds"]
            ],
        },
    }


def selected_dependencies(
    component: dict[str, Any],
    rule: dict[str, Any],
    components: list[dict[str, Any]],
) -> list[str]:
    result: set[str] = set()
    for selector in rule["dependencySelectors"]:
        for candidate in components:
            if candidate["type"] != selector["componentType"]:
                continue
            if selector["scope"] == "same-site" and candidate["siteId"] != component["siteId"]:
                continue
            if selector["scope"] not in {"same-site", "global"}:
                raise VerificationFailure(f"unknown dependency selector scope {selector['scope']!r}")
            result.add(candidate["id"])
    return sorted(result)


def expected_plan(estate: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    gate_conditions = {gate["id"]: gate["condition"] for gate in snapshot["gateDefinitions"]}
    components_with_rules = [
        (component, find_rule(component, snapshot)) for component in estate["components"]
    ]
    components_with_rules.sort(
        key=lambda value: (value[1]["phase"], value[0]["siteId"], value[0]["id"])
    )
    steps: list[dict[str, Any]] = []
    for order, (component, rule) in enumerate(components_with_rules, start=1):
        resource_id = (
            rule["targetResourceTemplate"]
            .replace("{componentId}", component["id"])
            .replace("{siteId}", component["siteId"])
        )
        steps.append(
            {
                "order": order,
                "phase": rule["phase"],
                "componentId": component["id"],
                "componentType": component["type"],
                "siteId": component["siteId"],
                "currentVersion": component["currentVersion"],
                "target": {
                    "product": rule["targetProduct"],
                    "version": rule["targetVersion"],
                    "resourceId": resource_id,
                },
                "upgradePath": list(rule["upgradePath"]),
                "action": rule["action"],
                "gates": [
                    {"id": gate_id, "condition": gate_conditions[gate_id]}
                    for gate_id in rule["gateIds"]
                ],
                "dependsOn": selected_dependencies(component, rule, estate["components"]),
            }
        )
    return {
        "schemaVersion": "1.0.0",
        "estateId": estate["estateId"],
        "targetFleetId": estate["targetFleet"]["id"],
        "targetFleetVersion": estate["targetFleet"]["version"],
        "steps": steps,
    }


def assert_exact(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        actual_text = json.dumps(actual, indent=2, sort_keys=True)
        expected_text = json.dumps(expected, indent=2, sort_keys=True)
        raise VerificationFailure(
            f"{label} does not match the protected fixture and pinned snapshot\n"
            f"expected:\n{expected_text}\nactual:\n{actual_text}"
        )


def normalized_sddc(document: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(document)
    result["hostSpecs"] = sorted(result["hostSpecs"], key=lambda item: item["hostname"])
    result["networkSpecs"] = sorted(
        result["networkSpecs"], key=lambda item: item["networkType"]
    )
    result["nsxtSpec"]["nsxtManagers"] = sorted(
        result["nsxtSpec"]["nsxtManagers"], key=lambda item: item["hostname"]
    )
    result["dnsSpec"]["nameservers"] = sorted(result["dnsSpec"]["nameservers"])
    result["ntpServers"] = sorted(result["ntpServers"])
    return result


def verify_sddc_semantics(sddc: dict[str, Any], estate: dict[str, Any]) -> None:
    assert_exact(
        normalized_sddc(sddc),
        normalized_sddc(expected_sddc(estate)),
        "greenfield-sddc-spec.json",
    )


def verify_plan_semantics(plan: dict[str, Any], estate: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expected = expected_plan(estate, snapshot)
    for name in ("schemaVersion", "estateId", "targetFleetId", "targetFleetVersion"):
        assert_exact(plan[name], expected[name], f"migration-plan.json {name}")

    steps = plan["steps"]
    expected_by_component = {step["componentId"]: step for step in expected["steps"]}
    actual_ids = [step["componentId"] for step in steps]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected_by_component):
        raise VerificationFailure("migration plan must name every inventory component exactly once")

    for step in steps:
        actual_step = copy.deepcopy(step)
        expected_step = copy.deepcopy(expected_by_component[step["componentId"]])
        actual_step.pop("order")
        expected_step.pop("order")
        actual_step["gates"] = sorted(actual_step["gates"], key=lambda gate: gate["id"])
        expected_step["gates"] = sorted(expected_step["gates"], key=lambda gate: gate["id"])
        actual_step["dependsOn"] = sorted(actual_step["dependsOn"])
        expected_step["dependsOn"] = sorted(expected_step["dependsOn"])
        assert_exact(
            actual_step,
            expected_step,
            f"migration-plan.json step {step['componentId']}",
        )

    orders = [step["order"] for step in steps]
    if orders != list(range(1, len(steps) + 1)):
        raise VerificationFailure("migration step orders must be contiguous from 1")
    phases = [step["phase"] for step in steps]
    if phases != sorted(phases):
        raise VerificationFailure("migration phases must be nondecreasing")
    order_by_component = {step["componentId"]: step["order"] for step in steps}
    for step in steps:
        for dependency in step["dependsOn"]:
            if dependency not in order_by_component:
                raise VerificationFailure(f"{step['componentId']} has unknown dependency {dependency}")
            if order_by_component[dependency] >= step["order"]:
                raise VerificationFailure(f"{step['componentId']} dependency {dependency} is not earlier")


def verify_topology_semantics(topology: dict[str, Any], estate: dict[str, Any], sddc: dict[str, Any]) -> None:
    expected = expected_topology(estate)
    actual_normalized = copy.deepcopy(topology)
    expected_normalized = copy.deepcopy(expected)
    for document in (actual_normalized, expected_normalized):
        document["managementDomain"]["dataSites"] = sorted(
            document["managementDomain"]["dataSites"], key=lambda item: item["siteId"]
        )
        document["managementDomain"]["hostPlacement"] = sorted(
            document["managementDomain"]["hostPlacement"],
            key=lambda item: item["componentId"],
        )
        document["protection"]["appliances"] = sorted(
            document["protection"]["appliances"], key=lambda item: item["siteId"]
        )
    assert_exact(actual_normalized, expected_normalized, "site-topology.json")
    management = topology["managementDomain"]
    data_sites = management["dataSites"]
    if len(data_sites) != 2 or not management["stretched"]:
        raise VerificationFailure("management domain must be stretched across exactly two data sites")
    witness = management["storage"]["witness"]
    data_site_ids = {site["siteId"] for site in data_sites}
    data_failure_domains = {site["failureDomain"] for site in data_sites}
    if witness["siteId"] in data_site_ids or witness["failureDomain"] in data_failure_domains:
        raise VerificationFailure("witness must be in a third site and third failure domain")
    if witness["runsOnManagementDomain"] is not False:
        raise VerificationFailure("witness must not run on the stretched management domain")
    hostnames = {host["hostname"] for host in sddc["hostSpecs"]}
    if witness["hostname"] in hostnames:
        raise VerificationFailure("witness must not appear in SddcSpec.hostSpecs")
    counts = {site_id: 0 for site_id in data_site_ids}
    for placement in management["hostPlacement"]:
        counts[placement["siteId"]] += 1
    if sorted(counts.values()) != [4, 4]:
        raise VerificationFailure("each data site must contain four management-domain data hosts")


def verify_artifact_set(
    directory: Path,
    estate: dict[str, Any],
    snapshot: dict[str, Any],
    openapi: dict[str, Any],
    topology_schema: dict[str, Any],
    plan_schema: dict[str, Any],
) -> None:
    sddc = load_json(directory / OUTPUT_NAMES[0])
    assert_schema(
        sddc,
        openapi["components"]["schemas"]["SddcSpec"],
        openapi,
        "generated SddcSpec",
    )
    verify_sddc_semantics(sddc, estate)
    topology = load_json(directory / OUTPUT_NAMES[1])
    plan = load_json(directory / OUTPUT_NAMES[2])
    assert_schema(topology, topology_schema, topology_schema, "generated site topology")
    assert_schema(plan, plan_schema, plan_schema, "generated migration plan")
    verify_topology_semantics(topology, estate, sddc)
    verify_plan_semantics(plan, estate, snapshot)


def verify_sdk_source() -> None:
    if not MODULE_PATH.is_file() or not MANIFEST_PATH.is_file():
        raise VerificationFailure("missing VcfFleetArchitecture module or manifest")
    module_text = MODULE_PATH.read_text(encoding="utf-8")
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    lower = module_text.lower()
    forbidden = (
        "invoke-webrequest",
        "invoke-restmethod",
        "system.net.http",
        "webclient",
        "start-process",
        "curl",
        "wget",
        "tcpclient",
        "httpclient",
    )
    hits = [token for token in forbidden if token in lower]
    if hits:
        raise VerificationFailure(f"module bypasses the SDK or launches a subprocess: {', '.join(hits)}")
    required_tokens = ("Import-Module VMware.Sdk.Vcf.Installer", *SDK_MODEL_COMMANDS)
    missing = [token for token in required_tokens if token not in module_text]
    if missing:
        raise VerificationFailure(f"module does not construct the required genuine SDK models: {', '.join(missing)}")
    if re.search(r"(?im)^\s*function\s+Initialize-VcfInstaller", module_text):
        raise VerificationFailure("module redefines a VMware SDK model command")
    if re.search(
        r"(?i)(?:new-alias|set-alias|function:|alias:)[^\r\n]*Initialize-VcfInstaller",
        module_text,
    ):
        raise VerificationFailure("module aliases or intercepts a VMware SDK model command")
    if "VMware.Sdk.Vcf.Installer" not in manifest_text or "13.5.0.25380678" not in manifest_text:
        raise VerificationFailure("manifest must require VMware.Sdk.Vcf.Installer 13.5.0.25380678")
    binary_extensions = {".dll", ".nupkg", ".psmxml"}
    for path in ROOT.rglob("*"):
        # The trace harness places its PowerShell runtime and the genuine
        # VMware module below this workspace-local home. Those files are
        # runner prerequisites, not authored seed content.
        if ".sandbox-home" in path.relative_to(ROOT).parts:
            continue
        if path.is_file() and path.suffix.lower() in binary_extensions:
            raise VerificationFailure(f"vendored binary/module package is not allowed: {path.relative_to(ROOT)}")


def run_module(estate_path: Path, snapshot_path: Path, output_dir: Path) -> None:
    audit_path = output_dir.parent / f"{output_dir.name}-sdk-parameter-binding.log"
    command = [
        "pwsh",
        "-NoLogo",
        "-NoProfile",
        "-File",
        str(INVOKER_PATH),
        "-Workspace",
        str(ROOT),
        "-EstatePath",
        str(estate_path),
        "-SnapshotPath",
        str(snapshot_path),
        "-OutputDirectory",
        str(output_dir),
        "-AuditPath",
        str(audit_path),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        combined = (completed.stdout + "\n" + completed.stderr).strip()
        raise VerificationFailure(f"PowerShell module generation failed:\n{combined}")
    if completed.stdout.strip():
        raise VerificationFailure(f"PowerShell module wrote unexpected success output: {completed.stdout.strip()}")
    try:
        audit_text = audit_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VerificationFailure("PowerShell SDK invocation audit was not produced") from exc
    missing_calls = [name for name in SDK_MODEL_COMMANDS if f"[{name}]" not in audit_text]
    if missing_calls:
        raise VerificationFailure(
            "module did not execute the required genuine SDK model commands: "
            + ", ".join(missing_calls)
        )


def assert_canonical_file(path: Path) -> None:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise VerificationFailure(f"{path.name} must be UTF-8 without BOM")
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise VerificationFailure(f"{path.name} must end with exactly one newline")


def verify_research(estate: dict[str, Any]) -> None:
    try:
        text = RESEARCH_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VerificationFailure("missing required file: research.md") from exc
    except UnicodeDecodeError as exc:
        raise VerificationFailure(f"research.md is not valid UTF-8: {exc}") from exc

    lower = text.lower()
    for heading in ("source", "url", "date consulted", "decision"):
        if heading not in lower:
            raise VerificationFailure(f"research.md does not identify the {heading}")
    if "conflict" not in lower:
        raise VerificationFailure("research.md must state whether live research conflicts with the snapshot")

    versions = {component["currentVersion"] for component in estate["components"]}
    versions.add(estate["targetFleet"]["version"])
    missing_versions = sorted(version for version in versions if version not in text)
    if missing_versions:
        raise VerificationFailure(
            "research.md does not cover exact inventory/target versions: "
            + ", ".join(missing_versions)
        )
    component_types = {component["type"] for component in estate["components"]}
    missing_types = sorted(component_type for component_type in component_types if component_type not in text)
    if missing_types:
        raise VerificationFailure(
            "research.md does not cover inventory product types: " + ", ".join(missing_types)
        )

    url_values = re.findall(r"https://[^\s|)>]+", text)
    broadcom_urls = [
        value
        for value in url_values
        if (urlparse(value).hostname or "").lower().endswith("broadcom.com")
    ]
    if len(set(broadcom_urls)) < 3:
        raise VerificationFailure("research.md must record at least three Broadcom-published sources")
    if not re.search(r"\b20\d{2}-\d{2}-\d{2}\b", text):
        raise VerificationFailure("research.md must record a consultation date")


def runtime_variant(estate: dict[str, Any]) -> dict[str, Any]:
    variant = copy.deepcopy(estate)
    variant["estateId"] = "runtime-northstar-estate"
    variant["targetFleet"]["id"] = "runtime-northstar-fleet"
    variant["greenfield"]["sddcId"] = "runtime-m01"
    variant["greenfield"]["vcenterHostname"] = "runtime-vc.northstar.example"
    variant["greenfield"]["vcenterVmSize"] = "large"
    variant["greenfield"]["vcenterStorageSize"] = "xlstorage"
    variant["greenfield"]["nsxManagerHostnames"] = ["runtime-nsx-01", "runtime-nsx-02", "runtime-nsx-03"]
    variant["greenfield"]["nsxVipFqdn"] = "runtime-nsx.northstar.example"
    variant["greenfield"]["nsxManagerSize"] = "large"
    variant["greenfield"]["nsxTransportVlanId"] = 141
    variant["greenfield"]["vsanEsaEnabled"] = False
    variant["greenfield"]["vsanFailuresToTolerate"] = 2
    variant["managementDomain"]["id"] = "runtime-m01"
    id_map: dict[str, str] = {}
    for component in variant["components"]:
        old_id = component["id"]
        component["id"] = f"rt-{old_id}"
        id_map[old_id] = component["id"]
        if "hostname" in component:
            component["hostname"] = f"rt-{component['hostname']}"
    variant["managementDomain"]["witnessComponentId"] = id_map[
        variant["managementDomain"]["witnessComponentId"]
    ]
    variant["recoveryPair"]["id"] = "runtime-recovery-pair"
    return variant


def main() -> int:
    # Binding requirement: the submitted greenfield artifact is validated against
    # the installer's own tagged SddcSpec schema before any other acceptance check.
    sddc = load_json(SDDC_PATH)
    openapi = load_json(OPENAPI_PATH)
    assert_schema(
        sddc,
        openapi["components"]["schemas"]["SddcSpec"],
        openapi,
        "greenfield SddcSpec",
    )

    if hashlib.sha256(OPENAPI_PATH.read_bytes()).hexdigest() != EXPECTED_SPEC_SHA256:
        raise VerificationFailure("pinned installer OpenAPI document hash is incorrect")
    provenance = load_json(PROVENANCE_PATH)
    if (
        provenance.get("tag") != "9.1.0.0"
        or provenance.get("commitSha") != "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
        or provenance.get("sha256") != EXPECTED_SPEC_SHA256
        or provenance.get("license") != "Apache-2.0"
    ):
        raise VerificationFailure("installer specification provenance is incorrect")
    if openapi.get("info", {}).get("version") != "9.1.0.0":
        raise VerificationFailure("installer specification is not version 9.1.0.0")

    estate = load_json(ESTATE_PATH)
    snapshot = load_json(SNAPSHOT_PATH)
    plan_schema = load_json(PLAN_SCHEMA_PATH)
    topology_schema = load_json(TOPOLOGY_SCHEMA_PATH)
    verify_sddc_semantics(sddc, estate)

    topology = load_json(TOPOLOGY_PATH)
    plan = load_json(PLAN_PATH)
    assert_schema(topology, topology_schema, topology_schema, "site topology")
    assert_schema(plan, plan_schema, plan_schema, "migration plan")
    verify_topology_semantics(topology, estate, sddc)
    verify_plan_semantics(plan, estate, snapshot)
    for output_name in OUTPUT_NAMES:
        assert_canonical_file(ARTIFACT_DIR / output_name)
    verify_research(estate)

    verify_sdk_source()

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
        temporary_path = Path(temporary)
        first = temporary_path / "first"
        second = temporary_path / "second"
        first.mkdir()
        second.mkdir()
        for output_name in OUTPUT_NAMES:
            (first / output_name).write_text('{"stale":true}\n', encoding="utf-8")
        run_module(ESTATE_PATH, SNAPSHOT_PATH, first)
        run_module(ESTATE_PATH, SNAPSHOT_PATH, second)
        verify_artifact_set(first, estate, snapshot, openapi, topology_schema, plan_schema)
        for output_name in OUTPUT_NAMES:
            generated = (first / output_name).read_bytes()
            if generated != (second / output_name).read_bytes():
                raise VerificationFailure(f"module output is not deterministic: {output_name}")
            if generated != (ARTIFACT_DIR / output_name).read_bytes():
                raise VerificationFailure(f"checked artifact was not generated by the module: {output_name}")

        variant = runtime_variant(estate)
        variant_path = temporary_path / "estate-variant.json"
        variant_path.write_text(json.dumps(variant, indent=2) + "\n", encoding="utf-8")
        variant_output = temporary_path / "variant-output"
        variant_output.mkdir()
        run_module(variant_path, SNAPSHOT_PATH, variant_output)
        verify_artifact_set(variant_output, variant, snapshot, openapi, topology_schema, plan_schema)

    print("PASS: SddcSpec schema, stretched topology, migration plan, SDK generation, and determinism")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except subprocess.TimeoutExpired:
        print("FAIL: PowerShell module generation timed out", file=sys.stderr)
        raise SystemExit(1)
