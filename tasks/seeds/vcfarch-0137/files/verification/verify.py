#!/usr/bin/env python3
"""Deterministic, offline verifier for the mixed-estate architecture artifact."""

from __future__ import annotations

import json
import ipaddress
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts" / "migration-plan.json"
OPENAPI = ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
PLAN_SCHEMA = ROOT / "fixtures" / "migration-plan.schema.json"
INVENTORY = ROOT / "fixtures" / "estate-inventory.json"
SNAPSHOT = ROOT / "verification" / "compatibility-snapshot.json"
MODULE_MANIFEST = ROOT / "src" / "Vcf.MixedEstate.psd1"
MODULE_IMPLEMENTATION = ROOT / "src" / "Vcf.MixedEstate.psm1"
RESEARCH_RECORD = ROOT / "research" / "consulted-sources.json"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path: Path, label: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        fail(f"missing {label}: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {label}: {exc}")


def validate_json_schema(instance: Any, schema: Any, label: str) -> None:
    """Use PowerShell's standards-based Test-Json without network access."""

    script = r"""
param([string] $InstancePath, [string] $SchemaPath)
$ErrorActionPreference = 'Stop'
try {
    $instance = Get-Content -LiteralPath $InstancePath -Raw
    $schema = Get-Content -LiteralPath $SchemaPath -Raw
    if (-not (Test-Json -Json $instance -Schema $schema -ErrorAction Stop)) {
        exit 1
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_dir:
        temp = Path(temp_dir)
        instance_path = temp / "instance.json"
        schema_path = temp / "schema.json"
        script_path = temp / "validate.ps1"
        instance_path.write_text(
            json.dumps(instance, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        schema_path.write_text(
            json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(script_path),
                str(instance_path),
                str(schema_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "schema validation failed"
        fail(f"{label} does not validate: {detail}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def validate_research_record() -> None:
    record = load_json(RESEARCH_RECORD, "consulted research sources")
    if isinstance(record, dict):
        sources = record.get("sources")
    else:
        sources = record
    require(isinstance(sources, list) and len(sources) > 0, "research record must list consulted sources")

    for index, source in enumerate(sources, start=1):
        label = f"research source {index}"
        require(isinstance(source, dict), f"{label} must be an object")
        require(isinstance(source.get("title"), str) and bool(source["title"].strip()), f"{label} has no title")
        require(
            isinstance(source.get("relevance"), str) and bool(source["relevance"].strip()),
            f"{label} has no relevance note",
        )

        accessed = source.get("accessed", source.get("accessDate", source.get("access_date")))
        require(isinstance(accessed, str), f"{label} has no access date")
        try:
            date.fromisoformat(accessed)
        except ValueError:
            fail(f"{label} access date must use YYYY-MM-DD")

        url = source.get("url")
        require(isinstance(url, str), f"{label} has no URL")
        parsed = urlsplit(url)
        require(parsed.scheme == "https" and bool(parsed.hostname), f"{label} must use a public HTTPS URL")
        hostname = parsed.hostname.lower()
        require(
            hostname != "localhost"
            and hostname != "example.com"
            and not hostname.endswith((".localhost", ".invalid", ".test", ".example", ".example.com")),
            f"{label} URL is not a real public research source",
        )
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        require(
            address is None or (address.is_global and not address.is_reserved),
            f"{label} URL is not a public address",
        )


def run_generator(inventory: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Exercise the submitted module with a local stand-in for SDK model constructors.

    The production VMware module is deliberately a prerequisite rather than a
    vendored seed dependency. The stand-in only turns constructor arguments into
    serializable objects; the pinned OpenAPI schema remains the authority for the
    resulting SddcSpec.
    """

    constructor_names = [
        "Initialize-VcfInstallerDnsSpec",
        "Initialize-VcfInstallerNsxtManagerSpec",
        "Initialize-VcfInstallerSddcClusterSpec",
        "Initialize-VcfInstallerSddcDatastoreSpec",
        "Initialize-VcfInstallerSddcHostSpec",
        "Initialize-VcfInstallerSddcManagerSpec",
        "Initialize-VcfInstallerSddcNetworkSpec",
        "Initialize-VcfInstallerSddcNsxtSpec",
        "Initialize-VcfInstallerSddcSpec",
        "Initialize-VcfInstallerSddcVcenterSpec",
        "Initialize-VcfInstallerVcfOperationsNode",
        "Initialize-VcfInstallerVcfOperationsSpec",
    ]

    fake_module = r'''
function New-VcfInstallerModel {
    $properties = [ordered]@{}
    for ($index = 0; $index -lt $args.Count; $index += 2) {
        $argumentName = [string] $args[$index]
        if (-not $argumentName.StartsWith('-') -or $index + 1 -ge $args.Count) {
            throw "Malformed constructor argument list"
        }
        $name = $argumentName.Substring(1)
        $wireName = $name.Substring(0, 1).ToLowerInvariant() + $name.Substring(1)
        $properties[$wireName] = $args[$index + 1]
    }
    $model = [pscustomobject] $properties
    $model | Add-Member -MemberType ScriptMethod -Name ToJson -Value {
        $this | ConvertTo-Json -Depth 100 -Compress
    } -Force
    Add-Content -LiteralPath $env:VCF_INSTALLER_CALL_LOG -Value $MyInvocation.InvocationName
    $model
}

$constructors = @(
__CONSTRUCTORS__
)
foreach ($constructor in $constructors) {
    Set-Alias -Name $constructor -Value New-VcfInstallerModel -Scope Script
}
Export-ModuleMember -Function New-VcfInstallerModel -Alias $constructors
'''.replace(
        "__CONSTRUCTORS__",
        "\n".join(f"    '{name}'" for name in constructor_names),
    )
    fake_manifest = r'''@{
    RootModule = 'VMware.Sdk.Vcf.Installer.psm1'
    ModuleVersion = '99.0.0'
    GUID = 'ce7df6e7-a2b7-4b75-963b-d3826dbf0137'
    FunctionsToExport = '*'
    CmdletsToExport = @()
    AliasesToExport = '*'
}
'''
    runner = r'''
param(
    [string] $ManifestPath,
    [string] $InventoryPath,
    [string] $OutputPath
)
$ErrorActionPreference = 'Stop'
$manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
if ($manifest.RootModule -ne 'Vcf.MixedEstate.psm1') {
    throw 'module manifest must name Vcf.MixedEstate.psm1 as RootModule'
}
$requiredNames = @($manifest.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ } else { $_.ModuleName }
})
if ($requiredNames -notcontains 'VMware.Sdk.Vcf.Installer') {
    throw 'module manifest must require VMware.Sdk.Vcf.Installer'
}
if (@($manifest.FunctionsToExport) -notcontains 'New-VcfMixedEstatePlan') {
    throw 'module manifest must export New-VcfMixedEstatePlan'
}
Import-Module -Name $ManifestPath -Force -ErrorAction Stop
New-VcfMixedEstatePlan -InventoryPath $InventoryPath -OutputPath $OutputPath | Out-Null
if (-not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
    throw 'New-VcfMixedEstatePlan did not create OutputPath'
}
'''

    with tempfile.TemporaryDirectory(prefix="vcfarch-module-") as temp_dir:
        temp = Path(temp_dir)
        module_dir = temp / "modules" / "VMware.Sdk.Vcf.Installer" / "99.0.0"
        module_dir.mkdir(parents=True)
        (module_dir / "VMware.Sdk.Vcf.Installer.psm1").write_text(fake_module, encoding="utf-8")
        (module_dir / "VMware.Sdk.Vcf.Installer.psd1").write_text(fake_manifest, encoding="utf-8")
        inventory_path = temp / "input" / "inventory.json"
        inventory_path.parent.mkdir()
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        output_path = temp / "nested" / "generated-plan.json"
        runner_path = temp / "run.ps1"
        runner_path.write_text(runner, encoding="utf-8")
        call_log = temp / "constructor-calls.txt"

        environment = os.environ.copy()
        environment["PSModulePath"] = str(temp / "modules") + os.pathsep + environment.get("PSModulePath", "")
        environment["VCF_INSTALLER_CALL_LOG"] = str(call_log)
        result = subprocess.run(
            [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(runner_path),
                str(MODULE_MANIFEST),
                str(inventory_path),
                str(output_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "module execution failed"
            fail(f"PowerShell module is not executable: {detail}")
        generated = load_json(output_path, "generated migration plan")
        calls = call_log.read_text(encoding="utf-8").splitlines() if call_log.exists() else []
    return generated, calls


def main() -> None:
    # The installer schema validation is intentionally the first verification.
    # Only enough parsing is done to locate targetSddcSpec; fixtures and the
    # compatibility authority are not read until this has passed.
    plan = load_json(ARTIFACT, "migration artifact")
    target_sddc_spec = plan.get("targetSddcSpec", {}) if isinstance(plan, dict) else {}
    openapi = load_json(OPENAPI, "pinned installer OpenAPI specification")
    installer_schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$ref": "#/components/schemas/SddcSpec",
        "components": {"schemas": openapi["components"]["schemas"]},
    }
    validate_json_schema(target_sddc_spec, installer_schema, "targetSddcSpec")
    print("ok 1 - targetSddcSpec validates against installer OpenAPI schema")

    # All remaining checks are offline and use only the artifact, fixture, and
    # pinned grading snapshot.
    plan_schema = load_json(PLAN_SCHEMA, "migration plan schema")
    validate_json_schema(plan, plan_schema, "migration plan")
    inventory = load_json(INVENTORY, "estate inventory")
    snapshot = load_json(SNAPSHOT, "compatibility snapshot")

    require(plan["schemaVersion"] == "1.0", "unexpected plan schemaVersion")
    require(plan["estateId"] == inventory["estateId"], "estateId differs from inventory")
    require(
        plan["targetRelease"] == inventory["targetRelease"] == snapshot["targetRelease"],
        "target release differs from fixture or snapshot",
    )

    topology = plan["topology"]
    site = inventory["site"]
    minimum_hosts = snapshot["minimumManagementHostCount"]
    require(topology["siteCount"] == site["siteCount"] == 1, "design must be single-site")
    require(
        topology["mode"] == site["topology"] == snapshot["topology"]["mode"],
        "design must retain the consolidated topology",
    )
    require(topology["managementDomain"] == site["managementDomain"], "management domain differs")
    require(topology["cluster"] == site["cluster"], "management cluster differs")
    require(
        topology["hostCount"] == site["hostCount"] == minimum_hosts,
        "management cluster must remain at the pinned four-host minimum",
    )

    steps = plan["steps"]
    require(
        [step["sequence"] for step in steps] == list(range(1, len(steps) + 1)),
        "step sequence must be unique, contiguous, and ordered",
    )
    step_by_id = {step["componentId"]: step for step in steps}
    require(len(step_by_id) == len(steps), "each component must appear exactly once")

    inventory_by_id = {component["id"]: component for component in inventory["components"]}
    required_by_id = {
        transition["componentId"]: transition
        for transition in snapshot["requiredTransitions"]
    }
    require(
        set(step_by_id) == set(inventory_by_id) == set(required_by_id),
        "plan must cover every and only inventory component",
    )

    for component_id, component in inventory_by_id.items():
        step = step_by_id[component_id]
        expected = required_by_id[component_id]
        require(step["componentName"] == component["name"], f"{component_id}: name differs from inventory")
        require(step["fromVersion"] == component["version"], f"{component_id}: source version differs")
        require(step["fromVersion"] == expected["fromVersion"], f"{component_id}: source is unsupported")
        require(step["targetVersion"] == expected["targetVersion"], f"{component_id}: wrong target")
        require(step["action"] == expected["action"], f"{component_id}: unsupported action")
        require(
            set(step["gates"]) == set(expected["requiredGates"]),
            f"{component_id}: gates differ from pinned compatibility authority",
        )
        require(
            step["retainedHostCount"] == minimum_hosts,
            f"{component_id}: migration does not retain the minimum host count",
        )

    for forbidden in snapshot["forbiddenTransitions"]:
        step = step_by_id[forbidden["componentId"]]
        same_transition = (
            step["fromVersion"] == forbidden["fromVersion"]
            and step["targetVersion"] == forbidden["targetVersion"]
        )
        same_action = "action" not in forbidden or step["action"] == forbidden["action"]
        require(not (same_transition and same_action), f"forbidden transition used: {forbidden['reasonCode']}")

    sequence_by_id = {step["componentId"]: step["sequence"] for step in steps}
    for before, after in snapshot["requiredOrder"]:
        require(sequence_by_id[before] < sequence_by_id[after], f"{before} must precede {after}")

    inputs = inventory["targetDesignInputs"]
    spec = target_sddc_spec
    require(spec["sddcId"] == inputs["sddcId"], "target SddcSpec has wrong sddcId")
    require(spec.get("workflowType") == "VCF", "target SddcSpec workflowType must be VCF")
    require(spec.get("version") == snapshot["targetRelease"], "target SddcSpec has wrong release")

    expected_hosts = [
        component["hostname"]
        for component in inventory["components"]
        if component["kind"] == "hypervisor"
    ]
    actual_hosts = [host.get("hostname") for host in spec.get("hostSpecs", [])]
    require(actual_hosts == expected_hosts, "target SddcSpec must contain the four inventory hosts in order")
    require(len(actual_hosts) == minimum_hosts, "target SddcSpec is not at the four-host minimum")

    require(spec["dnsSpec"]["subdomain"] == inputs["subdomain"], "DNS subdomain differs")
    require(spec["dnsSpec"].get("nameservers") == inputs["nameservers"], "DNS servers differ")
    require(spec.get("ntpServers") == inputs["ntpServers"], "NTP servers differ")
    require(
        spec["vcenterSpec"].get("vcenterHostname") == inputs["vcenterHostname"],
        "vCenter hostname differs",
    )
    require(spec["vcenterSpec"].get("version") == snapshot["targetRelease"], "vCenter target differs")
    require(spec["vcenterSpec"].get("useExistingDeployment") is True, "vCenter must be reused")
    require(
        spec.get("sddcManagerSpec", {}).get("version") == snapshot["targetRelease"],
        "SDDC Manager target differs",
    )
    require(
        spec["sddcManagerSpec"].get("hostname") == inputs["sddcManagerHostname"],
        "SDDC Manager hostname differs",
    )
    require(spec["sddcManagerSpec"].get("useExistingDeployment") is True, "SDDC Manager must be reused")
    require(spec.get("nsxtSpec", {}).get("version") == snapshot["targetRelease"], "NSX target differs")
    require(spec["nsxtSpec"].get("vipFqdn") == inputs["nsxVipFqdn"], "NSX VIP differs")
    require(
        [manager.get("hostname") for manager in spec["nsxtSpec"].get("nsxtManagers", [])]
        == inputs["nsxManagerHostnames"],
        "NSX manager set differs",
    )
    require(spec["nsxtSpec"].get("useExistingDeployment") is True, "NSX must be reused")
    require(
        spec.get("vcfOperationsSpec", {}).get("version") == snapshot["targetRelease"],
        "VCF Operations target differs",
    )
    require(spec["vcfOperationsSpec"].get("useExistingDeployment") is True, "VCF Operations must be reused")
    require(
        [node.get("hostname") for node in spec["vcfOperationsSpec"].get("nodes", [])]
        == [inputs["vcfOperationsHostname"]],
        "VCF Operations node differs",
    )
    require(
        spec.get("clusterSpec", {}).get("datacenterName") == inputs["datacenterName"],
        "target datacenter differs",
    )
    require(
        spec.get("clusterSpec", {}).get("clusterName") == inputs["clusterName"],
        "target cluster differs",
    )
    require(
        spec.get("datastoreSpec", {}).get("existingDatastoreName") == inputs["datastoreName"],
        "target datastore differs",
    )

    expected_networks = {
        network["networkType"]: network for network in inputs["networks"]
    }
    actual_networks = {
        network.get("networkType"): network for network in spec.get("networkSpecs", [])
    }
    require(set(actual_networks) == set(expected_networks), "target network set differs")
    for network_type, expected in expected_networks.items():
        actual = actual_networks[network_type]
        for field in ("subnet", "gateway", "vlanId", "mtu"):
            require(actual.get(field) == expected[field], f"{network_type}: {field} differs")

    print("ok 2 - migration plan matches inventory and pinned compatibility snapshot")

    validate_research_record()
    print("ok 3 - consulted web research is recorded with public sources")

    vendored_sdk_files = [
        path.relative_to(ROOT)
        for path in ROOT.rglob("*")
        if path.is_file()
        and (
            "vmware.sdk.vcf.installer" in path.name.lower()
            or path.suffix.lower() == ".dll"
        )
    ]
    require(not vendored_sdk_files, f"VMware SDK must remain a prerequisite, not be vendored: {vendored_sdk_files}")

    generated, constructor_calls = run_generator(inventory)
    require(generated == plan, "checked-in migration artifact differs from module-generated output")
    require(
        "Initialize-VcfInstallerSddcSpec" in constructor_calls,
        "module did not construct targetSddcSpec with Initialize-VcfInstallerSddcSpec",
    )
    require(
        any(call != "Initialize-VcfInstallerSddcSpec" for call in constructor_calls),
        "module did not use SDK constructors for nested SddcSpec models",
    )

    # A second input catches implementations that merely copy the checked-in
    # artifact instead of honoring InventoryPath and OutputPath.
    alternate_inventory = json.loads(json.dumps(inventory))
    alternate_inventory["estateId"] = "verify-alt-estate"
    alternate_inventory["site"]["managementDomain"] = "verify-alt-domain"
    alternate_inventory["site"]["cluster"] = "verify-alt-cluster"
    alternate_inputs = alternate_inventory["targetDesignInputs"]
    alternate_inputs["sddcId"] = "verify-alt-sddc"
    alternate_inputs["clusterName"] = "verify-alt-target-cluster"
    alternate_inputs["nameservers"] = ["192.0.2.10", "192.0.2.11"]
    alternate_inputs["ntpServers"] = ["192.0.2.12", "192.0.2.13"]
    alternate_hosts = []
    for index, component in enumerate(
        (item for item in alternate_inventory["components"] if item["kind"] == "hypervisor"),
        start=1,
    ):
        component["hostname"] = f"verify-alt-esx{index:02d}"
        alternate_hosts.append(component["hostname"])

    alternate_plan, _ = run_generator(alternate_inventory)
    require(alternate_plan["estateId"] == "verify-alt-estate", "module does not read estateId from InventoryPath")
    require(
        alternate_plan["topology"]["managementDomain"] == "verify-alt-domain"
        and alternate_plan["topology"]["cluster"] == "verify-alt-cluster",
        "module does not read topology from InventoryPath",
    )
    alternate_spec = alternate_plan["targetSddcSpec"]
    require(alternate_spec["sddcId"] == "verify-alt-sddc", "module hard-codes target SddcSpec identity")
    require(
        alternate_spec["clusterSpec"]["clusterName"] == "verify-alt-target-cluster",
        "module hard-codes target cluster design",
    )
    require(
        [host["hostname"] for host in alternate_spec["hostSpecs"]] == alternate_hosts,
        "module hard-codes target host specifications",
    )
    require(
        alternate_spec["dnsSpec"]["nameservers"] == alternate_inputs["nameservers"]
        and alternate_spec["ntpServers"] == alternate_inputs["ntpServers"],
        "module hard-codes target DNS or NTP inputs",
    )
    validate_json_schema(alternate_spec, installer_schema, "alternate targetSddcSpec")
    validate_json_schema(alternate_plan, plan_schema, "alternate migration plan")
    print("ok 4 - module and manifest generate the checked-in plan from supplied inputs using SDK constructors")
    print("PASS")


if __name__ == "__main__":
    main()
