#!/usr/bin/env python3
"""Protected, offline acceptance verifier for the VCF architecture artifact."""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing required file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as error:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {error}")


def resolve_ref(root_schema: dict[str, Any], reference: str) -> Any:
    if not reference.startswith("#/"):
        fail(f"unsupported non-local schema reference: {reference}")
    value: Any = root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[part]
        except (KeyError, TypeError):
            fail(f"unresolvable schema reference: {reference}")
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
    return True


def schema_errors(
    value: Any,
    schema: Any,
    root_schema: dict[str, Any],
    path: str = "$",
) -> list[str]:
    if schema is True:
        return []
    if schema is False:
        return [f"{path}: forbidden by schema"]
    if not isinstance(schema, dict):
        return [f"{path}: invalid schema node"]

    if "$ref" in schema:
        return schema_errors(value, resolve_ref(root_schema, schema["$ref"]), root_schema, path)

    errors: list[str] = []
    if value is None and schema.get("nullable") is True:
        return errors

    if "allOf" in schema:
        for branch in schema["allOf"]:
            errors.extend(schema_errors(value, branch, root_schema, path))
    if "anyOf" in schema:
        branch_errors = [schema_errors(value, branch, root_schema, path) for branch in schema["anyOf"]]
        if all(branch for branch in branch_errors):
            errors.append(f"{path}: does not satisfy anyOf")
    if "oneOf" in schema:
        matches = sum(not schema_errors(value, branch, root_schema, path) for branch in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: satisfies {matches} oneOf branches")
    if "not" in schema and not schema_errors(value, schema["not"], root_schema, path):
        errors.append(f"{path}: satisfies forbidden schema")

    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        valid_type = any(type_matches(value, item) for item in expected_type)
    elif isinstance(expected_type, str):
        valid_type = type_matches(value, expected_type)
    else:
        valid_type = True
    if not valid_type:
        errors.append(f"{path}: expected {expected_type}, got {type(value).__name__}")
        return errors

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} is outside enum")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                errors.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for name, item in value.items():
            item_path = f"{path}.{name}"
            if name in properties:
                errors.extend(schema_errors(item, properties[name], root_schema, item_path))
            elif additional is False:
                errors.append(f"{path}: unexpected property {name!r}")
            elif isinstance(additional, dict):
                errors.extend(schema_errors(item, additional, root_schema, item_path))
        if len(value) < schema.get("minProperties", 0):
            errors.append(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            errors.append(f"{path}: too many properties")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: too many items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(canonical) != len(set(canonical)):
                errors.append(f"{path}: items are not unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                errors.extend(schema_errors(item, item_schema, root_schema, f"{path}[{index}]"))

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: string is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: string is too long")
        pattern = schema.get("pattern")
        if pattern is not None:
            try:
                matched = re.search(pattern, value) is not None
            except re.error as error:
                fail(f"invalid protected schema pattern {pattern!r}: {error}")
            if not matched:
                errors.append(f"{path}: string does not match {pattern!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: value is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: value is above maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: value is not above exclusive minimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: value is not below exclusive maximum")

    return errors


def validate_sddc_before_all_other_checks() -> tuple[dict[str, Any], dict[str, Any]]:
    """The installer specification's SddcSpec is deliberately the first gate."""
    artifact_path = ROOT / "architecture.json"
    spec_path = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        installer_spec = json.loads(spec_path.read_text(encoding="utf-8"))
        sddc_spec = artifact["greenfield"]["sddcSpec"]
        schema = installer_spec["components"]["schemas"]["SddcSpec"]
    except FileNotFoundError as error:
        fail(f"SddcSpec schema validation failed: missing {Path(error.filename).name}")
    except json.JSONDecodeError as error:
        fail(f"SddcSpec schema validation failed: invalid JSON: {error}")
    except (KeyError, TypeError):
        fail("SddcSpec schema validation failed: architecture.greenfield.sddcSpec is missing")
    errors = schema_errors(sddc_spec, schema, installer_spec, "$.greenfield.sddcSpec")
    if errors:
        fail("SddcSpec schema validation failed:\n" + "\n".join(errors[:25]))
    return artifact, installer_spec


def require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def minimum_workload_hosts(scenario: dict[str, Any]) -> int:
    profile = scenario["hostProfile"]
    policy = scenario["capacityPolicy"]
    demand = scenario["workloadDemand"]
    available = len(scenario["hosts"])
    cores = profile["socketsPerHost"] * profile["coresPerSocket"]
    for count in range(2, available + 1):
        vcpu = ((count - 1) * cores * policy["cpuVcpuPerCore"]
                - policy["managementReserveVcpu"])
        memory = ((count - 1) * profile["memoryGiBPerHost"]
                  - policy["managementReserveMemoryGiB"])
        storage_numerator = (
            count
            * profile["rawVsanTiBPerHost"]
            * policy["vsanFtt1EfficiencyNumerator"]
            * (100 - policy["vsanFreeSlackPercent"])
        )
        storage_denominator = policy["vsanFtt1EfficiencyDenominator"] * 100
        storage = storage_numerator // storage_denominator
        if (vcpu >= demand["vcpu"] and memory >= demand["memoryGiB"]
                and storage >= demand["usableStorageTiB"]):
            return count
    fail("the supplied hosts cannot meet the workload demand under N+1")


def topology_requirements(
    scenario: dict[str, Any], compatibility: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    profile = scenario["hostProfile"]
    license_rules = compatibility["greenfield"]["licenseAccounting"]
    licensed_cores_per_host = profile["socketsPerHost"] * max(
        profile["coresPerSocket"], license_rules["minimumLicensedCoresPerSocket"]
    )
    workload_hosts = minimum_workload_hosts(scenario)
    requirements: dict[str, dict[str, Any]] = {}
    for topology in compatibility["greenfield"]["supportedTopologies"]:
        if not topology["greenfieldSupported"]:
            continue
        if topology["workloadsOnManagementDomain"]:
            hosts = max(workload_hosts, topology["minimumManagementHosts"])
        else:
            hosts = workload_hosts + topology["dedicatedManagementHosts"]
        cores = hosts * licensed_cores_per_host
        raw_tib = hosts * profile["rawVsanTiBPerHost"]
        reasons: list[str] = []
        if cores > scenario["entitlements"]["vcfCores"]:
            reasons.append("VCF_CORE_ENTITLEMENT")
        if raw_tib > scenario["entitlements"]["vsanRawTiB"]:
            reasons.append("VSAN_RAW_TIB_ENTITLEMENT")
        requirements[topology["id"]] = {
            "requiredHosts": hosts,
            "requiredVcfCores": cores,
            "requiredVsanRawTiB": raw_tib,
            "reasonCodes": reasons,
        }
    return requirements


def expected_network(
    network: dict[str, Any], design: dict[str, Any]
) -> dict[str, Any]:
    return {
        "networkType": network["networkType"],
        "subnet": network["subnet"],
        "gateway": network["gateway"],
        "subnetMask": network["subnetMask"],
        "includeIpAddressRanges": [{
            "startIpAddress": network["startIpAddress"],
            "endIpAddress": network["endIpAddress"],
        }],
        "vlanId": network["vlanId"],
        "mtu": network["mtu"],
        "teamingPolicy": design["networkTeamingPolicy"],
        "ipAddressVersion": design["ipAddressVersion"],
        "ipAddressAssignmentMode": design["ipAddressAssignmentMode"],
    }


def verify_greenfield(
    greenfield: dict[str, Any],
    scenario: dict[str, Any],
    compatibility: dict[str, Any],
) -> None:
    requirements = topology_requirements(scenario, compatibility)
    eligible = [name for name, values in requirements.items() if not values["reasonCodes"]]
    require_equal(eligible, ["CONSOLIDATED_MANAGEMENT_DOMAIN"], "eligible topology set")
    require_equal(greenfield["selectedTopology"], eligible[0], "selected topology")
    require_equal(greenfield["topologyDecision"]["selected"], eligible[0], "decision selection")
    expected_excluded = []
    for name, values in requirements.items():
        if values["reasonCodes"]:
            expected_excluded.append({"topology": name, **values})
    require_equal(greenfield["topologyDecision"]["excluded"], expected_excluded,
                  "excluded topology calculations")

    selected = requirements[eligible[0]]
    profile = scenario["hostProfile"]
    policy = scenario["capacityPolicy"]
    design = scenario["deploymentDesign"]
    count = selected["requiredHosts"]
    cores_per_host = profile["socketsPerHost"] * profile["coresPerSocket"]
    expected_capacity = {
        "hostCount": count,
        "licensedVcfCores": selected["requiredVcfCores"],
        "licensedVsanRawTiB": selected["requiredVsanRawTiB"],
        "nPlusOneWorkloadVcpu": (
            (count - 1) * cores_per_host * policy["cpuVcpuPerCore"]
            - policy["managementReserveVcpu"]
        ),
        "nPlusOneWorkloadMemoryGiB": (
            (count - 1) * profile["memoryGiBPerHost"]
            - policy["managementReserveMemoryGiB"]
        ),
        "usableVsanTiBAtFtt1AndSlack": (
            count
            * profile["rawVsanTiBPerHost"]
            * policy["vsanFtt1EfficiencyNumerator"]
            * (100 - policy["vsanFreeSlackPercent"])
            // (policy["vsanFtt1EfficiencyDenominator"] * 100)
        ),
    }
    require_equal(greenfield["capacity"], expected_capacity, "capacity calculation")
    demand = scenario["workloadDemand"]
    if expected_capacity["nPlusOneWorkloadVcpu"] < demand["vcpu"]:
        fail("selected topology misses N+1 vCPU demand")
    if expected_capacity["nPlusOneWorkloadMemoryGiB"] < demand["memoryGiB"]:
        fail("selected topology misses N+1 memory demand")
    if expected_capacity["usableVsanTiBAtFtt1AndSlack"] < demand["usableStorageTiB"]:
        fail("selected topology misses usable storage demand")

    expected_placement = [
        {"hostname": host["hostname"], "site": scenario["site"]["id"], "rack": host["rack"]}
        for host in scenario["hosts"]
    ]
    require_equal(greenfield["hostPlacement"], expected_placement, "host placement")
    rack_distribution = {
        rack: sum(host["rack"] == rack for host in scenario["hosts"])
        for rack in scenario["site"]["racks"]
    }
    expected_availability = {
        "hostFailuresToTolerate": scenario["availability"]["minimumHostFailuresToTolerate"],
        "siteFailureTolerance": scenario["site"]["siteFailureToleranceRequired"],
        "rackDistribution": rack_distribution,
    }
    require_equal(greenfield["availability"], expected_availability, "availability design")

    spec = greenfield["sddcSpec"]
    names = scenario["names"]
    networking = scenario["networking"]
    bom = compatibility["targetBom"]
    require_equal(spec["sddcId"], names["sddcId"], "SddcSpec sddcId")
    require_equal(spec["workflowType"], compatibility["greenfield"]["workflowType"],
                  "SddcSpec workflowType")
    require_equal(spec["version"], bom["VCF_INSTALLER"], "SddcSpec version")
    require_equal(spec["vcfInstanceName"], names["vcfInstanceName"], "VCF instance name")
    require_equal(spec["managementPoolName"], names["managementPoolName"], "network pool name")
    require_equal([item["hostname"] for item in spec["hostSpecs"]],
                  [item["hostname"] for item in scenario["hosts"]], "SddcSpec hosts")
    require_equal(spec["dnsSpec"], {
        "subdomain": networking["dnsSubdomain"],
        "nameservers": networking["nameServers"],
    }, "DNS design")
    require_equal(spec["ntpServers"], networking["ntpServers"], "NTP design")
    require_equal(spec["networkSpecs"],
                  [expected_network(item, design) for item in networking["networks"]],
                  "network design")
    require_equal(spec["vcenterSpec"], {
        "vcenterHostname": names["vcenterHostname"],
        "rootVcenterPassword": names["vcenterRootPassword"],
        "vmSize": design["vcenterVmSize"],
        "storageSize": design["vcenterStorageSize"],
        "version": bom["VCENTER"],
        "useExistingDeployment": False,
    }, "vCenter design")
    require_equal(spec["clusterSpec"], {
        "datacenterName": names["datacenterName"],
        "clusterName": names["clusterName"],
    }, "cluster design")
    require_equal(spec["dvsSpecs"], [{
        "dvsName": names["dvsName"],
        "networks": [item["networkType"] for item in networking["networks"]],
        "mtu": networking["dvsMtu"],
        "vmnicsToUplinks": design["dvsVmnicsToUplinks"],
    }], "DVS design")
    require_equal(spec["nsxtSpec"], {
        "nsxtManagers": [{"hostname": item} for item in names["nsxManagerHostnames"]],
        "nsxtManagerSize": design["nsxtManagerSize"],
        "vipFqdn": names["nsxVipFqdn"],
        "transportVlanId": networking["nsxTransportVlanId"],
        "version": bom["NSX"],
        "useExistingDeployment": False,
    }, "NSX design")
    require_equal(spec["datastoreSpec"], {"vsanSpec": {
        "datastoreName": names["vsanDatastoreName"],
        "vsanDedup": design["vsanDedup"],
        "esaConfig": {"enabled": True},
        "failuresToTolerate": scenario["availability"]["minimumHostFailuresToTolerate"],
    }}, "vSAN design")
    require_equal(spec["sddcManagerSpec"], {
        "hostname": names["sddcManagerHostname"],
        "version": bom["SDDC_MANAGER"],
        "useExistingDeployment": False,
    }, "SDDC Manager design")
    require_equal(spec["vspClusterSpec"], {
        "platformFqdn": names["vspPlatformFqdn"],
        "ipv4Pool": {"addresses": networking["vspIpv4Addresses"]},
        "size": design["vspClusterSize"],
        "instanceFqdn": names["vspInstanceFqdn"],
        "fleetFqdn": names["vspFleetFqdn"],
        "version": bom["VCF_SERVICES_RUNTIME"],
        "useExistingDeployment": False,
    }, "VCF Services Runtime design")
    require_equal(spec["fleetLcmSpec"], {
        "version": bom["VCF_FLEET_LCM"], "size": design["fleetLcmSize"]
    },
                  "Fleet LCM design")
    require_equal(spec["sddcLcmSpec"], {
        "version": bom["VCF_SDDC_LCM"], "size": design["sddcLcmSize"]
    },
                  "SDDC LCM design")
    require_equal(spec["fleetDepotSpec"], {
        "version": bom["VCF_DEPOT_SERVICE"], "size": design["fleetDepotSize"]
    },
                  "depot design")
    require_equal(spec["telemetryAcceptorSpec"], {
        "version": bom["VCF_TELEMETRY"], "size": design["telemetryAcceptorSize"]
    }, "telemetry design")
    require_equal(spec["vidbSpec"], {
        "hostname": names["vidbHostname"],
        "version": bom["VCF_IDENTITY_BROKER"],
        "size": design["vidbSize"],
    }, "identity broker design")
    require_equal(spec["vcfOperationsSpec"], {
        "nodes": [
            {"hostname": host, "type": design["vcfOperationsNodeTypes"][index]}
            for index, host in enumerate(names["vcfOperationsNodes"])
        ],
        "applianceSize": design["vcfOperationsApplianceSize"],
        "loadBalancerFqdn": names["vcfOperationsLoadBalancerFqdn"],
        "useExistingDeployment": False,
        "version": bom["VCF_OPERATIONS"],
    }, "VCF Operations design")
    require_equal(spec["vcfAutomationSpec"], {
        "hostname": names["vcfAutomationHostname"],
        "platformFqdn": names["vspPlatformFqdn"],
        "ipPool": networking["vcfAutomationIpPool"],
        "internalClusterCidr": networking["vcfAutomationInternalClusterCidr"],
        "nodePrefix": design["vcfAutomationNodePrefix"],
        "size": design["vcfAutomationSize"],
        "version": bom["VCF_AUTOMATION"],
        "useExistingDeployment": False,
    }, "VCF Automation design")
    fleet_network = next(
        item for item in networking["networks"]
        if item["networkType"] == "FLEET_MANAGEMENT"
    )
    require_equal(spec["vcfManagementComponentsInfrastructureSpec"], {
        "localRegionNetwork": {
            "networkName": fleet_network["networkType"],
            "subnetMask": fleet_network["subnetMask"],
            "gateway": fleet_network["gateway"],
        }
    }, "VCF management components network")
    require_equal(spec["licenseServerSpec"], {
        "hostname": names["licenseServerHostname"],
        "version": bom["VCF_LICENSE_SERVER"],
        "useExistingDeployment": False,
    }, "license server design")
    require_equal(spec["skipEsxThumbprintValidation"],
                  design["skipEsxThumbprintValidation"], "ESX thumbprint validation")
    require_equal(spec["skipGatewayPingValidation"],
                  design["skipGatewayPingValidation"], "gateway validation")


def verify_migration(
    plan: dict[str, Any], inventory: dict[str, Any], compatibility: dict[str, Any]
) -> None:
    require_equal(plan["estateId"], inventory["estateId"], "migration estate id")
    require_equal(plan["targetRelease"], compatibility["upgrade"]["targetRelease"],
                  "migration target release")
    steps = plan["steps"]
    require_equal([step["order"] for step in steps], list(range(1, len(steps) + 1)),
                  "migration order")
    state = {
        item["id"]: {"component": item["component"], "version": item["version"]}
        for item in inventory["components"]
    }
    if len(state) != len(inventory["components"]):
        fail("inventory component ids are not unique")
    rules = compatibility["upgrade"]["transitionRules"]
    seen: set[str] = set()
    for step in steps:
        component_id = step["componentId"]
        if component_id not in state:
            fail(f"migration step names unknown component id {component_id!r}")
        current = state[component_id]
        require_equal(step["component"], current["component"],
                      f"current component at step {step['order']}")
        require_equal(step["fromVersion"], current["version"],
                      f"current version at step {step['order']}")
        matches = [rule for rule in rules if (
            rule["componentId"] == component_id
            and rule["fromComponent"] == step["component"]
            and rule["fromVersion"] == step["fromVersion"]
            and rule["targetComponent"] == step["targetComponent"]
            and rule["targetVersion"] == step["targetVersion"]
        )]
        if len(matches) != 1:
            fail(f"step {step['order']} is not one pinned supported transition")
        rule = matches[0]
        require_equal(set(step["gates"]), set(rule["requiredGates"]),
                      f"gates at step {step['order']}")
        for prerequisite in rule["after"]:
            prerequisite_state = state.get(prerequisite["componentId"])
            if prerequisite_state is None or prerequisite_state["version"] != prerequisite["version"]:
                fail(f"step {step['order']} runs before prerequisite "
                     f"{prerequisite['componentId']} {prerequisite['version']}")
        state[component_id] = {
            "component": step["targetComponent"],
            "version": step["targetVersion"],
        }
        seen.add(component_id)
    require_equal(seen, set(state), "components represented in migration plan")
    require_equal(state, compatibility["upgrade"]["finalTargets"], "migration final state")


def verify_research_sources(compatibility: dict[str, Any]) -> None:
    sources = load_json(ROOT / "research_sources.json")
    if not isinstance(sources, list) or len(sources) < 2:
        fail("research_sources.json must contain at least two consulted sources")
    required = {"publisher", "title", "url", "consultedOn", "claimChecked"}
    earliest = date.fromisoformat(compatibility["capturedOn"])
    topic_text: list[str] = []
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            fail(f"research source {index} must be an object")
        missing = required - set(source)
        if missing:
            fail(f"research source {index} is missing {sorted(missing)!r}")
        for field in required:
            if not isinstance(source[field], str) or not source[field].strip():
                fail(f"research source {index} has an empty {field}")
        publisher = source["publisher"].lower()
        if "broadcom" not in publisher and "vmware" not in publisher:
            fail(f"research source {index} does not identify a Broadcom publisher")
        parsed = urlparse(source["url"])
        host = (parsed.hostname or "").lower()
        broadcom_host = host == "broadcom.com" or host.endswith(".broadcom.com")
        vmware_host = host == "vmware.com" or host.endswith(".vmware.com")
        if parsed.scheme != "https" or not (
            broadcom_host or vmware_host or host == "vmware.github.io"
        ):
            fail(f"research source {index} is not a reachable Broadcom-published URL")
        try:
            consulted = date.fromisoformat(source["consultedOn"])
        except ValueError:
            fail(f"research source {index} has a non-ISO consultation date")
        if consulted < earliest:
            fail(f"research source {index} predates the supplied compatibility snapshot")
        topic_text.append(f"{source['title']} {source['claimChecked']}".lower())
    corpus = " ".join(topic_text)
    required_topics = {
        "compatibility or interoperability": ("compatib", "interoperab"),
        "licensing or entitlement": ("licens", "entitlement", "core", "tib"),
        "upgrade path or sequence": ("upgrade", "transition", "sequence"),
    }
    for label, signals in required_topics.items():
        if not any(signal in corpus for signal in signals):
            fail(f"research sources do not record a {label} check")


def verify_stdlib_package(artifact: dict[str, Any]) -> None:
    package = ROOT / "vcf_architect"
    required = [package / "__init__.py", package / "__main__.py"]
    for path in required:
        if not path.is_file():
            fail(f"missing package file: {path.relative_to(ROOT)}")
    python_files = sorted(package.rglob("*.py"))
    if not python_files:
        fail("vcf_architect contains no Python source")
    standard = set(getattr(sys, "stdlib_module_names", {
        "argparse", "collections", "copy", "dataclasses", "datetime", "decimal",
        "enum", "functools", "heapq", "itertools", "json", "math", "pathlib",
        "re", "statistics", "sys", "typing",
    }))
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            fail(f"syntax error in {path.relative_to(ROOT)}: {error}")
        for node in ast.walk(tree):
            root: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root not in standard and root != "vcf_architect":
                        fail(f"non-stdlib import {alias.name!r} in {path.relative_to(ROOT)}")
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                root = node.module.split(".", 1)[0]
                if root not in standard and root != "vcf_architect":
                    fail(f"non-stdlib import {node.module!r} in {path.relative_to(ROOT)}")

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
        temp = Path(temporary)
        cli_outputs = [temp / "cli-one.json", temp / "cli-two.json"]
        command_prefix = [
            sys.executable, "-B", "-m", "vcf_architect",
            "--scenario", "fixtures/greenfield-scenario.json",
            "--inventory", "fixtures/estate-inventory.json",
            "--compatibility", "fixtures/compatibility-snapshot.json",
        ]
        for output in cli_outputs:
            result = subprocess.run(
                [*command_prefix, "--output", str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                fail("package CLI failed:\n" + result.stdout + result.stderr)
        first = load_json(cli_outputs[0])
        second = load_json(cli_outputs[1])
        require_equal(first, artifact, "CLI output versus committed architecture")
        require_equal(second, first, "deterministic CLI output")

        alternate_scenario = load_json(ROOT / "fixtures/greenfield-scenario.json")
        alternate_inventory = load_json(ROOT / "fixtures/estate-inventory.json")
        alternate_compatibility = load_json(ROOT / "fixtures/compatibility-snapshot.json")
        alternate_scenario["names"]["sddcId"] = "alt-vcf01"
        alternate_scenario["names"]["dvsName"] = "ALT-MGMT-VDS01"
        alternate_scenario["hosts"][0]["hostname"] = "alt-r1-esx01"
        alternate_scenario["networking"]["dnsSubdomain"] = "alt.example.com"
        alternate_scenario["networking"]["networks"][0]["vlanId"] = 111
        alternate_scenario["capacityPolicy"]["managementReserveMemoryGiB"] = 500
        alternate_scenario["deploymentDesign"]["vcenterVmSize"] = "small"
        alternate_scenario["deploymentDesign"]["dvsVmnicsToUplinks"] = [
            {"id": "vmnic2", "uplink": "uplink1"},
            {"id": "vmnic3", "uplink": "uplink2"},
        ]
        alternate_inventory["estateId"] = "alternate-estate"
        alternate_compatibility["targetBom"]["NSX"] = "9.1.0.0999"
        alternate_compatibility["upgrade"]["transitionRules"][0]["requiredGates"] = [
            "ALTERNATE_SOURCE_HEALTH_GATE"
        ]
        alternate_scenario_path = temp / "scenario.json"
        alternate_inventory_path = temp / "inventory.json"
        alternate_compatibility_path = temp / "compatibility.json"
        alternate_scenario_path.write_text(json.dumps(alternate_scenario), encoding="utf-8")
        alternate_inventory_path.write_text(json.dumps(alternate_inventory), encoding="utf-8")
        alternate_compatibility_path.write_text(
            json.dumps(alternate_compatibility), encoding="utf-8"
        )
        alternate_output = temp / "alternate.json"
        alternate_result = subprocess.run(
            [
                sys.executable, "-B", "-m", "vcf_architect",
                "--scenario", str(alternate_scenario_path),
                "--inventory", str(alternate_inventory_path),
                "--compatibility", str(alternate_compatibility_path),
                "--output", str(alternate_output),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if alternate_result.returncode != 0:
            fail("package CLI failed for supplied alternate mappings:\n"
                 + alternate_result.stdout + alternate_result.stderr)
        alternate = load_json(alternate_output)
        require_equal(alternate["greenfield"]["sddcSpec"]["sddcId"], "alt-vcf01",
                      "CLI scenario path is honored")
        require_equal(alternate["migrationPlan"]["estateId"], "alternate-estate",
                      "CLI inventory path is honored")
        alternate_spec = alternate["greenfield"]["sddcSpec"]
        require_equal(alternate_spec["hostSpecs"][0]["hostname"], "alt-r1-esx01",
                      "CLI scenario hosts are honored")
        require_equal(alternate["greenfield"]["hostPlacement"][0]["hostname"],
                      "alt-r1-esx01", "host placement derives from the scenario")
        require_equal(alternate_spec["dnsSpec"]["subdomain"], "alt.example.com",
                      "CLI scenario DNS is honored")
        require_equal(alternate_spec["networkSpecs"][0]["vlanId"], 111,
                      "CLI scenario networks are honored")
        require_equal(alternate_spec["dvsSpecs"][0], {
            **artifact["greenfield"]["sddcSpec"]["dvsSpecs"][0],
            "dvsName": "ALT-MGMT-VDS01",
            "vmnicsToUplinks": alternate_scenario["deploymentDesign"]["dvsVmnicsToUplinks"],
        }, "DVS design derives from the scenario")
        require_equal(alternate_spec["vcenterSpec"]["vmSize"], "small",
                      "deployment design sizing is honored")
        require_equal(alternate_spec["nsxtSpec"]["version"], "9.1.0.0999",
                      "compatibility BOM is honored")
        require_equal(alternate["migrationPlan"]["steps"][0]["gates"],
                      ["ALTERNATE_SOURCE_HEALTH_GATE"],
                      "migration gates derive from compatibility input")
        require_equal(
            alternate["greenfield"]["capacity"]["nPlusOneWorkloadMemoryGiB"], 3084,
            "capacity policy derives from the scenario",
        )

        api_output = temp / "api.json"
        api_probe = (
            "import json, pathlib; "
            "from vcf_architect import build_architecture; "
            "load=lambda p: json.loads(pathlib.Path(p).read_text(encoding='utf-8')); "
            "result=build_architecture(load('fixtures/greenfield-scenario.json'), "
            "load('fixtures/estate-inventory.json'), load('fixtures/compatibility-snapshot.json')); "
            f"pathlib.Path({str(api_output)!r}).write_text(json.dumps(result, sort_keys=True), encoding='utf-8')"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", api_probe],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            fail("public build_architecture API failed:\n" + result.stdout + result.stderr)
        require_equal(load_json(api_output), artifact, "public API output")


def main() -> int:
    artifact, _installer_spec = validate_sddc_before_all_other_checks()

    scenario = load_json(ROOT / "fixtures/greenfield-scenario.json")
    inventory = load_json(ROOT / "fixtures/estate-inventory.json")
    compatibility = load_json(ROOT / "fixtures/compatibility-snapshot.json")
    greenfield_schema = load_json(ROOT / "schemas/greenfield-architecture.schema.json")
    migration_schema = load_json(ROOT / "schemas/migration-plan.schema.json")

    require_equal(set(artifact), {"artifactVersion", "greenfield", "migrationPlan"},
                  "architecture top-level members")
    require_equal(artifact["artifactVersion"], "1.0", "artifact version")
    greenfield_errors = schema_errors(
        artifact["greenfield"], greenfield_schema, greenfield_schema, "$.greenfield"
    )
    if greenfield_errors:
        fail("greenfield architecture schema failed:\n" + "\n".join(greenfield_errors[:25]))
    migration_errors = schema_errors(
        artifact["migrationPlan"], migration_schema, migration_schema, "$.migrationPlan"
    )
    if migration_errors:
        fail("migration plan schema failed:\n" + "\n".join(migration_errors[:25]))

    verify_research_sources(compatibility)
    verify_greenfield(artifact["greenfield"], scenario, compatibility)
    verify_migration(artifact["migrationPlan"], inventory, compatibility)
    verify_stdlib_package(artifact)
    print("PASS: VCF 9.1 architecture and migration plan are valid")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
