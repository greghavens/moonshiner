#!/usr/bin/env python3
"""Offline acceptance verifier for the VCF architecture deliverable."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
OPENAPI = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
MIGRATION_SCHEMA = ROOT / "schemas/migration-plan.schema.json"
ESTATE = ROOT / "fixtures/estate.json"
COMPATIBILITY = ROOT / "fixtures/compatibility-snapshot.json"
RESEARCH = ROOT / "research/consulted-sources.json"


class ContractError(AssertionError):
    """A deterministic artifact contract was not met."""


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as error:
        raise ContractError(f"missing artifact or contract: {path}") from error
    except json.JSONDecodeError as error:
        raise ContractError(f"invalid JSON in {path}: {error}") from error


def resolve_pointer(root_schema: dict[str, Any], pointer: str) -> Any:
    if not pointer.startswith("#/"):
        raise ContractError(f"only local JSON Schema references are supported: {pointer}")
    value: Any = root_schema
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            raise ContractError(f"unresolvable schema reference: {pointer}")
        value = value[part]
    return value


def json_type_matches(value: Any, expected: str) -> bool:
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
    raise ContractError(f"unsupported schema type in protected contract: {expected}")


def validate_json_schema(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> None:
    """Validate the JSON Schema vocabulary used by the protected contracts."""
    if "$ref" in schema:
        validate_json_schema(value, resolve_pointer(root_schema, schema["$ref"]), root_schema, path)
        return

    if "enum" in schema and value not in schema["enum"]:
        raise ContractError(f"{path}: {value!r} is not one of {schema['enum']!r}")
    if "const" in schema and value != schema["const"]:
        raise ContractError(f"{path}: expected constant {schema['const']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(json_type_matches(value, item) for item in allowed):
            raise ContractError(f"{path}: expected type {expected_type!r}, got {type(value).__name__}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise ContractError(f"{path}: missing required properties {missing!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise ContractError(f"{path}: additional properties are forbidden: {extra!r}")
        for name, child in value.items():
            child_schema = properties.get(name)
            if child_schema is not None:
                validate_json_schema(child, child_schema, root_schema, f"{path}.{name}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ContractError(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ContractError(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                raise ContractError(f"{path}: array items must be unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                validate_json_schema(item, item_schema, root_schema, f"{path}[{index}]")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ContractError(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ContractError(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ContractError(f"{path}: value does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ContractError(f"{path}: value is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ContractError(f"{path}: value is above maximum {schema['maximum']}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def version_with_build(component: dict[str, Any]) -> str:
    return f"{component['version']}.{component['build']}"


def validate_research_record(record: Any) -> None:
    require(isinstance(record, dict), "research record must be a JSON object")
    sources = record.get("sources")
    require(isinstance(sources, list) and sources, "research record must contain consulted sources")

    for index, source in enumerate(sources):
        label = f"research source {index + 1}"
        require(isinstance(source, dict), f"{label} must be an object")
        for field in ("title", "url", "consultedAt"):
            require(
                isinstance(source.get(field), str) and source[field].strip(),
                f"{label} has no {field}",
            )

        url = source["url"]
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        require(
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and (hostname == "broadcom.com" or hostname.endswith(".broadcom.com")),
            f"{label} must use a live Broadcom HTTPS URL",
        )
        try:
            consulted_at = datetime.fromisoformat(source["consultedAt"].replace("Z", "+00:00"))
        except ValueError as error:
            raise ContractError(f"{label} has an invalid consultation time") from error
        require(consulted_at.tzinfo is not None, f"{label} consultation time needs a UTC offset")

        findings = source.get("findings")
        require(
            isinstance(findings, list)
            and findings
            and all(isinstance(item, str) and item.strip() for item in findings),
            f"{label} must record relevant conclusions",
        )


def validate_sddc_semantics(
    sddc: dict[str, Any], estate: dict[str, Any], compatibility: dict[str, Any]
) -> None:
    requirements = estate["requirements"]["greenfield"]
    target = compatibility["targetBundle"]

    require(sddc["sddcId"] == requirements["sddcId"], "SddcSpec uses the wrong SDDC id")
    require(sddc.get("workflowType") == "VCF", "greenfield workflowType must be VCF")
    require(sddc.get("version") == target["version"], "SddcSpec target bundle is wrong")
    require(
        sddc.get("vcfInstanceName") == requirements["vcfInstanceName"],
        "VCF instance name does not match the fixture",
    )
    actual_hosts = [host["hostname"] for host in sddc.get("hostSpecs", [])]
    expected_hosts = requirements["primarySite"]["hostnames"]
    require(
        len(actual_hosts) == len(set(actual_hosts)) == len(expected_hosts)
        and set(actual_hosts) == set(expected_hosts),
        "management host inventory does not match the four-host primary site",
    )

    services = requirements["services"]
    vc_target = target["components"]["VCENTER"]
    vcenter = sddc["vcenterSpec"]
    require(vcenter["vcenterHostname"] == services["vcenterHostname"], "wrong vCenter hostname")
    require(vcenter.get("version") == version_with_build(vc_target), "wrong vCenter target build")
    require(vcenter.get("useExistingDeployment") is False, "vCenter must be greenfield")

    manager_target = target["components"]["SDDC_MANAGER"]
    manager = sddc.get("sddcManagerSpec", {})
    require(manager.get("hostname") == services["sddcManagerHostname"], "wrong SDDC Manager hostname")
    require(manager.get("version") == version_with_build(manager_target), "wrong SDDC Manager target build")
    require(manager.get("useExistingDeployment") is False, "SDDC Manager must be greenfield")

    require(sddc["dnsSpec"] == requirements["dns"], "DNS design does not match the fixture")
    require(sddc.get("ntpServers") == requirements["ntpServers"], "NTP design does not match the fixture")
    require(sddc.get("skipEsxThumbprintValidation") is False, "ESX thumbprint validation must remain enabled")
    require(sddc.get("skipGatewayPingValidation") is False, "gateway validation must remain enabled")

    expected_networks = {item["networkType"]: item for item in requirements["networks"]}
    actual_networks = {item["networkType"]: item for item in sddc.get("networkSpecs", [])}
    require(
        len(sddc.get("networkSpecs", [])) == len(actual_networks) == len(expected_networks)
        and set(actual_networks) == set(expected_networks),
        "SddcSpec must contain every fixture network exactly once",
    )
    for network_type, expected in expected_networks.items():
        actual = actual_networks[network_type]
        for key in ("vlanId", "subnet", "gateway", "subnetMask", "mtu"):
            require(actual.get(key) == expected[key], f"{network_type} has the wrong {key}")
        require(
            actual.get("includeIpAddressRanges")
            == [{"startIpAddress": expected["rangeStart"], "endIpAddress": expected["rangeEnd"]}],
            f"{network_type} has the wrong address range",
        )

    dvs = sddc.get("dvsSpecs", [])
    require(dvs, "the design must contain a distributed-switch uplink design")
    assigned_networks: list[str] = []
    for switch in dvs:
        traffic_types = switch.get("networks", [])
        require(traffic_types, "each distributed switch must name its traffic types")
        require(
            set(traffic_types).issubset(expected_networks),
            "distributed switch names a traffic type outside the fixture",
        )
        assigned_networks.extend(traffic_types)
        required_mtu = max(expected_networks[item]["mtu"] for item in traffic_types)
        require(switch.get("mtu", 0) >= required_mtu, "distributed switch MTU is too small")
        mappings = switch.get("vmnicsToUplinks", [])
        require(
            len({item.get("id") for item in mappings}) >= 2
            and len({item.get("uplink") for item in mappings}) >= 2,
            "each distributed switch must use redundant physical NICs and uplinks",
        )
    require(
        len(assigned_networks) == len(set(assigned_networks))
        and set(assigned_networks) == set(expected_networks),
        "distributed-switch traffic assignments must cover each fixture network exactly once",
    )

    nsx_req = requirements["nsx"]
    nsx_target = target["components"]["NSX_T_MANAGER"]
    nsx = sddc.get("nsxtSpec", {})
    require(nsx.get("version") == version_with_build(nsx_target), "wrong NSX target build")
    require(nsx.get("useExistingDeployment") is False, "NSX must be a new deployment")
    require(nsx.get("transportVlanId") == nsx_req["transportVlanId"], "wrong NSX transport VLAN")
    require(nsx.get("vipFqdn") == nsx_req["vipFqdn"], "wrong NSX VIP")
    actual_nsx_nodes = [node.get("hostname") for node in nsx.get("nsxtManagers", [])]
    require(
        len(actual_nsx_nodes) == len(set(actual_nsx_nodes)) == len(nsx_req["managerHostnames"])
        and set(actual_nsx_nodes) == set(nsx_req["managerHostnames"]),
        "NSX must have the three fixture manager nodes",
    )
    expected_tep_subnets = [
        {
            "cidr": nsx_req["tepCidr"],
            "gateway": nsx_req["tepGateway"],
            "ipAddressPoolRanges": [
                {"start": nsx_req["tepRangeStart"], "end": nsx_req["tepRangeEnd"]}
            ],
        }
    ]
    require(
        nsx.get("ipAddressPoolSpec", {}).get("subnets") == expected_tep_subnets,
        "wrong NSX TEP pool",
    )

    vsan = sddc.get("datastoreSpec", {}).get("vsanSpec", {})
    require(vsan.get("esaConfig", {}).get("enabled") is True, "vSAN ESA must be enabled")
    require(
        vsan.get("failuresToTolerate")
        == requirements["availability"]["storageFailuresToTolerate"],
        "vSAN failures-to-tolerate does not match the availability requirement",
    )

    vsp = sddc.get("vspClusterSpec", {})
    require(vsp.get("platformFqdn") == services["vspPlatformFqdn"], "wrong VSP platform FQDN")
    require(vsp.get("instanceFqdn") == services["vspInstanceFqdn"], "wrong VSP instance FQDN")
    require(vsp.get("fleetFqdn") == services["vspFleetFqdn"], "wrong VSP fleet FQDN")
    require(vsp.get("useExistingDeployment") is False, "VSP must be greenfield")
    require(vsp.get("version") == target["version"], "VSP target version is wrong")
    fleet_network = expected_networks["FLEET_MANAGEMENT"]
    pool_start = ip_address(fleet_network["rangeStart"])
    pool_size = requirements["availability"]["managementServicesMinimumIPv4Addresses"]
    pool_end = pool_start + pool_size - 1
    require(pool_end <= ip_address(fleet_network["rangeEnd"]), "fixture cannot hold the service pool")
    require(
        vsp.get("ipv4Pool", {}).get("ipRange")
        == {"startIpAddress": str(pool_start), "endIpAddress": str(pool_end)},
        "VCF Management Services must reserve the fixture-sized address pool",
    )

    operations = sddc.get("vcfOperationsSpec", {})
    require(operations.get("version") == target["version"], "VCF Operations target version is wrong")
    require(operations.get("useExistingDeployment") is False, "VCF Operations must be greenfield")
    require(
        operations.get("loadBalancerFqdn") == services["vcfOperationsLoadBalancerFqdn"],
        "wrong VCF Operations load balancer FQDN",
    )
    actual_operations_nodes = [node.get("hostname") for node in operations.get("nodes", [])]
    require(
        len(actual_operations_nodes)
        == len(set(actual_operations_nodes))
        == len(services["vcfOperationsNodes"])
        and set(actual_operations_nodes) == set(services["vcfOperationsNodes"]),
        "VCF Operations must contain all three fixture nodes",
    )
    require(
        sddc.get("licenseServerSpec", {}).get("hostname") == services["licenseServerHostname"],
        "wrong license server hostname",
    )
    require(
        sddc.get("licenseServerSpec", {}).get("useExistingDeployment") is False,
        "license server must be greenfield",
    )


def expected_capacity(requirements: dict[str, Any], host_count: int) -> dict[str, int]:
    per_host = requirements["perHostCapacity"]
    availability = requirements["availability"]
    surviving_hosts = host_count - availability["hostFailuresToTolerate"]
    raw_after_failure = surviving_hosts * per_host["rawStorageGB"]
    ftt_divisor = availability["storageFailuresToTolerate"] + 1
    return {
        "physicalCores": surviving_hosts * per_host["physicalCores"],
        "memoryGiB": surviving_hosts * per_host["memoryGiB"],
        "protectedUsableStorageGB": (raw_after_failure // ftt_divisor) * 80 // 100,
    }


def validate_migration_semantics(
    plan: dict[str, Any], estate: dict[str, Any], compatibility: dict[str, Any]
) -> None:
    current_estate = estate["estate"]
    requirements = estate["requirements"]["greenfield"]
    assessment = compatibility["estateAssessment"]
    target_components = compatibility["targetBundle"]["components"]

    require(plan["estateId"] == current_estate["estateId"], "migration plan names the wrong estate")
    require(plan["targetBundle"] == compatibility["targetBundle"]["version"], "wrong migration target bundle")
    require(plan["strategy"] == assessment["requiredStrategy"], "wrong migration strategy")
    require(plan["directInPlaceUpgrade"] is assessment["directInPlaceUpgradeAllowed"], "direct-upgrade decision is wrong")
    require(plan["blockingComponentIds"] == assessment["blockingComponentIds"], "blocking component list is wrong")

    inventory = {item["componentId"]: item for item in current_estate["components"]}
    component_plan = {item["componentId"]: item for item in plan["componentPlan"]}
    require(len(component_plan) == len(plan["componentPlan"]), "duplicate component plan entries")
    require(set(component_plan) == set(inventory), "migration plan must name every and only inventoried component")
    for component_id, current in inventory.items():
        actual = component_plan[component_id]
        target = target_components[current["productType"]]
        authority = compatibility["componentPlans"][current["productType"]]
        require(actual["name"] == current["name"], f"{component_id}: wrong component name")
        require(actual["productType"] == current["productType"], f"{component_id}: wrong product type")
        require(actual["currentVersion"] == current["version"], f"{component_id}: wrong current version")
        require(actual["currentBuild"] == current["build"], f"{component_id}: wrong current build")
        require(actual["targetVersion"] == target["version"], f"{component_id}: wrong target version")
        require(actual["targetBuild"] == target["build"], f"{component_id}: wrong target build")
        require(actual["migrationMode"] == authority["migrationMode"], f"{component_id}: wrong migration mode")
        require(
            actual["sourceDisposition"] == authority["sourceDisposition"],
            f"{component_id}: wrong source disposition",
        )
        require(
            set(actual["gates"]) == set(authority["requiredGateIds"]),
            f"{component_id}: wrong gates",
        )

    nsx_current = inventory["nsx"]["build"]
    nsx_target = target_components["NSX_T_MANAGER"]["build"]
    require(nsx_current > nsx_target, "pinned estate no longer exercises a back-in-time component")
    require(
        component_plan["nsx"]["migrationMode"] == "replace-no-downgrade",
        "newer NSX must be replaced through greenfield migration, not downgraded",
    )

    sites = {item["role"]: item for item in plan["sitePlan"]}
    require(set(sites) == {"primary", "recovery"}, "site plan must contain primary and recovery roles")
    site_requirements = {
        "primary": requirements["primarySite"],
        "recovery": requirements["recoverySite"],
    }
    capacity_requirements = requirements["requiredCapacityAfterOneHostFailure"]
    for role in ("primary", "recovery"):
        actual = sites[role]
        expected_site = site_requirements[role]
        host_count = expected_site.get("managementHostCount", expected_site.get("hostCount"))
        require(actual["siteId"] == expected_site["siteId"], f"{role}: wrong site id")
        require(actual["hostCount"] == host_count, f"{role}: wrong host count")
        require(
            actual["failureReserveHosts"]
            == requirements["availability"]["hostFailuresToTolerate"],
            f"{role}: wrong host-failure reserve",
        )
        require(
            actual["capacityAfterOneHostFailure"] == expected_capacity(requirements, host_count),
            f"{role}: N+1 capacity arithmetic is wrong",
        )
        require(actual["requiredCapacity"] == capacity_requirements[role], f"{role}: wrong required capacity")
        require(actual["meetsRequiredCapacity"] is True, f"{role}: capacity must meet the fixture")
        for resource, needed in capacity_requirements[role].items():
            require(
                actual["capacityAfterOneHostFailure"][resource] >= needed,
                f"{role}: {resource} does not meet the post-failure requirement",
            )
    require(
        sites["primary"]["rpoMinutes"] == requirements["primarySite"]["rpoMinutes"],
        "primary-site RPO marker is wrong",
    )
    require(
        sites["recovery"]["rpoMinutes"] == requirements["recoverySite"]["rpoMinutes"],
        "recovery-site RPO is wrong",
    )

    steps = plan["steps"]
    require([item["order"] for item in steps] == list(range(1, len(steps) + 1)), "step order must be contiguous")
    require(
        [item["action"] for item in steps] == compatibility["requiredStepActions"],
        "migration actions are missing or out of order",
    )
    require(steps[0]["requires"] == [], "the first migration step must not depend on a later step")
    prior_actions: set[str] = set()
    for step in steps:
        require(
            set(step["requires"]).issubset(prior_actions),
            f"{step['action']}: dependencies must refer only to earlier actions",
        )
        prior_actions.add(step["action"])
    referenced = {component_id for step in steps for component_id in step["componentIds"]}
    require(referenced == set(inventory), "ordered steps do not cover every inventoried component")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
        output = Path(temporary) / "architecture"
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        command = [
            sys.executable,
            "-B",
            "-S",
            "-m",
            "vcf_architecture",
            "--estate",
            str(ESTATE),
            "--compatibility",
            str(COMPATIBILITY),
            "--output-dir",
            str(output),
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            raise ContractError(
                f"generator exited {completed.returncode}: {completed.stdout[-1200:]}"
            )

        # This is deliberately the first artifact check: validate the generated
        # SddcSpec against the tagged installer's own SddcSpec schema and refs.
        openapi = load_json(OPENAPI)
        sddc = load_json(output / "sddc-spec.json")
        try:
            sddc_schema = openapi["components"]["schemas"]["SddcSpec"]
        except (KeyError, TypeError) as error:
            raise ContractError("protected installer contract has no SddcSpec schema") from error
        validate_json_schema(sddc, sddc_schema, openapi)

        expected_outputs = ["migration-plan.json", "sddc-spec.json"]
        require(
            sorted(path.name for path in output.iterdir() if path.is_file()) == expected_outputs,
            "generator must write exactly the two requested JSON documents",
        )
        repeat_output = Path(temporary) / "architecture-repeat"
        repeat_command = command[:-1] + [str(repeat_output)]
        repeated = subprocess.run(
            repeat_command,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        require(repeated.returncode == 0, "generator failed on deterministic repeat run")
        require(
            sorted(path.name for path in repeat_output.iterdir() if path.is_file())
            == expected_outputs,
            "repeat run wrote unexpected files",
        )
        for filename in expected_outputs:
            require(
                (output / filename).read_bytes() == (repeat_output / filename).read_bytes(),
                f"{filename} is not byte-deterministic",
            )

        migration = load_json(output / "migration-plan.json")
        migration_schema = load_json(MIGRATION_SCHEMA)
        validate_json_schema(migration, migration_schema, migration_schema)

        estate = load_json(ESTATE)
        compatibility = load_json(COMPATIBILITY)
        validate_sddc_semantics(sddc, estate, compatibility)
        validate_migration_semantics(migration, estate, compatibility)
        validate_research_record(load_json(RESEARCH))

    print("ok: research record, SddcSpec, greenfield design, and migration plan verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
