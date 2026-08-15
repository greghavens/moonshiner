#!/usr/bin/env python3
"""Deterministic verifier for vcfarch-0002. It performs no network access."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
SDDC_PATH = ROOT / "artifacts" / "sddc-spec.json"
PLAN_PATH = ROOT / "artifacts" / "migration-plan.json"
RESEARCH_PATH = ROOT / "artifacts" / "research-sources.json"

PROTECTED_SHA256 = {
    "scenario.json": "d11ae44d17fe06074580c8aa4b40d09f898f71b41cedc75423784d246a6e4941",
    "estate-inventory.json": "6d3fe245d4e37b741151b1d393f06eaa6c60b70ceacfebffdd7594f6d29891c6",
    "compatibility-snapshot.json": "1260dbca91c4c9b1796d5f4426ea0fd500a539181a0d213c9f3d6218653c5408",
    "schemas/migration-plan.schema.json": "06122a18d2e14887049c80e729c78fe8239d174b95f543227494750ffa9c73bb",
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
}


class VerificationError(Exception):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def load_json(path: Path) -> Any:
    try:
        display_path = str(path.relative_to(ROOT))
    except ValueError:
        display_path = str(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"missing required file: {display_path}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {display_path}: {exc}")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def resolve_pointer(document: Any, pointer: str) -> Any:
    require(pointer.startswith("#/"), f"only local JSON pointers are supported: {pointer}")
    value = document
    for raw_token in pointer[2:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        require(isinstance(value, dict) and token in value, f"unresolved schema reference: {pointer}")
        value = value[token]
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
    fail(f"unsupported JSON Schema type: {expected}")


def validate_schema(instance: Any, schema: dict[str, Any], document: dict[str, Any], path: str = "$") -> None:
    if "$ref" in schema:
        validate_schema(instance, resolve_pointer(document, schema["$ref"]), document, path)
        return

    if "const" in schema:
        require(instance == schema["const"], f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema:
        require(instance in schema["enum"], f"{path}: value is not in enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        if isinstance(expected_type, list):
            valid_type = any(type_matches(instance, item) for item in expected_type)
        else:
            valid_type = type_matches(instance, expected_type)
        require(valid_type, f"{path}: expected type {expected_type}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            require(key in instance, f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                validate_schema(value, properties[key], document, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                fail(f"{path}: unexpected property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(value, schema["additionalProperties"], document, f"{path}.{key}")

    if isinstance(instance, list):
        if "minItems" in schema:
            require(len(instance) >= schema["minItems"], f"{path}: too few items")
        if "maxItems" in schema:
            require(len(instance) <= schema["maxItems"], f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            require(len(encoded) == len(set(encoded)), f"{path}: items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                validate_schema(item, item_schema, document, f"{path}[{index}]")

    if isinstance(instance, str):
        if "minLength" in schema:
            require(len(instance) >= schema["minLength"], f"{path}: string is too short")
        if "maxLength" in schema:
            require(len(instance) <= schema["maxLength"], f"{path}: string is too long")
        if "pattern" in schema:
            require(re.search(schema["pattern"], instance) is not None, f"{path}: string does not match pattern")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema:
            require(instance >= schema["minimum"], f"{path}: number is below minimum")
        if "maximum" in schema:
            require(instance <= schema["maximum"], f"{path}: number is above maximum")


def check_protected_files() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        require(path.is_file(), f"protected fixture missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        require(actual == expected, f"protected fixture changed: {relative}")


def check_capacity(scenario: dict[str, Any]) -> None:
    hosts = scenario["hosts"]
    capacity = scenario["capacity"]
    failures = scenario["availability"]["hostFailuresToTolerate"]
    require(failures == 1, "scenario must tolerate one host failure")
    headroom = 1 + capacity["headroomPercent"] / 100

    cores_after_failure = sum(host["cpuCores"] for host in hosts) - max(host["cpuCores"] for host in hosts)
    available_vcpu = cores_after_failure * capacity["maxVcpuPerPhysicalCore"] - capacity["managementReservedVcpu"]
    require(available_vcpu >= capacity["workloadVcpu"] * headroom, "N+1 CPU capacity does not meet demand and headroom")

    memory_after_failure = sum(host["memoryGiB"] for host in hosts) - max(host["memoryGiB"] for host in hosts)
    available_memory = memory_after_failure - capacity["managementReservedMemoryGiB"]
    require(available_memory >= capacity["workloadMemoryGiB"] * headroom, "N+1 memory capacity does not meet demand and headroom")

    raw_after_failure = sum(host["vsanRawTiB"] for host in hosts) - max(host["vsanRawTiB"] for host in hosts)
    available_usable = raw_after_failure * 0.5 - capacity["managementReservedUsableStorageTiB"]
    require(available_usable >= capacity["workloadUsableStorageTiB"] * headroom, "N+1 mirrored storage does not meet demand and headroom")


def check_sddc_architecture(spec: dict[str, Any], scenario: dict[str, Any], snapshot: dict[str, Any]) -> None:
    greenfield = snapshot["greenfield"]
    versions = greenfield["versions"]
    require(scenario["targetRelease"] == snapshot["targetRelease"], "scenario and compatibility snapshot releases differ")
    require(spec.get("sddcId") == scenario["sddcId"], "incorrect sddcId")
    require(spec.get("vcfInstanceName") == scenario["vcfInstanceName"], "incorrect VCF instance name")
    require(spec.get("workflowType") == greenfield["requiredWorkflowType"], "workflow must be a new VCF deployment")
    require(spec.get("version") == snapshot["targetRelease"], "incorrect SddcSpec release")
    require(scenario["siteCount"] == greenfield["siteCount"] == 1, "design must be single-site")
    require(scenario["deploymentModel"] == "consolidated", "design must be consolidated")

    expected_hosts = [host["hostname"] for host in scenario["hosts"]]
    actual_hosts = [host.get("hostname") for host in spec.get("hostSpecs", [])]
    require(len(actual_hosts) == greenfield["minimumConsolidatedHosts"] == 4, "consolidated cluster must stay at four hosts")
    require(actual_hosts == expected_hosts, "SddcSpec hosts do not match the scenario")

    cluster = spec.get("clusterSpec", {})
    require(cluster.get("datacenterName") == scenario["cluster"]["datacenterName"], "incorrect datacenter")
    require(cluster.get("clusterName") == scenario["cluster"]["clusterName"], "incorrect consolidated cluster")
    require(cluster.get("clusterEvcMode") == scenario["cluster"]["evcMode"], "incorrect EVC mode")

    vcenter = spec.get("vcenterSpec", {})
    require(vcenter.get("vcenterHostname") == scenario["vcenter"]["hostname"], "incorrect vCenter hostname")
    require(vcenter.get("rootVcenterPassword") == scenario["secretTokens"]["vcenterRootPassword"], "vCenter secret token changed")
    require(vcenter.get("vmSize") == scenario["vcenter"]["vmSize"], "incorrect vCenter size")
    require(vcenter.get("storageSize") == scenario["vcenter"]["storageSize"], "incorrect vCenter storage size")
    require(vcenter.get("ssoDomain") == scenario["vcenter"]["ssoDomain"], "incorrect SSO domain")
    require(vcenter.get("version") == versions["vcenter"], "incorrect vCenter target version")
    require(vcenter.get("useExistingDeployment") is False, "vCenter must be greenfield")

    manager = spec.get("sddcManagerSpec", {})
    require(manager.get("hostname") == scenario["sddcManager"]["hostname"], "incorrect SDDC Manager hostname")
    require(manager.get("version") == versions["sddcManager"], "incorrect SDDC Manager target version")
    require(manager.get("useExistingDeployment") is False, "SDDC Manager must be greenfield")

    dns = spec.get("dnsSpec", {})
    require(dns.get("subdomain") == scenario["dns"]["subdomain"], "incorrect DNS suffix")
    require(dns.get("nameservers") == scenario["dns"]["nameservers"], "incorrect DNS servers")
    require(spec.get("ntpServers") == scenario["ntpServers"], "incorrect NTP servers")

    expected_networks = {item["networkType"]: item for item in scenario["networks"]}
    actual_network_list = spec.get("networkSpecs", [])
    actual_networks = {item.get("networkType"): item for item in actual_network_list}
    require(len(actual_network_list) == len(expected_networks) == 5, "exactly five scenario networks are required")
    require(set(actual_networks) == set(expected_networks), "network types do not match the scenario")
    for network_type, expected in expected_networks.items():
        actual = actual_networks[network_type]
        for field in ("vlanId", "subnet", "gateway", "subnetMask", "mtu"):
            require(actual.get(field) == expected[field], f"{network_type} has incorrect {field}")
        if "ipRange" in expected:
            require(actual.get("includeIpAddressRanges") == [expected["ipRange"]], f"{network_type} has incorrect IP range")

    dvs_specs = spec.get("dvsSpecs", [])
    require(len(dvs_specs) == 1, "single consolidated design requires one distributed switch")
    dvs = dvs_specs[0]
    require(dvs.get("dvsName") == scenario["distributedSwitch"]["name"], "incorrect distributed switch name")
    require(dvs.get("mtu") == scenario["distributedSwitch"]["mtu"], "incorrect distributed switch MTU")
    require(dvs.get("vmnicsToUplinks") == scenario["distributedSwitch"]["vmnicsToUplinks"], "incorrect dual-uplink mapping")
    require(dvs.get("networks") == [item["networkType"] for item in scenario["networks"]], "distributed switch network attachment differs")

    nsx = spec.get("nsxtSpec", {})
    require([item.get("hostname") for item in nsx.get("nsxtManagers", [])] == scenario["nsx"]["managers"], "incorrect NSX manager set")
    require(nsx.get("nsxtManagerSize") == scenario["nsx"]["managerSize"], "incorrect NSX manager size")
    require(nsx.get("vipFqdn") == scenario["nsx"]["vipFqdn"], "incorrect NSX VIP")
    require(nsx.get("transportVlanId") == scenario["nsx"]["transportVlanId"], "incorrect NSX transport VLAN")
    require(nsx.get("version") == versions["nsx"], "incorrect NSX target version")
    require(nsx.get("useExistingDeployment") is False, "NSX must be greenfield")

    vsan = spec.get("datastoreSpec", {}).get("vsanSpec", {})
    require(vsan.get("datastoreName") == scenario["vsan"]["datastoreName"], "incorrect vSAN datastore")
    require(vsan.get("failuresToTolerate") == scenario["vsan"]["failuresToTolerate"] == 1, "vSAN must tolerate one host failure")
    require(vsan.get("esaConfig", {}).get("enabled") is True, "vSAN ESA must be enabled")

    services = scenario["managementServices"]
    vsp = spec.get("vspClusterSpec", {})
    require(vsp.get("platformFqdn") == services["platformFqdn"], "incorrect management-services platform FQDN")
    require(vsp.get("instanceFqdn") == services["instanceFqdn"], "incorrect management-services instance FQDN")
    require(vsp.get("fleetFqdn") == services["fleetFqdn"], "incorrect fleet FQDN")
    require(vsp.get("version") == versions["managementServices"], "incorrect management-services version")
    addresses = vsp.get("ipv4Pool", {}).get("addresses", [])
    require(len(addresses) == greenfield["managementServicesAddressCount"] == 12, "management services require twelve addresses")
    require(addresses == services["ipv4Addresses"], "management-services address pool differs")

    local_network = spec.get("vcfManagementComponentsInfrastructureSpec", {}).get("localRegionNetwork", {})
    fleet_network = expected_networks["FLEET_MANAGEMENT"]
    require(local_network.get("networkName") == "FLEET_MANAGEMENT", "management services must use FLEET_MANAGEMENT")
    require(local_network.get("gateway") == fleet_network["gateway"], "incorrect management-services gateway")
    require(local_network.get("subnetMask") == fleet_network["subnetMask"], "incorrect management-services subnet mask")

    for key in ("fleetLcmSpec", "sddcLcmSpec", "fleetDepotSpec", "telemetryAcceptorSpec", "vidbSpec", "saltSpec", "saltRaasSpec", "licenseServerSpec"):
        require(spec.get(key, {}).get("version") == versions["managementServices"], f"{key} has incorrect version")
    require(spec.get("securitySpec", {}).get("esxiCertsMode") == "VMCA", "ESXi certificate mode must be VMCA")
    require(spec.get("ceipEnabled") is False, "CEIP setting differs from the architecture")
    require(spec.get("skipEsxThumbprintValidation") is False, "ESXi thumbprint validation must not be skipped")
    require(spec.get("skipGatewayPingValidation") is False, "gateway validation must not be skipped")

    check_capacity(scenario)


def check_migration_plan(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any], schema: dict[str, Any]) -> None:
    validate_schema(plan, schema, schema)
    require(plan["estateId"] == inventory["estateId"], "migration plan estateId differs")
    require(plan["targetVcfVersion"] == snapshot["targetRelease"], "migration plan target VCF release differs")

    inventory_by_id = {item["id"]: item for item in inventory["components"]}
    rules = sorted(snapshot["migrationRules"], key=lambda item: item["order"])
    steps = plan["steps"]
    require(len(steps) == len(inventory_by_id) == len(rules), "migration plan must have one step per inventoried component")
    require([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "migration step order must be consecutive")
    require({step["componentId"] for step in steps} == set(inventory_by_id), "migration plan omits or invents a component")

    for step, rule in zip(steps, rules):
        component = inventory_by_id[rule["componentId"]]
        require(step["order"] == rule["order"], f"incorrect order for {component['id']}")
        require(step["componentId"] == component["id"], f"incorrect component at step {rule['order']}")
        require(step["componentName"] == component["name"], f"incorrect name for {component['id']}")
        require(step["currentVersion"] == component["version"], f"incorrect current version for {component['id']}")
        require(step["targetComponentName"] == rule["targetComponentName"], f"incorrect target component for {component['id']}")
        require(step["targetVersion"] == rule["targetVersion"], f"incorrect target version for {component['id']}")
        require(step["action"] == rule["action"], f"incorrect action for {component['id']}")
        require(step["gates"] == rule["gates"], f"incorrect gates for {component['id']}")


def check_research_sources(research: Any) -> None:
    require(isinstance(research, dict), "research-sources.json must contain a JSON object")
    researched_at = research.get("researchedAt")
    require(
        isinstance(researched_at, str) and researched_at.strip() == researched_at and researched_at,
        "researchedAt must be a non-empty string",
    )
    sources = research.get("sources")
    require(isinstance(sources, list) and sources, "research sources must be a non-empty array")

    for index, source in enumerate(sources):
        label = f"sources[{index}]"
        require(isinstance(source, dict), f"{label} must be an object")
        for field in ("title", "publisher", "url", "consultedAt", "decision"):
            value = source.get(field)
            require(
                isinstance(value, str) and value.strip() == value and value,
                f"{label}.{field} must be a non-empty string",
            )

        try:
            parsed = urlsplit(source["url"])
            _ = parsed.port
        except ValueError:
            fail(f"{label}.url is invalid")
        hostname = (parsed.hostname or "").lower()
        require(parsed.scheme in ("http", "https"), f"{label}.url must be an HTTP(S) source")
        require(
            "." in hostname
            and hostname != "localhost"
            and not hostname.endswith((".invalid", ".localhost", ".test")),
            f"{label}.url must identify a public source",
        )


def quote_pwsh(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def invoke_architecture_module(
    pwsh: str,
    manifest: Path,
    scenario: Path,
    inventory: Path,
    snapshot: Path,
    output: Path,
) -> None:
    command = (
        f"Import-Module {quote_pwsh(manifest)} -Force -ErrorAction Stop; "
        f"New-VcfArchitecture -ScenarioPath {quote_pwsh(scenario)} "
        f"-EstateInventoryPath {quote_pwsh(inventory)} "
        f"-CompatibilitySnapshotPath {quote_pwsh(snapshot)} "
        f"-OutputDirectory {quote_pwsh(output)} -ErrorAction Stop | Out-Null"
    )
    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
        check=False,
    )
    require(completed.returncode == 0, f"New-VcfArchitecture failed:\n{completed.stdout.strip()}")


def get_powershell_commands(pwsh: str, module: Path) -> set[str]:
    command = (
        "$tokens = $null; $errors = $null; "
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile({quote_pwsh(module)}, [ref] $tokens, [ref] $errors); "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }; "
        "$ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true) | "
        "ForEach-Object { $_.GetCommandName() } | Where-Object { $_ } | Sort-Object -Unique | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    require(completed.returncode == 0, f"PowerShell module does not parse:\n{completed.stdout.strip()}")
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        fail("could not inspect commands used by the PowerShell module")
    if isinstance(parsed, str):
        return {parsed}
    require(isinstance(parsed, list), "could not inspect commands used by the PowerShell module")
    return set(parsed)


def check_module_reproduction(sddc: dict[str, Any], plan: dict[str, Any]) -> None:
    manifest = ROOT / "VcfArchitecture" / "VcfArchitecture.psd1"
    module = ROOT / "VcfArchitecture" / "VcfArchitecture.psm1"
    require(manifest.is_file(), "missing PowerShell module manifest")
    require(module.is_file(), "missing PowerShell module implementation")
    manifest_text = manifest.read_text(encoding="utf-8")
    require("VMware.Sdk.Vcf.Installer" in manifest_text, "module manifest must declare the installer SDK prerequisite")
    require("New-VcfArchitecture" in manifest_text, "module manifest must export New-VcfArchitecture")

    pwsh = shutil.which("pwsh")
    require(pwsh is not None, "PowerShell 7 is required")
    commands = get_powershell_commands(pwsh, module)
    require("Initialize-VcfInstallerSddcSpec" in commands, "module must construct SddcSpec with the installer SDK")
    for forbidden in ("Connect-VcfInstallerServer", "Invoke-VcfInstallerDeploySddc"):
        require(forbidden not in commands, f"module must not use {forbidden}")

    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temporary:
        temporary_root = Path(temporary)
        output = temporary_root / "original-output"
        invoke_architecture_module(
            pwsh,
            manifest,
            ROOT / "scenario.json",
            ROOT / "estate-inventory.json",
            ROOT / "compatibility-snapshot.json",
            output,
        )
        generated_sddc = load_json(output / "sddc-spec.json")
        generated_plan = load_json(output / "migration-plan.json")
        require(generated_sddc == sddc, "checked-in SddcSpec is not reproduced by the module")
        require(generated_plan == plan, "checked-in migration plan is not reproduced by the module")

        inputs = temporary_root / "changed-inputs"
        inputs.mkdir()
        changed_scenario = load_json(ROOT / "scenario.json")
        changed_inventory = load_json(ROOT / "estate-inventory.json")
        changed_snapshot = load_json(ROOT / "compatibility-snapshot.json")

        changed_scenario["sddcId"] = "changed-sddc-id"
        changed_scenario["vcfInstanceName"] = "Changed VCF Instance"
        changed_scenario["hosts"][0]["hostname"] = "changed-esx01"
        changed_scenario["cluster"]["clusterName"] = "changed-cluster"
        changed_scenario["networks"][0]["vlanId"] = 2601
        changed_inventory["estateId"] = "changed-estate"
        changed_inventory["components"][0]["name"] = "Changed Operations Name"
        changed_inventory["components"][0]["version"] = "8.18.5-changed"
        changed_snapshot["migrationRules"][0]["targetComponentName"] = "Changed Target Operations"
        changed_snapshot["migrationRules"][0]["targetVersion"] = "9.1.0-changed"
        changed_snapshot["migrationRules"][0]["action"] = "retain"
        changed_snapshot["migrationRules"][0]["gates"] = ["changed-compatibility-gate"]

        scenario_path = inputs / "scenario.json"
        inventory_path = inputs / "estate-inventory.json"
        snapshot_path = inputs / "compatibility-snapshot.json"
        write_json(scenario_path, changed_scenario)
        write_json(inventory_path, changed_inventory)
        write_json(snapshot_path, changed_snapshot)

        changed_output = temporary_root / "changed-output"
        invoke_architecture_module(
            pwsh,
            manifest,
            scenario_path,
            inventory_path,
            snapshot_path,
            changed_output,
        )
        changed_sddc = load_json(changed_output / "sddc-spec.json")
        changed_plan = load_json(changed_output / "migration-plan.json")
        require(changed_sddc != sddc, "SddcSpec must be generated from the supplied scenario")
        require(changed_sddc.get("sddcId") == "changed-sddc-id", "SddcSpec ignores the supplied sddcId")
        require(changed_sddc.get("vcfInstanceName") == "Changed VCF Instance", "SddcSpec ignores the supplied instance name")
        require(changed_sddc.get("hostSpecs", [{}])[0].get("hostname") == "changed-esx01", "SddcSpec ignores supplied hosts")
        require(changed_sddc.get("clusterSpec", {}).get("clusterName") == "changed-cluster", "SddcSpec ignores the supplied cluster")
        require(changed_sddc.get("networkSpecs", [{}])[0].get("vlanId") == 2601, "SddcSpec ignores supplied networks")
        require(changed_plan != plan, "migration plan must be generated from the supplied inputs")
        require(changed_plan.get("estateId") == "changed-estate", "migration plan ignores the supplied estate")
        changed_step = changed_plan.get("steps", [{}])[0]
        require(changed_step.get("componentName") == "Changed Operations Name", "migration plan ignores component names")
        require(changed_step.get("currentVersion") == "8.18.5-changed", "migration plan ignores current versions")
        require(changed_step.get("targetComponentName") == "Changed Target Operations", "migration plan ignores target components")
        require(changed_step.get("targetVersion") == "9.1.0-changed", "migration plan ignores target versions")
        require(changed_step.get("action") == "retain", "migration plan ignores actions")
        require(changed_step.get("gates") == ["changed-compatibility-gate"], "migration plan ignores gates")


def main() -> int:
    # This is intentionally first: the submitted greenfield artifact is validated
    # against the installer's own pinned SddcSpec schema before all other checks.
    openapi = load_json(OPENAPI_PATH)
    sddc = load_json(SDDC_PATH)
    sddc_schema = resolve_pointer(openapi, "#/components/schemas/SddcSpec")
    validate_schema(sddc, sddc_schema, openapi)

    check_protected_files()
    scenario = load_json(ROOT / "scenario.json")
    inventory = load_json(ROOT / "estate-inventory.json")
    snapshot = load_json(ROOT / "compatibility-snapshot.json")
    migration_schema = load_json(ROOT / "schemas" / "migration-plan.schema.json")
    plan = load_json(PLAN_PATH)
    research = load_json(RESEARCH_PATH)

    check_sddc_architecture(sddc, scenario, snapshot)
    check_migration_plan(plan, inventory, snapshot, migration_schema)
    check_research_sources(research)
    check_module_reproduction(sddc, plan)
    print("PASS: SddcSpec schema, architecture, capacity, migration plan, research record, and module reproduction")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
