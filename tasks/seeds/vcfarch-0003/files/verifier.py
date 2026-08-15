#!/usr/bin/env python3
"""Deterministic acceptance verifier for the VCF architecture artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
OPENAPI_PATH = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
SDDC_SPEC_PATH = ROOT / "output/sddc-spec.json"


class VerificationError(Exception):
    pass


class SchemaValidator:
    """Small dependency-free validator for the keywords used by the pinned schemas."""

    def __init__(self, document: dict[str, Any]):
        self.document = document

    def resolve(self, reference: str) -> dict[str, Any]:
        if not reference.startswith("#/"):
            raise VerificationError(f"unsupported schema reference: {reference}")
        node: Any = self.document
        for raw in reference[2:].split("/"):
            key = raw.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or key not in node:
                raise VerificationError(f"unresolvable schema reference: {reference}")
            node = node[key]
        if not isinstance(node, dict):
            raise VerificationError(f"schema reference is not an object: {reference}")
        return node

    def validate(self, value: Any, schema: dict[str, Any], path: str = "$") -> None:
        if "$ref" in schema:
            self.validate(value, self.resolve(schema["$ref"]), path)
            return

        if "const" in schema and value != schema["const"]:
            raise VerificationError(f"{path}: expected constant {schema['const']!r}")
        if "enum" in schema and value not in schema["enum"]:
            raise VerificationError(f"{path}: value {value!r} is not in enum")

        expected = schema.get("type")
        matches = {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }
        if expected and (expected not in matches or not matches[expected]):
            raise VerificationError(f"{path}: expected {expected}, got {type(value).__name__}")

        if isinstance(value, dict):
            required = schema.get("required", [])
            for key in required:
                if key not in value:
                    raise VerificationError(f"{path}: missing required property {key!r}")
            properties = schema.get("properties", {})
            for key, child in value.items():
                if key in properties:
                    self.validate(child, properties[key], f"{path}.{key}")
                elif schema.get("additionalProperties") is False:
                    raise VerificationError(f"{path}: unexpected property {key!r}")

        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                raise VerificationError(f"{path}: too few items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                raise VerificationError(f"{path}: too many items")
            if schema.get("uniqueItems"):
                canonical = [json.dumps(item, sort_keys=True) for item in value]
                if len(canonical) != len(set(canonical)):
                    raise VerificationError(f"{path}: items must be unique")
            if "items" in schema:
                for index, item in enumerate(value):
                    self.validate(item, schema["items"], f"{path}[{index}]")

        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                raise VerificationError(f"{path}: string is shorter than minLength")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                raise VerificationError(f"{path}: string is longer than maxLength")
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                raise VerificationError(f"{path}: string does not match pattern")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                raise VerificationError(f"{path}: value is below minimum")
            if "maximum" in schema and value > schema["maximum"]:
                raise VerificationError(f"{path}: value is above maximum")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc


def validate_installer_schema_first() -> tuple[dict[str, Any], dict[str, Any]]:
    """This is intentionally the first acceptance check."""
    openapi = load_json(OPENAPI_PATH)
    sddc_spec = load_json(SDDC_SPEC_PATH)
    SchemaValidator(openapi).validate(
        sddc_spec, {"$ref": "#/components/schemas/SddcSpec"}, "$.sddcSpec"
    )
    if openapi.get("info", {}).get("version") != "9.1.0.0":
        raise VerificationError("pinned installer specification is not version 9.1.0.0")
    return openapi, sddc_spec


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def verify_greenfield(
    spec: dict[str, Any], requirements: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    target = snapshot["targetRelease"]
    green = snapshot["greenfield"]
    require(spec["sddcId"] == requirements["designId"], "wrong sddcId")
    require(spec.get("workflowType") == green["workflowType"], "design must extend the fleet")
    require(spec.get("version") == target["vcfVersion"], "wrong VCF target version")
    require(spec.get("vcfInstanceName") == requirements["names"]["vcfInstanceName"], "wrong VCF instance name")

    expected_hosts = requirements["site"]["greenfieldHosts"]
    actual_hostnames = [host["hostname"] for host in spec.get("hostSpecs", [])]
    require(len(actual_hostnames) == len(set(actual_hostnames)), "duplicate greenfield host")
    require(set(actual_hostnames) == {host["id"] for host in expected_hosts}, "greenfield host set differs from site fixture")
    require(len(actual_hostnames) >= green["minimumHosts"], "host count is below pinned minimum")

    rack_counts: dict[str, int] = {}
    for host in expected_hosts:
        rack_counts[host["rack"]] = rack_counts.get(host["rack"], 0) + 1
    required_per_rack = requirements["availability"]["requiredHostsPerRack"]
    require(set(rack_counts) == set(requirements["site"]["racks"]), "host rack set is incomplete")
    require(all(count == required_per_rack for count in rack_counts.values()), "hosts are not evenly split across racks")

    failures = requirements["availability"]["hostFailuresToTolerate"]
    survivors = sorted(expected_hosts, key=lambda host: (host["cpuCores"], host["memoryGiB"], host["rawStorageTiB"]))[:-failures]
    require(len(survivors) >= requirements["availability"]["minimumHostsRemaining"], "insufficient surviving hosts")
    cpu = sum(host["cpuCores"] for host in survivors)
    memory = sum(host["memoryGiB"] for host in survivors)
    raw_storage = sum(host["rawStorageTiB"] for host in survivors)
    capacity = requirements["capacity"]
    usable_storage = raw_storage * capacity["storageEfficiencyFactor"] / capacity["storageReplicaFactor"]
    require(cpu >= capacity["requiredCpuCoresAfterFailure"], "post-failure CPU capacity is insufficient")
    require(memory >= capacity["requiredMemoryGiBAfterFailure"], "post-failure memory capacity is insufficient")
    require(usable_storage + 1e-9 >= capacity["requiredUsableStorageTiBAfterFailure"], "post-failure usable storage is insufficient")

    names = requirements["names"]
    vcenter = spec["vcenterSpec"]
    require(vcenter["vcenterHostname"] == names["vcenterHostname"], "wrong vCenter hostname")
    require(vcenter["rootVcenterPassword"] == "${VC_ROOT_PASS}", "serialized vCenter credential must be the required placeholder")
    require(vcenter.get("useExistingDeployment") is False, "vCenter must be greenfield")
    require(vcenter.get("version") == target["componentVersions"]["VCENTER"], "vCenter target is not compatible")
    require(spec.get("sddcManagerSpec", {}).get("hostname") == names["sddcManagerHostname"], "wrong SDDC Manager hostname")
    require(spec["sddcManagerSpec"].get("useExistingDeployment") is False, "SDDC Manager must be greenfield")
    require(spec["sddcManagerSpec"].get("version") == target["componentVersions"]["SDDC_MANAGER_VCF"], "SDDC Manager target is not compatible")
    require(spec.get("clusterSpec", {}).get("datacenterName") == names["datacenterName"], "wrong datacenter name")
    require(spec["clusterSpec"].get("clusterName") == names["clusterName"], "wrong cluster name")
    require(spec.get("managementPoolName") == names["managementPoolName"], "wrong network-pool name")

    nsx = spec.get("nsxtSpec", {})
    require(nsx.get("version") == target["componentVersions"]["NSX_T_MANAGER"], "NSX target is not compatible")
    require(nsx.get("useExistingDeployment") is False, "NSX must be greenfield")
    require(nsx.get("vipFqdn") == names["nsxVipFqdn"], "wrong NSX VIP")
    managers = [node.get("hostname") for node in nsx.get("nsxtManagers", [])]
    require(managers == names["nsxManagerHostnames"], "wrong NSX manager nodes")
    require(len(managers) == green["nsxManagerNodeCount"], "wrong NSX manager node count")

    expected_networks = {item["networkType"]: item for item in requirements["networking"]["networks"]}
    actual_networks = {item["networkType"]: item for item in spec.get("networkSpecs", [])}
    require(set(actual_networks) == set(expected_networks), "network types differ from requirements")
    for network_type, expected in expected_networks.items():
        actual = actual_networks[network_type]
        for key in ("vlanId", "subnet", "gateway", "mtu"):
            require(actual.get(key) == expected[key], f"{network_type} has wrong {key}")
        ranges = actual.get("includeIpAddressRanges", [])
        require(ranges == [{"startIpAddress": expected["rangeStart"], "endIpAddress": expected["rangeEnd"]}], f"{network_type} has wrong IP range")

    require(nsx.get("transportVlanId") == expected_networks["HOST_OVERLAY"]["vlanId"], "wrong NSX transport VLAN")
    pool = nsx.get("ipAddressPoolSpec", {})
    subnets = pool.get("subnets", [])
    overlay = expected_networks["HOST_OVERLAY"]
    require(len(subnets) == 1 and subnets[0].get("cidr") == overlay["subnet"], "wrong NSX TEP subnet")
    require(subnets[0].get("gateway") == overlay["gateway"], "wrong NSX TEP gateway")
    require(subnets[0].get("ipAddressPoolRanges") == [{"start": overlay["rangeStart"], "end": overlay["rangeEnd"]}], "wrong NSX TEP pool")

    expected_switches = {item["name"]: item for item in requirements["networking"]["distributedSwitches"]}
    actual_switches = {item.get("dvsName"): item for item in spec.get("dvsSpecs", [])}
    require(set(actual_switches) == set(expected_switches), "distributed-switch set differs from requirements")
    all_nics: list[str] = []
    for name, expected in expected_switches.items():
        actual = actual_switches[name]
        require(actual.get("mtu") == expected["mtu"], f"{name} has wrong MTU")
        require(actual.get("networks") == expected["networks"], f"{name} has wrong network attachment")
        nic_map = {item["id"]: item["uplink"] for item in actual.get("vmnicsToUplinks", [])}
        require(nic_map == expected["nicMap"], f"{name} has wrong pNIC map")
        all_nics.extend(nic_map)
    expected_nics = expected_hosts[0]["physicalNics"]
    require(all(host["physicalNics"] == expected_nics for host in expected_hosts), "greenfield host pNIC layouts are not uniform")
    require(sorted(all_nics) == sorted(expected_nics), "pNICs must be assigned once across the two VDSes")

    datastore = spec.get("datastoreSpec", {}).get("vsanSpec", {})
    require(datastore.get("datastoreName") == names["vsanDatastoreName"], "wrong vSAN datastore name")
    require(datastore.get("failuresToTolerate") == green["failuresToTolerate"], "wrong vSAN FTT")
    require(datastore.get("esaConfig", {}).get("enabled") is True, "vSAN ESA must be enabled")
    require(spec.get("skipEsxThumbprintValidation") is False, "ESX thumbprint validation cannot be skipped")
    require(spec.get("skipGatewayPingValidation") is False, "gateway validation cannot be skipped")
    require(spec["dnsSpec"] == {"subdomain": requirements["networking"]["dnsDomain"], "nameservers": requirements["networking"]["dnsServers"]}, "DNS design differs from requirements")
    require(spec.get("ntpServers") == requirements["networking"]["ntpServers"], "NTP design differs from requirements")

    forbidden = requirements["fleet"]["primaryManagementDomainId"]
    require(forbidden not in json.dumps(spec, sort_keys=True), "greenfield spec references the protected management domain")


def verify_migration(
    plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any], schema: dict[str, Any]
) -> None:
    SchemaValidator(schema).validate(plan, schema)
    require(plan["estateId"] == inventory["estateId"], "migration plan has wrong estateId")
    require(plan["targetVcfVersion"] == inventory["targetVcfVersion"], "migration plan has wrong target VCF version")
    require(plan["managementDomainId"] == inventory["managementDomainId"], "migration plan has wrong management domain")

    steps = plan["steps"]
    require([step["order"] for step in steps] == list(range(1, len(steps) + 1)), "migration step order must be contiguous and deterministic")
    components = {component["id"]: component for component in inventory["components"]}
    require(len(steps) == len(components), "migration plan must have exactly one step per component")
    require({step["componentId"] for step in steps} == set(components), "migration plan component set differs from inventory")
    require(len({step["componentId"] for step in steps}) == len(steps), "migration plan contains duplicate components")

    paths = {path["componentType"]: path for path in snapshot["migrationPaths"]}
    preserve = snapshot["preservation"]
    workload_ranks: list[int] = []
    for step in steps:
        component = components[step["componentId"]]
        require(step["componentName"] == component["name"], f"wrong name for {component['id']}")
        require(step["componentType"] == component["componentType"], f"wrong type for {component['id']}")
        require(step["domainId"] == component["domainId"], f"wrong domain for {component['id']}")
        require(step["currentVersion"] == component["version"], f"wrong current version for {component['id']}")
        if not component["mutable"]:
            require(component["role"] == preserve["role"], f"immutable component {component['id']} has unexpected role")
            require(step["action"] == preserve["action"], f"management component {component['id']} must be preserved")
            require(step["targetVersion"] == component["version"], f"management component {component['id']} target changed")
            require(set(step["gates"]) == set(preserve["requiredGates"]), f"wrong preservation gates for {component['id']}")
        else:
            require(component["componentType"] in paths, f"no pinned migration path for {component['id']}")
            path = paths[component["componentType"]]
            require(component["version"] == path["fromVersion"], f"source version is outside pinned path for {component['id']}")
            require(step["action"] == "UPGRADE", f"workload component {component['id']} must be upgraded")
            require(step["targetVersion"] == path["toVersion"], f"wrong target for {component['id']}")
            require(set(step["gates"]) == set(path["requiredGates"]), f"wrong gates for {component['id']}")
            workload_ranks.append(path["sequence"])
    require(workload_ranks == sorted(workload_ranks), "workload components violate the pinned NSX/vCenter/ESX order")


def verify_research(research: dict[str, Any]) -> None:
    require(isinstance(research, dict), "research-sources.json must be an object")
    sources = research.get("sources")
    require(isinstance(sources, list), "research-sources.json must contain a sources array")
    require(len(sources) >= 2, "research must include compatibility and upgrade-path sources")

    urls: set[str] = set()
    evidence_text: list[str] = []
    for index, source in enumerate(sources):
        path = f"research source {index + 1}"
        require(isinstance(source, dict), f"{path} must be an object")
        for field in ("title", "url", "accessedAt", "decision"):
            require(isinstance(source.get(field), str) and source[field].strip(), f"{path} has no {field}")

        require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", source["accessedAt"]) is not None, f"{path} accessedAt must use YYYY-MM-DD")
        try:
            date.fromisoformat(source["accessedAt"])
        except ValueError as exc:
            raise VerificationError(f"{path} accessedAt must use YYYY-MM-DD") from exc

        parsed = urlparse(source["url"])
        hostname = (parsed.hostname or "").lower()
        require(parsed.scheme in {"http", "https"} and bool(hostname), f"{path} URL must be a web page")
        require(
            hostname == "broadcom.com" or hostname.endswith(".broadcom.com"),
            f"{path} URL must identify published Broadcom material",
        )
        normalized_url = source["url"].rstrip("/")
        require(normalized_url not in urls, "research sources contain a duplicate URL")
        urls.add(normalized_url)
        evidence_text.append(f"{source['title']} {source['url']} {source['decision']}".lower())

    evidence = " ".join(evidence_text)
    require(re.search(r"compatib|interop", evidence) is not None, "research does not identify compatibility/interoperability evidence")
    require(re.search(r"upgrad|migration|sequence", evidence) is not None, "research does not identify upgrade-path guidance")
    require("9.1" in evidence, "research decisions do not identify the VCF 9.1 target")


def powershell_ast(path: Path) -> dict[str, Any]:
    script = r"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:VCF_ARCH_PARSE_PATH, [ref]$tokens, [ref]$errors)
$functionAsts = @($ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]}, $true))
$functionCommands = [ordered]@{}
foreach ($functionAst in $functionAsts) {
  $functionCommands[$functionAst.Name] = @(
    $functionAst.Body.FindAll({param($node) $node -is [System.Management.Automation.Language.CommandAst]}, $true) |
      ForEach-Object { $_.GetCommandName() } | Where-Object { $_ } | Sort-Object -Unique
  )
}
[pscustomobject]@{
  Errors = @($errors | ForEach-Object { $_.Message })
  Commands = @($ast.FindAll({param($node) $node -is [System.Management.Automation.Language.CommandAst]}, $true) | ForEach-Object { $_.GetCommandName() } | Where-Object { $_ } | Sort-Object -Unique)
  Functions = @($functionAsts | ForEach-Object { $_.Name } | Sort-Object -Unique)
  FunctionCommands = $functionCommands
} | ConvertTo-Json -Depth 4 -Compress
"""
    completed = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", script],
        cwd=ROOT,
        env={**os.environ, "VCF_ARCH_PARSE_PATH": str(path)},
        text=True,
        capture_output=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise VerificationError(f"PowerShell parser failed for {path.relative_to(ROOT)}: {completed.stderr.strip()}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError(f"PowerShell parser returned invalid data for {path.relative_to(ROOT)}") from exc


def powershell_manifest(path: Path) -> dict[str, Any]:
    script = r"""
$data = Import-PowerShellDataFile -LiteralPath $env:VCF_ARCH_MANIFEST_PATH
$requiredModuleNames = @(
  foreach ($module in @($data.RequiredModules)) {
    if ($module -is [string]) { $module } else { $module.ModuleName }
  }
)
[pscustomobject]@{
  RootModule = $data.RootModule
  RequiredModuleNames = $requiredModuleNames
  FunctionsToExport = @($data.FunctionsToExport)
} | ConvertTo-Json -Depth 4 -Compress
"""
    completed = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", script],
        cwd=ROOT,
        env={**os.environ, "VCF_ARCH_MANIFEST_PATH": str(path)},
        text=True,
        capture_output=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise VerificationError(f"invalid PowerShell manifest {path.relative_to(ROOT)}: {completed.stderr.strip()}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError(f"PowerShell returned invalid manifest data for {path.relative_to(ROOT)}") from exc


def verify_package_layout() -> None:
    expected_module_files = {"VcfArchitecture.psd1", "VcfArchitecture.psm1"}
    module_dir = ROOT / "VcfArchitecture"
    actual_module_files = {
        str(path.relative_to(module_dir)) for path in module_dir.rglob("*") if path.is_file()
    } if module_dir.is_dir() else set()
    require(actual_module_files == expected_module_files, "VcfArchitecture package contains missing or extra files")

    expected_output_files = {"sddc-spec.json", "migration-plan.json", "research-sources.json"}
    output_dir = ROOT / "output"
    actual_output_files = {
        str(path.relative_to(output_dir)) for path in output_dir.rglob("*") if path.is_file()
    } if output_dir.is_dir() else set()
    require(actual_output_files == expected_output_files, "output package contains missing or extra files")


def verify_module() -> None:
    module_dir = ROOT / "VcfArchitecture"
    manifest_path = module_dir / "VcfArchitecture.psd1"
    module_path = module_dir / "VcfArchitecture.psm1"
    if not manifest_path.is_file() or not module_path.is_file():
        raise VerificationError("missing VcfArchitecture PowerShell module files")

    ast = powershell_ast(module_path)
    require(not ast["Errors"], f"PowerShell module has syntax errors: {ast['Errors']}")
    expected_functions = {"New-VcfGreenfieldSpec", "New-VcfEstateMigrationPlan", "Test-VcfGreenfieldSpec"}
    require(expected_functions.issubset(set(ast["Functions"])), "PowerShell module is missing required public functions")
    required_greenfield_commands = {
        "Initialize-VcfInstallerSddcSpec",
        "Initialize-VcfInstallerSddcHostSpec",
        "Initialize-VcfInstallerSddcNetworkSpec",
        "Initialize-VcfInstallerSddcVcenterSpec",
        "Initialize-VcfInstallerSddcNsxtSpec",
    }
    function_commands = ast.get("FunctionCommands", {})
    greenfield_commands = set(function_commands.get("New-VcfGreenfieldSpec", []))
    test_commands = set(function_commands.get("Test-VcfGreenfieldSpec", []))
    require(required_greenfield_commands.issubset(greenfield_commands), "New-VcfGreenfieldSpec does not construct the design with the required VMware SDK builders")
    require("Invoke-VcfInstallerValidateSddcSpec" in test_commands, "Test-VcfGreenfieldSpec does not invoke the VMware SDK validator")

    manifest = powershell_manifest(manifest_path)
    require(manifest.get("RootModule") == "VcfArchitecture.psm1", "manifest has wrong RootModule")
    require("VMware.Sdk.Vcf.Installer" in manifest.get("RequiredModuleNames", []), "manifest does not declare the VMware SDK prerequisite")
    require(expected_functions.issubset(set(manifest.get("FunctionsToExport", []))), "manifest does not export all required public functions")
    forbidden_vendor_files = [
        path for path in module_dir.rglob("*")
        if path.is_file() and (path.suffix.lower() in {".dll", ".nupkg"} or path.name.startswith("VMware.Sdk."))
    ]
    require(not forbidden_vendor_files, "VMware SDK binaries/modules must not be vendored")


def main() -> int:
    try:
        _, sddc_spec = validate_installer_schema_first()
        verify_package_layout()

        requirements = load_json(ROOT / "fixtures/design-requirements.json")
        inventory = load_json(ROOT / "fixtures/estate-inventory.json")
        snapshot = load_json(ROOT / "fixtures/compatibility-snapshot.json")
        schema_hash = hashlib.sha256(OPENAPI_PATH.read_bytes()).hexdigest()
        require(schema_hash == snapshot["installerSpecification"]["sha256"], "installer specification hash differs from the pinned snapshot")
        migration_schema = load_json(ROOT / "specifications/migration-plan.schema.json")
        migration_plan = load_json(ROOT / "output/migration-plan.json")
        research = load_json(ROOT / "output/research-sources.json")

        verify_greenfield(sddc_spec, requirements, snapshot)
        verify_migration(migration_plan, inventory, snapshot, migration_schema)
        verify_research(research)
        verify_module()
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: VCF 9.1 architecture artifacts satisfy the pinned design and compatibility contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
