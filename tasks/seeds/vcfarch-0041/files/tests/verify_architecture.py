#!/usr/bin/env python3
"""Deterministic offline acceptance checks for the VCF architecture artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from datetime import date
from typing import Any
from urllib.parse import urlsplit

sys.dont_write_bytecode = True

from openapi_schema_validator import ValidationError, validate


ROOT = Path(__file__).resolve().parents[1]
OPENAPI = ROOT / "specifications/vcf-api-specs/specifications/vcf-installer/vcf-installer-openapi.json"
GREENFIELD = ROOT / "artifacts/greenfield-sddc.json"
RESEARCH = ROOT / "research/consulted-sources.md"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_installer_artifact_first() -> tuple[dict[str, Any], dict[str, Any]]:
    """The installer schema gate intentionally runs before every semantic check."""
    if not OPENAPI.is_file():
        raise AssertionError(f"pinned installer specification is missing: {OPENAPI.relative_to(ROOT)}")
    if not GREENFIELD.is_file():
        raise AssertionError("missing artifacts/greenfield-sddc.json; cannot run the first SddcSpec schema gate")

    openapi = load_json(OPENAPI)
    artifact = load_json(GREENFIELD)
    schema = openapi.get("components", {}).get("schemas", {}).get("SddcSpec")
    if not isinstance(schema, dict):
        raise AssertionError("pinned installer specification has no SddcSpec schema")
    try:
        validate(artifact, schema, openapi)
    except ValidationError as exc:
        raise AssertionError(f"installer SddcSpec schema validation failed: {exc}") from exc
    return artifact, openapi


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_pin() -> None:
    pin = load_json(ROOT / "specifications/vcf-api-specs/PIN.json")
    digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest()
    openapi = load_json(OPENAPI)
    require(pin["tag"] == "9.0.0.0", "installer schema tag pin changed")
    require(openapi.get("info", {}).get("version") == "9.0.0.0", "pinned installer specification is not version 9.0.0.0")
    require(digest == pin["sha256"], "installer OpenAPI does not match its protected SHA-256 pin")
    require(pin["license"] == "Apache-2.0", "installer schema license pin changed")


def check_sddc_semantics(spec: dict[str, Any], openapi: dict[str, Any]) -> None:
    requirements = load_json(ROOT / "fixtures/design-requirements.json")
    snapshot = load_json(ROOT / "compatibility/pinned-compatibility.json")
    domain = requirements["managementDomain"]
    compatibility = snapshot["greenfield"]

    allowed = set(openapi["components"]["schemas"]["SddcSpec"]["properties"])
    require(set(spec).issubset(allowed), "SddcSpec contains custom properties that are not installer payload fields")
    require(spec.get("sddcId") == domain["sddcId"], "SddcSpec sddcId does not match requirements")
    require(spec.get("workflowType") == compatibility["workflowType"], "greenfield workflowType is not pinned VCF")
    require(spec.get("version") == requirements["targetRelease"], "SddcSpec release does not match requirements")
    require(spec.get("vcfInstanceName") == requirements["vcfInstanceName"], "VCF instance name does not match requirements")
    require(spec.get("managementPoolName") == domain["managementPoolName"], "management pool name does not match")
    require(spec.get("skipEsxThumbprintValidation") is False, "ESXi thumbprint validation must remain enabled")
    require(spec.get("skipGatewayPingValidation") is False, "gateway ping validation must remain enabled")

    required_hosts = [host for site in requirements["sites"]["data"] for host in site["hosts"]]
    actual_hosts = [host.get("hostname") for host in spec.get("hostSpecs", [])]
    require(actual_hosts == required_hosts, "SddcSpec must contain the eight data hosts in fixture order")
    require(len(actual_hosts) == 8 and len(set(actual_hosts)) == 8, "management-domain data hosts must be eight unique hosts")
    witness_name = requirements["sites"]["witness"]["hostname"]
    require(witness_name not in actual_hosts, "witness must not be an SddcSpec data host")

    versions = compatibility["componentVersions"]
    require(spec.get("vcenterSpec", {}).get("version") == versions["vcenter"], "vCenter version is not compatible")
    require(spec.get("sddcManagerSpec", {}).get("version") == versions["sddcManager"], "SDDC Manager version is not compatible")
    require(spec.get("nsxtSpec", {}).get("version") == versions["nsx"], "NSX version is not compatible")
    require(spec.get("clusterSpec", {}).get("datacenterName") == domain["datacenterName"], "datacenter name mismatch")
    require(spec.get("clusterSpec", {}).get("clusterName") == domain["clusterName"], "cluster name mismatch")

    services = requirements["services"]
    require(spec["vcenterSpec"]["vcenterHostname"] == services["vcenterHostname"], "vCenter hostname mismatch")
    require(spec["sddcManagerSpec"]["hostname"] == services["sddcManagerHostname"], "SDDC Manager hostname mismatch")
    nsx_names = [node["hostname"] for node in spec["nsxtSpec"]["nsxtManagers"]]
    require(nsx_names == services["nsxManagerHostnames"], "NSX manager hostnames mismatch")
    require(spec["nsxtSpec"]["vipFqdn"] == services["nsxVipFqdn"], "NSX VIP mismatch")

    actual_networks = {entry["networkType"]: entry for entry in spec.get("networkSpecs", [])}
    expected_networks = {entry["networkType"]: entry for entry in requirements["networking"]["networks"]}
    require(set(actual_networks) == set(expected_networks), "SddcSpec network types do not match requirements")
    for name, expected in expected_networks.items():
        actual = actual_networks[name]
        for field in ("vlanId", "subnet", "gateway", "subnetMask", "mtu"):
            require(actual.get(field) == expected[field], f"{name} {field} mismatch")
        ranges = actual.get("includeIpAddressRanges", [])
        require(ranges == [{"startIpAddress": expected["startIpAddress"], "endIpAddress": expected["endIpAddress"]}], f"{name} IP range mismatch")

    dvs = spec.get("dvsSpecs", [])
    require(len(dvs) == 1, "architecture requires one management distributed switch")
    require(dvs[0].get("dvsName") == requirements["networking"]["dvsName"], "DVS name mismatch")
    require(set(dvs[0].get("networks", [])) == set(expected_networks), "DVS must carry every required network")
    mapping = {entry["id"]: entry["uplink"] for entry in dvs[0].get("vmnicsToUplinks", [])}
    require(mapping == requirements["networking"]["vmnicToUplink"], "vmnic-to-uplink mapping mismatch")

    vsan = spec.get("datastoreSpec", {}).get("vsanSpec", {})
    require(vsan.get("esaConfig", {}).get("enabled") is True, "vSAN ESA must be enabled")
    require(vsan.get("failuresToTolerate") == compatibility["stretchedManagementDomain"]["requiredVsanFailuresToTolerate"], "vSAN failuresToTolerate mismatch")
    require(spec.get("dnsSpec") == {"subdomain": services["dnsSubdomain"], "nameservers": services["dnsServers"]}, "DNS settings mismatch")
    require(spec.get("ntpServers") == services["ntpServers"], "NTP settings mismatch")


def check_topology() -> None:
    topology_path = ROOT / "artifacts/greenfield-topology.json"
    require(topology_path.is_file(), "missing artifacts/greenfield-topology.json")
    topology = load_json(topology_path)
    requirements = load_json(ROOT / "fixtures/design-requirements.json")
    snapshot = load_json(ROOT / "compatibility/pinned-compatibility.json")
    stretched = snapshot["greenfield"]["stretchedManagementDomain"]

    require(topology.get("designId") == requirements["designId"], "topology designId mismatch")
    require(topology.get("targetRelease") == requirements["targetRelease"], "topology target release mismatch")
    cluster = topology.get("managementDomain", {})
    require(cluster.get("topology") == "stretched", "management domain must be modeled as stretched")
    require(cluster.get("clusterName") == requirements["managementDomain"]["clusterName"], "topology cluster name mismatch")

    expected_sites = requirements["sites"]["data"]
    actual_sites = cluster.get("dataSites", [])
    require(len(actual_sites) == stretched["dataSiteCount"], "topology must have exactly two data sites")
    for expected, actual in zip(expected_sites, actual_sites):
        require(actual.get("siteId") == expected["siteId"], "data site order/name mismatch")
        require(actual.get("failureDomain") == expected["failureDomain"], "data-site failure domain mismatch")
        require(actual.get("hosts") == expected["hosts"], "data-site host placement mismatch")
        require(len(actual["hosts"]) >= stretched["minimumHostsPerDataSite"], "too few hosts in a data site")

    profile = requirements["hostProfile"]
    total_hosts = sum(len(site["hosts"]) for site in expected_sites)
    survivor_hosts = total_hosts - len(expected_sites)
    expected_capacity = {
        "physicalHosts": total_hosts,
        "totalCpuCores": total_hosts * profile["coresPerHost"],
        "totalMemoryGiB": total_hosts * profile["memoryGiBPerHost"],
        "totalRawStorageTiB": total_hosts * profile["rawNvmeTiBPerHost"],
        "survivingHostsAfterOneFailurePerDataSite": survivor_hosts,
        "survivingCpuCores": survivor_hosts * profile["coresPerHost"],
        "survivingMemoryGiB": survivor_hosts * profile["memoryGiBPerHost"],
        "survivingUsableStorageTiBAtFtt1": survivor_hosts * profile["rawNvmeTiBPerHost"] / 2
    }
    actual_capacity = cluster.get("capacity", {})
    for key, value in expected_capacity.items():
        actual = actual_capacity.get(key)
        if isinstance(value, float):
            require(isinstance(actual, (int, float)) and math.isclose(actual, value, rel_tol=0, abs_tol=0.001), f"capacity {key} is not calculated from the fixture")
        else:
            require(actual == value, f"capacity {key} is not calculated from the fixture")

    demand = requirements["capacityDemandAfterOneHostFailurePerDataSite"]
    require(actual_capacity["survivingCpuCores"] >= demand["cpuCores"], "surviving CPU capacity misses demand")
    require(actual_capacity["survivingMemoryGiB"] >= demand["memoryGiB"], "surviving memory capacity misses demand")
    require(actual_capacity["survivingUsableStorageTiBAtFtt1"] >= demand["usableStorageTiB"], "surviving usable storage misses demand")

    expected_witness = requirements["sites"]["witness"]
    witness = cluster.get("witness", {})
    data_site_ids = {site["siteId"] for site in actual_sites}
    data_failure_domains = {site["failureDomain"] for site in actual_sites}
    data_hosts = {host for site in actual_sites for host in site["hosts"]}
    require(witness.get("siteId") == expected_witness["siteId"] and witness["siteId"] not in data_site_ids, "witness must be at the specified third site")
    require(witness.get("failureDomain") == expected_witness["failureDomain"] and witness["failureDomain"] not in data_failure_domains, "witness failure domain must be independent")
    require(witness.get("hostname") == expected_witness["hostname"] and witness["hostname"] not in data_hosts, "witness hostname/placement mismatch")
    require(witness.get("address") == expected_witness["address"], "witness address mismatch")
    require(witness.get("isDataNode") is stretched["witnessIsDataNode"], "witness must not be a data node")
    require(witness.get("isManagementClusterMember") is False, "witness must not be a management-cluster member")
    require(witness.get("version") == snapshot["greenfield"]["componentVersions"]["vsanWitness"], "witness version mismatch")
    require(witness.get("latencyRttMsToDataSites") == expected_witness["latencyRttMsToDataSites"], "witness latency map mismatch")
    require(all(value <= stretched["maximumWitnessRttMs"] for value in witness["latencyRttMsToDataSites"].values()), "witness RTT exceeds compatibility snapshot")

    placements = topology.get("appliancePlacement", {})
    require(placements == requirements["appliancePlacement"], "management appliance placement must match site requirements")
    require(set(placements.values()).issubset(data_site_ids), "management appliances must be placed at data sites")


def check_migration_plan() -> None:
    path = ROOT / "artifacts/migration-plan.json"
    require(path.is_file(), "missing artifacts/migration-plan.json")
    plan = load_json(path)
    plan_schema = load_json(ROOT / "schemas/migration-plan.schema.json")
    try:
        validate(plan, plan_schema, plan_schema)
    except ValidationError as exc:
        raise AssertionError(f"migration-plan schema validation failed: {exc}") from exc

    inventory = load_json(ROOT / "fixtures/estate-inventory.json")
    snapshot = load_json(ROOT / "compatibility/pinned-compatibility.json")
    required_steps = snapshot["migration"]["steps"]
    actual_steps = plan["steps"]
    require(plan["estateId"] == inventory["estateId"], "migration estateId mismatch")
    require(plan["sourceRelease"] == inventory["sourceRelease"], "migration source release mismatch")
    require(plan["targetRelease"] == snapshot["targetRelease"], "migration target release mismatch")
    require(len(actual_steps) == len(inventory["components"]) == len(required_steps), "migration must have exactly one step per inventory component")
    require([step["order"] for step in actual_steps] == list(range(1, len(actual_steps) + 1)), "migration order must be contiguous")

    inventory_by_id = {component["componentId"]: component for component in inventory["components"]}
    definitions = {entry["gateId"]: entry["condition"] for entry in plan["gateDefinitions"]}
    pinned_definitions = {entry["gateId"]: entry["condition"] for entry in snapshot["migration"]["gateDefinitions"]}
    required_gate_ids = {gate for step in required_steps for gate in step["gates"]}
    require(definitions == pinned_definitions, "gateDefinitions must match the pinned technical conditions")
    require(set(definitions) == required_gate_ids, "gateDefinitions must define exactly the gates used by pinned steps")

    seen: set[str] = set()
    for expected, actual in zip(required_steps, actual_steps):
        component_id = expected["componentId"]
        require(component_id not in seen, f"duplicate migration component {component_id}")
        seen.add(component_id)
        inventory_component = inventory_by_id[component_id]
        require(actual["order"] == expected["order"], f"{component_id} order mismatch")
        require(actual["componentId"] == component_id, f"migration step {expected['order']} component mismatch")
        require(actual["componentName"] == inventory_component["componentName"], f"{component_id} current component name mismatch")
        require(actual["currentVersion"] == inventory_component["currentVersion"], f"{component_id} current version mismatch")
        for field in ("targetComponentName", "targetVersion", "action", "gates"):
            require(actual[field] == expected[field], f"{component_id} {field} does not match pinned compatibility")
    require(seen == set(inventory_by_id), "migration omitted an inventory component")


def check_research_artifact() -> None:
    require(RESEARCH.is_file(), "missing research/consulted-sources.md")
    content = RESEARCH.read_text(encoding="utf-8")
    entries = re.split(r"(?m)^- \*\*", content)[1:]
    require(len(entries) >= 2, "research log must contain multiple titled Broadcom source entries")

    for index, entry in enumerate(entries, start=1):
        title_end = entry.find("**")
        title = entry[:title_end].strip() if title_end >= 0 else ""
        require(len(title) >= 5, f"research source {index} has no usable title")

        urls = re.findall(r"https://[^\s)>]+", entry)
        require(len(urls) == 1, f"research source {index} must contain exactly one HTTPS URL")
        parsed_url = urlsplit(urls[0])
        hostname = (parsed_url.hostname or "").lower()
        require(hostname == "broadcom.com" or hostname.endswith(".broadcom.com"), f"research source {index} is not a published Broadcom source")
        require(not hostname.endswith(".invalid"), f"research source {index} uses a non-reachable placeholder domain")

        dates = re.findall(r"(?m)^\s*-\s*Access date:\s*(\d{4}-\d{2}-\d{2})\s*$", entry)
        require(len(dates) == 1, f"research source {index} must contain one ISO access date")
        try:
            parsed_date = date.fromisoformat(dates[0])
        except ValueError as exc:
            raise AssertionError(f"research source {index} has an invalid access date") from exc
        require(parsed_date.isoformat() == dates[0], f"research source {index} access date is not canonical ISO format")

        notes = re.findall(r"(?mi)^\s*-\s*(?:Used|Consulted):\s*(.+)$", entry)
        require(len(notes) == 1 and len(notes[0].strip()) >= 20, f"research source {index} must explain the fact that was used")

    normalized = content.lower()
    coverage = {
        "compatibility": r"compatib",
        "interoperability": r"interoperab",
        "bill of materials": r"bill[- ]of[- ]materials|\bbom\b",
        "upgrade ordering": r"(?:upgrade|update).{0,40}(?:order|sequence)|component order",
    }
    for subject, pattern in coverage.items():
        require(re.search(pattern, normalized, re.DOTALL) is not None, f"research log does not cover Broadcom {subject} guidance")


def check_powershell_module() -> None:
    module_dir = ROOT / "VcfArchitecture"
    manifest = module_dir / "VcfArchitecture.psd1"
    implementation = module_dir / "VcfArchitecture.psm1"
    require(manifest.is_file() and implementation.is_file(), "PowerShell module manifest/implementation is missing")

    parse_script = r'''$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:VCF_ARCH_MODULE, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_.Message }; exit 2 }
$data = Import-PowerShellDataFile -Path $env:VCF_ARCH_MANIFEST
$commands = @(
    $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true) |
        ForEach-Object { $_.GetCommandName() } |
        Where-Object { $_ } |
        Sort-Object -Unique
)
$functions = @(
    $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) |
        ForEach-Object { $_.Name } |
        Sort-Object -Unique
)
$functionCommands = [ordered]@{}
foreach ($function in $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    $functionCommands[$function.Name] = @(
        $function.Body.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true) |
            ForEach-Object { $_.GetCommandName() } |
            Where-Object { $_ } |
            Sort-Object -Unique
    )
}
[pscustomobject]@{ Manifest = $data; Commands = $commands; Functions = $functions; FunctionCommands = $functionCommands } |
    ConvertTo-Json -Depth 12 -Compress
'''
    environment = os.environ.copy()
    environment["VCF_ARCH_MODULE"] = str(implementation)
    environment["VCF_ARCH_MANIFEST"] = str(manifest)
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", parse_script],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    require(result.returncode == 0, f"PowerShell module parse/manifest check failed: {result.stderr.strip()}")
    parsed = json.loads(result.stdout)
    manifest_data = parsed["Manifest"]
    require(manifest_data.get("RootModule") == "VcfArchitecture.psm1", "manifest RootModule mismatch")
    exports = set(manifest_data.get("FunctionsToExport", []))
    expected_exports = {"New-VcfArchitecture", "Connect-VcfArchitectureInstaller", "Test-VcfArchitectureSpecOnline"}
    require(exports == expected_exports, "manifest must export exactly the three architecture functions")

    required_modules = manifest_data.get("RequiredModules", [])
    if isinstance(required_modules, dict):
        required_modules = [required_modules]
    sdk_matches = [entry for entry in required_modules if isinstance(entry, dict) and entry.get("ModuleName") == "VMware.Sdk.Vcf.Installer"]
    require(len(sdk_matches) == 1, "manifest must require VMware.Sdk.Vcf.Installer")
    require(sdk_matches[0].get("ModuleVersion") == "13.4.0.24798382", "installer SDK module version mismatch")

    functions = set(parsed["Functions"])
    require(expected_exports.issubset(functions), "module implementation is missing an exported function definition")
    commands = set(parsed["Commands"])
    function_commands = {name: set(entries) for name, entries in parsed["FunctionCommands"].items()}
    require("Connect-VcfInstallerServer" in function_commands["Connect-VcfArchitectureInstaller"], "connection function does not invoke Connect-VcfInstallerServer")
    require("ConvertTo-VcfSdkSpec" in function_commands["Test-VcfArchitectureSpecOnline"], "online validation function does not convert JSON to SDK models")
    require("Invoke-VcfInstallerValidateSddcSpec" in function_commands["Test-VcfArchitectureSpecOnline"], "online validation function does not invoke the SDK validation cmdlet")
    require("Initialize-VcfInstallerSddcSpec" in function_commands.get("ConvertTo-VcfSdkSpec", set()), "SDK conversion does not initialize an SddcSpec model")
    raw_http_commands = {"invoke-restmethod", "invoke-webrequest", "irm", "iwr", "curl", "curl.exe", "wget", "wget.exe"}
    require({command.lower() for command in commands}.isdisjoint(raw_http_commands), "module must not replace the SDK with a raw HTTP client")

    vendored = [
        path for path in module_dir.rglob("*")
        if path.is_file() and (path.suffix.lower() in {".dll", ".nupkg"} or path.name.startswith("VMware.Sdk."))
    ]
    require(not vendored, "PowerCLI/SDK binaries or modules were vendored into VcfArchitecture")

    generation_script = r'''$ErrorActionPreference = 'Stop'
Import-Module $env:VCF_ARCH_MODULE -Force
New-VcfArchitecture `
    -RequirementsPath $env:VCF_ARCH_REQUIREMENTS `
    -EstatePath $env:VCF_ARCH_ESTATE `
    -CompatibilityPath $env:VCF_ARCH_COMPATIBILITY `
    -OutputDirectory $env:VCF_ARCH_OUTPUT | Out-Null
'''
    with tempfile.TemporaryDirectory(prefix="vcfarch-") as output_directory:
        generation_environment = os.environ.copy()
        generation_environment.update({
            "VCF_ARCH_MODULE": str(implementation),
            "VCF_ARCH_REQUIREMENTS": str(ROOT / "fixtures/design-requirements.json"),
            "VCF_ARCH_ESTATE": str(ROOT / "fixtures/estate-inventory.json"),
            "VCF_ARCH_COMPATIBILITY": str(ROOT / "compatibility/pinned-compatibility.json"),
            "VCF_ARCH_OUTPUT": output_directory,
        })
        generated = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-Command", generation_script],
            cwd=ROOT,
            env=generation_environment,
            text=True,
            capture_output=True,
            check=False,
        )
        require(generated.returncode == 0, f"New-VcfArchitecture failed: {generated.stderr.strip()}")
        for artifact_name in ("greenfield-sddc.json", "greenfield-topology.json", "migration-plan.json"):
            expected = load_json(ROOT / "artifacts" / artifact_name)
            actual = load_json(Path(output_directory) / artifact_name)
            require(actual == expected, f"New-VcfArchitecture did not reproduce artifacts/{artifact_name}")

    requirements_variant = load_json(ROOT / "fixtures/design-requirements.json")
    estate_variant = load_json(ROOT / "fixtures/estate-inventory.json")
    compatibility_variant = load_json(ROOT / "compatibility/pinned-compatibility.json")
    requirements_variant["designId"] = "northstar-vcf9-variant"
    requirements_variant["sites"]["data"][0]["hosts"][0] = "dfw-a-esx99"
    requirements_variant["hostProfile"]["memoryGiBPerHost"] = 1600
    estate_variant["components"][0]["currentVersion"] = "8.18.0-variant"
    compatibility_variant["greenfield"]["componentVersions"]["vcenter"] = "9.0.0.0-variant"

    with tempfile.TemporaryDirectory(prefix="vcfarch-inputs-") as input_directory, tempfile.TemporaryDirectory(prefix="vcfarch-output-") as output_directory:
        input_path = Path(input_directory)
        variant_paths = {
            "VCF_ARCH_REQUIREMENTS": input_path / "requirements.json",
            "VCF_ARCH_ESTATE": input_path / "estate.json",
            "VCF_ARCH_COMPATIBILITY": input_path / "compatibility.json",
        }
        for variable, value in (
            ("VCF_ARCH_REQUIREMENTS", requirements_variant),
            ("VCF_ARCH_ESTATE", estate_variant),
            ("VCF_ARCH_COMPATIBILITY", compatibility_variant),
        ):
            variant_paths[variable].write_text(json.dumps(value), encoding="utf-8")

        generation_environment = os.environ.copy()
        generation_environment.update({
            "VCF_ARCH_MODULE": str(implementation),
            "VCF_ARCH_REQUIREMENTS": str(variant_paths["VCF_ARCH_REQUIREMENTS"]),
            "VCF_ARCH_ESTATE": str(variant_paths["VCF_ARCH_ESTATE"]),
            "VCF_ARCH_COMPATIBILITY": str(variant_paths["VCF_ARCH_COMPATIBILITY"]),
            "VCF_ARCH_OUTPUT": output_directory,
        })
        generated = subprocess.run(
            ["pwsh", "-NoLogo", "-NoProfile", "-Command", generation_script],
            cwd=ROOT,
            env=generation_environment,
            text=True,
            capture_output=True,
            check=False,
        )
        require(generated.returncode == 0, f"New-VcfArchitecture failed with variant inputs: {generated.stderr.strip()}")
        variant_sddc = load_json(Path(output_directory) / "greenfield-sddc.json")
        variant_topology = load_json(Path(output_directory) / "greenfield-topology.json")
        variant_migration = load_json(Path(output_directory) / "migration-plan.json")
        require(variant_sddc["hostSpecs"][0]["hostname"] == "dfw-a-esx99", "generator does not consume the requirements host inventory")
        require(variant_sddc["vcenterSpec"]["version"] == "9.0.0.0-variant", "generator does not consume pinned component versions")
        require(variant_topology["designId"] == "northstar-vcf9-variant", "generator does not consume the requirements design ID")
        require(variant_topology["managementDomain"]["capacity"]["totalMemoryGiB"] == 12800, "generator does not calculate capacity from the requirements host profile")
        require(variant_migration["steps"][0]["currentVersion"] == "8.18.0-variant", "generator does not consume the estate inventory")


def main() -> int:
    try:
        spec, openapi = validate_installer_artifact_first()
        print("[1/7] installer SddcSpec schema validation passed")
        check_pin()
        print("[2/7] installer specification pin passed")
        check_sddc_semantics(spec, openapi)
        print("[3/7] greenfield SddcSpec architecture passed")
        check_topology()
        print("[4/7] stretched topology, witness, and capacity passed")
        check_migration_plan()
        print("[5/7] estate migration schema and compatibility passed")
        check_research_artifact()
        print("[6/7] live research log artifact passed")
        check_powershell_module()
        print("[7/7] PowerShell SDK module contract passed")
    except (AssertionError, KeyError, TypeError, ValueError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: VCF architecture package is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
