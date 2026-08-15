#!/usr/bin/env python3
"""Deterministic acceptance checks for vcfarch-0008.

The SddcSpec schema check is intentionally the first acceptance check.  Live
publications are never queried during grading; their recorded provenance is
validated locally and the pinned compatibility snapshot remains authoritative.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent


class VerificationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required artifact: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.relative_to(ROOT)}: line {exc.lineno}, column {exc.colno}"
        ) from exc


class SchemaValidator:
    """Small dependency-free validator for the keywords used by the pinned schemas."""

    def __init__(self, document: dict[str, Any]):
        self.document = document

    def resolve(self, reference: str) -> dict[str, Any]:
        if not reference.startswith("#/"):
            raise VerificationError(f"unsupported non-local schema reference: {reference}")
        value: Any = self.document
        for raw_part in reference[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            try:
                value = value[part]
            except (KeyError, TypeError) as exc:
                raise VerificationError(f"unresolvable schema reference: {reference}") from exc
        if not isinstance(value, dict):
            raise VerificationError(f"schema reference is not an object: {reference}")
        return value

    @staticmethod
    def _matches_type(instance: Any, expected: str) -> bool:
        return {
            "object": isinstance(instance, dict),
            "array": isinstance(instance, list),
            "string": isinstance(instance, str),
            "integer": isinstance(instance, int) and not isinstance(instance, bool),
            "number": isinstance(instance, (int, float)) and not isinstance(instance, bool),
            "boolean": isinstance(instance, bool),
            "null": instance is None,
        }.get(expected, True)

    def validate(self, instance: Any, schema: dict[str, Any], path: str = "$") -> None:
        if "$ref" in schema:
            self.validate(instance, self.resolve(schema["$ref"]), path)
            return

        if instance is None and schema.get("nullable") is True:
            return

        if "allOf" in schema:
            for subschema in schema["allOf"]:
                self.validate(instance, subschema, path)
        if "anyOf" in schema:
            if not self._valid_for_any(instance, schema["anyOf"], path):
                raise VerificationError(f"{path}: does not satisfy anyOf")
        if "oneOf" in schema:
            matches = sum(self._is_valid(instance, item, path) for item in schema["oneOf"])
            if matches != 1:
                raise VerificationError(f"{path}: must satisfy exactly one oneOf branch (matched {matches})")

        if "const" in schema and instance != schema["const"]:
            raise VerificationError(f"{path}: expected constant {schema['const']!r}")
        if "enum" in schema and instance not in schema["enum"]:
            raise VerificationError(f"{path}: {instance!r} is not in {schema['enum']!r}")

        expected_type = schema.get("type")
        if isinstance(expected_type, list):
            if not any(self._matches_type(instance, item) for item in expected_type):
                raise VerificationError(f"{path}: wrong type")
        elif isinstance(expected_type, str) and not self._matches_type(instance, expected_type):
            raise VerificationError(f"{path}: expected {expected_type}, got {type(instance).__name__}")

        if isinstance(instance, dict):
            required = schema.get("required", [])
            for name in required:
                if name not in instance:
                    raise VerificationError(f"{path}: missing required property {name!r}")
            properties = schema.get("properties", {})
            for name, value in instance.items():
                child_path = f"{path}.{name}"
                if name in properties:
                    self.validate(value, properties[name], child_path)
                elif schema.get("additionalProperties") is False:
                    raise VerificationError(f"{path}: unexpected property {name!r}")
                elif isinstance(schema.get("additionalProperties"), dict):
                    self.validate(value, schema["additionalProperties"], child_path)
            if "minProperties" in schema and len(instance) < schema["minProperties"]:
                raise VerificationError(f"{path}: too few properties")
            if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
                raise VerificationError(f"{path}: too many properties")

        if isinstance(instance, list):
            if "minItems" in schema and len(instance) < schema["minItems"]:
                raise VerificationError(f"{path}: requires at least {schema['minItems']} items")
            if "maxItems" in schema and len(instance) > schema["maxItems"]:
                raise VerificationError(f"{path}: permits at most {schema['maxItems']} items")
            if schema.get("uniqueItems"):
                encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
                if len(encoded) != len(set(encoded)):
                    raise VerificationError(f"{path}: array items must be unique")
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, value in enumerate(instance):
                    self.validate(value, item_schema, f"{path}[{index}]")

        if isinstance(instance, str):
            if "minLength" in schema and len(instance) < schema["minLength"]:
                raise VerificationError(f"{path}: string is shorter than {schema['minLength']}")
            if "maxLength" in schema and len(instance) > schema["maxLength"]:
                raise VerificationError(f"{path}: string is longer than {schema['maxLength']}")
            if "pattern" in schema:
                try:
                    matched = re.search(schema["pattern"], instance)
                except re.error as exc:
                    raise VerificationError(f"invalid regular expression in schema at {path}: {exc}") from exc
                if matched is None:
                    raise VerificationError(f"{path}: string does not match {schema['pattern']!r}")

        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            if "minimum" in schema and instance < schema["minimum"]:
                raise VerificationError(f"{path}: value is below {schema['minimum']}")
            if "maximum" in schema and instance > schema["maximum"]:
                raise VerificationError(f"{path}: value is above {schema['maximum']}")

    def _is_valid(self, instance: Any, schema: dict[str, Any], path: str) -> bool:
        try:
            self.validate(instance, schema, path)
            return True
        except VerificationError:
            return False

    def _valid_for_any(self, instance: Any, schemas: list[dict[str, Any]], path: str) -> bool:
        return any(self._is_valid(instance, item, path) for item in schemas)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def check_installer_schema_first() -> dict[str, Any]:
    """This must remain the first acceptance check in main()."""
    sddc = load_json(ROOT / "out" / "sddc-spec.json")
    openapi = load_json(ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json")
    SchemaValidator(openapi).validate(sddc, {"$ref": "#/components/schemas/SddcSpec"})

    expected_hash = "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"
    import hashlib

    actual_hash = hashlib.sha256(
        (ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json").read_bytes()
    ).hexdigest()
    expect(actual_hash == expected_hash, "the pinned VCF Installer OpenAPI document was modified")
    expect(
        openapi.get("info", {}).get("version") == "9.1.0.0",
        "the installer OpenAPI document is not release 9.1.0.0",
    )
    print("PASS 1: out/sddc-spec.json validates as the pinned OpenAPI SddcSpec")
    return sddc


def check_sddc_semantics(sddc: dict[str, Any], requirements: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expect(sddc.get("workflowType") == "VCF_COMPLETE", "greenfield workflowType must be VCF_COMPLETE")
    expect(sddc.get("version") == requirements["targetRelease"], "SddcSpec target version is wrong")
    expect(sddc.get("sddcId") == requirements["naming"]["sddcId"], "SddcSpec sddcId is wrong")
    expect(
        sddc.get("vcfInstanceName") == requirements["naming"]["vcfInstanceName"],
        "VCF instance name is wrong",
    )

    expected_hosts = requirements["hostProfile"]["hostnames"]
    actual_hosts = [item.get("hostname") for item in sddc.get("hostSpecs", [])]
    expect(actual_hosts == expected_hosts, "hostSpecs must contain the six fixture hosts in fixture order")
    expect(len(set(actual_hosts)) == len(actual_hosts), "hostSpecs contain duplicate hostnames")

    host_profile = requirements["hostProfile"]
    cores_per_host = host_profile["socketsPerHost"] * host_profile["physicalCoresPerSocket"]
    selected_cores = len(actual_hosts) * cores_per_host
    licensed_cores = requirements["entitlement"]["licensedPhysicalCores"]
    expect(selected_cores == licensed_cores == 192, "selected topology must consume exactly 192 licensed cores")

    remaining_hosts = len(actual_hosts) - requirements["availability"]["hostFailuresToTolerate"]
    model = requirements["capacityModel"]
    capacity = requirements["requiredCapacityAfterHostFailure"]
    available_vcpu = remaining_hosts * cores_per_host * model["vCpuPerPhysicalCore"] - model["managementReserveVCpu"]
    available_memory = (
        remaining_hosts * host_profile["memoryGiBPerHost"] - model["managementReserveMemoryGiB"]
    )
    available_storage = (
        remaining_hosts
        * host_profile["rawStorageTiBPerHost"]
        * model["vsanFtt1UsableFraction"]
        * model["vsanOperationalHeadroomFraction"]
    )
    expect(available_vcpu >= capacity["vCpu"], "N+1 vCPU capacity is insufficient")
    expect(available_memory >= capacity["memoryGiB"], "N+1 memory capacity is insufficient")
    expect(
        available_storage + 1e-9 >= capacity["usableStorageTiB"],
        "N+1 usable vSAN capacity is insufficient",
    )

    pools = {item.get("type") for item in sddc.get("clusterSpec", {}).get("resourcePoolSpecs", [])}
    expect(pools == {"management", "compute"}, "consolidated cluster needs management and compute pools")
    expect(
        sddc.get("clusterSpec", {}).get("datacenterName") == requirements["naming"]["datacenterName"],
        "management datacenter name is wrong",
    )
    expect(
        sddc.get("clusterSpec", {}).get("clusterName") == requirements["naming"]["clusterName"],
        "management cluster name is wrong",
    )
    expect(
        snapshot["topologies"]["CONSOLIDATED"]["permitsUserWorkloadsInManagementDomain"],
        "pinned authority does not allow consolidated workloads",
    )

    vsan = sddc.get("datastoreSpec", {}).get("vsanSpec", {})
    expect(vsan.get("failuresToTolerate") == 1, "vSAN must tolerate one host failure")

    expected_networks = requirements["networks"]
    actual_networks = sddc.get("networkSpecs", [])
    expect(len(actual_networks) == len(expected_networks), "networkSpecs count is wrong")
    for expected, actual in zip(expected_networks, actual_networks):
        for field in ("networkType", "vlanId", "subnet", "gateway", "subnetMask", "mtu"):
            expect(actual.get(field) == expected[field], f"network {expected['networkType']} has wrong {field}")
        ranges = actual.get("includeIpAddressRanges", [])
        expect(len(ranges) == 1, f"network {expected['networkType']} needs one IP range")
        expect(ranges[0].get("startIpAddress") == expected["startIpAddress"], "network start IP is wrong")
        expect(ranges[0].get("endIpAddress") == expected["endIpAddress"], "network end IP is wrong")

    expect(sddc.get("dnsSpec", {}).get("subdomain") == requirements["naming"]["dnsSubdomain"], "DNS domain is wrong")
    expect(
        sddc.get("dnsSpec", {}).get("nameservers") == requirements["infrastructureServices"]["dnsServers"],
        "DNS server list is wrong",
    )
    expect(sddc.get("ntpServers") == requirements["infrastructureServices"]["ntpServers"], "NTP list is wrong")
    vcenter = sddc.get("vcenterSpec", {})
    expect(vcenter.get("vcenterHostname") == requirements["naming"]["vcenterFqdn"], "vCenter hostname is wrong")
    expect(vcenter.get("version") == requirements["targetRelease"], "vCenter target version is wrong")
    expect(vcenter.get("useExistingDeployment") is False, "vCenter must be a new deployment")
    manager = sddc.get("sddcManagerSpec", {})
    expect(manager.get("hostname") == requirements["naming"]["sddcManagerHostname"], "SDDC Manager hostname is wrong")
    expect(manager.get("version") == requirements["targetRelease"], "SDDC Manager target version is wrong")
    expect(manager.get("useExistingDeployment") is False, "SDDC Manager must be a new deployment")
    expect(sddc.get("skipEsxThumbprintValidation") is False, "ESX thumbprint validation cannot be skipped")
    expect(sddc.get("skipGatewayPingValidation") is False, "gateway validation cannot be skipped")
    expect(sddc.get("securitySpec", {}).get("esxiCertsMode") == "VMCA", "ESXi certificate mode is wrong")

    expected_nsx = requirements["nsx"]
    actual_nsx = sddc.get("nsxtSpec", {})
    expect(
        [item.get("hostname") for item in actual_nsx.get("nsxtManagers", [])]
        == expected_nsx["managerFqdns"],
        "NSX manager list is wrong",
    )
    expect(actual_nsx.get("nsxtManagerSize") == expected_nsx["managerSize"], "NSX manager size is wrong")
    expect(actual_nsx.get("vipFqdn") == expected_nsx["vipFqdn"], "NSX VIP is wrong")
    expect(actual_nsx.get("transportVlanId") == expected_nsx["transportVlanId"], "NSX transport VLAN is wrong")
    expect(actual_nsx.get("version") == requirements["targetRelease"], "NSX target version is wrong")
    expect(actual_nsx.get("useExistingDeployment") is False, "NSX must be a new deployment")

    license_server = sddc.get("licenseServerSpec", {})
    expect(
        license_server.get("hostname") == requirements["entitlement"]["licenseServerFqdn"],
        "required local license server is missing or wrong",
    )
    expect(license_server.get("version") == requirements["targetRelease"], "license server target version is wrong")
    expect(license_server.get("useExistingDeployment") is False, "license server must be new")

    management_services = requirements["managementServices"]
    vsp = sddc.get("vspClusterSpec", {})
    expect(vsp.get("platformFqdn") == management_services["vspPlatformFqdn"], "VSP platform FQDN is wrong")
    expect(vsp.get("instanceFqdn") == management_services["vspInstanceFqdn"], "VSP instance FQDN is wrong")
    expect(vsp.get("fleetFqdn") == management_services["vspFleetFqdn"], "VSP fleet FQDN is wrong")
    expect(vsp.get("version") == requirements["targetRelease"], "VSP target version is wrong")
    expect(vsp.get("useExistingDeployment") is False, "VSP cluster must be a new deployment")
    expect(vsp.get("internalClusterCidrIpv4") == management_services["internalClusterCidrIpv4"], "VSP internal CIDR is wrong")
    vsp_pool = vsp.get("ipv4Pool", {})
    expect(vsp_pool.get("cidr") == management_services["ipv4PoolCidr"], "VSP IPv4 pool CIDR is wrong")
    expect(
        vsp_pool.get("ipRange")
        == {
            "startIpAddress": management_services["ipv4PoolStart"],
            "endIpAddress": management_services["ipv4PoolEnd"],
        },
        "VSP IPv4 pool range is wrong",
    )
    operations_nodes = sddc.get("vcfOperationsSpec", {}).get("nodes", [])
    expect(len(operations_nodes) == 3, "VCF Operations must have three nodes")
    expect(
        [node.get("hostname") for node in operations_nodes]
        == management_services["vcfOperationsNodes"],
        "VCF Operations node list is wrong",
    )
    expect(
        [node.get("type") for node in operations_nodes] == ["master", "replica", "data"],
        "VCF Operations node roles must be master, replica, and data",
    )
    operations = sddc.get("vcfOperationsSpec", {})
    expect(
        operations.get("loadBalancerFqdn") == management_services["vcfOperationsLoadBalancerFqdn"],
        "VCF Operations load balancer is wrong",
    )
    expect(operations.get("version") == requirements["targetRelease"], "VCF Operations target version is wrong")
    expect(operations.get("useExistingDeployment") is False, "VCF Operations must be a new deployment")
    expect(sddc.get("vcenterSpec", {}).get("rootVcenterPassword") == "__VC_ROOT_SECRET__", "use the required runtime secret placeholder")
    print("PASS 2: SddcSpec implements the selected licensed, available, capacity-compliant architecture")


def check_research_sources() -> None:
    research = load_json(ROOT / "out" / "research-sources.json")
    if isinstance(research, list):
        sources = research
    elif isinstance(research, dict):
        sources = research.get("sources")
    else:
        sources = None
    expect(isinstance(sources, list) and len(sources) > 0, "research log must contain one or more sources")

    for index, source in enumerate(sources):
        label = f"research source {index + 1}"
        expect(isinstance(source, dict), f"{label} must be an object")
        for field in ("title", "publisher", "url"):
            expect(
                isinstance(source.get(field), str) and bool(source[field].strip()),
                f"{label} has no {field}",
            )

        try:
            parsed = urlsplit(source["url"])
        except ValueError as exc:
            raise VerificationError(f"{label} URL is invalid") from exc
        hostname = (parsed.hostname or "").lower()
        is_broadcom_host = hostname == "broadcom.com" or hostname.endswith(".broadcom.com")
        is_vmware_host = hostname == "vmware.com" or hostname.endswith(".vmware.com")
        is_vmware_github = hostname == "github.com" and parsed.path.startswith("/vmware/")
        expect(
            parsed.scheme == "https" and bool(parsed.path.strip("/")),
            f"{label} URL must be a non-root HTTPS publication URL",
        )
        expect(
            is_broadcom_host or is_vmware_host or is_vmware_github,
            f"{label} is not a Broadcom/VMware-published source",
        )

        accessed_at = source.get("accessedAt", source.get("accessTime"))
        expect(isinstance(accessed_at, str) and bool(accessed_at.strip()), f"{label} has no access time")
        try:
            datetime.fromisoformat(accessed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise VerificationError(f"{label} access time is not ISO 8601") from exc

        claims = source.get("claims", source.get("relevantClaims"))
        if isinstance(claims, str):
            valid_claims = bool(claims.strip())
        else:
            valid_claims = (
                isinstance(claims, list)
                and len(claims) > 0
                and all(isinstance(claim, str) and bool(claim.strip()) for claim in claims)
            )
        expect(valid_claims, f"{label} has no relevant claims")

    print("PASS 3: research provenance records Broadcom/VMware source metadata")


def check_migration_plan(
    plan: dict[str, Any], inventory: dict[str, Any], requirements: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    plan_schema = load_json(ROOT / "specifications" / "migration-plan.schema.json")
    SchemaValidator(plan_schema).validate(plan, plan_schema)

    expect(plan["sourceEstateId"] == inventory["estateId"], "migration sourceEstateId is wrong")
    expect(plan["targetRelease"] == inventory["targetVcfRelease"], "migration targetRelease is wrong")
    decision = plan["topologyDecision"]
    expect(decision["selectedTopology"] == "CONSOLIDATED", "selected topology must be CONSOLIDATED")
    expect(decision["licensedCores"] == requirements["entitlement"]["licensedPhysicalCores"], "licensed core count is wrong")
    expect(decision["selectedTopologyCores"] == 192, "consolidated topology core count is wrong")

    standard = snapshot["topologies"]["STANDARD"]
    cores_per_host = (
        requirements["hostProfile"]["socketsPerHost"]
        * requirements["hostProfile"]["physicalCoresPerSocket"]
    )
    standard_cores = (
        standard["minimumManagementDomainHosts"] + standard["minimumViWorkloadDomainHosts"]
    ) * cores_per_host
    rejected = decision["rejectedTopologies"]
    expect(
        rejected
        == [
            {
                "topology": "STANDARD",
                "reasonCode": "ENTITLEMENT_CORE_LIMIT",
                "requiredCores": standard_cores,
            }
        ],
        "the otherwise-supported STANDARD topology must be rejected at 224 cores",
    )

    inventory_by_id = {item["componentId"]: item for item in inventory["components"]}
    transitions = snapshot["componentTransitions"]
    steps = plan["steps"]
    expect(len(steps) == len(inventory_by_id) == len(transitions), "plan must name every inventory component once")
    expect([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "plan order must be contiguous")
    expect(len({step["componentId"] for step in steps}) == len(steps), "plan has duplicate components")

    for step, transition in zip(steps, transitions):
        component_id = transition["componentId"]
        expect(step["componentId"] == component_id, f"wrong component at plan step {transition['order']}")
        expect(component_id in inventory_by_id, f"unknown plan component {component_id}")
        source = inventory_by_id[component_id]
        expect(step["componentName"] == source["name"], f"wrong componentName for {component_id}")
        expect(step["currentVersion"] == source["currentVersion"], f"wrong currentVersion for {component_id}")
        expect(step["currentVersion"] == transition["fromVersion"], f"unsupported source for {component_id}")
        expect(step["targetVersion"] == transition["targetVersion"], f"wrong targetVersion for {component_id}")
        expect(step["action"] == transition["action"], f"wrong action for {component_id}")
        expect(step["gates"] == transition["gates"], f"wrong or unordered gates for {component_id}")

    if inventory["identityBrokerNetwork"] != "MANAGEMENT":
        raise VerificationError("the pinned identity-broker transition applies only on the management network")
    print("PASS 4: migration plan matches its schema, inventory, and pinned transition authority")


def check_powershell_implementation(expected_sddc: dict[str, Any], expected_plan: dict[str, Any]) -> None:
    module_dir = ROOT / "Vcf.GreenfieldArchitecture"
    manifest = module_dir / "Vcf.GreenfieldArchitecture.psd1"
    module = module_dir / "Vcf.GreenfieldArchitecture.psm1"
    build_script = ROOT / "Build-Architecture.ps1"
    for path in (manifest, module, build_script):
        expect(path.is_file(), f"missing PowerShell deliverable: {path.relative_to(ROOT)}")

    parser_script = r"""
$Paths = @(
  'Vcf.GreenfieldArchitecture/Vcf.GreenfieldArchitecture.psd1',
  'Vcf.GreenfieldArchitecture/Vcf.GreenfieldArchitecture.psm1',
  'Build-Architecture.ps1'
)
$failed = $false
foreach ($path in $Paths) {
  $tokens = $null
  $errors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
  if ($errors.Count -gt 0) {
    $errors | ForEach-Object { [Console]::Error.WriteLine("${path}: $($_.Message)") }
    $failed = $true
  }
}

$manifestData = Import-PowerShellDataFile -LiteralPath $Paths[0]
$installerRequirement = @(
  @($manifestData.RequiredModules) |
    Where-Object { $_.ModuleName -eq 'VMware.Sdk.Vcf.Installer' }
)
if ($installerRequirement.Count -ne 1 -or
    [string]$installerRequirement[0].RequiredVersion -ne '13.5.0.25380678') {
  [Console]::Error.WriteLine('manifest must require VMware.Sdk.Vcf.Installer 13.5.0.25380678 exactly')
  $failed = $true
}

$requiredFunctions = @(
  'New-VcfGreenfieldArchitecture',
  'New-VcfMigrationPlan',
  'Export-VcfArchitecture'
)
foreach ($functionName in $requiredFunctions) {
  if ($manifestData.FunctionsToExport -notcontains $functionName) {
    [Console]::Error.WriteLine("manifest does not export ${functionName}")
    $failed = $true
  }
}

$tokens = $null
$errors = $null
$moduleAst = [System.Management.Automation.Language.Parser]::ParseFile($Paths[1], [ref]$tokens, [ref]$errors)
$moduleCommands = @(
  $moduleAst.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
  }, $true) | ForEach-Object { $_.GetCommandName() }
)
$requiredSdkCommands = @(
  'Initialize-VcfInstallerSddcHostSpec',
  'Initialize-VcfInstallerSddcNetworkSpec',
  'Initialize-VcfInstallerSddcVcenterSpec',
  'Initialize-VcfInstallerSddcNsxtSpec',
  'Initialize-VcfInstallerSddcSpec'
)
foreach ($commandName in $requiredSdkCommands) {
  if ($moduleCommands -notcontains $commandName) {
    [Console]::Error.WriteLine("PowerShell AST has no invocation of ${commandName}")
    $failed = $true
  }
}

$tokens = $null
$errors = $null
$buildAst = [System.Management.Automation.Language.Parser]::ParseFile($Paths[2], [ref]$tokens, [ref]$errors)
$buildCommands = @(
  $buildAst.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
  }, $true) | ForEach-Object { $_.GetCommandName() }
)
if ($buildCommands -notcontains 'Export-VcfArchitecture') {
  [Console]::Error.WriteLine('Build-Architecture.ps1 does not invoke Export-VcfArchitecture')
  $failed = $true
}
if ($failed) { exit 1 }
"""
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", parser_script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    expect(result.returncode == 0, f"PowerShell parse failed: {result.stderr.strip()}")

    module_text = module.read_text(encoding="utf-8")
    for function_name in ("New-VcfGreenfieldArchitecture", "New-VcfMigrationPlan", "Export-VcfArchitecture"):
        expect(re.search(rf"function\s+{re.escape(function_name)}\b", module_text, re.IGNORECASE) is not None, f"missing {function_name}")

    forbidden_suffixes = {".dll", ".nupkg", ".psmcat", ".cat"}
    vendored = [path for path in module_dir.rglob("*") if path.is_file() and path.suffix.lower() in forbidden_suffixes]
    expect(not vendored, "do not vendor VCF PowerCLI binaries")

    with tempfile.TemporaryDirectory(prefix="vcfarch-0008-") as output_directory:
        runner = Path(output_directory) / "verify-build.ps1"
        runner.write_text(
            r"""
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $BuildScript,
    [Parameter(Mandatory)] [string] $ModuleManifest,
    [Parameter(Mandatory)] [string] $OutputDirectory
)

$ErrorActionPreference = 'Stop'
Import-Module $ModuleManifest -Force
$installer = Get-Module -Name 'VMware.Sdk.Vcf.Installer'
if ($null -eq $installer -or [string]$installer.Version -ne '13.5.0.25380678') {
    throw 'the exact VMware.Sdk.Vcf.Installer prerequisite was not loaded'
}

$requiredSdkCommands = @(
    'Initialize-VcfInstallerSddcHostSpec',
    'Initialize-VcfInstallerSddcNetworkSpec',
    'Initialize-VcfInstallerSddcVcenterSpec',
    'Initialize-VcfInstallerSddcNsxtSpec',
    'Initialize-VcfInstallerSddcSpec'
)
$global:VcfArchitectureSdkHits = @{}
$breakpoints = @(
    foreach ($commandName in $requiredSdkCommands) {
        $action = [scriptblock]::Create(
            "`$global:VcfArchitectureSdkHits['$commandName'] = `$true"
        )
        Set-PSBreakpoint -Command $commandName -Action $action
    }
)

try {
    & $BuildScript -OutputDirectory $OutputDirectory
    $missing = @($requiredSdkCommands | Where-Object { -not $global:VcfArchitectureSdkHits[$_] })
    if ($missing.Count -gt 0) {
        throw "build did not execute required SDK initializers: $($missing -join ', ')"
    }
}
finally {
    $breakpoints | Remove-PSBreakpoint
    Remove-Variable -Name VcfArchitectureSdkHits -Scope Global -ErrorAction SilentlyContinue
}
""".strip(),
            encoding="utf-8",
        )
        build_result = subprocess.run(
            [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(runner),
                "-BuildScript",
                str(build_script),
                "-ModuleManifest",
                str(manifest),
                "-OutputDirectory",
                output_directory,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
        expect(
            build_result.returncode == 0,
            "Build-Architecture.ps1 failed against VMware.Sdk.Vcf.Installer: "
            + (build_result.stdout + "\n" + build_result.stderr).strip()[-1200:],
        )
        try:
            generated_sddc = json.loads((Path(output_directory) / "sddc-spec.json").read_text(encoding="utf-8"))
            generated_plan = json.loads((Path(output_directory) / "migration-plan.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise VerificationError(f"PowerShell build did not produce valid JSON artifacts: {exc}") from exc
        expect(generated_sddc == expected_sddc, "PowerShell build does not reproduce out/sddc-spec.json")
        expect(generated_plan == expected_plan, "PowerShell build does not reproduce out/migration-plan.json")

    print("PASS 5: PowerShell module executes through the genuine SDK and reproduces both artifacts")


def main() -> int:
    try:
        # Do not put any acceptance check before this call.
        sddc = check_installer_schema_first()

        requirements = load_json(ROOT / "fixtures" / "design-requirements.json")
        inventory = load_json(ROOT / "fixtures" / "estate-inventory.json")
        snapshot = load_json(ROOT / "specifications" / "compatibility-snapshot.json")
        check_sddc_semantics(sddc, requirements, snapshot)
        check_research_sources()

        plan = load_json(ROOT / "out" / "migration-plan.json")
        check_migration_plan(plan, inventory, requirements, snapshot)
        check_powershell_implementation(sddc, plan)
    except (VerificationError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
